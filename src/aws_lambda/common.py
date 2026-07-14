"""Shared wiring for Lambda handlers.

Each handler in this package is a thin shell: build the same dependency
objects the local orchestrator nodes already expect (GitHubClient,
StateStore, LLMAnalyzer), then call the existing, unmodified function from
src/orchestrator/nodes/*.py. This module holds the wiring so individual
handlers don't repeat it.

Step Functions passes the full orchestrator state dict as a Lambda's
event and expects the full updated state back - unlike LangGraph, it
does not merge a node's partial return into prior state automatically.
merge_update() below is what every handler uses to replicate that
merging behavior before returning.
"""

from __future__ import annotations

import functools
import os

import boto3

from src.analysis.llm_analyzer import LLMAnalyzer
from src.mock_infra.aws_clients import AWSClientFactory
from src.tools.github_client import GitHubClient
from src.tools.state_store import StateStore

_secrets_client = boto3.client("secretsmanager")


@functools.lru_cache(maxsize=None)
def _get_secret(secret_id: str) -> str:
    """Cached per warm Lambda execution environment - avoids a Secrets
    Manager call on every invocation when a container is reused."""
    return _secrets_client.get_secret_value(SecretId=secret_id)["SecretString"]


def get_github_token() -> str:
    return _get_secret(os.environ["GITHUB_TOKEN_SECRET_ID"])


def get_openai_api_key() -> str:
    return _get_secret(os.environ["OPENAI_API_KEY_SECRET_ID"])


def get_state_store() -> StateStore:
    factory = AWSClientFactory(use_mocks=False)
    return StateStore(factory.get_dynamodb_resource())


def get_github_client(manifest: dict) -> GitHubClient:
    source_control = manifest["source_control"]
    return GitHubClient(
        token=get_github_token(),
        owner=source_control["owner"],
        repo=source_control["repo"],
    )


def get_llm_analyzer(manifest: dict) -> LLMAnalyzer:
    return LLMAnalyzer(
        api_key=get_openai_api_key(),
        migration_notes=manifest["upgrade_strategy"]["migration_notes"],
    )


def merge_update(event: dict, update: dict) -> dict:
    return {**event, **update}
