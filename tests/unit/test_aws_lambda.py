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

    def test_effective_spark_config_strips_spark_master(self, manifest_dict):
        from src.aws_lambda import prepare_execution_handler as peh
        from src.config.manifest import TestManifest

        manifest = TestManifest.model_validate(manifest_dict)
        github_client = MagicMock()
        github_client.get_file_content.return_value = ("spark_config: {}\n", "sha123")

        effective = peh._effective_spark_config(
            github_client, manifest, "some-branch", "3.5.4"
        )

        # manifest.pipeline.spark_config sets spark.master for local runs -
        # EMR Serverless rejects this outright (confirmed via a real
        # failed job run: ValidationException, "Option 'spark.master' is
        # not supported").
        assert "spark.master" not in effective

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
        mock_state_store = MagicMock()

        with (
            patch.object(peh, "get_github_client", return_value=mock_github_client),
            patch.object(peh, "get_state_store", return_value=mock_state_store),
            patch.object(peh, "s3") as mock_s3,
        ):
            result = peh.handler(event, context=None)

        assert result["emr_job"]["applicationId"] == "test-baseline-app-id"
        assert "s3://test-artifacts-bucket/run-1/baseline/spark_job.py" == result["emr_job"]["entryPoint"]
        mock_s3.put_object.assert_called_once()
        mock_github_client.close.assert_called_once()

    def test_handler_marks_baseline_status_running_before_dispatching_job(self, manifest_dict):
        from src.aws_lambda import prepare_execution_handler as peh

        event = {
            "run_id": "run-1",
            "manifest": manifest_dict,
            "baseline_branch": "baseline-branch",
            "target_branch": "target-branch",
            "variant": "target",
        }

        mock_github_client = MagicMock()
        mock_github_client.get_file_content.side_effect = [
            ("print('hi')", "entry-script-sha"),
            ("spark_config: {}\n", "config-sha"),
        ]
        mock_state_store = MagicMock()

        with (
            patch.object(peh, "get_github_client", return_value=mock_github_client),
            patch.object(peh, "get_state_store", return_value=mock_state_store),
            patch.object(peh, "s3"),
        ):
            peh.handler(event, context=None)

        mock_state_store.update_pipeline_status.assert_called_once_with(
            "run-1", "customer-transactions", target_status="RUNNING"
        )

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
            # EMR Serverless reports "SUCCESS", not the local mock's
            # "SUCCEEDED" - the handler normalizes this before calling the
            # shared analyze_node.
            "target_execution": {"status": "SUCCESS"},
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

    def test_syncs_baseline_and_target_status_for_dashboard(self, manifest_dict):
        from src.aws_lambda import analyze_logs_handler as alh

        # Neither prepare_execution_handler nor any other AWS Lambda wrote
        # the *final* baseline_status/target_status once a job finished -
        # this is the fix, and it must run regardless of which analysis
        # branch is taken below (target succeeded here).
        event = {
            "run_id": "run-1",
            "manifest": manifest_dict,
            "target_branch": "target-branch",
            "baseline_execution": {"status": "SUCCESS", "jobRunId": "b-1"},
            "target_execution": {"status": "SUCCESS", "jobRunId": "t-1"},
            "retry_count": 0,
        }
        mock_state_store = MagicMock()

        with (
            patch.object(alh, "get_github_client", return_value=MagicMock()),
            patch.object(alh, "get_state_store", return_value=mock_state_store),
            patch.object(alh, "get_llm_analyzer", return_value=MagicMock()),
            patch.object(alh, "s3"),
        ):
            alh.handler(event, context=None)

        mock_state_store.update_pipeline_status.assert_any_call(
            "run-1", "customer-transactions", baseline_status="SUCCEEDED", target_status="SUCCEEDED"
        )

    def test_escalates_when_emr_task_itself_failed_with_no_job_run_id(self, manifest_dict):
        from src.aws_lambda import analyze_logs_handler as alh

        event = {
            "run_id": "run-1",
            "manifest": manifest_dict,
            "target_branch": "target-branch",
            # No jobRunId - the EMR task itself failed (e.g. a rejected
            # spark-submit parameter) before any job run started.
            "target_execution": {"status": "FAILED"},
            "retry_count": 0,
        }

        mock_state_store = MagicMock()

        with (
            patch.object(alh, "get_state_store", return_value=mock_state_store),
            patch.object(alh, "s3") as mock_s3,
        ):
            result = alh.handler(event, context=None)

        mock_s3.download_file.assert_not_called()
        assert result["phase"] == "REPORT"
        assert result["status"] == "FAILED"
        assert result["analysis_result"]["source"] == "infrastructure"
        # Called twice: once to sync target_status="FAILED" for the
        # dashboard (this handler's own status-sync fix), once for the
        # escalation's diagnosis/error_message fields.
        assert mock_state_store.update_pipeline_status.call_count == 2
        mock_state_store.update_pipeline_status.assert_any_call(
            "run-1", "customer-transactions", target_status="FAILED"
        )

    def test_recovers_job_run_id_from_catch_cause_when_job_actually_ran(self, manifest_dict):
        from src.aws_lambda import analyze_logs_handler as alh

        # emr-serverless:startJobRun.sync's Catch block loses jobRunId -
        # the state machine passes the raw Cause JSON through instead. When
        # the job actually ran and Spark itself failed, that Cause contains
        # State/JobRunId and log analysis should proceed normally rather
        # than escalating as an infrastructure failure.
        cause = json.dumps({"JobRunId": "job-123", "State": "FAILED"})
        event = {
            "run_id": "run-1",
            "manifest": manifest_dict,
            "target_branch": "target-branch",
            "target_execution": {"status": "FAILED", "cause": cause},
            "emr_job": {"applicationId": "app-1"},
            "retry_count": 0,
        }

        with (
            patch.object(alh, "get_github_client", return_value=MagicMock()),
            patch.object(alh, "get_state_store", return_value=MagicMock()),
            patch.object(alh, "get_llm_analyzer", return_value=MagicMock()),
            patch.object(alh, "_download_and_decompress_driver_logs", return_value="/tmp/log") as mock_download,
            patch.object(alh, "make_analyze_logs_node") as mock_make_node,
        ):
            mock_make_node.return_value = lambda state: {"phase": "REPORT"}
            result = alh.handler(event, context=None)

        mock_download.assert_called_once_with(application_id="app-1", job_run_id="job-123")
        assert result["phase"] == "REPORT"

    def test_escalates_when_catch_cause_is_not_json(self, manifest_dict):
        from src.aws_lambda import analyze_logs_handler as alh

        # A task-level failure before any job run starts (e.g. a rejected
        # spark-submit parameter) produces a non-JSON Cause - recovery must
        # fail gracefully and fall through to the infrastructure escalation
        # path rather than raising.
        event = {
            "run_id": "run-1",
            "manifest": manifest_dict,
            "target_branch": "target-branch",
            "target_execution": {
                "status": "FAILED",
                "cause": "EMRServerless.ValidationException: Option 'spark.master' is not supported",
            },
            "retry_count": 0,
        }

        mock_state_store = MagicMock()

        with (
            patch.object(alh, "get_state_store", return_value=mock_state_store),
            patch.object(alh, "s3") as mock_s3,
        ):
            result = alh.handler(event, context=None)

        mock_s3.download_file.assert_not_called()
        assert result["analysis_result"]["source"] == "infrastructure"

    def test_downloads_and_decompresses_both_stdout_and_stderr(self, tmp_path):
        """Some exceptions (found via a real seeded failure - an
        IllegalArgumentException thrown during SparkSession setup) land
        in stdout, not stderr - stderr can look like a totally clean run
        while the real traceback is in stdout. Both streams must be
        fetched and included."""
        from src.aws_lambda import analyze_logs_handler as alh
        import gzip

        def make_gz(tmp_path, name, content: bytes):
            path = tmp_path / name
            with gzip.open(path, "wb") as f:
                f.write(content)
            return path

        stdout_gz = make_gz(tmp_path, "stdout.gz", b"IllegalArgumentException: Codec [lz4raw] is not available")
        stderr_gz = make_gz(tmp_path, "stderr.gz", b"SparkContext is stopping with exitCode 0")

        def fake_download_file(bucket, key, local_path):
            source = stdout_gz if "stdout" in key else stderr_gz
            with open(source, "rb") as src, open(local_path, "wb") as dst:
                dst.write(src.read())

        with patch.object(alh, "s3") as mock_s3:
            mock_s3.download_file.side_effect = fake_download_file
            local_path = alh._download_and_decompress_driver_logs("app-123", "job-456")

        content = open(local_path).read()
        assert "Codec [lz4raw] is not available" in content
        assert "SparkContext is stopping with exitCode 0" in content

    def test_missing_stdout_stream_does_not_fail_the_download(self, tmp_path):
        from src.aws_lambda import analyze_logs_handler as alh
        from botocore.exceptions import ClientError
        import gzip

        stderr_gz = tmp_path / "stderr.gz"
        with gzip.open(stderr_gz, "wb") as f:
            f.write(b"some real error")

        def fake_download_file(bucket, key, local_path):
            if "stdout" in key:
                raise ClientError({"Error": {"Code": "404"}}, "GetObject")
            with open(stderr_gz, "rb") as src, open(local_path, "wb") as dst:
                dst.write(src.read())

        with patch.object(alh, "s3") as mock_s3:
            mock_s3.download_file.side_effect = fake_download_file
            local_path = alh._download_and_decompress_driver_logs("app-123", "job-456")

        assert "some real error" in open(local_path).read()


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


