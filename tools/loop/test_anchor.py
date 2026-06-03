# SPDX-License-Identifier: Proprietary
# Copyright (C) 2026 ProtocolWarden
"""Tests for the loop controller's ContextLifecycle anchoring."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import controller  # noqa: E402


class _Result:
    def __init__(self, returncode: int, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout


def test_anchor_merges_cl_exports(monkeypatch):
    monkeypatch.setattr(
        controller.subprocess,
        "run",
        lambda *a, **k: _Result(
            0, "export CL_ANCHOR=/x/PlatformManifest\nexport CL_SESSION_ID=sid-1\n"
        ),
    )
    env: dict[str, str] = {}
    controller._anchor_via_cl(env)
    assert env["CL_ANCHOR"] == "/x/PlatformManifest"
    assert env["CL_SESSION_ID"] == "sid-1"


def test_anchor_noop_when_unhooked(monkeypatch):
    # cl session start exits non-zero (repo not hooked to a manifest)
    monkeypatch.setattr(controller.subprocess, "run", lambda *a, **k: _Result(1, ""))
    env: dict[str, str] = {}
    controller._anchor_via_cl(env)
    assert env == {}


def test_anchor_noop_when_cl_missing(monkeypatch):
    def _raise(*a, **k):
        raise OSError("cl not found")

    monkeypatch.setattr(controller.subprocess, "run", _raise)
    env: dict[str, str] = {}
    controller._anchor_via_cl(env)
    assert env == {}


# --- session-boundary hydrate/capture (codex/aider; claude uses hooks) ---


def test_session_boundary_gating():
    assert controller._cl_session_boundary("claude", {"CL_ANCHOR": "/m"}) is False
    assert controller._cl_session_boundary("codex", {"CL_ANCHOR": "/m"}) is True
    assert controller._cl_session_boundary("codex", {}) is False


def test_hydrate_prepends_for_codex(monkeypatch):
    monkeypatch.setattr(controller.subprocess, "run", lambda *a, **k: _Result(0, '{"capsule": {}}'))
    out = controller._cl_hydrate("codex", {"CL_ANCHOR": "/m"}, 1, "BASE")
    assert "ContextLifecycle hydrate" in out and out.endswith("BASE")


def test_hydrate_noop_for_claude():
    assert controller._cl_hydrate("claude", {"CL_ANCHOR": "/m"}, 1, "BASE") == "BASE"


def test_capture_runs_for_codex_only(monkeypatch):
    calls = []
    monkeypatch.setattr(
        controller.subprocess, "run", lambda *a, **k: calls.append(a[0]) or _Result(0, "")
    )
    import pathlib

    controller._cl_capture("codex", {"CL_ANCHOR": "/m"}, 1, 0, pathlib.Path("/x.log"))
    controller._cl_capture("claude", {"CL_ANCHOR": "/m"}, 1, 0, pathlib.Path("/x.log"))
    assert len(calls) == 1 and "capture" in calls[0]
