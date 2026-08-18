# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Hold the board seam, and make the remaining coupling shrink rather than drift.

OC's board is Plane today and will not be. What makes replacing it expensive is
not the surface — eleven operations — but that callers name `PlaneClient`
directly, construct it from the same four settings fields, and type-hint against
the concrete class. Ten of them had independently hand-rolled the identical
`_make_plane_client()` helper.

`operations_center.adapters.board` is the seam: a `BoardClient` protocol and one
`make_board_client()` factory. These tests do two jobs:

* pin the seam itself — the protocol matches what the fleet actually calls, and
  the concrete client still satisfies it;
* ratchet the migration — `STILL_IMPORTING_PLANE` is the accepted remainder, and
  it may only shrink. A new file reaching past the boundary fails here, which is
  the difference between a boundary and a suggestion.
"""

from __future__ import annotations

import pathlib
import re

import pytest

SRC = pathlib.Path(__file__).resolve().parents[3] / "src" / "operations_center"

#: Files that name `PlaneClient` on purpose, and should keep doing so.
#:
#: This began as a burn-down list of 37 unmigrated callers. It is now empty of
#: migration work — every caller goes through the seam. What remains are two
#: files that exercise Plane *specifically*; routing them through
#: `make_board_client` would delete the thing they test.
#:
#: Adding to this set is not a way to avoid migrating. A new entry needs a reason
#: of the same kind: "this tests Plane itself", not "this was easier".
PLANE_SPECIFIC_BY_DESIGN = {
    # The setup wizard verifies credentials the operator has just typed, before
    # any Settings object exists — `make_board_client(settings)` has nothing to
    # build from. It still walks a new operator through Plane, which stopped
    # being the board at the 2026-08-18 Forgejo cutover; rewriting the wizard
    # for Forgejo is a scoped follow-up, and until then this entry records the
    # remaining coupling honestly.
    "entrypoints/setup/main.py",
}

#: Kept as the old name so the ratchet tests below read unchanged.
STILL_IMPORTING_PLANE = PLANE_SPECIFIC_BY_DESIGN

_IMPORTS_PLANE = re.compile(
    r"^[ \t]*from operations_center\.adapters\.plane(?:\.client)? import PlaneClient",
    re.M,
)


def _importers() -> set[str]:
    """Files outside adapters/ that import the concrete client."""
    found = set()
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("adapters/") or "__pycache__" in rel:
            continue
        if _IMPORTS_PLANE.search(path.read_text(encoding="utf-8", errors="replace")):
            found.add(rel)
    return found


# ── the seam ─────────────────────────────────────────────────────────────────


#: The board operations, named once. Derived from real call sites, not taste.
BOARD_OPERATIONS = frozenset({
    "fetch_issue", "fetch_project", "list_issues", "list_states",
    "list_labels", "list_comments", "to_board_task", "transition_issue",
    "create_issue", "update_issue_description", "update_issue_labels",
    "comment_issue", "set_priority", "close",
})


def _declared_operations(proto: type) -> set[str]:
    """Public members a Protocol declares.

    Deliberately not `__protocol_attrs__`: that is a CPython internal added in
    3.12. Using it made these tests pass on a 3.12 developer machine and fail on
    CI's 3.11 with `AttributeError`, which is how #503 went in with red CI.
    `dir()` is stable across both.
    """
    return {n for n in dir(proto) if not n.startswith("_")}


def test_the_concrete_client_satisfies_the_protocol():
    """PlaneClient must remain usable as a BoardClient.

    If it stops, callers type-hinting the protocol are lying about what they
    accept, and the seam is decorative.
    """
    from operations_center.adapters.plane import PlaneClient

    missing = sorted(op for op in BOARD_OPERATIONS if not hasattr(PlaneClient, op))
    assert not missing, f"PlaneClient no longer provides: {missing}"


def test_protocol_declares_every_operation_the_fleet_calls():
    """The protocol must not be narrower than actual usage.

    A protocol missing an operation pushes callers back to the concrete class —
    which is exactly what happened with `set_priority`: it was absent, so
    triage_scan reached through the adapter's private httpx client to PATCH
    Plane's URL directly.
    """
    from operations_center.adapters.board import BoardClient

    declared = _declared_operations(BoardClient)
    missing = sorted(BOARD_OPERATIONS - declared)
    assert not missing, f"BoardClient does not declare: {missing}"


def test_factory_builds_from_settings_without_naming_a_backend(monkeypatch):
    """make_board_client is the one place a concrete backend is named."""
    from operations_center.adapters import board

    captured = {}

    class _Fake:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr(
        "operations_center.adapters.plane.PlaneClient", _Fake, raising=False
    )

    class _Board:
        base_url = "http://board.local"
        workspace_slug = "ws"
        project_id = "proj"

    class _Settings:
        plane = _Board()

        def plane_token(self):
            return "tok"

    board.make_board_client(_Settings())
    assert captured == {
        "base_url": "http://board.local",
        "api_token": "tok",
        "workspace_slug": "ws",
        "project_id": "proj",
    }, "the factory changed the construction contract the callers relied on"


def test_factory_tolerates_a_settings_double(monkeypatch):
    """A MagicMock settings object must still build the default backend.

    `getattr(settings, "board_backend", "plane")` looks like it defaults, but a
    MagicMock answers every attribute, so the default is unreachable and the
    factory raised "unknown board_backend <MagicMock ...>". That is not a
    hypothetical: it broke
    `tests/maintenance/test_orphan_branch_check.py::test_emit_plane_task_updates_existing_issue`
    from #509 until now, and went unnoticed because CI runs `tests/unit` and
    never `tests/maintenance/`.
    """
    from unittest.mock import MagicMock

    from operations_center.adapters import board

    built = {}

    class _Fake:
        def __init__(self, **kw):
            built.update(kw)

    monkeypatch.setattr(
        "operations_center.adapters.plane.PlaneClient", _Fake, raising=False
    )

    board.make_board_client(MagicMock())
    assert built, "a settings double no longer builds the default backend"


def test_factory_still_rejects_a_real_unknown_backend():
    """Tolerating a mock must not tolerate a typo in the config."""
    from operations_center.adapters import board

    class _Settings:
        board_backend = "gitlab"

    with pytest.raises(RuntimeError, match="unknown board_backend"):
        board.make_board_client(_Settings())


def test_factory_refuses_plane_backend_without_a_plane_block():
    """`plane` is optional in Settings since the Forgejo cutover.

    A config that says board_backend: plane but carries no plane block must fail
    loudly at construction — the same contract the forgejo branch has — because
    a board the fleet cannot reach looks like an empty queue, not an error.
    """
    from operations_center.adapters import board

    class _Settings:
        board_backend = "plane"
        plane = None

    with pytest.raises(RuntimeError, match="no `plane:` settings block"):
        board.make_board_client(_Settings())


class _ForgejoBlock:
    owner = "Operations_Center_Admin"
    repo = "board"


class _PlaneBlock:
    project_id = "proj-uuid"


def test_board_project_id_follows_the_forgejo_backend():
    """Forgejo's natural identifier is the board repo itself."""
    from operations_center.adapters.board import board_project_id

    class _Settings:
        board_backend = "forgejo"
        forgejo = _ForgejoBlock()

    assert board_project_id(_Settings()) == "Operations_Center_Admin/board"


