# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the lineage display CLI — the human-facing observability view."""

from __future__ import annotations

import json
from pathlib import Path

from operations_center.lineage.cli import main, render_chain
from operations_center.lineage.projection import build_chain

_TASK = "abcde123-0000"


def _seed(tmp_path: Path) -> tuple[Path, Path]:
    runs = tmp_path / "runs"
    d = runs / "run-1"
    d.mkdir(parents=True)
    (d / "proposal.json").write_text(
        json.dumps({"proposal_id": "p1", "task_id": _TASK, "goal_text": "g", "target": {"repo_key": "a/b"}})
    )
    (d / "run_metadata.json").write_text(
        json.dumps({"run_id": "run-1", "status": "succeeded", "success": True, "written_at": "2026-06-22T12:00:00+00:00"})
    )
    (d / "result.json").write_text(json.dumps({"run_id": "run-1", "pull_request_url": None}))
    return runs, tmp_path / "state"


def test_render_marks_edges_display_only(tmp_path: Path):
    runs, state = _seed(tmp_path)
    chain = build_chain(_TASK, runs_root=runs, state_dir=state)
    out = render_chain(chain)
    assert "display-only" in out  # nothing steerable today → honest label
    assert "steerable edges: 0" in out


def test_cli_json_roundtrips(tmp_path: Path, capsys):
    runs, state = _seed(tmp_path)
    rc = main([_TASK, "--runs-root", str(runs), "--state-dir", str(state), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["task_id"] == _TASK
    assert any(n["kind"] == "run" for n in payload["nodes"])


def test_cli_list_all(tmp_path: Path, capsys):
    runs, state = _seed(tmp_path)
    rc = main(["--runs-root", str(runs), "--state-dir", str(state)])
    assert rc == 0
    assert _TASK in capsys.readouterr().out
