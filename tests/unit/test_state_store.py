import threading

import pytest

from src.config.manifest import ManifestLoader
from src.mock_infra.aws_clients import AWSClientFactory
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


def test_init_run_creates_metadata_and_pipeline_records(state_store, manifest):
    state_store.init_run("run-1", manifest)

    metadata = state_store.get_run_metadata("run-1")
    assert metadata["overall_status"] == "RUNNING"
    assert metadata["phase"] == "BRANCH"
    assert metadata["config"]["pipeline"]["id"] == manifest.pipeline.id

    pipeline = state_store.get_pipeline_status("run-1", manifest.pipeline.id)
    assert pipeline["pipeline_id"] == manifest.pipeline.id
    assert pipeline["status"] == "PENDING"
    assert pipeline["retry_count"] == 0
    assert pipeline["metrics"] == {}


def test_update_pipeline_status_modifies_only_specified_fields(state_store, manifest):
    state_store.init_run("run-2", manifest)

    state_store.update_pipeline_status(
        "run-2", manifest.pipeline.id, status="FAILED", retry_count=1
    )

    pipeline = state_store.get_pipeline_status("run-2", manifest.pipeline.id)
    assert pipeline["status"] == "FAILED"
    assert pipeline["retry_count"] == 1
    # untouched fields remain as initialized
    assert pipeline["phase"] == "BRANCH"
    assert pipeline["baseline_status"] == "PENDING"


def test_get_all_pipelines_excludes_metadata_record(state_store, manifest):
    state_store.init_run("run-3", manifest)

    pipelines = state_store.get_all_pipelines("run-3")

    assert len(pipelines) == 1
    assert pipelines[0]["pipeline_id"] == manifest.pipeline.id
    assert all(p["record_type"] != "_metadata" for p in pipelines)


def test_concurrent_updates_do_not_lose_data(state_store, manifest):
    state_store.init_run("run-4", manifest)

    def update_metric(index: int):
        state_store.update_pipeline_status(
            "run-4", manifest.pipeline.id, **{f"field_{index}": index}
        )

    threads = [threading.Thread(target=update_metric, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    pipeline = state_store.get_pipeline_status("run-4", manifest.pipeline.id)
    for i in range(10):
        assert pipeline[f"field_{i}"] == i
