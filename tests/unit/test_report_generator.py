import json

from src.config.manifest import ManifestLoader
from src.reporting.report_generator import ReportGenerator

MANIFEST_PATH = "manifests/spark-3.5-to-4.0.yaml"


def test_report_generator_renders_successful_run_with_retry():
    manifest = ManifestLoader.load_from_file(MANIFEST_PATH)
    final_state = {
        "baseline_branch": "auto/upgrade-test/run-1-baseline",
        "target_branch": "auto/upgrade-test/run-1-target",
        "baseline_execution": {"status": "SUCCEEDED", "log_path": "/tmp/baseline.log"},
        "target_execution": {"status": "SUCCEEDED", "log_path": "/tmp/target.log"},
        "retry_count": 1,
        "status": "PASSED",
        "analysis_result": {
            "source": "pattern_matcher",
            "diagnosis": "ANSI SQL mode enabled by default in Spark 4.0",
            "fix_config": {"key": "spark.sql.ansi.enabled", "value": "false"},
        },
        "validation_results": {
            "overall_status": "PASSED",
            "checks": [
                {"name": "row_count_match", "status": "PASSED", "severity": "critical", "details": "1000 rows in both"},
            ],
        },
        "report_path": "",
    }

    html_report, json_report = ReportGenerator().generate("run-1", manifest, final_state)

    assert "run-1" in html_report
    assert "PASSED" in html_report
    assert "spark.sql.ansi.enabled" in html_report
    assert "row_count_match" in html_report

    parsed = json.loads(json_report)
    assert parsed["retry_count"] == 1
    assert parsed["overall_status"] == "PASSED"


def test_report_generator_renders_escalated_failure():
    manifest = ManifestLoader.load_from_file(MANIFEST_PATH)
    final_state = {
        "baseline_branch": "auto/upgrade-test/run-2-baseline",
        "target_branch": "auto/upgrade-test/run-2-target",
        "baseline_execution": {"status": "SUCCEEDED", "log_path": "/tmp/baseline.log"},
        "target_execution": {"status": "FAILED", "log_path": "/tmp/target.log"},
        "retry_count": 0,
        "status": "FAILED",
        "analysis_result": {
            "source": "pattern_matcher",
            "diagnosis": "Spark 4.0 migrated from javax.servlet to jakarta.servlet",
            "auto_fix": False,
            "fix_config": None,
        },
        "validation_results": {},
        "report_path": "",
    }

    html_report, json_report = ReportGenerator().generate("run-2", manifest, final_state)

    assert "FAILED" in html_report
    assert "javax.servlet" in html_report

    parsed = json.loads(json_report)
    assert parsed["overall_status"] == "FAILED"
    assert len(parsed["recommendations"]) > 0
