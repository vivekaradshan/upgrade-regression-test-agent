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
