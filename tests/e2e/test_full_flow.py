"""Full end-to-end test driving the actual CLI as a subprocess (not
run_upgrade_test() called in-process, which is what the other integration
tests in tests/unit/test_orchestrator_retry_loop.py exercise) - this is
the real user-facing interface: run -> status -> cleanup.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.tools.github_client import GitHubAPIError, GitHubClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = "manifests/spark-3.5-to-4.0.yaml"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
OWNER = "vivekaradshan"
REPO = "customer-transactions-pipeline"

pytestmark = pytest.mark.e2e


def _run_cli(*args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "src.cli", *args],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture
def cleanup_pr():
    pr_numbers: list[int] = []
    yield pr_numbers

    if not pr_numbers:
        return

    gh = GitHubClient(token=GITHUB_TOKEN, owner=OWNER, repo=REPO)
    for pr_number in pr_numbers:
        try:
            gh.close_pull_request(pr_number)
        except GitHubAPIError:
            pass
    gh.close()


def test_full_flow_via_cli_run_status_cleanup(cleanup_pr):
    """Drives the whole system through the real CLI: run detects the ANSI
    failure, auto-fixes, retries to success, validates, reports, and opens
    a PR; status reads that run back; cleanup removes the branches."""
    if not GITHUB_TOKEN:
        pytest.skip("GITHUB_TOKEN not set")

    # 1. run
    run_result = _run_cli("run", "--manifest", MANIFEST_PATH)
    assert run_result.returncode == 0, run_result.stderr

    run_summary = json.loads(run_result.stdout)
    run_id = run_summary["run_id"]

    if run_summary.get("pr_url"):
        cleanup_pr.append(int(run_summary["pr_url"].rstrip("/").split("/")[-1]))

    assert run_summary["phase"] == "DONE"
    assert run_summary["status"] == "PASSED"
    assert run_summary["retry_count"] == 1
    assert run_summary["report_path"]
    assert Path(run_summary["report_path"]).exists()
    assert run_summary["pr_url"].startswith(f"https://github.com/{OWNER}/{REPO}/pull/")

    try:
        # 2. status
        status_result = _run_cli("status", "--run-id", run_id)
        assert status_result.returncode == 0, status_result.stderr

        snapshot = json.loads(status_result.stdout)
        assert snapshot["run_id"] == run_id
        assert snapshot["metadata"]["overall_status"] == "PASSED"

        pipelines = snapshot["pipelines"]
        assert len(pipelines) == 1
        pipeline = pipelines[0]
        assert pipeline["baseline_status"] == "SUCCEEDED"
        assert pipeline["target_status"] == "SUCCEEDED"
        assert pipeline["retry_count"] == 1
        assert pipeline["validation_results"]["overall_status"] == "PASSED"

        # verify the branches genuinely exist on GitHub before cleanup
        gh = GitHubClient(token=GITHUB_TOKEN, owner=OWNER, repo=REPO)
        branches = {b["name"] for b in gh._request("GET", f"/repos/{OWNER}/{REPO}/branches").json()}
        gh.close()
        baseline_branch = f"auto/upgrade-test/{run_id}-baseline"
        target_branch = f"auto/upgrade-test/{run_id}-target"
        assert baseline_branch in branches
        assert target_branch in branches
    finally:
        # 3. cleanup (always attempted, even if assertions above failed)
        cleanup_result = _run_cli("cleanup", "--run-id", run_id)
        assert cleanup_result.returncode == 0, cleanup_result.stderr

    gh = GitHubClient(token=GITHUB_TOKEN, owner=OWNER, repo=REPO)
    remaining_branches = {b["name"] for b in gh._request("GET", f"/repos/{OWNER}/{REPO}/branches").json()}
    gh.close()
    assert baseline_branch not in remaining_branches
    assert target_branch not in remaining_branches


def test_status_command_reports_error_for_unknown_run():
    result = _run_cli("status", "--run-id", "run-does-not-exist-xyz")

    assert result.returncode == 1
    assert "No snapshot found" in result.stderr