def test_board_project_id_follows_the_plane_backend():
    from operations_center.adapters.board import board_project_id

    class _Settings:
        board_backend = "plane"
        plane = _PlaneBlock()

    assert board_project_id(_Settings()) == "proj-uuid"


@pytest.mark.parametrize("backend", ["plane", "forgejo"])
def test_board_project_id_fails_loudly_without_the_active_block(backend):
    """The council's #516 concern: `settings.plane.project_id` sat on the
    dispatch path, so a Forgejo-only config (exactly what the example now
    recommends) raised AttributeError before any task could execute. The id
    must come from the active backend, and a missing block must be a loud
    RuntimeError, not an AttributeError."""
    from operations_center.adapters.board import board_project_id

    class _Settings:
        board_backend = backend
        plane = None
        forgejo = None

    with pytest.raises(RuntimeError, match="settings block"):
        board_project_id(_Settings())


def test_board_project_id_tolerates_a_settings_double():
    """Same MagicMock normalisation the factory has (#513)."""
    from unittest.mock import MagicMock

    from operations_center.adapters.board import board_project_id

    settings = MagicMock()
    settings.plane.project_id = "proj-uuid"
    assert board_project_id(settings) == "proj-uuid"


# ── the ratchet ──────────────────────────────────────────────────────────────


def test_no_new_file_reaches_past_the_boundary():
    """The accepted remainder may shrink, never grow."""
    actual = _importers()
    added = sorted(actual - STILL_IMPORTING_PLANE)
    assert not added, (
        f"{len(added)} file(s) import PlaneClient directly without being on the "
        f"accepted list: {added}. Use "
        "`from operations_center.adapters.board import BoardClient, make_board_client` "
        "instead — the point of the seam is that swapping the board is one change, "
        "not one per caller."
    )


def test_allowlist_has_no_stale_entries():
    """A migrated file must be struck off, so the list measures real remaining work."""
    actual = _importers()
    stale = sorted(STILL_IMPORTING_PLANE - actual)
    assert not stale, (
        f"{len(stale)} file(s) no longer import PlaneClient but are still listed: "
        f"{stale}. Remove them from STILL_IMPORTING_PLANE."
    )


@pytest.mark.parametrize("migrated", [
    "entrypoints/maintenance/board_unblock.py",
    "entrypoints/maintenance/board_unblock_task.py",
    "entrypoints/maintenance/triage_scan.py",
    "entrypoints/board_worker/main.py",
    "entrypoints/pr_review_watcher/main.py",
    "entrypoints/proposer/main.py",
    "entrypoints/spec_hygiene/main.py",
    "propagation/plane_adapter.py",
    "scheduled_tasks/runner.py",
    "priority_scans.py",
])
def test_migrated_files_stay_migrated(migrated):
    """Pin this slice so it cannot quietly regress."""
    text = (SRC / migrated).read_text(encoding="utf-8")
    assert "PlaneClient" not in text, f"{migrated} names PlaneClient again"
    assert "adapters.board" in text, f"{migrated} no longer uses the seam"


def test_the_hand_rolled_factories_are_gone():
    """Ten copies of the same constructor was the evidence the seam was missing."""
    remaining = [
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if "__pycache__" not in p.as_posix()
        and re.search(r"def _(?:make_)?plane_client\b", p.read_text(encoding="utf-8", errors="replace"))
        and re.search(r"PlaneClient\(", p.read_text(encoding="utf-8", errors="replace"))
    ]
    assert not remaining, (
        f"{len(remaining)} file(s) still hand-roll the client constructor: {remaining}"
    )


def test_the_migration_is_finished():
    """No caller should be left to migrate.

    The seam existed to make swapping the board a one-place change. That is only
    true once every caller goes through it — a seam with stragglers still forces
    a per-caller change at cutover, which is the cost it was built to remove.
    """
    actual = _importers()
    unmigrated = sorted(actual - PLANE_SPECIFIC_BY_DESIGN)
    assert not unmigrated, (
        f"{len(unmigrated)} caller(s) still import PlaneClient without a "
        f"design reason: {unmigrated}"
    )
