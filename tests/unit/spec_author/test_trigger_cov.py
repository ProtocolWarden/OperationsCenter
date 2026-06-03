# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import logging
from pathlib import Path

from operations_center.spec_author.models import TriggerSource
from operations_center.spec_author.trigger import TriggerDetector, TriggerResult


def _make_detector(tmp_path: Path, name: str = "spec_direction.md") -> tuple[TriggerDetector, Path]:
    drop = tmp_path / name
    return TriggerDetector(drop), drop


# --- TriggerResult dataclass -------------------------------------------------


def test_trigger_result_fields():
    res = TriggerResult(source=TriggerSource.DROP_FILE, seed_text="hi")
    assert res.source is TriggerSource.DROP_FILE
    assert res.seed_text == "hi"


def test_trigger_result_equality():
    a = TriggerResult(source=TriggerSource.QUEUE_DRAIN, seed_text="")
    b = TriggerResult(source=TriggerSource.QUEUE_DRAIN, seed_text="")
    assert a == b


# --- __init__ ----------------------------------------------------------------


def test_init_stores_drop_file(tmp_path: Path):
    det, drop = _make_detector(tmp_path)
    assert det._drop_file == drop


def test_init_queue_threshold_ignored(tmp_path: Path):
    # queue_threshold is accepted for config compat but not stored/used.
    drop = tmp_path / "spec_direction.md"
    det = TriggerDetector(drop, queue_threshold=99)
    assert det._drop_file == drop
    assert not hasattr(det, "_queue_threshold")


# --- detect: active campaign short-circuit -----------------------------------


def test_detect_active_campaign_returns_none(tmp_path: Path):
    det, drop = _make_detector(tmp_path)
    drop.write_text("seed", encoding="utf-8")
    # Even with a drop-file present and idle board, an active campaign wins.
    result = det.detect(ready_count=0, running_count=0, has_active_campaign=True)
    assert result is None


# --- detect: drop-file priority ----------------------------------------------


def test_detect_drop_file_present(tmp_path: Path, caplog):
    det, drop = _make_detector(tmp_path)
    drop.write_text("  do the thing  \n", encoding="utf-8")
    with caplog.at_level(logging.INFO):
        result = det.detect(ready_count=5, running_count=3, has_active_campaign=False)
    assert result is not None
    assert result.source is TriggerSource.DROP_FILE
    # text is stripped
    assert result.seed_text == "do the thing"
    assert "spec_trigger_drop_file" in caplog.text


def test_detect_drop_file_beats_queue_drain(tmp_path: Path):
    # Drop-file fires even when the board is idle (priority 1 over priority 2).
    det, drop = _make_detector(tmp_path)
    drop.write_text("seed", encoding="utf-8")
    result = det.detect(ready_count=0, running_count=0, has_active_campaign=False)
    assert result is not None
    assert result.source is TriggerSource.DROP_FILE


def test_detect_drop_file_empty_content(tmp_path: Path):
    det, drop = _make_detector(tmp_path)
    drop.write_text("   \n\t ", encoding="utf-8")
    result = det.detect(ready_count=1, running_count=0, has_active_campaign=False)
    assert result is not None
    assert result.source is TriggerSource.DROP_FILE
    assert result.seed_text == ""


# --- detect: queue drain -----------------------------------------------------


def test_detect_queue_drain(tmp_path: Path, caplog):
    det, _ = _make_detector(tmp_path)
    with caplog.at_level(logging.INFO):
        result = det.detect(ready_count=0, running_count=0, has_active_campaign=False)
    assert result is not None
    assert result.source is TriggerSource.QUEUE_DRAIN
    assert result.seed_text == ""
    assert "spec_trigger_queue_drain" in caplog.text


def test_detect_no_trigger_ready_nonzero(tmp_path: Path):
    det, _ = _make_detector(tmp_path)
    result = det.detect(ready_count=2, running_count=0, has_active_campaign=False)
    assert result is None


def test_detect_no_trigger_running_nonzero(tmp_path: Path):
    det, _ = _make_detector(tmp_path)
    result = det.detect(ready_count=0, running_count=4, has_active_campaign=False)
    assert result is None


def test_detect_no_trigger_both_nonzero(tmp_path: Path):
    det, _ = _make_detector(tmp_path)
    result = det.detect(ready_count=3, running_count=1, has_active_campaign=False)
    assert result is None


# --- archive_drop_file -------------------------------------------------------


def test_archive_drop_file_missing_noop(tmp_path: Path):
    det, drop = _make_detector(tmp_path)
    assert not drop.exists()
    det.archive_drop_file()  # should not raise, no archive dir created
    assert not (tmp_path / "spec_direction.archive").exists()


def test_archive_drop_file_moves_to_archive(tmp_path: Path, caplog):
    det, drop = _make_detector(tmp_path)
    drop.write_text("payload", encoding="utf-8")
    with caplog.at_level(logging.INFO):
        det.archive_drop_file()
    assert not drop.exists()
    archive_dir = tmp_path / "spec_direction.archive"
    assert archive_dir.is_dir()
    archived = list(archive_dir.glob("*.md"))
    assert len(archived) == 1
    assert archived[0].read_text(encoding="utf-8") == "payload"
    # name is a timestamp like 20260602T120000.md
    assert archived[0].stem.isalnum()
    assert "spec_drop_file_archived" in caplog.text


def test_archive_drop_file_uses_frozen_timestamp(tmp_path: Path, monkeypatch):
    det, drop = _make_detector(tmp_path)
    drop.write_text("payload", encoding="utf-8")

    import operations_center.spec_author.trigger as trigger_mod

    class _FrozenDateTime:
        @staticmethod
        def now(tz=None):
            import datetime as _dt

            return _dt.datetime(2026, 6, 2, 13, 45, 7, tzinfo=tz)

    monkeypatch.setattr(trigger_mod, "datetime", _FrozenDateTime)
    det.archive_drop_file()
    archived = list((tmp_path / "spec_direction.archive").glob("*.md"))
    assert len(archived) == 1
    assert archived[0].name == "20260602T134507.md"


def test_archive_drop_file_reuses_existing_archive_dir(tmp_path: Path, monkeypatch):
    # mkdir(exist_ok=True) branch: archive dir already present.
    det, drop = _make_detector(tmp_path)
    archive_dir = tmp_path / "spec_direction.archive"
    archive_dir.mkdir()
    (archive_dir / "old.md").write_text("old", encoding="utf-8")
    drop.write_text("new", encoding="utf-8")

    import datetime as _dt

    import operations_center.spec_author.trigger as trigger_mod

    class _FrozenDateTime:
        @staticmethod
        def now(tz=None):
            return _dt.datetime(2026, 6, 2, 8, 0, 0, tzinfo=tz)

    monkeypatch.setattr(trigger_mod, "datetime", _FrozenDateTime)
    det.archive_drop_file()
    assert not drop.exists()
    names = sorted(p.name for p in archive_dir.glob("*.md"))
    assert names == ["20260602T080000.md", "old.md"]
