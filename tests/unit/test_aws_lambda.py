import json
from unittest.mock import MagicMock, patch

import pytest

from src.aws_lambda import common
from src.config.manifest import ManifestLoader

MANIFEST_PATH = "manifests/spark-3.5-to-4.0.yaml"


@pytest.fixture
def manifest_dict():
    return ManifestLoader.load_from_file(MANIFEST_PATH).model_dump(mode="json")


def test_merge_update_overlays_partial_update_onto_full_state():
    event = {"run_id": "run-1", "phase": "BRANCH", "retry_count": 0}
    update = {"phase": "BUILD"}

    result = common.merge_update(event, update)

    assert result == {"run_id": "run-1", "phase": "BUILD", "retry_count": 0}
    # original event dict is untouched
    assert event["phase"] == "BRANCH"


def test_get_secret_is_cached_across_calls():
    common._get_secret.cache_clear()
    mock_client = MagicMock()
    mock_client.get_secret_value.return_value = {"SecretString": "shh"}

    with patch.object(common, "_secrets_client", mock_client):
        first = common._get_secret("my-secret-id")
        second = common._get_secret("my-secret-id")

    assert first == "shh"
    assert second == "shh"
    mock_client.get_secret_value.assert_called_once_with(SecretId="my-secret-id")


class TestCreateBranchesHandler:
    def test_wraps_node_and_merges_update(self, manifest_dict):
        from src.aws_lambda import create_branches_handler

        fake_update = {"baseline_branch": "b", "target_branch": "t", "phase": "BUILD"}
        event = {"run_id": "run-1", "manifest": manifest_dict}

        with (
            patch.object(create_branches_handler, "get_github_client") as mock_get_client,
            patch.object(create_branches_handler, "get_state_store"),
            patch.object(
                create_branches_handler, "make_create_branches_node"
            ) as mock_make_node,
        ):
            mock_make_node.return_value = lambda state: fake_update
            result = create_branches_handler.handler(event, context=None)

        assert result["baseline_branch"] == "b"
        assert result["run_id"] == "run-1"  # original field preserved
        mock_get_client.return_value.close.assert_called_once()


class TestPrepareExecutionHandler:
    def test_effective_spark_config_merges_branch_over_manifest(self, manifest_dict):
        from src.aws_lambda import prepare_execution_handler as peh
        from src.config.manifest import TestManifest

        manifest = TestManifest.model_validate(manifest_dict)
        github_client = MagicMock()
        github_client.get_file_content.return_value = (
            "spark_config:\n  spark.sql.ansi.enabled: \"false\"\n",
            "sha123",
        )

        effective = peh._effective_spark_config(
            github_client, manifest, "some-branch", "4.0.0"
        )

        assert effective["spark.sql.ansi.enabled"] == "false"  # branch wins, not manifest default

    def test_effective_spark_config_injects_ansi_default_for_spark4(self, manifest_dict):
        from src.aws_lambda import prepare_execution_handler as peh
        from src.config.manifest import TestManifest

        manifest = TestManifest.model_validate(manifest_dict)
        github_client = MagicMock()
        github_client.get_file_content.return_value = ("spark_config: {}\n", "sha123")

        effective = peh._effective_spark_config(
            github_client, manifest, "some-branch", "4.0.0"
        )

        assert effective["spark.sql.ansi.enabled"] == "true"

    def test_effective_spark_config_no_injection_for_spark35(self, manifest_dict):
        from src.aws_lambda import prepare_execution_handler as peh
        from src.config.manifest import TestManifest

        manifest = TestManifest.model_validate(manifest_dict)
        github_client = MagicMock()
        github_client.get_file_content.return_value = ("spark_config: {}\n", "sha123")

        effective = peh._effective_spark_config(
            github_client, manifest, "some-branch", "3.5.4"
        )

        assert "spark.sql.ansi.enabled" not in effective

    def test_build_spark_submit_parameters_includes_pyfiles_and_confs(self):
        from src.aws_lambda import prepare_execution_handler as peh

        result = peh._build_spark_submit_parameters({"spark.executor.cores": "1"})

        assert "--py-files" in result
        assert "--conf spark.executor.cores=1" in result

    def test_handler_prepares_baseline_job(self, manifest_dict):
        from src.aws_lambda import prepare_execution_handler as peh

        event = {
            "run_id": "run-1",
            "manifest": manifest_dict,
            "baseline_branch": "baseline-branch",
            "target_branch": "target-branch",
            "variant": "baseline",
        }

        mock_github_client = MagicMock()
        mock_github_client.get_file_content.side_effect = [
            ("print('hi')", "entry-script-sha"),  # spark_job.py content
            ("spark_config: {}\n", "config-sha"),  # config.yaml content
        ]

        with (
            patch.object(peh, "get_github_client", return_value=mock_github_client),
            patch.object(peh, "s3") as mock_s3,
        ):
            result = peh.handler(event, context=None)

        assert result["emr_job"]["applicationId"] == "test-baseline-app-id"
        assert "s3://test-artifacts-bucket/run-1/baseline/spark_job.py" == result["emr_job"]["entryPoint"]
        mock_s3.put_object.assert_called_once()
        mock_github_client.close.assert_called_once()

    def test_handler_prepares_validate_job(self, manifest_dict):
        from src.aws_lambda import prepare_execution_handler as peh

        event = {
            "run_id": "run-1",
            "manifest": manifest_dict,
            "baseline_branch": "baseline-branch",
            "target_branch": "target-branch",
            "variant": "validate",
        }

        with patch.object(peh, "get_github_client", return_value=MagicMock()):
            result = peh.handler(event, context=None)

        emr_job = result["emr_job"]
        assert emr_job["entryPoint"].endswith("validate_job.py")
        assert "--baseline-path" in emr_job["entryPointArguments"]
        checks_arg_index = emr_job["entryPointArguments"].index("--checks-json") + 1
        checks = json.loads(emr_job["entryPointArguments"][checks_arg_index])
        assert len(checks) == 3


