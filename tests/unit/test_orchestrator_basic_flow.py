import os

import pytest

from src.orchestrator.graph import run_upgrade_test
from src.tools.github_client import GitHubAPIError, GitHubClient

MANIFEST_PATH = "manifests/spark-3.5-to-4.0.yaml"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

pytestmark = pytest.mark.integration


@pytest.fixture
def cleanup_branches():
    branches_to_delete: list[str] = []
    yield branches_to_delete

    if not branches_to_delete:
        return

    gh = GitHubClient(token=GITHUB_TOKEN, owner="vivekaradshan", repo="customer-transactions-pipeline")
    for branch in branches_to_delete:
        try:
            gh.delete_branch(branch)
        except GitHubAPIError:
            pass
    gh.close()


def test_basic_flow_branches_build_execute(tmp_path, cleanup_branches):
    if not GITHUB_TOKEN:
        pytest.skip("GITHUB_TOKEN not set")

    final_state = run_upgrade_test(MANIFEST_PATH, workspace_dir=str(tmp_path))

    cleanup_branches.append(final_state["baseline_branch"])
    cleanup_branches.append(final_state["target_branch"])

    assert final_state["build_status"] == "SUCCEEDED"
    assert final_state["phase"] == "ANALYZE"

    assert final_state["baseline_execution"]["status"] == "SUCCEEDED"
    assert final_state["target_execution"]["status"] == "FAILED"

    target_log = open(final_state["target_execution"]["log_path"]).read()
    assert "ArithmeticException" in target_log or "DIVIDE_BY_ZERO" in target_log
