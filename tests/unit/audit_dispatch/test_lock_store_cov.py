# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Supplementary coverage tests for the persistent lock store.

Complements ``test_lock_store.py`` by exercising the helper functions,
serialization edge cases, sentinel filtering, and the atomic-write
error-cleanup path without duplicating the existing happy-path coverage.
"""

from __future__ import annotations

import errno
import json
import os
import re
import socket
from pathlib import Path

import pytest

from operations_center.audit_dispatch import lock_store as ls
from operations_center.audit_dispatch.errors import LockStoreCorruptError
from operations_center.audit_dispatch.lock_store import (
    LOCK_SCHEMA_VERSION,
    PersistentLockPayload,
    PersistentLockStore,
    _is_pid_alive,
    _now_iso,
)


def _payload(**overrides) -> PersistentLockPayload:
    base = dict(
        repo_id="repo_x",
        run_id="run_1",
        audit_type="audit_type_1",
        oc_pid=os.getpid(),
        started_at="2026-05-04T12:00:00Z",
        command="python -m tools.audit.run",
        expected_run_status_path="/tmp/run_status.json",
    )
    base.update(overrides)
    return PersistentLockPayload(**base)


# ---------------------------------------------------------------------------
# _now_iso
# ---------------------------------------------------------------------------


class TestNowIso:
    def test_returns_z_suffixed_iso_string(self) -> None:
        value = _now_iso()
        assert value.endswith("Z")
        assert "+00:00" not in value
        # YYYY-MM-DDTHH:MM:SSZ — second precision, no microseconds.
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value)


# ---------------------------------------------------------------------------
# _is_pid_alive — every branch
# ---------------------------------------------------------------------------


class TestIsPidAlive:
    def test_none_is_not_alive(self) -> None:
        assert _is_pid_alive(None) is False

    def test_zero_is_not_alive(self) -> None:
        assert _is_pid_alive(0) is False

    def test_negative_is_not_alive(self) -> None:
        assert _is_pid_alive(-5) is False

    def test_current_pid_is_alive(self) -> None:
        assert _is_pid_alive(os.getpid()) is True

    def test_process_lookup_error_is_dead(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_kill(pid: int, sig: int) -> None:
            raise ProcessLookupError

        monkeypatch.setattr(ls.os, "kill", fake_kill)
        assert _is_pid_alive(1234) is False

    def test_permission_error_is_alive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_kill(pid: int, sig: int) -> None:
            raise PermissionError

        monkeypatch.setattr(ls.os, "kill", fake_kill)
        assert _is_pid_alive(1234) is True

    def test_oserror_esrch_is_dead(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_kill(pid: int, sig: int) -> None:
            raise OSError(errno.ESRCH, "no such process")

        monkeypatch.setattr(ls.os, "kill", fake_kill)
        assert _is_pid_alive(1234) is False

    def test_oserror_other_errno_is_alive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_kill(pid: int, sig: int) -> None:
            raise OSError(errno.EINVAL, "invalid")

        monkeypatch.setattr(ls.os, "kill", fake_kill)
        assert _is_pid_alive(1234) is True


# ---------------------------------------------------------------------------
# PersistentLockPayload serialization
# ---------------------------------------------------------------------------


class TestPayloadSerialization:
    def test_to_json_contains_all_fields(self) -> None:
        p = _payload(audit_pid=11, audit_pgid=22, owner_hostname="hostA")
        data = p.to_json()
        assert data == {
            "lock_schema_version": LOCK_SCHEMA_VERSION,
            "repo_id": "repo_x",
            "run_id": "run_1",
            "audit_type": "audit_type_1",
            "oc_pid": os.getpid(),
            "audit_pid": 11,
            "audit_pgid": 22,
            "started_at": "2026-05-04T12:00:00Z",
            "command": "python -m tools.audit.run",
            "expected_run_status_path": "/tmp/run_status.json",
            "owner_hostname": "hostA",
        }

    def test_round_trip_json(self) -> None:
        p = _payload(audit_pid=11, audit_pgid=22, owner_hostname="hostA")
        restored = PersistentLockPayload.from_json(p.to_json())
        assert restored == p

    def test_owner_hostname_defaults_to_gethostname(self) -> None:
        p = _payload()
        assert p.owner_hostname == socket.gethostname()

    def test_from_json_null_audit_pid_and_pgid(self) -> None:
        data = _payload().to_json()
        data["audit_pid"] = None
        data["audit_pgid"] = None
        restored = PersistentLockPayload.from_json(data)
        assert restored.audit_pid is None
        assert restored.audit_pgid is None

    def test_from_json_coerces_string_numbers(self) -> None:
        data = _payload().to_json()
        data["oc_pid"] = "777"
        data["audit_pid"] = "888"
        data["audit_pgid"] = "999"
        restored = PersistentLockPayload.from_json(data)
        assert restored.oc_pid == 777
        assert restored.audit_pid == 888
        assert restored.audit_pgid == 999

    def test_from_json_defaults_owner_hostname_when_absent(self) -> None:
        data = _payload().to_json()
        del data["owner_hostname"]
        restored = PersistentLockPayload.from_json(data)
        assert restored.owner_hostname == socket.gethostname()

    def test_from_json_defaults_schema_version_when_absent(self) -> None:
        data = _payload().to_json()
        del data["lock_schema_version"]
        restored = PersistentLockPayload.from_json(data)
        assert restored.lock_schema_version == LOCK_SCHEMA_VERSION

    def test_from_json_missing_required_field_raises(self) -> None:
        data = _payload().to_json()
        del data["run_id"]
        with pytest.raises(LockStoreCorruptError, match="malformed lock payload"):
            PersistentLockPayload.from_json(data)

    def test_from_json_non_numeric_pid_raises_value_error_path(self) -> None:
        data = _payload().to_json()
        data["oc_pid"] = "not-a-number"
        with pytest.raises(LockStoreCorruptError):
            PersistentLockPayload.from_json(data)

    def test_from_json_type_error_path(self) -> None:
        # int(None) raises TypeError, exercised via a non-coercible oc_pid.
        data = _payload().to_json()
        data["oc_pid"] = ["list-is-not-int"]
        with pytest.raises(LockStoreCorruptError):
            PersistentLockPayload.from_json(data)


# ---------------------------------------------------------------------------
# PersistentLockStore construction / properties
# ---------------------------------------------------------------------------


class TestStoreConstruction:
    def test_init_creates_state_dir(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "locks"
        store = PersistentLockStore(target)
        assert target.is_dir()
        assert store.state_dir == target

    def test_init_accepts_existing_dir(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        assert store.state_dir == tmp_path

    def test_state_dir_coerced_to_path(self, tmp_path: Path) -> None:
        store = PersistentLockStore(str(tmp_path))
        assert isinstance(store.state_dir, Path)


# ---------------------------------------------------------------------------
# _iter_lock_files — sentinel filtering / missing dir
# ---------------------------------------------------------------------------


class TestIterLockFiles:
    def test_skips_sentinel_files_with_dotted_stem(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        (tmp_path / "repo_a.lock").write_text("{}")
        # Sentinel created by locked_state_file would look like this.
        (tmp_path / "repo_a.lock.lock").write_text("")
        paths = store._iter_lock_files()
        names = {p.name for p in paths}
        assert names == {"repo_a.lock"}

    def test_returns_sorted_first_tier_files(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        (tmp_path / "b.lock").write_text("{}")
        (tmp_path / "a.lock").write_text("{}")
        paths = store._iter_lock_files()
        assert [p.name for p in paths] == ["a.lock", "b.lock"]

    def test_empty_when_state_dir_missing(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        # Remove the directory after construction.
        store._state_dir = tmp_path / "gone"
        assert store._iter_lock_files() == []

    def test_empty_when_no_lock_files(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        assert store._iter_lock_files() == []


# ---------------------------------------------------------------------------
# reclaim_if_stale — the None branch (no lock present)
# ---------------------------------------------------------------------------


class TestReclaimNone:
    def test_reclaim_returns_false_when_no_lock(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        assert store.reclaim_if_stale("never_held") is False

    def test_sweep_stale_empty_when_nothing_held(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        assert store.sweep_stale() == []


# ---------------------------------------------------------------------------
# _write_atomic — error cleanup path
# ---------------------------------------------------------------------------


class TestWriteAtomicCleanup:
    def test_tempfile_removed_when_replace_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = PersistentLockStore(tmp_path)
        path = tmp_path / "repo_x.lock"

        def boom(src: str, dst: str) -> None:
            raise OSError("replace failed")

        monkeypatch.setattr(ls.os, "replace", boom)

        with pytest.raises(OSError, match="replace failed"):
            store._write_atomic(path, _payload())

        # No leftover *.tmp tempfiles and the target was never created.
        leftovers = list(tmp_path.glob("*.tmp"))
        assert leftovers == []
        assert not path.exists()

    def test_cleanup_tolerates_missing_tempfile(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = PersistentLockStore(tmp_path)
        path = tmp_path / "repo_x.lock"

        def boom(src: str, dst: str) -> None:
            raise OSError("replace failed")

        def vanish(target: str) -> None:
            raise OSError("already gone")

        monkeypatch.setattr(ls.os, "replace", boom)
        monkeypatch.setattr(ls.os, "unlink", vanish)

        # The unlink-failure is swallowed; the original error still propagates.
        with pytest.raises(OSError, match="replace failed"):
            store._write_atomic(path, _payload())

    def test_write_atomic_creates_parent_dirs(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        nested = tmp_path / "deep" / "repo_x.lock"
        store._write_atomic(nested, _payload())
        assert nested.exists()
        on_disk = json.loads(nested.read_text())
        assert on_disk["repo_id"] == "repo_x"

    def test_write_atomic_output_ends_with_newline(self, tmp_path: Path) -> None:
        store = PersistentLockStore(tmp_path)
        path = tmp_path / "repo_x.lock"
        store._write_atomic(path, _payload())
        assert path.read_text().endswith("\n")
