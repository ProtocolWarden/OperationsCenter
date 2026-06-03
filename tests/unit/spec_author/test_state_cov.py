# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
from pathlib import Path

import pytest

from operations_center.spec_author.models import ActiveCampaigns, CampaignRecord
from operations_center.spec_author.state import (
    CampaignStateManager,
    _DEFAULT_STATE_PATH,
)


def _record(campaign_id: str = "c1", status: str = "active") -> CampaignRecord:
    return CampaignRecord(
        campaign_id=campaign_id,
        slug="my-slug",
        spec_file="specs/foo.md",
        status=status,
        created_at="2026-01-01T00:00:00",
    )


def _state_path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "campaigns" / "active.json"


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


def test_init_uses_default_path_when_none() -> None:
    mgr = CampaignStateManager()
    assert mgr.state_path == _DEFAULT_STATE_PATH


def test_init_uses_provided_path(tmp_path: Path) -> None:
    p = _state_path(tmp_path)
    mgr = CampaignStateManager(state_path=p)
    assert mgr.state_path == p


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------


def test_load_returns_empty_when_missing(tmp_path: Path) -> None:
    mgr = CampaignStateManager(state_path=_state_path(tmp_path))
    state = mgr.load()
    assert isinstance(state, ActiveCampaigns)
    assert state.campaigns == []


def test_load_parses_valid_file(tmp_path: Path) -> None:
    p = _state_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text(
        ActiveCampaigns(campaigns=[_record()]).model_dump_json(),
        encoding="utf-8",
    )
    mgr = CampaignStateManager(state_path=p)
    state = mgr.load()
    assert len(state.campaigns) == 1
    assert state.campaigns[0].campaign_id == "c1"


def test_load_corrupt_invalid_json_renames_and_returns_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    p = _state_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text("{ not valid json", encoding="utf-8")
    mgr = CampaignStateManager(state_path=p)

    import logging

    with caplog.at_level(logging.ERROR):
        state = mgr.load()

    assert state.campaigns == []
    # original file got renamed away
    assert not p.exists()
    corrupt_files = list(p.parent.glob("active.json.corrupt.*"))
    assert len(corrupt_files) == 1
    assert "spec_campaign_state_corrupt" in caplog.text


def test_load_corrupt_schema_mismatch(tmp_path: Path) -> None:
    p = _state_path(tmp_path)
    p.parent.mkdir(parents=True)
    # valid json but campaigns is wrong type -> model_validate raises
    p.write_text(json.dumps({"campaigns": "nope"}), encoding="utf-8")
    mgr = CampaignStateManager(state_path=p)
    state = mgr.load()
    assert state.campaigns == []
    assert not p.exists()


def test_load_corrupt_rename_oserror_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _state_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text("garbage", encoding="utf-8")
    mgr = CampaignStateManager(state_path=p)

    orig_rename = Path.rename

    def boom(self: Path, target: object) -> None:  # noqa: ANN401
        raise OSError("cannot rename")

    monkeypatch.setattr(Path, "rename", boom)
    state = mgr.load()
    assert state.campaigns == []
    # rename failed so original remains
    monkeypatch.setattr(Path, "rename", orig_rename)
    assert p.exists()


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------


def test_save_creates_parents_and_writes(tmp_path: Path) -> None:
    p = _state_path(tmp_path)
    mgr = CampaignStateManager(state_path=p)
    state = ActiveCampaigns(campaigns=[_record()])
    mgr.save(state)
    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["campaigns"][0]["campaign_id"] == "c1"
    # tmp file should have been renamed away
    assert not p.with_suffix(".tmp").exists()


def test_save_roundtrip_via_load(tmp_path: Path) -> None:
    p = _state_path(tmp_path)
    mgr = CampaignStateManager(state_path=p)
    mgr.save(ActiveCampaigns(campaigns=[_record("x"), _record("y", "complete")]))
    loaded = mgr.load()
    assert [c.campaign_id for c in loaded.campaigns] == ["x", "y"]
    assert loaded.campaigns[1].status == "complete"


# ---------------------------------------------------------------------------
# add_campaign
# ---------------------------------------------------------------------------


