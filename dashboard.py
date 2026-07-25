"""
dashboard.py
Demo Piece 4 -- Advanced Visualization Dashboard (Streamlit)

Dark-themed, multi-tab dashboard with:
  - Overview: KPIs + risk distribution + anomaly rate donut
  - Risk Analysis: feature correlation heatmap, risk-by-tier breakdown
  - High-Risk Applications: filterable/searchable table
  - Model Performance: confusion matrix, precision/recall/F1

Usage:
    streamlit run dashboard.py
"""

import pandas as pd
import numpy as np
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from sklearn.metrics import confusion_matrix, precision_score, recall_score, f1_score, accuracy_score
from db_config import get_engine

st.set_page_config(
    page_title="KYC Behavioral Observability Dashboard",
    layout="wide",
    initial_sidebar_state="expanded",
)

px.defaults.template = "plotly_dark"
DARK_BG = "#0e1117"
ACCENT = "#00cc96"
DANGER = "#ff4b4b"

FEATURE_COLS = [
    "session_velocity_score", "device_reuse_score", "address_stability_score",
    "identity_consistency_score", "geographic_risk_score", "financial_risk_score",
    "risk_anomaly_score",
]


@st.cache_data(ttl=300)
def load_data():
    engine = get_engine()
    df = pd.read_sql("SELECT * FROM anomaly_scores", engine)
    return df


def kpi_card(label: str, value: str):
    st.metric(label, value)


