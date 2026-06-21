# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""BackendCheckExtractor: prompt build, parse, and drift-monitor integration."""

from __future__ import annotations

from operations_center.eval.check_extractors import (
    BackendCheckExtractor,
    build_extraction_prompt,
    parse_checks,
)
from operations_center.eval.corpus import Case
from operations_center.eval.critic import run_drift_monitor


def _case(cid="c") -> Case:
    return Case(
        case_id=cid,
        kind="verdict",
        input={"diff": "def f():\n-  return 1\n+  return None"},
        ground_truth={"result": "CONCERNS", "failing": ["code_quality"]},
    )


def test_build_prompt_includes_change_and_schema():
    prompt = build_extraction_prompt(_case())
    assert "return None" in prompt
    assert "verdict.json" in prompt  # the typed-verdict schema fragment


def test_parse_checks_plain_json():
    raw = '{"checks": [{"check_id": "code_quality", "status": "fail"}]}'
    assert parse_checks(raw) == [{"check_id": "code_quality", "status": "fail"}]


def test_parse_checks_prose_wrapped_json():
    raw = "Sure! Here is my review:\n{\"checks\": [{\"check_id\": \"x\"}]}\nHope that helps."
    assert parse_checks(raw) == [{"check_id": "x"}]


def test_parse_checks_malformed_returns_empty():
    assert parse_checks("not json at all") == []
    assert parse_checks('{"checks": "not a list"}') == []
    assert parse_checks("") == []


def test_extractor_returns_parsed_checks():
    ext = BackendCheckExtractor(
        invoke=lambda prompt, vote: '{"checks": [{"check_id": "code_quality", "status": "fail"},'
        '{"check_id": "no_tooling_artifacts", "status": "pass"}]}'
    )
    checks = ext(_case(), vote=0)
    assert {c["check_id"] for c in checks} == {"code_quality", "no_tooling_artifacts"}


def test_extractor_backend_error_reads_as_drift_not_pass():
    def _boom(prompt, vote):
        raise RuntimeError("backend down")

    ext = BackendCheckExtractor(invoke=_boom)
    assert ext(_case(), vote=0) == []  # empty → compute_verdict → CONCERNS (drift), never a pass


def test_drift_monitor_with_backend_extractor_agrees():
    # Model reproduces the answer (code_quality fail) → no drift.
    ext = BackendCheckExtractor(
        invoke=lambda p, v: '{"checks": [{"check_id": "code_quality", "status": "fail"},'
        '{"check_id": "no_tooling_artifacts", "status": "pass"}]}'
    )
    results = run_drift_monitor([_case()], ext, votes=3)
    assert results[0].drifted is False


def test_drift_monitor_with_backend_extractor_flags_drift():
    # Model now says everything passes → computes LGTM, answer is CONCERNS → drift.
    ext = BackendCheckExtractor(
        invoke=lambda p, v: '{"checks": [{"check_id": "code_quality", "status": "pass"},'
        '{"check_id": "no_tooling_artifacts", "status": "pass"}]}'
    )
    results = run_drift_monitor([_case()], ext, votes=3)
    assert results[0].drifted is True
