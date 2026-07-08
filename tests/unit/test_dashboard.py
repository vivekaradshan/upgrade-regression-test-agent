import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard"))

from app import badge, format_duration, load_snapshots  # noqa: E402


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
