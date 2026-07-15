# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import typer
from typer.testing import CliRunner

from operations_center.artifact_index import (
    ManifestInvalidError,
    ManifestNotFoundError,
)
from operations_center.behavior_calibration import (
    AnalysisProfile,
    FindingSeverity,
)
from operations_center.entrypoints.calibration import main as mod

runner = CliRunner()


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
def _make_summary(
    *,
    total=3,
    singleton=1,
    by_status=None,
    missing=0,
    unresolved=0,
    excluded=0,
):
    return SimpleNamespace(
        total_artifacts=total,
        singleton_count=singleton,
        by_status=by_status if by_status is not None else {"ok": 3},
        missing_file_count=missing,
        unresolved_path_count=unresolved,
        excluded_path_count=excluded,
    )


def _make_finding(severity=FindingSeverity.WARNING, category="coverage", summary="a gap"):
    return SimpleNamespace(
        severity=severity,
        category=SimpleNamespace(value=category),
        summary=summary,
    )


def _make_recommendation(priority="high", summary="do x", risk="low"):
    return SimpleNamespace(
        priority=SimpleNamespace(value=priority),
        summary=summary,
        risk=risk,
    )


def _make_report(
    *,
    has_errors=False,
    findings=None,
    recommendations=None,
    summary=None,
    profile=AnalysisProfile.SUMMARY,
):
    return SimpleNamespace(
        repo_id="repo-1",
        audit_type="full",
        analysis_profile=profile,
        artifact_index_summary=summary if summary is not None else _make_summary(),
        findings=findings if findings is not None else [],
        recommendations=recommendations if recommendations is not None else [],
        has_errors=has_errors,
        model_dump_json=MagicMock(return_value='{"json": true}'),
    )


def _make_index():
    return SimpleNamespace(
        source=SimpleNamespace(repo_id="repo-1", run_id="run-1", audit_type="full")
    )


@pytest.fixture
def patched(monkeypatch):
    """Patch every external collaborator on the module."""
    state = SimpleNamespace()
    state.manifest = SimpleNamespace(name="manifest")
    state.index = _make_index()
    state.report = _make_report()

    state.load_manifest = MagicMock(return_value=state.manifest)
    state.build_index = MagicMock(return_value=state.index)
    state.analyze = MagicMock(return_value=state.report)
    state.write_report = MagicMock(return_value="/tmp/out/report.json")
    state.load_report = MagicMock(return_value=state.report)

    monkeypatch.setattr(mod, "load_artifact_manifest", state.load_manifest)
    monkeypatch.setattr(mod, "build_artifact_index", state.build_index)
    monkeypatch.setattr(mod, "analyze_artifacts", state.analyze)
    monkeypatch.setattr(mod, "write_calibration_report", state.write_report)
    monkeypatch.setattr(mod, "load_calibration_report", state.load_report)
    return state


# --------------------------------------------------------------------------- #
# _load_index
# --------------------------------------------------------------------------- #
def test_load_index_happy_no_repo_root(patched):
    manifest, index = mod._load_index("/some/manifest.json")
    assert manifest is patched.manifest
    assert index is patched.index
    # build_artifact_index called with repo_root=None when not supplied
    _, kwargs = patched.build_index.call_args
    assert kwargs["repo_root"] is None


def test_load_index_with_repo_root(patched):
    mod._load_index("/some/manifest.json", "/repo/root")
    _, kwargs = patched.build_index.call_args
    assert str(kwargs["repo_root"]) == "/repo/root"


def test_load_index_not_found(patched):
    patched.load_manifest.side_effect = ManifestNotFoundError("missing")
    with pytest.raises(typer.Exit) as ei:
        mod._load_index("/missing.json")
    assert ei.value.exit_code == 1


def test_load_index_invalid(patched):
    patched.load_manifest.side_effect = ManifestInvalidError("bad json")
    with pytest.raises(typer.Exit) as ei:
        mod._load_index("/bad.json")
    assert ei.value.exit_code == 2


# --------------------------------------------------------------------------- #
# analyze command
# --------------------------------------------------------------------------- #
def test_analyze_invalid_profile(patched):
    result = runner.invoke(mod.app, ["analyze", "-m", "/m.json", "-p", "nonsense"])
    assert result.exit_code == 3
    assert "Invalid profile" in result.output
    # never reached analysis
    patched.analyze.assert_not_called()


def test_analyze_happy_summary_exit0(patched):
    result = runner.invoke(mod.app, ["analyze", "-m", "/m.json"])
    assert result.exit_code == 0
    patched.analyze.assert_called_once()
    ci = patched.analyze.call_args.args[0]
    assert ci.analysis_profile is AnalysisProfile.SUMMARY
    assert ci.include_artifact_content is False


def test_analyze_has_errors_exit1(patched):
    patched.report = _make_report(has_errors=True)
    patched.analyze.return_value = patched.report
    result = runner.invoke(mod.app, ["analyze", "-m", "/m.json"])
    assert result.exit_code == 1


def test_analyze_json_output(patched, monkeypatch):
    ps = MagicMock()
    monkeypatch.setattr(mod, "print_structured", ps)
    result = runner.invoke(mod.app, ["analyze", "-m", "/m.json", "--json"])
    assert result.exit_code == 0
    ps.assert_called_once_with(mod.console, patched.report)


def test_analyze_include_content_flag(patched):
    result = runner.invoke(mod.app, ["analyze", "-m", "/m.json", "--include-content"])
    assert result.exit_code == 0
    ci = patched.analyze.call_args.args[0]
    assert ci.include_artifact_content is True


