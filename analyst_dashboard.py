"""
analyst_dashboard.py
Layer 7 -- Analyst Review Interface + SHAP & Counterfactual Insights
Behavioral Observability Framework for KYC Onboarding
Student: Pranali Pandharinath Supekar (2024DA04387)

Closes out Layer 7's remaining boxes: "SHAP & Counterfactual Insights"
and "Analyst Review Interface: case investigation & feedback loop".

Pulls together results already computed and stored by earlier scripts
-- no new modeling here, purely a presentation + review layer:
    shap_explanations           <- shap_explainability.py
    counterfactual_explanations <- counterfactual_analysis.py
    identity_mismatch_results   <- identity_mismatch_detection.py
    drift_report                <- drift_detection.py
    real_time_scores            <- kafka_consumer_etl.py / api.py

Adds ONE new table, analyst_review_notes, to close the feedback loop:
an analyst can mark a flagged case as Confirmed Fraud / False Positive
/ Needs Investigation with notes, which is what makes this an "Analyst
Review Interface" rather than just a read-only report viewer.

Usage:
    streamlit run analyst_dashboard.py

Requires:
    pip install streamlit plotly
"""

import datetime as dt

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import text

from db_config import get_engine

st.set_page_config(page_title="KYC Analyst Review", layout="wide")

