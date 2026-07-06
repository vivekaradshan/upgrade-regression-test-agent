"""Step 1 of the orchestrator: create baseline/target branches and apply
the manifest's target-branch modifications (e.g. bumping spark_version).
"""

from __future__ import annotations

from src.config.manifest import TestManifest
from src.orchestrator.state import UpgradeTestState
from src.tools.github_client import GitHubClient
from src.tools.state_store import StateStore


def make_create_branches_node(github_client: GitHubClient, state_store: StateStore):
    def create_branches(state: UpgradeTestState) -> dict:
        manifest = TestManifest.model_validate(state["manifest"])
        run_id = state["run_id"]
        source_control = manifest.source_control

        main_sha = github_client.get_default_branch_sha(source_control.baseline.branch)

        baseline_branch = f"{source_control.target.branch_prefix}/{run_id}-baseline"
        target_branch = f"{source_control.target.branch_prefix}/{run_id}-target"

        github_client.create_branch(baseline_branch, main_sha)
        github_client.create_branch(target_branch, main_sha)

        for modification in source_control.target.modifications:
            content, sha = github_client.get_file_content(modification.file, target_branch)
            for change in modification.changes:
                content = content.replace(change.from_value, change.to_value)
            github_client.update_file(
                path=modification.file,
                branch=target_branch,
                content=content,
                sha=sha,
                message=f"upgrade test {run_id}: apply target modifications",
            )

        state_store.record_event(
            run_id,
            phase="BRANCH",
            event="branches_created",
            baseline_branch=baseline_branch,
            target_branch=target_branch,
        )
        state_store.update_run_status(run_id, phase="BUILD")

        return {
            "baseline_branch": baseline_branch,
            "target_branch": target_branch,
            "phase": "BUILD",
        }

    return create_branches
