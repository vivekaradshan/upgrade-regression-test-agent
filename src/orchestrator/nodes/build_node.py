"""Step 2 of the orchestrator: verify the pipeline entry script exists on
both branches. Stand-in for a real CI build; the real thing (Jenkins/Jules)
would compile/package the pipeline instead of just checking file presence.
"""

from __future__ import annotations

from src.config.manifest import TestManifest
from src.orchestrator.state import UpgradeTestState
from src.tools.github_client import GitHubAPIError, GitHubClient
from src.tools.state_store import StateStore


def make_mock_build_node(github_client: GitHubClient, state_store: StateStore):
    def mock_build(state: UpgradeTestState) -> dict:
        manifest = TestManifest.model_validate(state["manifest"])
        run_id = state["run_id"]
        entry_script = manifest.pipeline.entry_script

        for branch in (state["baseline_branch"], state["target_branch"]):
            try:
                github_client.get_file_content(entry_script, branch)
            except GitHubAPIError as e:
                state_store.record_event(
                    run_id, phase="BUILD", event="build_failed", branch=branch, error=str(e)
                )
                state_store.update_run_status(run_id, phase="BUILD", status="FAILED")
                return {
                    "build_status": "FAILED",
                    "phase": "BUILD",
                    "status": "FAILED",
                    "error": f"entry script {entry_script} missing on branch {branch}",
                }

        state_store.record_event(run_id, phase="BUILD", event="build_verified")
        state_store.update_run_status(run_id, phase="EXECUTE")

        return {"build_status": "SUCCEEDED", "phase": "EXECUTE"}

    return mock_build
