import json
from unittest.mock import MagicMock, patch

import pytest

from src import cli


@pytest.fixture
def fake_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "STATE_DIR", tmp_path)

    snapshot = {
        "run_id": "run-abc123",
        "metadata": {
            "overall_status": "PASSED",
            "phase": "DONE",
            "config": {
                "source_control": {
                    "owner": "vivekaradshan",
                    "repo": "customer-transactions-pipeline",
                    "target": {"branch_prefix": "auto/upgrade-test"},
                }
            },
        },
        "pipelines": [{"pipeline_id": "customer-transactions", "status": "PASSED"}],
    }
    (tmp_path / "run-abc123.json").write_text(json.dumps(snapshot))
    return snapshot


def test_status_prints_snapshot(fake_snapshot, capsys):
    args = cli.build_parser().parse_args(["status", "--run-id", "run-abc123"])
    args.func(args)

    output = json.loads(capsys.readouterr().out)
    assert output["run_id"] == "run-abc123"
    assert output["metadata"]["overall_status"] == "PASSED"


def test_status_exits_with_error_for_missing_run(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "STATE_DIR", tmp_path)
    args = cli.build_parser().parse_args(["status", "--run-id", "does-not-exist"])

    with pytest.raises(SystemExit) as exc_info:
        args.func(args)
    assert exc_info.value.code == 1


def test_cleanup_deletes_expected_branches(fake_snapshot, capsys):
    args = cli.build_parser().parse_args(["cleanup", "--run-id", "run-abc123"])

    mock_client = MagicMock()
    with patch.object(cli, "GitHubClient", return_value=mock_client):
        args.func(args)

    mock_client.delete_branch.assert_any_call("auto/upgrade-test/run-abc123-baseline")
    mock_client.delete_branch.assert_any_call("auto/upgrade-test/run-abc123-target")
    mock_client.close.assert_called_once()

    output = capsys.readouterr().out
    assert "Deleted branch auto/upgrade-test/run-abc123-baseline" in output


def test_run_command_invokes_orchestrator_and_prints_summary(capsys):
    args = cli.build_parser().parse_args(["run", "--manifest", "manifests/spark-3.5-to-4.0.yaml"])

    fake_final_state = {
        "run_id": "run-xyz",
        "phase": "DONE",
        "status": "PASSED",
        "retry_count": 1,
        "report_path": "/tmp/report.html",
        "pr_url": "https://github.com/x/y/pull/1",
    }
    with patch.object(cli, "run_upgrade_test", return_value=fake_final_state) as mock_run:
        args.func(args)

    mock_run.assert_called_once_with("manifests/spark-3.5-to-4.0.yaml")
    output = json.loads(capsys.readouterr().out)
    assert output["run_id"] == "run-xyz"
    assert output["pr_url"] == "https://github.com/x/y/pull/1"


def test_run_target_aws_posts_signed_request_and_prints_response(capsys, monkeypatch):
    monkeypatch.setenv("UPGRADE_AGENT_API_ENDPOINT", "https://api.example.com")
    args = cli.build_parser().parse_args(
        ["run", "--manifest", "manifests/spark-3.5-to-4.0.yaml", "--target", "aws"]
    )

    fake_response = {"run_id": "run-abc", "execution_arn": "arn:aws:states:...:execution:x"}
    with patch.object(cli, "signed_post", return_value=fake_response) as mock_post:
        args.func(args)

    call_args = mock_post.call_args.args
    assert call_args[0] == "https://api.example.com/runs"
    assert "manifest" in call_args[1]
    output = json.loads(capsys.readouterr().out)
    assert output == fake_response


def test_run_target_aws_without_endpoint_exits(monkeypatch):
    monkeypatch.delenv("UPGRADE_AGENT_API_ENDPOINT", raising=False)
    args = cli.build_parser().parse_args(
        ["run", "--manifest", "manifests/spark-3.5-to-4.0.yaml", "--target", "aws"]
    )

    with pytest.raises(SystemExit) as exc_info:
        args.func(args)
    assert exc_info.value.code == 1


def test_status_target_aws_reads_from_state_store(capsys):
    args = cli.build_parser().parse_args(["status", "--run-id", "run-abc123", "--target", "aws"])

    mock_state_store = MagicMock()
    mock_state_store.get_run_metadata.return_value = {"overall_status": "PASSED"}
    mock_state_store.get_all_pipelines.return_value = [{"pipeline_id": "customer-transactions"}]

    with patch.object(cli, "_get_state_store", return_value=mock_state_store):
        args.func(args)

    output = json.loads(capsys.readouterr().out)
    assert output["run_id"] == "run-abc123"
    assert output["metadata"]["overall_status"] == "PASSED"
    assert output["pipelines"][0]["pipeline_id"] == "customer-transactions"


def test_status_target_aws_exits_for_missing_run():
    args = cli.build_parser().parse_args(["status", "--run-id", "does-not-exist", "--target", "aws"])

    mock_state_store = MagicMock()
    mock_state_store.get_run_metadata.side_effect = KeyError("not found")

    with patch.object(cli, "_get_state_store", return_value=mock_state_store):
        with pytest.raises(SystemExit) as exc_info:
            args.func(args)
    assert exc_info.value.code == 1


def test_cleanup_target_aws_reads_config_from_state_store(capsys):
    args = cli.build_parser().parse_args(["cleanup", "--run-id", "run-abc123", "--target", "aws"])

    mock_state_store = MagicMock()
    mock_state_store.get_run_metadata.return_value = {
        "config": {
            "source_control": {
                "owner": "vivekaradshan",
                "repo": "customer-transactions-pipeline",
                "target": {"branch_prefix": "auto/upgrade-test"},
            }
        }
    }
    mock_github_client = MagicMock()

    with (
        patch.object(cli, "_get_state_store", return_value=mock_state_store),
        patch.object(cli, "GitHubClient", return_value=mock_github_client),
    ):
        args.func(args)

    mock_github_client.delete_branch.assert_any_call("auto/upgrade-test/run-abc123-baseline")
    mock_github_client.delete_branch.assert_any_call("auto/upgrade-test/run-abc123-target")