def main():
    st.title("🔍 Behavioral Observability Framework — KYC Onboarding")
    st.caption("Isolation Forest anomaly detection · PostgreSQL 17 · Real-time analyst view")

    df = load_data()

    st.sidebar.header("Filters")
    show_flagged_only = st.sidebar.checkbox("Show flagged anomalies only", value=False)

    min_score, max_score = float(df["risk_anomaly_score_model"].min()), float(df["risk_anomaly_score_model"].max())
    score_range = st.sidebar.slider(
        "Anomaly score range", min_value=min_score, max_value=max_score,
        value=(min_score, max_score),
    )

    filtered = df[
        (df["risk_anomaly_score_model"] >= score_range[0])
        & (df["risk_anomaly_score_model"] <= score_range[1])
    ]
    if show_flagged_only:
        filtered = filtered[filtered["flagged_anomaly"] == 1]

    st.sidebar.markdown("---")
    st.sidebar.metric("Rows after filters", f"{len(filtered):,}")

    tab_overview, tab_risk, tab_highrisk, tab_model = st.tabs(
        ["📊 Overview", "🧬 Risk Analysis", "🚨 High-Risk Applications", "🎯 Model Performance"]
    )

    # ===== TAB 1: OVERVIEW =====
    with tab_overview:
        total_records = len(filtered)
        n_anomalies = int(filtered["flagged_anomaly"].sum())
        anomaly_pct = (n_anomalies / total_records * 100) if total_records else 0

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            kpi_card("Total Records", f"{total_records:,}")
        with c2:
            kpi_card("Anomalies Flagged", f"{n_anomalies:,}")
        with c3:
            kpi_card("Anomaly Rate", f"{anomaly_pct:.2f}%")
        with c4:
            if "fraud_bool" in filtered.columns:
                detected = int(((filtered["flagged_anomaly"] == 1) & (filtered["fraud_bool"] == 1)).sum())
                kpi_card("Fraud Cases Caught", f"{detected:,}")

        col_a, col_b = st.columns([2, 1])
        with col_a:
            st.subheader("Risk Score Distribution")
            fig = px.histogram(
                filtered, x="risk_anomaly_score_model", nbins=60,
                color_discrete_sequence=[ACCENT],
            )
            fig.update_layout(paper_bgcolor=DARK_BG, plot_bgcolor=DARK_BG,
                               xaxis_title="Anomaly Score (higher = more anomalous)",
                               yaxis_title="Count", showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

        with col_b:
            st.subheader("Flagged vs Normal")
            donut_df = pd.DataFrame({
                "Category": ["Normal", "Flagged Anomaly"],
                "Count": [total_records - n_anomalies, n_anomalies],
            })
            fig = px.pie(
                donut_df, names="Category", values="Count", hole=0.55,
                color="Category",
                color_discrete_map={"Normal": "#3d4455", "Flagged Anomaly": DANGER},
            )
            fig.update_layout(paper_bgcolor=DARK_BG)
            st.plotly_chart(fig, use_container_width=True)

    # ===== TAB 2: RISK ANALYSIS =====
    with tab_risk:
        st.subheader("Feature Correlation Heatmap")
        corr = filtered[FEATURE_COLS].corr()
        fig = px.imshow(
            corr, text_auto=".2f", color_continuous_scale="RdBu_r", zmin=-1, zmax=1,
            aspect="auto",
        )
        fig.update_layout(paper_bgcolor=DARK_BG, plot_bgcolor=DARK_BG)
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("Behavioral Feature Averages: Flagged vs Normal")
        grouped = filtered.groupby("flagged_anomaly")[FEATURE_COLS].mean()
        fig = go.Figure()
        if 0 in grouped.index:
            fig.add_trace(go.Bar(name="Normal", x=FEATURE_COLS, y=grouped.loc[0].values, marker_color="#3d4455"))
        if 1 in grouped.index:
            fig.add_trace(go.Bar(name="Flagged", x=FEATURE_COLS, y=grouped.loc[1].values, marker_color=DANGER))
        fig.update_layout(barmode="group", paper_bgcolor=DARK_BG, plot_bgcolor=DARK_BG,
                           yaxis_title="Average Score")
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("Risk Tiers")
        tiered = filtered.copy()
        tiered["risk_tier"] = pd.cut(
            tiered["risk_anomaly_score_model"],
            bins=[-np.inf, tiered["risk_anomaly_score_model"].quantile(0.5),
                  tiered["risk_anomaly_score_model"].quantile(0.9), np.inf],
            labels=["Low", "Medium", "High"],
        )
        tier_counts = tiered["risk_tier"].value_counts().reindex(["Low", "Medium", "High"])
        fig = px.bar(
            x=tier_counts.index, y=tier_counts.values,
            color=tier_counts.index,
            color_discrete_map={"Low": "#3d4455", "Medium": "#f0a500", "High": DANGER},
        )
        fig.update_layout(paper_bgcolor=DARK_BG, plot_bgcolor=DARK_BG,
                           xaxis_title="Risk Tier", yaxis_title="Applications", showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    # ===== TAB 3: HIGH-RISK APPLICATIONS =====
    with tab_highrisk:
        st.subheader("Flagged Applications — Analyst Review")

        search_id = st.text_input("Search by row_id")
        high_risk = filtered[filtered["flagged_anomaly"] == 1].sort_values(
            "risk_anomaly_score_model", ascending=False
        )
        if search_id:
            try:
                high_risk = high_risk[high_risk["row_id"] == int(search_id)]
            except ValueError:
                st.warning("row_id must be a number")

        display_cols = ["row_id", "risk_anomaly_score_model"] + FEATURE_COLS
        if "fraud_bool" in high_risk.columns:
            display_cols.append("fraud_bool")

        st.dataframe(high_risk[display_cols].head(300), use_container_width=True, height=500)
        st.download_button(
            "⬇ Download flagged applications (CSV)",
            high_risk.to_csv(index=False),
            file_name="flagged_applications.csv",
            mime="text/csv",
        )

    # ===== TAB 4: MODEL PERFORMANCE =====
    with tab_model:
        if "fraud_bool" not in filtered.columns:
            st.info("fraud_bool not available -- cannot compute performance metrics.")
        else:
            y_true = filtered["fraud_bool"].astype(int)
            y_pred = filtered["flagged_anomaly"].astype(int)

            tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
            acc = accuracy_score(y_true, y_pred)
            prec = precision_score(y_true, y_pred, zero_division=0)
            rec = recall_score(y_true, y_pred, zero_division=0)
            f1 = f1_score(y_true, y_pred, zero_division=0)

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Accuracy", f"{acc*100:.2f}%")
            c2.metric("Precision", f"{prec*100:.2f}%")
            c3.metric("Recall", f"{rec*100:.2f}%")
            c4.metric("F1-Score", f"{f1*100:.2f}%")

            col_a, col_b = st.columns(2)
            with col_a:
                st.subheader("Confusion Matrix")
                cm_df = pd.DataFrame(
                    [[tn, fp], [fn, tp]],
                    index=["Actual: Normal", "Actual: Fraud"],
                    columns=["Predicted: Normal", "Predicted: Anomaly"],
                )
                fig = px.imshow(cm_df, text_auto=True, color_continuous_scale="Blues")
                fig.update_layout(paper_bgcolor=DARK_BG, plot_bgcolor=DARK_BG)
                st.plotly_chart(fig, use_container_width=True)

            with col_b:
                st.subheader("Precision / Recall / F1")
                metrics_df = pd.DataFrame({
                    "Metric": ["Precision", "Recall", "F1-Score"],
                    "Value": [prec * 100, rec * 100, f1 * 100],
                })
                fig = px.bar(metrics_df, x="Metric", y="Value", color="Metric",
                             color_discrete_sequence=[ACCENT, "#f0a500", "#636efa"])
                fig.update_layout(paper_bgcolor=DARK_BG, plot_bgcolor=DARK_BG, showlegend=False,
                                   yaxis_title="Percent (%)")
                st.plotly_chart(fig, use_container_width=True)

            st.caption(
                "Note: fraud_bool is used here only for evaluation, never during model "
                "training -- this remains a genuinely unsupervised anomaly detection result."
            )


if __name__ == "__main__":
    main()
