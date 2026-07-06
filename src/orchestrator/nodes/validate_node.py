"""Step 5 of the orchestrator: compare baseline vs target output for data parity."""

from __future__ import annotations

from pyspark.sql import SparkSession

from src.config.manifest import TestManifest
from src.orchestrator.state import UpgradeTestState
from src.tools.data_validator import DataValidator
from src.tools.state_store import StateStore


def make_validate_data_node(state_store: StateStore):
    def validate_data(state: UpgradeTestState) -> dict:
        manifest = TestManifest.model_validate(state["manifest"])
        run_id = state["run_id"]

        if state["target_execution"]["status"] != "SUCCEEDED":
            state_store.record_event(
                run_id, phase="VALIDATE", event="validation_skipped", reason="target_not_succeeded"
            )
            return {"phase": "REPORT"}

        baseline_transactions = f"{state['baseline_execution']['output_path']}/transactions"
        target_transactions = f"{state['target_execution']['output_path']}/transactions"

        spark = SparkSession.builder.appName("upgrade-test-validation").master("local[*]").getOrCreate()
        try:
            checks = [check.model_dump() for check in manifest.validation.checks]
            report = DataValidator(spark).validate(baseline_transactions, target_transactions, checks)
        finally:
            spark.stop()

        validation_results = {
            "overall_status": report.overall_status,
            "checks": [
                {"name": c.name, "status": c.status, "details": c.details, "severity": c.severity}
                for c in report.checks
            ],
        }

        state_store.update_pipeline_status(
            run_id,
            manifest.pipeline.id,
            validation_results=validation_results,
            status=report.overall_status,
        )
        state_store.record_event(
            run_id, phase="VALIDATE", event="validation_completed", overall_status=report.overall_status
        )
        state_store.update_run_status(run_id, phase="REPORT", overall_status=report.overall_status)

        return {
            "validation_results": validation_results,
            "phase": "REPORT",
            "status": report.overall_status,
        }

    return validate_data
