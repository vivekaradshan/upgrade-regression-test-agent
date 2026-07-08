"""Step 6 of the orchestrator: generate the HTML/JSON test report."""

from __future__ import annotations

from pathlib import Path

from src.config.manifest import TestManifest
from src.orchestrator.state import UpgradeTestState
from src.reporting.report_generator import ReportGenerator
from src.tools.state_store import StateStore


def make_generate_report_node(state_store: StateStore, reports_dir: str):
    def generate_report(state: UpgradeTestState) -> dict:
        manifest = TestManifest.model_validate(state["manifest"])
        run_id = state["run_id"]

        html_report, json_report = ReportGenerator().generate(run_id, manifest, state)

        run_reports_dir = Path(reports_dir) / run_id
        run_reports_dir.mkdir(parents=True, exist_ok=True)
        html_path = run_reports_dir / "report.html"
        json_path = run_reports_dir / "report.json"
        html_path.write_text(html_report)
        json_path.write_text(json_report)

        state_store.record_event(
            run_id, phase="REPORT", event="report_generated", report_path=str(html_path)
        )
        state_store.update_run_status(run_id, phase="PR", report_path=str(html_path))

        return {"report_path": str(html_path), "phase": "PR"}

    return generate_report
