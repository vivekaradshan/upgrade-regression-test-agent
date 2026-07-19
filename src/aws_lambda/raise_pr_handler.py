"""Step Functions Task Lambda wrapping pr_node.py's raise_pr logic
unchanged - opens a PR with the run's result summary (diagnosis, fix
applied if any, validation results), head=target branch, base=baseline
branch. auto_merge stays enforced False by the manifest schema itself.

Also the last step of Phase 15.5's self-improving pattern library slice:
if a human approved an LLM-diagnosed fix (see approve_run_handler.py) and
the run went on to actually PASS, this proposes adding that diagnosis to
this project's own known_failure_patterns via a second PR against THIS
repo (not the pipeline repo the first PR targets) - never auto-merged,
same review discipline as any other change. Once merged, the same
failure auto-fixes via the ordinary pattern-matcher path forever after,
with no LLM call and no human approval needed.
"""

from __future__ import annotations

import re

import yaml

from src.aws_lambda.common import get_github_client, get_github_token, get_state_store, merge_update
from src.orchestrator.nodes.pr_node import make_raise_pr_node
from src.tools.github_client import GitHubClient

PATTERN_LIBRARY_OWNER = "vivekaradshan"
PATTERN_LIBRARY_REPO = "upgrade-regression-test-agent"
PATTERN_LIBRARY_MANIFEST_PATH = "manifests/spark-3.5-to-4.0.yaml"


def handler(event: dict, context) -> dict:
    manifest = event["manifest"]
    run_id = event["run_id"]
    github_client = get_github_client(manifest)
    state_store = get_state_store()

    raise_pr = make_raise_pr_node(github_client, state_store)
    try:
        update = raise_pr(event)
    finally:
        github_client.close()

    merged = merge_update(event, update)
    _maybe_propose_pattern(state_store, run_id, merged)

    return merged


def _maybe_propose_pattern(state_store, run_id: str, state: dict) -> None:
    if state.get("status") not in ("PASSED", "SUCCEEDED"):
        return

    metadata = state_store.get_run_metadata(run_id)
    if not metadata.get("approved_llm_fix"):
        return

    # analysis_result is still whatever the AWAIT_APPROVAL-triggering
    # AnalyzeLogs call set - the later "target already SUCCEEDED" pass
    # only returns {"phase": "VALIDATE"}, it doesn't touch or clear this
    # field, so it survives all the way to here unchanged.
    analysis_result = state.get("analysis_result") or {}
    fix_config = analysis_result.get("fix_config")
    diagnosis = analysis_result.get("diagnosis")
    if not fix_config or not diagnosis:
        return

    pattern_client = GitHubClient(token=get_github_token(), owner=PATTERN_LIBRARY_OWNER, repo=PATTERN_LIBRARY_REPO)
    try:
        _open_pattern_library_pr(pattern_client, run_id, diagnosis, fix_config)
    finally:
        pattern_client.close()

    state_store.record_event(run_id, phase="PR", event="pattern_library_pr_proposed", diagnosis=diagnosis)


def _open_pattern_library_pr(pattern_client: GitHubClient, run_id: str, diagnosis: str, fix_config: dict) -> None:
    main_sha = pattern_client.get_default_branch_sha("main")
    branch = f"auto/pattern-library/{run_id}"
    pattern_client.create_branch(branch, main_sha)

    content, sha = pattern_client.get_file_content(PATTERN_LIBRARY_MANIFEST_PATH, branch)
    manifest_dict = yaml.safe_load(content)
    manifest_dict["log_analysis"]["known_failure_patterns"].append(
        {
            # A verbatim escape of this run's exact diagnosis text - a
            # defensible starting point (guarantees a match on an
            # identical recurrence), explicitly flagged in the PR body as
            # needing human judgment on whether it's too narrow (won't
            # match near-miss variations) or too broad.
            "pattern": re.escape(diagnosis)[:300],
            "diagnosis": diagnosis,
            "action": "config_fix",
            "auto_fix": True,
            "fix_config": {"key": fix_config["key"], "value": fix_config["value"]},
        }
    )

    pattern_client.update_file(
        path=PATTERN_LIBRARY_MANIFEST_PATH,
        branch=branch,
        content=yaml.safe_dump(manifest_dict, sort_keys=False),
        sha=sha,
        message=f"Add known_failure_pattern from human-approved LLM fix (run {run_id})",
    )

    pattern_client.create_pull_request(
        title=f"Add known failure pattern from run {run_id}",
        body=(
            f"A human approved this LLM-diagnosed fix during run `{run_id}`:\n\n"
            f"- Diagnosis: {diagnosis}\n"
            f"- Fix: `{fix_config['key']} = {fix_config['value']}`\n\n"
            "This adds it to `known_failure_patterns` so the same failure "
            "auto-fixes via the pattern matcher next time, without needing "
            "the LLM or a human in the loop.\n\n"
            "**Review before merging** - the generated regex is a verbatim "
            "escape of this run's exact diagnosis text, which may be too "
            "narrow (won't match near-miss variations of the same "
            "underlying issue) or too broad depending on how much the "
            "message actually varies run to run."
        ),
        head=branch,
        base="main",
    )
