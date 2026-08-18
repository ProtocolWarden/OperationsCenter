# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""The smoke entrypoint goes through the seam, so it is tested through it too.

Its Plane predecessor mocked `PlaneClient` transport-level; this one fakes the
board client behind `make_board_client`, which is the boundary the smoke (and
the fleet) actually depends on.
"""

from types import SimpleNamespace

import pytest

from operations_center.entrypoints.smoke import forgejo


class _FakeBoard:
    def __init__(self, statuses: list[str]):
        self._statuses = list(statuses)
        self.calls: list[tuple] = []

    def list_states(self):
        return [{"id": n, "name": n} for n in ("Backlog", "Done")]

    def list_issues(self):
        return []

    def create_issue(self, *, name, description):
        self.calls.append(("create", name))
        return {"number": 7}

    def transition_issue(self, task_id, state):
        self.calls.append(("transition", task_id, state))

    def fetch_issue(self, task_id):
        return {"number": task_id}

    def to_board_task(self, issue):
        return SimpleNamespace(status=self._statuses.pop(0))

    def comment_issue(self, task_id, body):
        self.calls.append(("comment", task_id))

    def set_priority(self, task_id, priority):
        self.calls.append(("priority", task_id, priority))


def _wire(monkeypatch, board):
    monkeypatch.setattr("operations_center.config.load_settings", lambda _: object())
    monkeypatch.setattr(
        "operations_center.adapters.board.make_board_client", lambda _: board
    )


def test_read_only_smoke_never_writes(monkeypatch, capsys):
    board = _FakeBoard(statuses=[])
    _wire(monkeypatch, board)
    monkeypatch.setattr("sys.argv", ["smoke", "--config", "unused.yaml"])

    assert forgejo.main() == 0

    out = capsys.readouterr().out
    assert "read-only smoke OK" in out
    assert board.calls == [], "read-only smoke performed writes"


def test_write_smoke_runs_the_fleet_round_trip(monkeypatch, capsys):
    board = _FakeBoard(statuses=["Ready for AI", "Done"])
    _wire(monkeypatch, board)
    monkeypatch.setattr("sys.argv", ["smoke", "--config", "unused.yaml", "--write"])

    assert forgejo.main() == 0

    assert [c[0] for c in board.calls] == [
        "create",
        "transition",
        "comment",
        "priority",
        "transition",
    ]
    assert ("transition", "7", "Ready for AI") in board.calls
    assert ("transition", "7", "Done") in board.calls
    assert "write smoke OK" in capsys.readouterr().out


def test_write_smoke_fails_loudly_when_a_transition_does_not_stick(monkeypatch):
    board = _FakeBoard(statuses=["Backlog"])  # transition did not take
    _wire(monkeypatch, board)
    monkeypatch.setattr("sys.argv", ["smoke", "--config", "unused.yaml", "--write"])

    with pytest.raises(RuntimeError, match="expected 'Ready for AI'"):
        forgejo.main()
