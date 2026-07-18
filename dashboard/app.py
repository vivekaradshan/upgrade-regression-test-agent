"""Streamlit dashboard showing live upgrade test run status.

Two modes, picked via UPGRADE_AGENT_MODE (default "local"):

- "local": reads workspace/state/<run_id>.json snapshots (see
  StateStore.export_snapshot) rather than DynamoDB directly, since this
  runs in a separate process from the orchestrator and moto's mocked
  DynamoDB only lives in-memory within the process that started it.
- "aws": reads directly from real DynamoDB via StateStore
  (AWSClientFactory(use_mocks=False)) - an AWS-triggered run has no local
  snapshot file to read - and can trigger new runs via the same
  SigV4-signed POST to API Gateway that `cli.py --target aws` uses (see
  src/tools/signed_http.py). Not hosted anywhere (ECS Express Mode and
  App Runner both turned out to require a persistent, non-scale-to-zero
  Application Load Balancer that alone exceeds this project's $5/month
  budget) - run locally with `UPGRADE_AGENT_MODE=aws streamlit run
  dashboard/app.py` against real AWS data.

Run with: streamlit run dashboard/app.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config.manifest import ManifestLoader  # noqa: E402
from src.config.settings import Settings  # noqa: E402
from src.mock_infra.aws_clients import AWSClientFactory  # noqa: E402
from src.tools.signed_http import NoCredentialsError, SignedRequestError, signed_post  # noqa: E402
from src.tools.state_store import StateStore  # noqa: E402

STATE_DIR = Path(__file__).resolve().parents[1] / "workspace" / "state"
DEFAULT_MANIFEST_PATH = Path(__file__).resolve().parents[1] / "manifests" / "spark-3.5-to-4.0.yaml"
MODE = os.environ.get("UPGRADE_AGENT_MODE", "local")

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


def load_aws_snapshots(state_store: StateStore) -> dict[str, dict]:
    """AWS-mode equivalent of load_snapshots() - builds the same
    {run_id: {metadata, pipelines}} shape render() expects, from real
    DynamoDB records instead of local files."""
    snapshots = {}
    for run in state_store.list_runs():
        run_id = run["run_id"]
        snapshots[run_id] = {
            "run_id": run_id,
            "metadata": run,
            "pipelines": state_store.get_all_pipelines(run_id),
        }
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
    if MODE == "aws":
        if report_path:
            st.subheader("Report")
            st.info(f"Report available in S3: `{report_path}`")
        return

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


def render_trigger_form(settings: Settings) -> None:
    """AWS-mode only: POSTs the bundled default manifest to API Gateway,
    the same signed request cli.py's `run --target aws` makes - Streamlit
    runs server-side, so it already has the operator's AWS credentials
    (or, if ever hosted, an instance role's) to sign with. Single fixed
    manifest since this project tests one pipeline; a manifest picker
    would be the natural next step if that changes (see the "generalizing
    beyond Spark version bumps" future enhancement in the README)."""
    st.subheader("Trigger a new run")

    if not settings.api_endpoint:
        st.warning("UPGRADE_AGENT_API_ENDPOINT is not set - cannot trigger runs.")
        return

    st.caption(f"Manifest: `{DEFAULT_MANIFEST_PATH.name}`")
    if st.button("Run customer-transactions Spark 3.5 → 4.0 upgrade test"):
        try:
            manifest = ManifestLoader.load_from_file(str(DEFAULT_MANIFEST_PATH))
            with st.spinner("Starting run..."):
                result = signed_post(
                    f"{settings.api_endpoint}/runs",
                    {"manifest": manifest.model_dump(mode="json")},
                    settings.aws_region,
                )
        except (NoCredentialsError, SignedRequestError) as e:
            st.error(str(e))
            return

        st.success(f"Started run `{result['run_id']}` (execution: {result['execution_arn']})")
        st.rerun()


def main() -> None:
    st.set_page_config(page_title="Upgrade Test Dashboard", layout="wide")
    st.title("Upgrade Regression Test Dashboard" + (" (AWS)" if MODE == "aws" else ""))

    if MODE == "aws":
        settings = Settings()
        factory = AWSClientFactory(use_mocks=False)
        state_store = StateStore(factory.get_dynamodb_resource())

        render_trigger_form(settings)
        st.divider()

        snapshots = load_aws_snapshots(state_store)
    else:
        snapshots = load_snapshots()

    auto_refresh = st.checkbox("Auto-refresh every 10s", value=False)

    if not snapshots:
        if MODE == "aws":
            st.info("No runs found yet. Trigger one above.")
        else:
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
