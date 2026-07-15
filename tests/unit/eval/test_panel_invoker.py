# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""C3 — live cross-family invoker: family resolution, availability probing,
and the codex-stdout-instead-of-file fallback (verdict.last_json_object)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from operations_center.eval import panel_invoker
from operations_center.eval.corpus import Case
from operations_center.eval.panel_invoker import (
    DEFAULT_MODEL_BY_FAMILY,
    LiveFamilyExtractor,
    build_family_extractor,
    build_panel_extractors,
    resolve_available_families,
)


def _case() -> Case:
    return Case(
        case_id="c",
        kind="extraction",
        input={"diff": "def f():\n-  return 1\n+  return None"},
        ground_truth={"result": "CONCERNS", "failing": ["code_quality"]},
    )


class _FakeProc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


def test_resolve_available_families_filters_missing_binaries(monkeypatch):
    monkeypatch.setattr(
        panel_invoker.shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None
    )
    assert resolve_available_families(["claude_code", "codex_cli"]) == ["claude_code"]


def test_resolve_available_families_all_present(monkeypatch):
    monkeypatch.setattr(panel_invoker.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert resolve_available_families(["claude_code", "codex_cli"]) == ["claude_code", "codex_cli"]


def test_resolve_available_families_none_present(monkeypatch):
    monkeypatch.setattr(panel_invoker.shutil, "which", lambda name: None)
    assert resolve_available_families(["claude_code", "codex_cli"]) == []


def test_build_family_extractor_known_families_use_defaults():
    for family, model in DEFAULT_MODEL_BY_FAMILY.items():
        ext = build_family_extractor(family)
        assert isinstance(ext, LiveFamilyExtractor)
        assert ext._model == model
        assert ext._backend == family


def test_build_family_extractor_unknown_family_raises():
    with pytest.raises(ValueError):
        build_family_extractor("some_unknown_family")


def test_build_family_extractor_explicit_model_overrides_default():
    ext = build_family_extractor("claude_code", model="haiku")
    assert ext._model == "haiku"


def test_build_panel_extractors_builds_one_per_family():
    extractors = build_panel_extractors(["claude_code", "codex_cli"])
    assert set(extractors) == {"claude_code", "codex_cli"}
    assert all(isinstance(e, LiveFamilyExtractor) for e in extractors.values())


def test_live_extractor_prefers_verdict_json_file(monkeypatch):
    """The claude-style contract: the backend writes verdict.json to its cwd."""
    written = {"checks": [{"check_id": "code_quality", "status": "fail"}]}

    def _fake_run(argv, *, cwd, capture_output, text, timeout):
        (Path(cwd) / "verdict.json").write_text(json.dumps(written), encoding="utf-8")
        return _FakeProc()

    monkeypatch.setattr(panel_invoker.subprocess, "run", _fake_run)
    ext = LiveFamilyExtractor("claude_code", "sonnet")
    assert ext(_case(), vote=0) == written["checks"]


def test_live_extractor_falls_back_to_stdout_codex_style(monkeypatch):
    """Codex-stdout fallback: no verdict.json written, answer only on stdout,
    prose-wrapped — reuses verdict.last_json_object (same fallback the C1
    council's _run_member_review uses)."""

    def _fake_run(argv, *, cwd, capture_output, text, timeout):
        return _FakeProc(
            stdout=(
                "Sure, here is my review:\n"
                '{"checks": [{"check_id": "code_quality", "status": "pass"}]}\n'
                "Hope that helps!"
            )
        )

    monkeypatch.setattr(panel_invoker.subprocess, "run", _fake_run)
    ext = LiveFamilyExtractor("codex_cli", "codex")
    checks = ext(_case(), vote=0)
    assert checks == [{"check_id": "code_quality", "status": "pass"}]


def test_live_extractor_returns_empty_on_unparseable_stdout(monkeypatch):
    monkeypatch.setattr(
        panel_invoker.subprocess, "run", lambda *a, **k: _FakeProc(stdout="not json at all")
    )
    ext = LiveFamilyExtractor("codex_cli", "codex")
    assert ext(_case(), vote=0) == []


def test_live_extractor_returns_empty_on_subprocess_failure(monkeypatch):
    def _boom(*a, **k):
        raise OSError("no such binary")

    monkeypatch.setattr(panel_invoker.subprocess, "run", _boom)
    ext = LiveFamilyExtractor("codex_cli", "codex")
    # A crash reads as drift (empty checks -> CONCERNS), never a silent pass.
    assert ext(_case(), vote=0) == []


def test_live_extractor_unsupported_backend_returns_empty_without_spawning(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("must not spawn a subprocess for an unsupported backend")

    monkeypatch.setattr(panel_invoker.subprocess, "run", _boom)
    ext = LiveFamilyExtractor("unknown_backend", "model")
    assert ext(_case(), vote=0) == []