def test_add_campaign_appends(tmp_path: Path) -> None:
    p = _state_path(tmp_path)
    mgr = CampaignStateManager(state_path=p)
    mgr.add_campaign(_record("a"))
    mgr.add_campaign(_record("b"))
    loaded = mgr.load()
    assert [c.campaign_id for c in loaded.campaigns] == ["a", "b"]


# ---------------------------------------------------------------------------
# mark_complete / mark_cancelled / _update_status
# ---------------------------------------------------------------------------


def test_mark_complete_updates_status(tmp_path: Path) -> None:
    p = _state_path(tmp_path)
    mgr = CampaignStateManager(state_path=p)
    mgr.add_campaign(_record("a"))
    mgr.mark_complete("a")
    assert mgr.load().campaigns[0].status == "complete"


def test_mark_cancelled_updates_status(tmp_path: Path) -> None:
    p = _state_path(tmp_path)
    mgr = CampaignStateManager(state_path=p)
    mgr.add_campaign(_record("a"))
    mgr.mark_cancelled("a")
    assert mgr.load().campaigns[0].status == "cancelled"


def test_update_status_updates_all_matching(tmp_path: Path) -> None:
    p = _state_path(tmp_path)
    mgr = CampaignStateManager(state_path=p)
    mgr.add_campaign(_record("dup"))
    mgr.add_campaign(_record("dup"))
    mgr.add_campaign(_record("other"))
    mgr.mark_complete("dup")
    loaded = mgr.load()
    statuses = {c.campaign_id: c.status for c in loaded.campaigns}
    assert statuses["dup"] == "complete"
    assert statuses["other"] == "active"


def test_update_status_no_match_does_not_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _state_path(tmp_path)
    mgr = CampaignStateManager(state_path=p)
    mgr.add_campaign(_record("a"))

    calls: list[object] = []
    orig_save = mgr.save

    def tracking_save(state: ActiveCampaigns) -> None:
        calls.append(state)
        orig_save(state)

    monkeypatch.setattr(mgr, "save", tracking_save)
    mgr.mark_complete("nonexistent")
    assert calls == []
    assert mgr.load().campaigns[0].status == "active"


# ---------------------------------------------------------------------------
# rebuild_from_specs
# ---------------------------------------------------------------------------


SPEC_ACTIVE = """---
campaign_id: camp-1
slug: active-one
status: active
created_at: 2026-01-02T00:00:00
phases:
  - test
---
body
"""

SPEC_COMPLETE = """---
campaign_id: camp-2
slug: done-one
status: complete
created_at: 2026-01-03T00:00:00
---
body
"""

SPEC_BAD = "no front matter here"


def test_rebuild_from_specs_only_active(tmp_path: Path) -> None:
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "01_active.md").write_text(SPEC_ACTIVE, encoding="utf-8")
    (specs / "02_complete.md").write_text(SPEC_COMPLETE, encoding="utf-8")

    p = _state_path(tmp_path)
    mgr = CampaignStateManager(state_path=p)
    result = mgr.rebuild_from_specs(specs)

    assert [c.campaign_id for c in result.campaigns] == ["camp-1"]
    rec = result.campaigns[0]
    assert rec.slug == "active-one"
    assert rec.status == "active"
    assert rec.spec_file.endswith("01_active.md")
    # rebuilt state was persisted
    assert mgr.load().campaigns[0].campaign_id == "camp-1"


def test_rebuild_from_specs_skips_unparseable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "01_active.md").write_text(SPEC_ACTIVE, encoding="utf-8")
    (specs / "02_bad.md").write_text(SPEC_BAD, encoding="utf-8")

    import logging

    mgr = CampaignStateManager(state_path=_state_path(tmp_path))
    with caplog.at_level(logging.WARNING):
        result = mgr.rebuild_from_specs(specs)

    assert [c.campaign_id for c in result.campaigns] == ["camp-1"]
    assert "spec_rebuild_skip" in caplog.text


def test_rebuild_from_specs_empty_dir(tmp_path: Path) -> None:
    specs = tmp_path / "specs"
    specs.mkdir()
    mgr = CampaignStateManager(state_path=_state_path(tmp_path))
    result = mgr.rebuild_from_specs(specs)
    assert result.campaigns == []
    # empty state still saved
    assert mgr.load().campaigns == []