REVIEW_TABLE = "analyst_review_notes"
CREATE_REVIEW_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {REVIEW_TABLE} (
    id SERIAL PRIMARY KEY,
    row_id BIGINT NOT NULL,
    decision TEXT NOT NULL,
    notes TEXT,
    reviewed_by TEXT,
    reviewed_at TIMESTAMP NOT NULL DEFAULT NOW()
);
"""


@st.cache_resource
def get_db_engine():
    return get_engine()


def ensure_review_table(engine):
    with engine.connect() as conn:
        conn.execute(text(CREATE_REVIEW_TABLE_SQL))
        conn.commit()


@st.cache_data(ttl=30)
def load_table(_engine, table_name: str) -> pd.DataFrame:
    try:
        return pd.read_sql(f"SELECT * FROM {table_name}", _engine)
    except Exception:
        return pd.DataFrame()


def save_review(engine, row_id: int, decision: str, notes: str, reviewed_by: str):
    with engine.connect() as conn:
        conn.execute(
            text(
                f"""INSERT INTO {REVIEW_TABLE} (row_id, decision, notes, reviewed_by)
                    VALUES (:row_id, :decision, :notes, :reviewed_by)"""
            ),
            {"row_id": row_id, "decision": decision, "notes": notes, "reviewed_by": reviewed_by},
        )
        conn.commit()


def main():
    st.title("🔍 KYC Behavioral Observability — Analyst Review")
    st.caption("Case investigation interface — SHAP attribution, counterfactual explanations, "
               "identity checks, and reviewer feedback for flagged onboarding records.")

    engine = get_db_engine()
    ensure_review_table(engine)

    shap_df = load_table(engine, "shap_explanations")
    cf_df = load_table(engine, "counterfactual_explanations")
    identity_df = load_table(engine, "identity_mismatch_results")
    drift_df = load_table(engine, "drift_report")
    reviews_df = load_table(engine, REVIEW_TABLE)

    tab_overview, tab_cases, tab_drift = st.tabs(
        ["📊 Overview", "🕵️ Case Review", "📈 Drift & Alerts"]
    )

    # -----------------------------------------------------------------
    # Overview tab
    # -----------------------------------------------------------------
    with tab_overview:
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Flagged records (SHAP-explained)", len(shap_df))
        col2.metric("Counterfactuals computed", len(cf_df))
        col3.metric("Identity mismatch cases", len(identity_df))
        col4.metric("Cases reviewed by analyst", len(reviews_df))

        if not reviews_df.empty:
            st.subheader("Review decisions so far")
            st.bar_chart(reviews_df["decision"].value_counts())

        if not identity_df.empty:
            st.subheader("Identity mismatch breakdown")
            st.dataframe(
                identity_df.groupby("scenario")["identity_mismatch"]
                .agg(flagged="sum", total="count")
                .reset_index(),
                use_container_width=True,
            )

    # -----------------------------------------------------------------
    # Case review tab -- the core "analyst interface"
    # -----------------------------------------------------------------
    with tab_cases:
        if shap_df.empty:
            st.warning("No SHAP explanations found. Run `python shap_explainability.py` first.")
        else:
            reviewed_ids = set(reviews_df["row_id"]) if not reviews_df.empty else set()

            filter_option = st.radio(
                "Show:", ["All flagged cases", "Unreviewed only", "Reviewed only"], horizontal=True
            )
            display_df = shap_df.copy()
            if filter_option == "Unreviewed only":
                display_df = display_df[~display_df["row_id"].isin(reviewed_ids)]
            elif filter_option == "Reviewed only":
                display_df = display_df[display_df["row_id"].isin(reviewed_ids)]

            display_df = display_df.sort_values("anomaly_score", ascending=False)

            selected_row_id = st.selectbox(
                f"Select a flagged record to investigate ({len(display_df)} available):",
                options=display_df["row_id"].tolist(),
                format_func=lambda rid: f"row_id={rid}  "
                                         f"(reviewed)" if rid in reviewed_ids else f"row_id={rid}",
            )

            if selected_row_id is not None:
                render_case_detail(
                    selected_row_id, shap_df, cf_df, identity_df, reviews_df, engine
                )

    # -----------------------------------------------------------------
    # Drift & alerts tab
    # -----------------------------------------------------------------
    with tab_drift:
        if drift_df.empty:
            st.info("No drift reports found yet. Run `python drift_detection.py` first.")
        else:
            latest_run = drift_df["run_timestamp"].max()
            latest = drift_df[drift_df["run_timestamp"] == latest_run]

            st.subheader(f"Latest drift check — {latest_run}")
            for _, row in latest.iterrows():
                icon = {"OK": "🟢", "WARNING": "🟡", "ALERT": "🔴"}.get(row["status"], "⚪")
                st.write(f"{icon} **{row['feature']}** — PSI={row['psi']:.4f}, "
                         f"KS p-value={row['ks_pvalue']:.6f}  [{row['status']}]")

            st.subheader("Drift history")
            st.dataframe(drift_df.sort_values("run_timestamp", ascending=False), use_container_width=True)


def render_case_detail(row_id, shap_df, cf_df, identity_df, reviews_df, engine):
    record = shap_df[shap_df["row_id"] == row_id].iloc[0]

    st.markdown(f"### Case: row_id = {row_id}")

    col1, col2 = st.columns([1, 1])

    with col1:
        st.markdown("**Anomaly Score:** " + f"{record['anomaly_score']:.4f}")
        fraud_status = record.get("fraud_bool")
        if pd.notna(fraud_status):
            st.markdown(f"**Ground-truth label:** {'Actual Fraud' if fraud_status == 1 else 'Not Fraud'}")

        st.markdown("#### Why was this flagged? (SHAP)")
        drivers = []
        values = []
        for i in (1, 2, 3):
            feat = record.get(f"top_driver_{i}")
            val = record.get(f"top_driver_{i}_shap")
            if pd.notna(feat):
                drivers.append(feat)
                values.append(val)
        if drivers:
            fig = go.Figure(go.Bar(x=values, y=drivers, orientation="h",
                                    marker_color=["crimson" if v < 0 else "steelblue" for v in values]))
            fig.update_layout(height=250, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.markdown("#### What would un-flag this record? (Counterfactual)")
        cf_row = cf_df[cf_df["row_id"] == row_id] if not cf_df.empty else pd.DataFrame()
        if not cf_row.empty:
            cf = cf_row.iloc[0]
            if pd.notna(cf.get("full_vector_flip_fraction")):
                st.write(f"Moving **{cf['full_vector_flip_fraction']:.0%}** of the way toward "
                         f"typical behavior (all features together) would un-flag this record.")
            if pd.notna(cf.get("easiest_feature_1")):
                st.write(f"**Easiest single change:** `{cf['easiest_feature_1']}` "
                         f"(shift {cf['easiest_feature_1_shift']:.0%} alone would flip it)")
            else:
                st.write("No single feature alone flips this record — it's a multi-feature anomaly.")
        else:
            st.caption("No counterfactual data available for this record. "
                       "Run `python counterfactual_analysis.py` to generate it.")

        identity_row = identity_df[identity_df["applicant_id"] == f"applicant_{row_id:04d}"] \
            if not identity_df.empty else pd.DataFrame()
        if not identity_row.empty:
            st.markdown("#### Identity Consistency")
            irow = identity_row.iloc[0]
            if irow["identity_mismatch"]:
                st.error(f"⚠️ Identity mismatch flagged — "
                         f"document_mismatch={irow['document_mismatch']}, "
                         f"biometric_mismatch={irow['biometric_mismatch']}")
            else:
                st.success("✅ No identity mismatch detected")

    st.markdown("---")
    st.markdown("#### Analyst Decision")

    prior_reviews = reviews_df[reviews_df["row_id"] == row_id] if not reviews_df.empty else pd.DataFrame()
    if not prior_reviews.empty:
        st.info(f"Previously reviewed {len(prior_reviews)} time(s):")
        st.dataframe(prior_reviews[["decision", "notes", "reviewed_by", "reviewed_at"]],
                     use_container_width=True)

    with st.form(key=f"review_form_{row_id}"):
        decision = st.selectbox("Decision", ["Confirmed Fraud", "False Positive", "Needs Investigation"])
        notes = st.text_area("Notes (optional)")
        reviewed_by = st.text_input("Reviewer name", value="Pranali Supekar")
        submitted = st.form_submit_button("Submit Review")

        if submitted:
            save_review(engine, int(row_id), decision, notes, reviewed_by)
            st.success(f"Review saved for row_id={row_id}. Refresh to see it reflected.")
            load_table.clear()


if __name__ == "__main__":
    main()