class TestReadValidationResultsHandler:
    def test_reads_results_from_s3_and_merges_into_state(self, manifest_dict):
        from src.aws_lambda import read_validation_results_handler as rvrh

        event = {
            "run_id": "run-1",
            "manifest": manifest_dict,
            "emr_job": {"resultsPath": "s3://test-artifacts-bucket/run-1/validate/results.json"},
        }

        results_payload = {
            "overall_status": "PASSED",
            "checks": [{"name": "row_count_match", "status": "PASSED", "severity": "critical", "details": "ok"}],
        }
        mock_body = MagicMock()
        mock_body.read.return_value = json.dumps(results_payload).encode("utf-8")
        mock_state_store = MagicMock()

        with (
            patch.object(rvrh, "get_state_store", return_value=mock_state_store),
            patch.object(rvrh, "s3") as mock_s3,
        ):
            mock_s3.get_object.return_value = {"Body": mock_body}
            result = rvrh.handler(event, context=None)

        mock_s3.get_object.assert_called_once_with(
            Bucket="test-artifacts-bucket", Key="run-1/validate/results.json"
        )
        assert result["validation_results"]["overall_status"] == "PASSED"
        assert result["phase"] == "REPORT"


class TestStartRunHandler:
    def test_starts_execution_and_returns_run_id(self, manifest_dict):
        from src.aws_lambda import start_run_handler as srh

        event = {"body": json.dumps({"manifest": manifest_dict})}
        mock_state_store = MagicMock()

        with (
            patch.object(srh, "get_state_store", return_value=mock_state_store),
            patch.object(srh, "sfn") as mock_sfn,
        ):
            mock_sfn.start_execution.return_value = {"executionArn": "arn:aws:states:...:execution:x"}
            result = srh.handler(event, context=None)

        assert result["statusCode"] == 202
        body = json.loads(result["body"])
        assert body["run_id"].startswith("run-")
        assert body["execution_arn"] == "arn:aws:states:...:execution:x"
        mock_state_store.init_run.assert_called_once()
        mock_sfn.start_execution.assert_called_once()
        call_kwargs = mock_sfn.start_execution.call_args.kwargs
        assert call_kwargs["name"] == body["run_id"]
        started_input = json.loads(call_kwargs["input"])
        assert started_input["run_id"] == body["run_id"]
        assert started_input["phase"] == "BRANCH"

    def test_returns_400_for_invalid_manifest(self):
        from src.aws_lambda import start_run_handler as srh

        event = {"body": json.dumps({"manifest": {"not": "a valid manifest"}})}

        with patch.object(srh, "get_state_store") as mock_get_state_store:
            result = srh.handler(event, context=None)

        assert result["statusCode"] == 400
        mock_get_state_store.assert_not_called()

    def test_returns_400_for_missing_body(self):
        from src.aws_lambda import start_run_handler as srh

        result = srh.handler({}, context=None)

        assert result["statusCode"] == 400


