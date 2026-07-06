"""Generates the HTML and JSON test report from a finished orchestrator run."""

from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATE_DIR = Path(__file__).parent / "templates"


class ReportGenerator:
    def __init__(self):
        self._env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))

    def generate(self, run_id: str, manifest, final_state: dict) -> tuple[str, str]:
        context = self._build_context(run_id, manifest, final_state)

        template = self._env.get_template("report.html")
        html_report = template.render(**context)
        json_report = json.dumps(context, indent=2, default=str)

        return html_report, json_report

    def _build_context(self, run_id: str, manifest, final_state: dict) -> dict:
        baseline_execution = final_state.get("baseline_execution") or {}
        target_execution = final_state.get("target_execution") or {}
        validation_results = final_state.get("validation_results") or {}
        analysis_result = final_state.get("analysis_result") or None
        overall_status = final_state.get("status", "RUNNING")

        return {
            "run_id": run_id,
            "overall_status": overall_status,
            "executive_summary": self._executive_summary(overall_status, final_state),
            "pipeline_id": manifest.pipeline.id,
            "baseline_spark_version": manifest.execution.baseline_spark_version,
            "target_spark_version": manifest.execution.target_spark_version,
            "baseline_branch": final_state.get("baseline_branch", ""),
            "target_branch": final_state.get("target_branch", ""),
            "baseline_status": baseline_execution.get("status", "UNKNOWN"),
            "target_status": target_execution.get("status", "UNKNOWN"),
            "retry_count": final_state.get("retry_count", 0),
            "validation_checks": validation_results.get("checks", []),
            "analysis_result": analysis_result,
            "recommendations": self._recommendations(overall_status, analysis_result),
        }

    def _executive_summary(self, overall_status: str, final_state: dict) -> str:
        retry_count = final_state.get("retry_count", 0)

        if overall_status in ("PASSED", "SUCCEEDED"):
            if retry_count > 0:
                return (
                    f"The upgrade test PASSED after {retry_count} automatic retry. "
                    "A known Spark 4.0 breaking change was detected and corrected "
                    "automatically; baseline and target outputs match."
                )
            return "The upgrade test PASSED on the first attempt with no corrective action needed."

        if overall_status == "WARNING":
            return "The upgrade test completed with warnings - review the validation results below."

        return (
            "The upgrade test FAILED. See the failure analysis below for the "
            "root cause and why it could not be automatically corrected."
        )

    def _recommendations(self, overall_status: str, analysis_result: dict | None) -> list[str]:
        if overall_status in ("PASSED", "SUCCEEDED"):
            return []

        if analysis_result and not analysis_result.get("auto_fix", False):
            return [
                "This failure requires manual investigation before the upgrade can proceed.",
                f"Diagnosis: {analysis_result.get('diagnosis', 'unknown')}",
            ]

        return ["Review the target job's logs and retry once the root cause is addressed."]
