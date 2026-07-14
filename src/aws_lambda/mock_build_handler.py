"""Step Functions Task Lambda wrapping build_node.py's mock_build logic
unchanged - verifies the pipeline entry script exists on both branches.
"""

from __future__ import annotations

from src.aws_lambda.common import get_github_client, get_state_store, merge_update
from src.orchestrator.nodes.build_node import make_mock_build_node


def handler(event: dict, context) -> dict:
    manifest = event["manifest"]
    github_client = get_github_client(manifest)
    state_store = get_state_store()

    mock_build = make_mock_build_node(github_client, state_store)
    try:
        update = mock_build(event)
    finally:
        github_client.close()

    return merge_update(event, update)
