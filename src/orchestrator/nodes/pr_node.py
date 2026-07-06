"""Step 7 of the orchestrator: raise a PR summarizing the test run.

Head is always the target branch (which carries both the initial
spark_version bump and any auto-fix commits from analyze_logs), base is
the manifest's baseline branch (typically main) - so the PR diff shows
exactly the config changes an upgrade requires. auto_merge is always
False (enforced by the manifest schema itself); this only ever creates
the PR for human review, never merges it.
"""

from __future__ import annotations

from src.config.manifest import TestManifest
from src.orchestrator.state import UpgradeTestState
from src.tools.github_client import GitHubClient
from src.tools.state_store import StateStore


def make_raise_pr_node(github_client: GitHubClient, state_store: StateStore):
    def raise_pr(state: UpgradeTestState) -> dict:
        manifest = TestManifest.model_validate(state["manifest"])
        run_id = state["run_id"]
        pr_config = manifest.source_control.pr

        if not pr_config.auto_create:
            state_store.record_event(
                run_id, phase="PR", event="pr_skipped", reason="auto_create_disabled"
            )
            return {"phase": "DONE"}

        overall_status = state.get("status", "RUNNING")
        title = (
            f"Spark {manifest.execution.baseline_spark_version} -> "
            f"{manifest.execution.target_spark_version} upgrade: "
            f"{manifest.pipeline.id} [{overall_status}]"
        )

        pr = github_client.create_pull_request(
            title=title,
            body=_build_pr_body(manifest, state),
            head=state["target_branch"],
            base=manifest.source_control.baseline.branch,
        )
        pr_url = pr["html_url"]

        state_store.update_run_status(run_id, phase="DONE", pr_url=pr_url)
        state_store.record_event(run_id, phase="PR", event="pr_created", pr_url=pr_url)

        return {"pr_url": pr_url, "phase": "DONE"}

    return raise_pr


def _build_pr_body(manifest: TestManifest, state: UpgradeTestState) -> str:
    overall_status = state.get("status", "RUNNING")
    analysis_result = state.get("analysis_result") or {}
    validation_results = state.get("validation_results") or {}

    lines = [
        f"## Upgrade Test Result: {overall_status}",
        "",
        f"- Pipeline: `{manifest.pipeline.id}`",
        f"- Baseline: Spark {manifest.execution.baseline_spark_version}",
        f"- Target: Spark {manifest.execution.target_spark_version}",
        f"- Retries: {state.get('retry_count', 0)}",
        "",
    ]

    if analysis_result:
        heading = "What broke and how it was fixed" if analysis_result.get("fix_config") else "Failure analysis"
        lines.append(f"### {heading}")
        lines.append(f"- Diagnosis: {analysis_result.get('diagnosis', 'n/a')}")
        if analysis_result.get("fix_config"):
            fix = analysis_result["fix_config"]
            lines.append(f"- Fix applied: `{fix['key']} = {fix['value']}`")
        lines.append("")

    if validation_results:
        lines.append("### Validation results")
        for check in validation_results.get("checks", []):
            lines.append(
                f"- **{check['name']}**: {check['status']} ({check['severity']}) - {check['details']}"
            )
        lines.append("")

    lines.append(f"Full report: `{state.get('report_path', 'N/A')}`")

    return "\n".join(lines)