def test_analyze_output_dir_writes_report(patched):
    result = runner.invoke(mod.app, ["analyze", "-m", "/m.json", "--output-dir", "/tmp/out"])
    assert result.exit_code == 0
    patched.write_report.assert_called_once_with(patched.report, "/tmp/out")
    assert "Report written" in result.output


def test_analyze_passes_explicit_profile_value(patched):
    result = runner.invoke(mod.app, ["analyze", "-m", "/m.json", "-p", "coverage_gaps"])
    assert result.exit_code == 0
    ci = patched.analyze.call_args.args[0]
    assert ci.analysis_profile is AnalysisProfile.COVERAGE_GAPS


def test_analyze_propagates_load_failure(patched):
    patched.load_manifest.side_effect = ManifestNotFoundError("nope")
    result = runner.invoke(mod.app, ["analyze", "-m", "/x.json"])
    assert result.exit_code == 1
    assert "Not found" in result.output


# --------------------------------------------------------------------------- #
# tune-autonomy command
# --------------------------------------------------------------------------- #
def test_tune_autonomy_uses_recommendation_profile(patched):
    result = runner.invoke(mod.app, ["tune-autonomy", "-m", "/m.json"])
    assert result.exit_code == 0
    ci = patched.analyze.call_args.args[0]
    assert ci.analysis_profile is AnalysisProfile.RECOMMENDATION


def test_tune_autonomy_json(patched, monkeypatch):
    ps = MagicMock()
    monkeypatch.setattr(mod, "print_structured", ps)
    result = runner.invoke(mod.app, ["tune-autonomy", "-m", "/m.json", "--json"])
    assert result.exit_code == 0
    ps.assert_called_once_with(mod.console, patched.report)


def test_tune_autonomy_with_recommendations_table(patched):
    rep = _make_report(
        recommendations=[_make_recommendation()],
        profile=AnalysisProfile.RECOMMENDATION,
    )
    patched.analyze.return_value = rep
    result = runner.invoke(mod.app, ["tune-autonomy", "-m", "/m.json"])
    assert result.exit_code == 0
    assert "Advisory Recommendations" in result.output


def test_tune_autonomy_no_recommendations(patched):
    rep = _make_report(recommendations=[], profile=AnalysisProfile.RECOMMENDATION)
    patched.analyze.return_value = rep
    result = runner.invoke(mod.app, ["tune-autonomy", "-m", "/m.json"])
    assert result.exit_code == 0
    assert "No recommendations produced" in result.output


def test_tune_autonomy_output_dir(patched):
    result = runner.invoke(mod.app, ["tune-autonomy", "-m", "/m.json", "--output-dir", "/tmp/o"])
    assert result.exit_code == 0
    patched.write_report.assert_called_once_with(patched.report, "/tmp/o")


# --------------------------------------------------------------------------- #
# report command
# --------------------------------------------------------------------------- #
def test_report_happy(patched):
    result = runner.invoke(mod.app, ["report", "/some/report.json"])
    assert result.exit_code == 0
    patched.load_report.assert_called_once_with("/some/report.json")
    assert "Calibration Report" in result.output


def test_report_json(patched, monkeypatch):
    ps = MagicMock()
    monkeypatch.setattr(mod, "print_structured", ps)
    result = runner.invoke(mod.app, ["report", "/some/report.json", "--json"])
    assert result.exit_code == 0
    ps.assert_called_once_with(mod.console, patched.report)


def test_report_not_found(patched):
    patched.load_report.side_effect = FileNotFoundError("gone")
    result = runner.invoke(mod.app, ["report", "/gone.json"])
    assert result.exit_code == 1
    assert "Not found" in result.output


def test_report_invalid(patched):
    patched.load_report.side_effect = ValueError("corrupt")
    result = runner.invoke(mod.app, ["report", "/bad.json"])
    assert result.exit_code == 2
    assert "Invalid report" in result.output


# --------------------------------------------------------------------------- #
# _print_report_summary branches
# --------------------------------------------------------------------------- #
def test_print_summary_no_findings(capsys, patched):
    rep = _make_report(findings=[])
    mod._print_report_summary(rep)
    out = capsys.readouterr().out
    assert "No findings" in out
    assert "Calibration Report" in out


def test_print_summary_with_findings_and_counts(capsys):
    summary = _make_summary(missing=2, unresolved=1, excluded=3)
    rep = _make_report(
        summary=summary,
        findings=[
            _make_finding(severity=FindingSeverity.CRITICAL),
            _make_finding(severity=FindingSeverity.INFO, summary="note"),
        ],
    )
    mod._print_report_summary(rep)
    out = capsys.readouterr().out
    assert "missing files: 2" in out
    assert "unresolved paths: 1" in out
    assert "excluded paths: 3" in out
    assert "Findings (2)" in out


def test_print_summary_unknown_severity_style():
    # severity not present in _SEVERITY_STYLE -> style defaults to "".
    # An empty style produces unbalanced rich markup ("[]...[/]"), which rich
    # rejects: this exercises the _SEVERITY_STYLE.get(..., "") default branch.
    from rich.errors import MarkupError

    class _Weird(str):
        value = "weird"

    finding = SimpleNamespace(
        severity=_Weird("weird"), category=SimpleNamespace(value="cat"), summary="s"
    )
    rep = _make_report(findings=[finding])
    with pytest.raises(MarkupError):
        mod._print_report_summary(rep)


def test_severity_style_map_complete():
    # all four known severities mapped
    for sev in FindingSeverity:
        assert sev in mod._SEVERITY_STYLE
