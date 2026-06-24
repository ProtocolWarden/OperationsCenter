# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for ParkedUnparkTask (inventory #5).

Pins the fail-safe-default contract and the activated unpark loop:
  * empty store → no-op (the healthy-fleet case), mutates nothing;
  * disabled by default; enabled only via settings.parked_unpark_enabled;
  * when parked + evidence changes → store cleared (unparked);
  * when parked + evidence stable → store refreshed (still parked), never deleted
    board work.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock

from operations_center.entrypoints.maintenance.parked_unpark_task import ParkedUnparkTask
from operations_center.evidence_fingerprints import evidence_fingerprint
from operations_center.maintenance.contracts import (
    MaintenanceContext,
    MaintenanceResult,
    MaintenanceTask,
)
from operations_center.recovery import ParkedState, ParkedStateStore


def _settings(**over):
    base = dict(
        plane=SimpleNamespace(base_url="http://x", workspace_slug="w", project_id="p"),
        plane_token=lambda: "tok",
        parked_unpark_enabled=False,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _ctx(client=None):
    resources = {"plane_client": client} if client is not None else {}
    return MaintenanceContext(cycle_id="c1", now=datetime.now(UTC), resources=resources)


def _board_evidence(issues):
    # Mirror the task's projection so tests can compute the expected hash.
    blocked = [
        {"id": str(i.get("id")), "state": "Blocked"}
        for i in issues
        if str((i.get("state") or {}).get("name", "")).lower() == "blocked"
    ]
    return {"blocked": blocked}


class TestFailSafeDefault:
    def test_disabled_by_default(self):
        assert ParkedUnparkTask(_settings()).enabled is False

    def test_enabled_when_setting_flipped(self):
        assert ParkedUnparkTask(_settings(parked_unpark_enabled=True)).enabled is True

    def test_satisfies_maintenance_protocol(self):
        assert isinstance(ParkedUnparkTask(_settings()), MaintenanceTask)

    def test_empty_store_is_noop(self, tmp_path):
        """No parked state on disk → ok, parked=False, no Plane call at all."""
        client = mock.Mock()
        task = ParkedUnparkTask(
            _settings(parked_unpark_enabled=True),
            store_path=tmp_path / "parked.json",
            plane_client=client,
        )
        result = task.run_once(_ctx(client))
        assert isinstance(result, MaintenanceResult)
        assert result.status == "ok"
        assert result.details["parked"] is False
        # The board is never even fetched when nothing is parked.
        client.list_issues.assert_not_called()


class TestActivatedPath:
    def test_unparks_when_evidence_changed(self, tmp_path):
        """Parked state whose evidence no longer matches the board → store cleared."""
        store_path = tmp_path / "parked.json"
        store = ParkedStateStore(store_path)
        store.save(
            ParkedState(
                root_cause_signature="sigkill_plan",
                parked_reason="cooldown exhausted",
                last_evidence_hash="STALE_HASH_THAT_WONT_MATCH",
            )
        )
        client = mock.Mock()
        client.list_issues.return_value = [
            {"id": "a", "name": "A", "state": {"name": "Blocked"}, "labels": []},
        ]
        task = ParkedUnparkTask(
            _settings(parked_unpark_enabled=True), store_path=store_path, plane_client=client
        )
        result = task.run_once(_ctx(client))

        assert result.status == "ok"
        assert result.details["unparked"] is True
        assert result.details["evidence_changed"] is True
        # Store cleared → next oversight pass may re-attempt.
        assert store.load() is None

    def test_stays_parked_and_refreshes_when_evidence_stable(self, tmp_path):
        """Evidence matches the parked hash → still parked; store re-saved with
        bumped unchanged_cycles + refreshed hash, never deleted board work."""
        store_path = tmp_path / "parked.json"
        issues = [{"id": "a", "name": "A", "state": {"name": "Blocked"}, "labels": []}]
        stable_hash = evidence_fingerprint(_board_evidence(issues))

        store = ParkedStateStore(store_path)
        store.save(
            ParkedState(
                root_cause_signature="sigkill_plan",
                parked_reason="cooldown exhausted",
                unchanged_cycles=2,
                last_evidence_hash=stable_hash,
            )
        )
        client = mock.Mock()
        client.list_issues.return_value = issues
        task = ParkedUnparkTask(
            _settings(parked_unpark_enabled=True), store_path=store_path, plane_client=client
        )
        result = task.run_once(_ctx(client))

        assert result.status == "ok"
        assert result.details["parked"] is True
        assert result.details["unparked"] is False
        assert result.details["evidence_changed"] is False
        # Re-saved, not cleared; unchanged_cycles incremented.
        reloaded = store.load()
        assert reloaded is not None
        assert reloaded.unchanged_cycles == 3
        assert reloaded.last_evidence_hash == stable_hash
        # Never mutates Plane work items.
        client.transition_issue.assert_not_called()

    def test_corrupt_store_is_failed_not_raise(self, tmp_path):
        store_path = tmp_path / "parked.json"
        store_path.write_text("{ not valid json", encoding="utf-8")
        task = ParkedUnparkTask(_settings(parked_unpark_enabled=True), store_path=store_path)
        result = task.run_once(_ctx())
        assert result.status == "failed"
        assert "parked_store_load_failed" in (result.error or "")
