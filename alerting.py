"""
alerting.py
Stage 7 -- Live Alert Notification (Visualization & Monitoring Layer)
Behavioral Observability Framework for KYC Onboarding
Student: Pranali Pandharinath Supekar (2024DA04387)

Sends a notification when drift_detection.py finds an ALERT or WARNING
status -- closes the loop from "detected" to "someone actually finds
out," per Layer 7's "Alert Monitoring & Management" box.

DESIGN CHOICE: uses a generic webhook (one HTTP POST with a JSON
payload) rather than SMTP email. This works out-of-the-box with Slack
incoming webhooks, Discord webhooks, Microsoft Teams connectors, or any
custom endpoint expecting JSON -- and doesn't require managing email
credentials for a PoC demo. If DRIFT_ALERT_WEBHOOK_URL isn't set, this
degrades gracefully: it prints what WOULD have been sent instead of
silently doing nothing or crashing, so the drift pipeline still runs
end-to-end without a webhook configured.

--- CHANGE LOG (added to fix a real bug found via integration testing) ---
Replaced the emoji status icons (red circle / yellow circle, U+1F534 /
U+1F7E1) with plain ASCII equivalents ([ALERT] / [WARN]). Same failure
mode as the drift_detection.py fix: these characters displayed fine in
an interactive Windows terminal, but crashed with a UnicodeEncodeError
under cp1252 the moment this script's print output was captured
non-interactively (confirmed live via a genuine drift ALERT firing
during an integration test run -- address_stability_score legitimately
triggered ALERT status on real accumulated live data, which is exactly
the code path that hit this crash). No message content, formatting
logic, or webhook-delivery behavior was changed -- only the two emoji
characters.
----------------------------------------------------------------------

Setup (optional):
    Set an environment variable before running drift_detection.py:
        set DRIFT_ALERT_WEBHOOK_URL=https://hooks.slack.com/services/...
    (Windows) or
        export DRIFT_ALERT_WEBHOOK_URL=https://hooks.slack.com/services/...
    (bash). Get a Slack webhook URL from https://api.slack.com/messaging/webhooks

Requires:
    pip install requests
"""

import os

import pandas as pd
import requests

WEBHOOK_URL_ENV_VAR = "DRIFT_ALERT_WEBHOOK_URL"
REQUEST_TIMEOUT_SECONDS = 5

# ASCII-safe status icons -- see CHANGE LOG above.
ICON_ALERT = "[ALERT]"
ICON_WARNING = "[WARN]"


def build_alert_message(report: pd.DataFrame, live_row_count: int, is_synthetic: bool) -> str:
    """
    Composes a human-readable summary of any ALERT/WARNING rows in the
    drift report. Pure function (no I/O) so it's independently
    testable without a live webhook.
    """
    flagged = report[report["status"].isin(["ALERT", "WARNING"])]
    if flagged.empty:
        return ""

    lines = [
        f"*KYC Drift Detection* -- {len(flagged)} feature(s) flagged "
        f"(live sample: {live_row_count:,} rows{' -- SYNTHETIC DEMO DATA' if is_synthetic else ''})",
    ]
    for _, row in flagged.sort_values("status", ascending=False).iterrows():
        icon = ICON_ALERT if row["status"] == "ALERT" else ICON_WARNING
        caveat = " _(low confidence -- small sample)_" if row.get("sample_size_warning") else ""
        lines.append(
            f"{icon} `{row['feature']}` -- PSI={row['psi']:.4f}, "
            f"KS_p={row['ks_pvalue']:.6f} [{row['status']}]{caveat}"
        )
    return "\n".join(lines)


def send_drift_alert(report: pd.DataFrame, live_row_count: int, is_synthetic: bool) -> bool:
    """
    Sends the alert if any ALERT/WARNING rows exist and a webhook URL
    is configured. Returns True if a notification was sent (or would
    have been sent, when no webhook is configured -- see docstring),
    False if there was nothing to alert on.
    """
    message = build_alert_message(report, live_row_count, is_synthetic)
    if not message:
        return False  # nothing flagged -- no alert needed

    webhook_url = os.environ.get(WEBHOOK_URL_ENV_VAR)

    if not webhook_url:
        print(f"  [alerting] No {WEBHOOK_URL_ENV_VAR} configured -- would have sent:")
        print("  " + message.replace("\n", "\n  "))
        print(f"  [alerting] Set {WEBHOOK_URL_ENV_VAR} to actually deliver this to Slack/Teams/Discord.")
        return True

    try:
        response = requests.post(
            webhook_url, json={"text": message}, timeout=REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        print(f"  [alerting] Alert sent successfully to configured webhook.")
        return True
    except requests.RequestException as e:
        # A failed notification should never crash the drift pipeline
        # itself -- the drift_report row is already written to
        # PostgreSQL regardless, so the finding isn't lost even if
        # the notification fails.
        print(f"  [alerting] WARNING: failed to send webhook notification: {e}")
        print("  " + message.replace("\n", "\n  "))
        return False
