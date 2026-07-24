import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard"))

from app import (  # noqa: E402
    _TIMELINE_EVENT_LABELS,
    badge,
    format_duration,
    load_aws_snapshots,
    load_snapshots,
)


def test_badge_maps_known_statuses_to_icons():
    assert badge("PASSED") == "🟢 PASSED"
    assert badge("FAILED") == "🔴 FAILED"
    assert badge("RUNNING") == "🟡 RUNNING"


def test_badge_falls_back_to_neutral_icon_for_unknown_status():
    assert badge("SOMETHING_NEW") == "⚪ SOMETHING_NEW"


def test_format_duration_computes_elapsed_time():
    duration = format_duration("2026-07-06T10:00:00+00:00", "2026-07-06T10:01:30+00:00")
    assert duration == "0:01:30"


def test_format_duration_handles_missing_timestamps():
    assert format_duration("", "2026-07-06T10:01:30+00:00") == "-"
    assert format_duration("2026-07-06T10:00:00+00:00", "") == "-"


def test_load_snapshots_reads_json_files(tmp_path, monkeypatch):
    import app

    monkeypatch.setattr(app, "STATE_DIR", tmp_path)

    (tmp_path / "run-abc.json").write_text(json.dumps({"run_id": "run-abc", "metadata": {}}))

    snapshots = load_snapshots()

    assert "run-abc" in snapshots
    assert snapshots["run-abc"]["run_id"] == "run-abc"


def test_load_snapshots_skips_malformed_files(tmp_path, monkeypatch):
    import app

    monkeypatch.setattr(app, "STATE_DIR", tmp_path)

    (tmp_path / "run-good.json").write_text(json.dumps({"run_id": "run-good"}))
    (tmp_path / "run-bad.json").write_text("{not valid json")

    snapshots = load_snapshots()

    assert "run-good" in snapshots
    assert "run-bad" not in snapshots


def test_load_snapshots_returns_empty_dict_when_dir_missing(tmp_path, monkeypatch):
    import app

    monkeypatch.setattr(app, "STATE_DIR", tmp_path / "does-not-exist")

    assert load_snapshots() == {}


def test_load_aws_snapshots_builds_same_shape_from_state_store():
    mock_state_store = MagicMock()
    mock_state_store.list_runs.return_value = [
        {"run_id": "run-aws-1", "overall_status": "RUNNING", "phase": "BUILD"}
    ]
    mock_state_store.get_all_pipelines.return_value = [{"pipeline_id": "customer-transactions"}]

    snapshots = load_aws_snapshots(mock_state_store)

    assert "run-aws-1" in snapshots
    assert snapshots["run-aws-1"]["metadata"]["overall_status"] == "RUNNING"
    assert snapshots["run-aws-1"]["pipelines"][0]["pipeline_id"] == "customer-transactions"
    mock_state_store.get_all_pipelines.assert_called_once_with("run-aws-1")


def test_load_aws_snapshots_includes_events_for_ai_decision_timeline():
    mock_state_store = MagicMock()
    mock_state_store.list_runs.return_value = [{"run_id": "run-aws-1"}]
    mock_state_store.get_all_pipelines.return_value = []
    mock_state_store.get_events.return_value = [{"event": "llm_call", "model": "gpt-4o-mini"}]

    snapshots = load_aws_snapshots(mock_state_store)

    assert snapshots["run-aws-1"]["events"] == [{"event": "llm_call", "model": "gpt-4o-mini"}]
    mock_state_store.get_events.assert_called_once_with("run-aws-1")


def test_timeline_labels_cover_every_event_the_analyze_and_approval_paths_record():
    # analyze_node.py, react_loop.py's tools, and approve_run_handler.py
    # are the three places that record ANALYZE-phase events - if one of
    # them starts emitting a new event name, this catches that the
    # timeline silently wouldn't show it.
    expected_events = {
        "failure_analyzed",
        "llm_call",
        "react_loop_started",
        "tool_call",
        "react_loop_finished",
        "auto_fix_applied",
        "awaiting_approval",
        "approval_approved",
        "retry_with_human_fix",
        "approval_rejected",
        "escalated",
    }
    assert set(_TIMELINE_EVENT_LABELS.keys()) == expected_events
