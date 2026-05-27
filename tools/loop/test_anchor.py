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
        lambda *a, **k: _Result(0, "export CL_ANCHOR=/x/PlatformManifest\nexport CL_SESSION_ID=sid-1\n"),
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
