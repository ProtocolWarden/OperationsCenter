# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Hermetic coverage tests for the run_show CLI entrypoint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from operations_center.entrypoints.run_show import main as mod


_runner = CliRunner()


def _write_trace(directory: Path, payload: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    trace = directory / "execution_trace.json"
    trace.write_text(json.dumps(payload), encoding="utf-8")
    return trace


def _full_payload(stdout: str = "", stderr: str = "", art: str = "") -> dict:
    return {
        "headline": "SUCCEEDED",
        "status": "succeeded",
        "summary": "did a thing",
        "routing": {
            "decision_id": "d-1",
            "selected_lane": "direct_local",
            "selected_backend": "aider_local",
            "policy_rule_matched": "rule-x",
            "rationale": "because",
            "switchboard_version": "1.0",
            "confidence": 0.9,
            "alternatives_considered": ["a", "b"],
            "ignored_field": "nope",
        },
        "provenance": {
            "source": "github",
            "repo": "x/y",
            "ref": "main",
            "patches": [],
        },
        "observed_runtime": {
            "worker_backend_strategy": "round_robin",
            "preferred_worker_backend": "claude_code",
            "selected_worker_backend": "codex_cli",
            "fallback_used": True,
            "worker_backend_cooldowns": {"claude_code": 30},
        },
        "runtime_invocation_ref": {
            "invocation_id": "iv-1",
            "runtime_name": "direct_local",
            "runtime_kind": "subprocess",
            "stdout_path": stdout,
            "stderr_path": stderr,
            "artifact_directory": art,
        },
        "warnings": ["w1", "w2"],
    }


# --- _render -----------------------------------------------------------------


def test_render_nonempty_list() -> None:
    assert mod._render(["a", "b", 1]) == "a, b, 1"


def test_render_empty_list() -> None:
    assert mod._render([]) == "(none)"


def test_render_none() -> None:
    assert mod._render(None) == "(none)"


def test_render_scalar() -> None:
    assert mod._render(42) == "42"


# --- _presence_tag -----------------------------------------------------------


def test_presence_tag_missing(tmp_path: Path) -> None:
    tag = mod._presence_tag(str(tmp_path / "nope.txt"))
    assert "missing" in tag


def test_presence_tag_dir(tmp_path: Path) -> None:
    d = tmp_path / "adir"
    d.mkdir()
    tag = mod._presence_tag(str(d))
    assert "dir present" in tag


def test_presence_tag_file_bytes(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("hello", encoding="utf-8")
    tag = mod._presence_tag(str(f))
    assert "5 bytes" in tag


# --- _default_search_roots ---------------------------------------------------


def test_default_search_roots_filters_nonexistent(tmp_path, monkeypatch) -> None:
    cwd = tmp_path / "cwd"
    runs = cwd / ".operations_center" / "runs"
    runs.mkdir(parents=True)
    monkeypatch.setattr(mod.Path, "cwd", staticmethod(lambda: cwd))
    monkeypatch.setattr(mod.Path, "home", staticmethod(lambda: tmp_path / "nohome"))
    monkeypatch.delenv("OC_RUNS_ROOT", raising=False)
    roots = mod._default_search_roots()
    assert roots == [runs]


def test_default_search_roots_includes_env(tmp_path, monkeypatch) -> None:
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    env_root = tmp_path / "envroot"
    env_root.mkdir()
    monkeypatch.setattr(mod.Path, "cwd", staticmethod(lambda: cwd))
    monkeypatch.setattr(mod.Path, "home", staticmethod(lambda: tmp_path / "nohome"))
    monkeypatch.setenv("OC_RUNS_ROOT", str(env_root))
    roots = mod._default_search_roots()
    assert env_root in roots
    # cwd/.operations_center/runs does not exist so it's filtered out
    assert all(p.exists() for p in roots)


# --- _resolve_trace ----------------------------------------------------------


def test_resolve_trace_explicit_exists(tmp_path: Path) -> None:
    trace = _write_trace(tmp_path / "run", _full_payload())
    assert mod._resolve_trace(None, trace, []) == trace


def test_resolve_trace_explicit_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    with pytest.raises(typer.BadParameter, match="does not exist"):
        mod._resolve_trace(None, missing, [])


def test_resolve_trace_no_run_id_no_explicit() -> None:
    with pytest.raises(typer.BadParameter, match="provide a run_id"):
        mod._resolve_trace(None, None, [])


def test_resolve_trace_direct_match(tmp_path: Path) -> None:
    root = tmp_path / "root"
    trace = _write_trace(root / "abcd1234", _full_payload())
    assert mod._resolve_trace("abcd1234", None, [root]) == trace


def test_resolve_trace_prefix_match(tmp_path: Path) -> None:
    root = tmp_path / "root"
    trace = _write_trace(root / "abcd1234ef", _full_payload())
    # no exact "abcd" dir -> prefix resolution
    assert mod._resolve_trace("abcd", None, [root]) == trace


def test_resolve_trace_not_found(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(typer.BadParameter, match="no execution_trace.json"):
        mod._resolve_trace("zzzz", None, [root])


def test_resolve_trace_not_found_no_roots() -> None:
    with pytest.raises(typer.BadParameter, match="no roots"):
        mod._resolve_trace("zzzz", None, [])


def test_resolve_trace_ambiguous(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _write_trace(root / "abcd1111", _full_payload())
    _write_trace(root / "abcd2222", _full_payload())
    with pytest.raises(typer.BadParameter, match="ambiguous"):
        mod._resolve_trace("abcd", None, [root])


def test_resolve_trace_prefix_child_is_file_skipped(tmp_path: Path) -> None:
    root = tmp_path / "root"
    trace = _write_trace(root / "abcdreal", _full_payload())
    # a non-dir child whose name also starts with the prefix must be ignored
    (root / "abcdfile").write_text("x", encoding="utf-8")
    assert mod._resolve_trace("abcd", None, [root]) == trace


def test_resolve_trace_prefix_dir_without_trace_skipped(tmp_path: Path) -> None:
    root = tmp_path / "root"
    trace = _write_trace(root / "abcdgood", _full_payload())
    (root / "abcdempty").mkdir()  # dir matches prefix but lacks trace
    assert mod._resolve_trace("abcd", None, [root]) == trace


# --- _print_trace via direct call --------------------------------------------


def test_print_trace_full(tmp_path: Path) -> None:
    stdout = tmp_path / "out.txt"
    stdout.write_text("abc", encoding="utf-8")
    artdir = tmp_path / "art"
    artdir.mkdir()
    payload = _full_payload(
        stdout=str(stdout), stderr=str(tmp_path / "missing.txt"), art=str(artdir)
    )
    # Should render without raising; covers all populated branches.
    mod._print_trace(payload)
    assert payload["status"] == "succeeded"


def test_print_trace_empty_blocks(capsys) -> None:
    # All optional blocks absent -> dim placeholder branches.
    mod._print_trace({})
    out = capsys.readouterr().out
    assert "no headline" in out
    assert "no routing block" in out


def test_print_trace_no_summary_no_warnings() -> None:
    payload = {"headline": "H", "status": "ok"}
    mod._print_trace(payload)
    assert payload["headline"] == "H"


def test_print_trace_ref_nonstring_paths() -> None:
    # stdout_path is non-string -> presence annotation branch is skipped.
    payload = {
        "runtime_invocation_ref": {
            "invocation_id": "iv",
            "stdout_path": 123,
        }
    }
    mod._print_trace(payload)
    assert payload["runtime_invocation_ref"]["stdout_path"] == 123


# --- show command (CLI) ------------------------------------------------------


def test_show_json_output(tmp_path: Path, monkeypatch) -> None:
    trace = _write_trace(tmp_path / "run", _full_payload())
    result = _runner.invoke(mod.app, ["--trace", str(trace), "--json"])
    assert result.exit_code == 0
    assert '"status": "succeeded"' in result.stdout


def test_show_formatted_with_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _write_trace(root / "abcd1234", _full_payload())
    result = _runner.invoke(mod.app, ["abcd1234", "--root", str(root)])
    assert result.exit_code == 0
    assert "source:" in result.stdout


def test_show_uses_default_roots(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}

    def _fake_default_roots() -> list[Path]:
        captured["called"] = True
        return []

    monkeypatch.setattr(mod, "_default_search_roots", _fake_default_roots)
    result = _runner.invoke(mod.app, ["someid"])
    assert captured.get("called") is True
    assert result.exit_code != 0


def test_show_no_args_is_help() -> None:
    result = _runner.invoke(mod.app, [])
    # no_args_is_help triggers a non-zero exit (typer exits with code 2).
    assert result.exit_code == 2


def test_show_explicit_missing_trace_errors(tmp_path: Path) -> None:
    result = _runner.invoke(mod.app, ["--trace", str(tmp_path / "nope.json")])
    assert result.exit_code != 0


# --- main() ------------------------------------------------------------------


def test_main_invokes_app(monkeypatch) -> None:
    called = {}

    def _fake_app() -> None:
        called["yes"] = True

    monkeypatch.setattr(mod, "app", _fake_app)
    mod.main()
    assert called.get("yes") is True
