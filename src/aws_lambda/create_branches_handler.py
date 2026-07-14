"""Step Functions Task Lambda wrapping branch_node.py's create_branches
logic unchanged - creates the baseline branch (unmodified copy of main)
and the target branch (main + the manifest's declared modifications),
exactly as it does locally.
"""

from __future__ import annotations

from src.aws_lambda.common import get_github_client, get_state_store, merge_update
from src.orchestrator.nodes.branch_node import make_create_branches_node


def handler(event: dict, context) -> dict:
    manifest = event["manifest"]
    github_client = get_github_client(manifest)
    state_store = get_state_store()

    create_branches = make_create_branches_node(github_client, state_store)
    try:
        update = create_branches(event)
    finally:
        github_client.close()

    return merge_update(event, update)
