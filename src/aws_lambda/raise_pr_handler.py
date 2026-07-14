"""Step Functions Task Lambda wrapping pr_node.py's raise_pr logic
unchanged - opens a PR with the run's result summary (diagnosis, fix
applied if any, validation results), head=target branch, base=baseline
branch. auto_merge stays enforced False by the manifest schema itself.
"""

from __future__ import annotations

from src.aws_lambda.common import get_github_client, get_state_store, merge_update
from src.orchestrator.nodes.pr_node import make_raise_pr_node


def handler(event: dict, context) -> dict:
    manifest = event["manifest"]
    github_client = get_github_client(manifest)
    state_store = get_state_store()

    raise_pr = make_raise_pr_node(github_client, state_store)
    try:
        update = raise_pr(event)
    finally:
        github_client.close()

    return merge_update(event, update)
