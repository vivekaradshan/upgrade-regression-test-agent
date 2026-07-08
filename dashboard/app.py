"""Streamlit dashboard showing live upgrade test run status.

Reads workspace/state/<run_id>.json snapshots (see
StateStore.export_snapshot) rather than DynamoDB directly, since this
runs in a separate process from the orchestrator and moto's mocked
DynamoDB only lives in-memory within the process that started it.

Run with: streamlit run dashboard/app.py
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

STATE_DIR = Path(__file__).resolve().parents[1] / "workspace" / "state"

STATUS_ICONS = {
    "PASSED": "🟢",
    "SUCCEEDED": "🟢",
    "WARNING": "🟡",
    "RUNNING": "🟡",
    "PENDING": "⚪",
    "FAILED": "🔴",
}


def badge(status: str) -> str:
    return f"{STATUS_ICONS.get(status, '⚪')} {status}"


def load_snapshots() -> dict[str, dict]:
    if not STATE_DIR.exists():
        return {}

    snapshots = {}
    files = sorted(STATE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for file in files:
        try:
            snapshots[file.stem] = json.loads(file.read_text())
        except (json.JSONDecodeError, OSError):
            continue
    return snapshots


def format_duration(created_at: str, updated_at: str) -> str:
    if not created_at or not updated_at:
        return "-"
    try:
        start = datetime.fromisoformat(created_at)
        end = datetime.fromisoformat(updated_at)
    except ValueError:
        return "-"
    return str(end - start).split(".")[0]


def render(run_id: str, snapshot: dict) -> None:
    metadata = snapshot.get("metadata", {})
    pipelines = snapshot.get("pipelines", [])

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Overall status", metadata.get("overall_status", "UNKNOWN"))
    col2.metric("Phase", metadata.get("phase", "UNKNOWN"))
    col3.metric("Started", metadata.get("created_at", "-"))
    col4.metric(
        "Duration", format_duration(metadata.get("created_at", ""), metadata.get("updated_at", ""))
    )

    st.subheader("Pipeline status")

    if not pipelines:
        st.info("No pipeline records yet for this run.")
        return

    rows = []
    for pipeline in pipelines:
        validation = pipeline.get("validation_results") or {}
        rows.append(
            {
                "Pipeline": pipeline.get("pipeline_id", "-"),
                "Baseline": badge(pipeline.get("baseline_status", "PENDING")),
                "Target": badge(pipeline.get("target_status", "PENDING")),
                "Retries": pipeline.get("retry_count", 0),
                "Validation": badge(validation.get("overall_status", "-")) if validation else "-",
                "Status": badge(pipeline.get("status", "PENDING")),
            }
        )
    st.table(rows)

    report_path = metadata.get("report_path")
    if report_path and Path(report_path).exists():
        st.subheader("Report")
        # The report renders in an <iframe>, a separate browsing context that
        # only sees the OS/browser's real prefers-color-scheme - it has no
        # visibility into Streamlit's own theme picker. They'll only mismatch
        # if Streamlit's theme is manually forced away from "System".
        with open(report_path) as f:
            st.components.v1.html(f.read(), height=800, scrolling=True)
    elif report_path:
        st.info(f"Report was recorded at `{report_path}` but the file is no longer there.")


def main() -> None:
    st.set_page_config(page_title="Upgrade Test Dashboard", layout="wide")
    st.title("Upgrade Regression Test Dashboard")

    auto_refresh = st.checkbox("Auto-refresh every 10s", value=False)

    snapshots = load_snapshots()

    if not snapshots:
        st.info(
            "No runs found yet. Runs appear here once an upgrade test starts "
            f"writing snapshots to `{STATE_DIR}`."
        )
    else:
        run_id = st.selectbox("Select run", list(snapshots.keys()))
        render(run_id, snapshots[run_id])

    if auto_refresh:
        time.sleep(10)
        st.rerun()


if __name__ == "__main__":
    main()