class TestAnalyzeLogsHandler:
    def test_skips_log_download_when_target_already_succeeded(self, manifest_dict):
        from src.aws_lambda import analyze_logs_handler as alh

        event = {
            "run_id": "run-1",
            "manifest": manifest_dict,
            "target_branch": "target-branch",
            "target_execution": {"status": "SUCCEEDED"},
            "retry_count": 0,
        }

        with (
            patch.object(alh, "get_github_client", return_value=MagicMock()),
            patch.object(alh, "get_state_store", return_value=MagicMock()),
            patch.object(alh, "get_llm_analyzer", return_value=MagicMock()),
            patch.object(alh, "s3") as mock_s3,
        ):
            result = alh.handler(event, context=None)

        mock_s3.download_file.assert_not_called()
        assert result["phase"] == "VALIDATE"

    def test_downloads_and_decompresses_stderr_log(self, tmp_path):
        from src.aws_lambda import analyze_logs_handler as alh
        import gzip

        gz_content = b"ArithmeticException: divide by zero"
        real_download_target = tmp_path / "downloaded.gz"
        with gzip.open(real_download_target, "wb") as f:
            f.write(gz_content)

        def fake_download_file(bucket, key, local_path):
            with open(real_download_target, "rb") as src, open(local_path, "wb") as dst:
                dst.write(src.read())

        with patch.object(alh, "s3") as mock_s3:
            mock_s3.download_file.side_effect = fake_download_file
            local_path = alh._download_and_decompress_stderr("app-123", "job-456")

        assert "divide by zero" in open(local_path).read()


class TestGenerateReportHandler:
    def test_writes_report_to_s3_and_updates_state(self, manifest_dict):
        from src.aws_lambda import generate_report_handler as grh

        event = {
            "run_id": "run-1",
            "manifest": manifest_dict,
            "baseline_branch": "b",
            "target_branch": "t",
            "baseline_execution": {"status": "SUCCEEDED", "log_path": "/tmp/b.log"},
            "target_execution": {"status": "SUCCEEDED", "log_path": "/tmp/t.log"},
            "retry_count": 0,
            "status": "PASSED",
            "analysis_result": {},
            "validation_results": {},
        }

        mock_state_store = MagicMock()

        with (
            patch.object(grh, "get_state_store", return_value=mock_state_store),
            patch.object(grh, "s3") as mock_s3,
        ):
            result = grh.handler(event, context=None)

        assert result["report_path"] == "s3://test-reports-bucket/run-1/report.html"
        assert result["phase"] == "PR"
        assert mock_s3.put_object.call_count == 2
        mock_state_store.update_run_status.assert_called_once()
