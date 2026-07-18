from unittest.mock import MagicMock

import pytest

from src.config.manifest import ManifestLoader
from src.mock_infra.aws_clients import AWSClientFactory
from src.orchestrator.nodes.branch_node import make_create_branches_node
from src.tools.state_store import StateStore

MANIFEST_PATH = "manifests/spark-3.5-to-4.0.yaml"


@pytest.fixture
def manifest():
    return ManifestLoader.load_from_file(MANIFEST_PATH)


@pytest.fixture
def state_store():
    with AWSClientFactory(use_mocks=True) as factory:
        store = StateStore(factory.get_dynamodb_resource())
        store.create_table()
        yield store


def test_create_branches_persists_branch_names_on_metadata_record(manifest, state_store):
    state_store.init_run("run-1", manifest)

    github_client = MagicMock()
    github_client.get_default_branch_sha.return_value = "main-sha"
    github_client.get_file_content.return_value = ("spark_version: 3.5.4\n", "config-sha")

    create_branches = make_create_branches_node(github_client, state_store)
    result = create_branches({"run_id": "run-1", "manifest": manifest.model_dump(mode="json")})

    metadata = state_store.get_run_metadata("run-1")

    # Previously only written to the append-only event log
    # (record_event), not the queryable _metadata record the dashboard
    # actually reads - so branch names were undiscoverable from there.
    assert metadata["baseline_branch"] == result["baseline_branch"]
    assert metadata["target_branch"] == result["target_branch"]
    assert result["baseline_branch"].endswith("run-1-baseline")
    assert result["target_branch"].endswith("run-1-target")