class TestAwaitApprovalHandler:
    def test_persists_task_token_and_state(self, manifest_dict):
        from src.aws_lambda import await_approval_handler as aah

        pending_state = {"run_id": "run-1", "manifest": manifest_dict, "phase": "AWAIT_APPROVAL"}
        event = {"taskToken": "token-abc", "state": pending_state}
        mock_state_store = MagicMock()

        with patch.object(aah, "get_state_store", return_value=mock_state_store):
            aah.handler(event, context=None)

        mock_state_store.update_run_status.assert_called_once()
        call_kwargs = mock_state_store.update_run_status.call_args.kwargs
        assert call_kwargs["pending_approval_task_token"] == "token-abc"
        assert json.loads(call_kwargs["pending_approval_state"]) == pending_state
        mock_state_store.record_event.assert_called_once()


class TestApproveRunHandler:
    def _pending_state(self, manifest_dict, fix_config=None):
        return {
            "run_id": "run-1",
            "manifest": manifest_dict,
            "target_branch": "auto/upgrade-test/run-1-target",
            "retry_count": 1,
            "analysis_result": {
                "source": "llm",
                "diagnosis": "Codec [lz4raw] is not available",
                "fix_config": fix_config,
                "confidence": 0.9,
            },
        }

    def test_approve_applies_fix_and_resumes_execution(self, manifest_dict):
        from src.aws_lambda import approve_run_handler as arh

        pending_state = self._pending_state(
            manifest_dict, fix_config={"key": "spark.sql.parquet.compression.codec", "value": "lz4_raw"}
        )
        mock_state_store = MagicMock()
        mock_state_store.get_run_metadata.return_value = {
            "pending_approval_task_token": "token-abc",
            "pending_approval_state": json.dumps(pending_state),
        }
        mock_github_client = MagicMock()
        mock_github_client.get_file_content.return_value = ("spark_config: {}\n", "sha-1")

        event = {"pathParameters": {"run_id": "run-1"}, "body": json.dumps({"approved": True})}

        with (
            patch.object(arh, "get_state_store", return_value=mock_state_store),
            patch.object(arh, "get_github_client", return_value=mock_github_client),
            patch.object(arh, "sfn") as mock_sfn,
        ):
            result = arh.handler(event, context=None)

        assert result["statusCode"] == 200
        mock_github_client.update_file.assert_called_once()
        mock_sfn.send_task_success.assert_called_once()
        resumed_output = json.loads(mock_sfn.send_task_success.call_args.kwargs["output"])
        assert resumed_output["phase"] == "RETRY"
        assert resumed_output["retry_count"] == 2
        mock_state_store.update_run_status.assert_called_once()
        assert mock_state_store.update_run_status.call_args.kwargs["approved_llm_fix"] is True

    def test_approve_without_structured_fix_returns_400(self, manifest_dict):
        from src.aws_lambda import approve_run_handler as arh

        pending_state = self._pending_state(manifest_dict, fix_config=None)
        mock_state_store = MagicMock()
        mock_state_store.get_run_metadata.return_value = {
            "pending_approval_task_token": "token-abc",
            "pending_approval_state": json.dumps(pending_state),
        }

        event = {"pathParameters": {"run_id": "run-1"}, "body": json.dumps({"approved": True})}

        with (
            patch.object(arh, "get_state_store", return_value=mock_state_store),
            patch.object(arh, "sfn") as mock_sfn,
        ):
            result = arh.handler(event, context=None)

        assert result["statusCode"] == 400
        mock_sfn.send_task_success.assert_not_called()

    def test_reject_sends_task_failure(self, manifest_dict):
        from src.aws_lambda import approve_run_handler as arh

        pending_state = self._pending_state(manifest_dict, fix_config=None)
        mock_state_store = MagicMock()
        mock_state_store.get_run_metadata.return_value = {
            "pending_approval_task_token": "token-abc",
            "pending_approval_state": json.dumps(pending_state),
        }

        event = {"pathParameters": {"run_id": "run-1"}, "body": json.dumps({"approved": False})}

        with (
            patch.object(arh, "get_state_store", return_value=mock_state_store),
            patch.object(arh, "sfn") as mock_sfn,
        ):
            result = arh.handler(event, context=None)

        assert result["statusCode"] == 200
        mock_sfn.send_task_failure.assert_called_once_with(
            taskToken="token-abc", error="ApprovalRejected", cause="Human rejected the proposed LLM fix"
        )
        mock_state_store.update_pipeline_status.assert_called_once()
        assert mock_state_store.update_pipeline_status.call_args.kwargs["status"] == "FAILED"

    def test_no_pending_approval_returns_409(self):
        from src.aws_lambda import approve_run_handler as arh

        mock_state_store = MagicMock()
        mock_state_store.get_run_metadata.return_value = {}

        event = {"pathParameters": {"run_id": "run-1"}, "body": json.dumps({"approved": True})}

        with patch.object(arh, "get_state_store", return_value=mock_state_store):
            result = arh.handler(event, context=None)

        assert result["statusCode"] == 409

    def test_missing_run_returns_404(self):
        from src.aws_lambda import approve_run_handler as arh

        mock_state_store = MagicMock()
        mock_state_store.get_run_metadata.side_effect = KeyError("not found")

        event = {"pathParameters": {"run_id": "does-not-exist"}, "body": json.dumps({"approved": True})}

        with patch.object(arh, "get_state_store", return_value=mock_state_store):
            result = arh.handler(event, context=None)

        assert result["statusCode"] == 404


