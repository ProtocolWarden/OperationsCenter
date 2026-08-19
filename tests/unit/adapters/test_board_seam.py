# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Hold the board seam, now that there is one backend behind it.

The seam was built to make replacing Plane a one-place change, and it did that:
37 files named `PlaneClient` directly, then 2, then none, and the adapter is
gone. What it protects from here is the same boundary aimed at the live
backend — because the reason a caller must not name a concrete client has
nothing to do with *which* client it is.

* the protocol matches what the fleet actually calls, and the concrete client
  still satisfies it;
* the factory is the one place a backend is named, and it refuses to guess;
* nothing outside ``adapters/`` imports the concrete client, with one
  allowlisted exception whose reason is recorded below.
"""

from __future__ import annotations

import pathlib
import re

import pytest

SRC = pathlib.Path(__file__).resolve().parents[3] / "src" / "operations_center"

#: Files that construct a concrete board client on purpose.
#:
#: The setup wizard validates credentials the operator has just typed, before a
#: `Settings` object exists for `make_board_client` to build from. That is a
#: real reason, not a shortcut — and it is the only one. This began as a
#: 37-entry burn-down list against `PlaneClient`; Plane is gone, so the same
#: boundary now guards `ForgejoClient`.
#:
#: Adding an entry needs a reason of the same kind: "this validates config that
#: does not exist yet", not "this was easier".
CONSTRUCTS_DIRECTLY_BY_DESIGN = {
    "entrypoints/setup/main.py",
}

_IMPORTS_CONCRETE = re.compile(
    r"^[ \t]*from operations_center\.adapters\.forgejo(?:\.client)? import .*ForgejoClient",
    re.M,
)


def _importers() -> set[str]:
    """Files outside adapters/ that import the concrete board client."""
    found = set()
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("adapters/") or "__pycache__" in rel:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        # The PR-side client lives in the same package and is a different seam.
        if _IMPORTS_CONCRETE.search(text) and "ForgejoPRClient" not in text:
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
    """ForgejoClient must remain usable as a BoardClient.

    If it stops, callers type-hinting the protocol are lying about what they
    accept, and the seam is decorative.
    """
    from operations_center.adapters.forgejo import ForgejoClient

    missing = sorted(op for op in BOARD_OPERATIONS if not hasattr(ForgejoClient, op))
    assert not missing, f"ForgejoClient no longer provides: {missing}"


def test_protocol_declares_every_operation_the_fleet_calls():
    """The protocol must not be narrower than actual usage.

    A protocol missing an operation pushes callers back to the concrete class —
    which is exactly what happened with `set_priority`: it was absent, so
    triage_scan reached through the adapter's private httpx client to PATCH the
    board's URL directly.
    """
    from operations_center.adapters.board import BoardClient

    declared = _declared_operations(BoardClient)
    missing = sorted(BOARD_OPERATIONS - declared)
    assert not missing, f"BoardClient does not declare: {missing}"


# ── construction ─────────────────────────────────────────────────────────────


class _Forgejo:
    base_url = "http://forge.local"
    owner = "protocolwarden"
    repo = "board"


def test_factory_builds_from_settings_without_naming_a_backend(monkeypatch):
    """make_board_client is the one place a concrete backend is named."""
    from operations_center.adapters import board

    captured = {}

    class _Fake:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr(
        "operations_center.adapters.forgejo.ForgejoClient", _Fake, raising=False
    )

    class _Settings:
        board_backend = "forgejo"
        forgejo = _Forgejo()

        def forgejo_token(self):
            return "tok"

    board.make_board_client(_Settings())
    assert captured == {
        "base_url": "http://forge.local",
        "api_token": "tok",
        "owner": "protocolwarden",
        "repo": "board",
    }, "the factory changed the construction contract the callers relied on"


def test_factory_tolerates_a_settings_double(monkeypatch):
    """A MagicMock settings object must still build the default backend.

    `getattr(settings, "board_backend", ...)` looks like it defaults, but a
    MagicMock answers every attribute, so the default is unreachable and the
    factory raised "unknown board_backend <MagicMock ...>". That is not
    hypothetical: it broke test_orphan_branch_check from #509 until #513, and
    went unnoticed because CI runs `tests/unit` and never `tests/maintenance/`.
    """
    from unittest.mock import MagicMock

    from operations_center.adapters import board

    built = {}

    class _Fake:
        def __init__(self, **kw):
            built.update(kw)

    monkeypatch.setattr(
        "operations_center.adapters.forgejo.ForgejoClient", _Fake, raising=False
    )

    board.make_board_client(MagicMock())
    assert built, "a settings double no longer builds the default backend"


def test_factory_refuses_forgejo_without_a_config_block():
    """No silent fallback: a board the fleet cannot reach must be an error, not
    an empty-looking queue."""
    from operations_center.adapters import board

    class _Settings:
        board_backend = "forgejo"
        forgejo = None

    with pytest.raises(RuntimeError, match="no `forgejo:` settings block"):
        board.make_board_client(_Settings())


def test_factory_still_rejects_a_real_unknown_backend():
    """Tolerating a mock must not tolerate a typo in the config."""
    from operations_center.adapters import board

    class _Settings:
        board_backend = "gitlab"

    with pytest.raises(RuntimeError, match="unknown board_backend"):
        board.make_board_client(_Settings())


def test_asking_for_plane_says_it_was_removed():
    """A config left on the retired backend deserves a straight answer.

    "unknown board_backend 'plane'" would read as a typo. It was a real backend
    until the 2026-08-18 cutover, and an operator with an old config is asking a
    reasonable question.
    """
    from operations_center.adapters import board

    class _Settings:
        board_backend = "plane"

    with pytest.raises(RuntimeError, match="removed"):
        board.make_board_client(_Settings())


# ── the project id ───────────────────────────────────────────────────────────


def test_board_project_id_comes_from_the_active_backend():
    from operations_center.adapters.board import board_project_id

    class _Settings:
        board_backend = "forgejo"
        forgejo = _Forgejo()

    assert board_project_id(_Settings()) == "protocolwarden/board"


def test_board_project_id_fails_loudly_without_a_config_block():
    """#516's concern: this sits on the dispatch path, so an AttributeError here
    means a correctly-configured-looking fleet executes nothing."""
    from operations_center.adapters.board import board_project_id

    class _Settings:
        board_backend = "forgejo"
        forgejo = None

    with pytest.raises(RuntimeError, match="no `forgejo:` settings block"):
        board_project_id(_Settings())


def test_board_project_id_tolerates_a_settings_double():
    """Same MagicMock normalisation the factory has (#513)."""
    from unittest.mock import MagicMock

    from operations_center.adapters.board import board_project_id

    settings = MagicMock()
    settings.forgejo.owner = "o"
    settings.forgejo.repo = "r"
    assert board_project_id(settings) == "o/r"


# ── the boundary ─────────────────────────────────────────────────────────────


def test_no_file_reaches_past_the_boundary():
    """Callers depend on `BoardClient`, never on the concrete class.

    This is the ratchet that took Plane from 37 importers to zero, aimed now at
    the backend that actually exists. The reason it existed never depended on
    which backend it was.
    """
    actual = _importers()
    added = sorted(actual - CONSTRUCTS_DIRECTLY_BY_DESIGN)
    assert not added, (
        f"{len(added)} file(s) import ForgejoClient directly without being on the "
        f"accepted list: {added}. Use "
        "`from operations_center.adapters.board import BoardClient, make_board_client` "
        "instead — the point of the seam is that swapping the board is one change, "
        "not one per caller."
    )


def test_allowlist_has_no_stale_entries():
    """A file that stops constructing directly must be struck off, so the list
    measures real remaining coupling."""
    actual = _importers()
    stale = sorted(CONSTRUCTS_DIRECTLY_BY_DESIGN - actual)
    assert not stale, (
        f"{len(stale)} file(s) no longer import ForgejoClient but are still "
        f"listed: {stale}. Remove them from CONSTRUCTS_DIRECTLY_BY_DESIGN."
    )


def test_the_hand_rolled_factories_are_gone():
    """Ten copies of the same constructor was the evidence the seam was missing.

    Kept pointed at the current backend so the pattern cannot grow back under a
    new name.
    """
    remaining = []
    for p in SRC.rglob("*.py"):
        rel = p.relative_to(SRC).as_posix()
        if rel.startswith("adapters/") or "__pycache__" in rel:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if re.search(r"def _(?:make_)?(?:board|forgejo)_client\b", text) and re.search(
            r"ForgejoClient\(", text
        ):
            remaining.append(rel)
    assert not remaining, (
        f"{len(remaining)} file(s) hand-roll the client constructor: {remaining}"
    )


def test_the_retired_backend_is_actually_gone():
    """The adapter package, not just its callers.

    Leaving 382 lines of unreachable client behind would be a second source of
    truth about how the fleet talks to a board — one nothing exercises, and so
    one nothing keeps honest.
    """
    assert not (SRC / "adapters" / "plane").exists(), (
        "adapters/plane is back; the board backend is Forgejo"
    )
    # Deliberately importers, not every mention: several docstrings narrate the
    # migration ("callers imported PlaneClient by name..."), and that history is
    # why the seam exists. What must not come back is a live dependency.
    importers = sorted(
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if "__pycache__" not in p.as_posix()
        and re.search(
            r"^[ \t]*(from operations_center\.adapters\.plane|import operations_center\.adapters\.plane)",
            p.read_text(encoding="utf-8", errors="replace"),
            re.M,
        )
    )
    assert not importers, f"files still import the removed adapter: {importers}"
