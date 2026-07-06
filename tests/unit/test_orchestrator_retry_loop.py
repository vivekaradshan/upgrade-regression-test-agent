import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.analysis.llm_analyzer import LLMAnalyzer
from src.config.manifest import ManifestLoader
from src.orchestrator.graph import run_upgrade_test
from src.orchestrator.nodes.analyze_node import make_analyze_logs_node
from src.tools.github_client import GitHubAPIError, GitHubClient

MANIFEST_PATH = "manifests/spark-3.5-to-4.0.yaml"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

pytestmark = pytest.mark.integration


@pytest.fixture
def cleanup_branches():
    branches_to_delete: list[str] = []
    pr_numbers_to_close: list[int] = []
    cleanup = {"branches": branches_to_delete, "pr_numbers": pr_numbers_to_close}
    yield cleanup

    if not branches_to_delete and not pr_numbers_to_close:
        return

    gh = GitHubClient(token=GITHUB_TOKEN, owner="vivekaradshan", repo="customer-transactions-pipeline")
    for pr_number in pr_numbers_to_close:
        try:
            gh.close_pull_request(pr_number)
        except GitHubAPIError:
            pass
    for branch in branches_to_delete:
        try:
            gh.delete_branch(branch)
        except GitHubAPIError:
            pass
    gh.close()


def test_full_flow_detects_ansi_failure_and_retries_to_success(tmp_path, cleanup_branches):
    """The money test: execute fails on ANSI mode -> analyze detects the known
    pattern -> auto-fix is committed to the target branch -> retry succeeds ->
    validation passes -> report generated -> PR raised."""
    if not GITHUB_TOKEN:
        pytest.skip("GITHUB_TOKEN not set")

    final_state = run_upgrade_test(MANIFEST_PATH, workspace_dir=str(tmp_path))

    cleanup_branches["branches"].append(final_state["baseline_branch"])
    cleanup_branches["branches"].append(final_state["target_branch"])
    if final_state.get("pr_url"):
        cleanup_branches["pr_numbers"].append(int(final_state["pr_url"].rstrip("/").split("/")[-1]))

    assert final_state["retry_count"] == 1
    assert final_state["analysis_result"]["source"] == "pattern_matcher"
    assert final_state["analysis_result"]["fix_config"] == {
        "key": "spark.sql.ansi.enabled",
        "value": "false",
    }

    assert final_state["baseline_execution"]["status"] == "SUCCEEDED"
    assert final_state["target_execution"]["status"] == "SUCCEEDED"

    assert final_state["validation_results"]["overall_status"] == "PASSED"
    check_names = {c["name"] for c in final_state["validation_results"]["checks"]}
    assert check_names == {"row_count_match", "schema_match", "column_level_diff"}

    assert final_state["phase"] == "DONE"
    assert final_state["report_path"]
    assert Path(final_state["report_path"]).exists()
    assert final_state["pr_url"].startswith(
        "https://github.com/vivekaradshan/customer-transactions-pipeline/pull/"
    )


def test_analyze_logs_escalates_when_not_auto_fixable(tmp_path):
    manifest = ManifestLoader.load_from_file(MANIFEST_PATH)

    log_path = tmp_path / "target.log"
    log_path.write_text(
        "ClassNotFoundException: javax.servlet.ServletException not found\n"
    )

    github_client = MagicMock()
    state_store = MagicMock()
    llm_analyzer = LLMAnalyzer(api_key="")

    analyze_logs = make_analyze_logs_node(github_client, state_store, llm_analyzer)

    state = {
        "run_id": "run-test-escalate",
        "manifest": manifest.model_dump(mode="json"),
        "target_branch": "irrelevant",
        "target_execution": {"status": "FAILED", "log_path": str(log_path)},
        "retry_count": 0,
    }

    result = analyze_logs(state)

    assert result["phase"] == "REPORT"
    assert result["status"] == "FAILED"
    assert result["analysis_result"]["auto_fix"] is False
    github_client.update_file.assert_not_called()
    state_store.update_pipeline_status.assert_called_once()
    assert state_store.update_pipeline_status.call_args.kwargs["status"] == "FAILED"


def test_analyze_logs_escalates_when_retries_exhausted(tmp_path):
    manifest = ManifestLoader.load_from_file(MANIFEST_PATH)
    max_retries = manifest.log_analysis.retry.max_retries

    log_path = tmp_path / "target.log"
    log_path.write_text("org.apache.spark.SparkArithmeticException: [DIVIDE_BY_ZERO] Division by zero\n")

    github_client = MagicMock()
    state_store = MagicMock()
    llm_analyzer = LLMAnalyzer(api_key="")

    analyze_logs = make_analyze_logs_node(github_client, state_store, llm_analyzer)

    state = {
        "run_id": "run-test-exhausted",
        "manifest": manifest.model_dump(mode="json"),
        "target_branch": "irrelevant",
        "target_execution": {"status": "FAILED", "log_path": str(log_path)},
        "retry_count": max_retries,
    }

    result = analyze_logs(state)

    assert result["phase"] == "REPORT"
    assert result["status"] == "FAILED"
    github_client.update_file.assert_not_called()