class TestRaisePrHandler:
    def test_no_pattern_pr_when_run_was_not_an_approved_llm_fix(self, manifest_dict):
        from src.aws_lambda import raise_pr_handler as rph

        event = {
            "run_id": "run-1",
            "manifest": manifest_dict,
            "status": "PASSED",
            "analysis_result": {},
        }
        mock_state_store = MagicMock()
        mock_state_store.get_run_metadata.return_value = {}  # no approved_llm_fix flag

        with (
            patch.object(rph, "get_github_client", return_value=MagicMock()),
            patch.object(rph, "get_state_store", return_value=mock_state_store),
            patch.object(rph, "make_raise_pr_node") as mock_make_node,
            patch.object(rph, "GitHubClient") as mock_github_client_cls,
        ):
            mock_make_node.return_value = lambda state: {"pr_url": "http://x", "phase": "DONE"}
            rph.handler(event, context=None)

        mock_github_client_cls.assert_not_called()

    def test_opens_pattern_library_pr_when_approved_fix_led_to_pass(self, manifest_dict):
        from src.aws_lambda import raise_pr_handler as rph

        event = {
            "run_id": "run-1",
            "manifest": manifest_dict,
            "status": "PASSED",
            "analysis_result": {
                "source": "llm",
                "diagnosis": "Codec [lz4raw] is not available",
                "fix_config": {"key": "spark.sql.parquet.compression.codec", "value": "lz4_raw"},
            },
        }
        mock_state_store = MagicMock()
        mock_state_store.get_run_metadata.return_value = {"approved_llm_fix": True}

        mock_pattern_client = MagicMock()
        mock_pattern_client.get_default_branch_sha.return_value = "main-sha"
        mock_pattern_client.get_file_content.return_value = (
            "log_analysis:\n  known_failure_patterns: []\n",
            "manifest-sha",
        )

        with (
            patch.object(rph, "get_github_client", return_value=MagicMock()),
            patch.object(rph, "get_state_store", return_value=mock_state_store),
            patch.object(rph, "make_raise_pr_node") as mock_make_node,
            patch.object(rph, "GitHubClient", return_value=mock_pattern_client),
            patch.object(rph, "get_github_token", return_value="token"),
        ):
            mock_make_node.return_value = lambda state: {"pr_url": "http://x", "phase": "DONE"}
            rph.handler(event, context=None)

        mock_pattern_client.create_branch.assert_called_once()
        mock_pattern_client.update_file.assert_called_once()
        mock_pattern_client.create_pull_request.assert_called_once()
        updated_content = mock_pattern_client.update_file.call_args.kwargs["content"]
        assert "lz4_raw" in updated_content
        mock_pattern_client.close.assert_called_once()
