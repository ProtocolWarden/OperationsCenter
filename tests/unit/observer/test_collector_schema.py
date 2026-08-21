# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Capture-gate tests for the watchdog collector report.

Each test here pins one way the PHASE 1 -> PHASE 2 handoff can go wrong. The
theme: a watchdog whose collector fails silently is worse than one that is
merely down, because absent signals read downstream as healthy ones. Every
malformed-report case must therefore RAISE, never degrade.
"""

from __future__ import annotations

import copy
import json

import pytest

from operations_center.observer.collector_schema import (
    CollectorReport,
    CollectorReportError,
    parse_report,
)


def _valid_payload() -> dict:
    """A complete, schema-conformant report — the happy path every test mutates."""
    return {
        "cycle_ts": "2026-08-21T09:00:00Z",
        "lock": "acquired",
        "repo_sync": {"behind": ["SwitchBoard"], "errors": []},
        "preflight": {
            "plane": "ok",
            "switchboard": "ok",
            "watchers_running": 8,
            "watchers_total": 8,
        },
        "custodian": {
            "all_zero": False,
            "findings": [{"repo": "RxP", "check": "OC3", "delta": 2}],
        },
        "ghost": {"total_events": 3, "active": ["g-1a2b"], "fixed": ["g-9f8e"]},
        "flow": {"gaps": 0},
        "graph": {"ok": True, "error": None},
        "reaudit": {"repos_needing_audit": []},
        "regressions": {"count": 0, "findings": []},
        "triage": {
            "escalation_commented": ["t-4411"],
            "healed": [{"task_id": "t-9920", "transition": "Blocked->Ready"}],
        },
        "board_unblock": {
            "applied": [
                {
                    "task_id": "t-3310",
                    "title": "fix flaky extraction test",
                    "rule": "stale-block",
                    "from": "Blocked",
                    "to": "Ready for AI",
                }
            ],
            "skipped": [
                {"task_id": "t-7712", "rule": "dup-suppress", "reason": "duplicate exists"}
            ],
        },
        "running_tasks": ["t-5150"],
        "extraction": {
            "success_rate": 87.5,
            "extracted_count": 35,
            "total_count": 40,
            "gap_count": 5,
            "edge_case_count": 1,
            "gaps": ["test_alpha"],
            "edge_cases": [{"test_id": "test_gamma", "issue": "no_assertion_message"}],
        },
        "watchers": [
            {
                "role": "invariant-watcher",
                "running": True,
                "exit_code": None,
                "consecutive_non143": 0,
                "last_error": None,
            }
        ],
        "executor_investigation": {
            "triggered": False,
            "oom_signals": False,
            "recent_sigkills": [],
            "memory_free_gb": 12.4,
        },
    }


def _parse(payload: dict):
    return parse_report(json.dumps(payload))


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #
def test_valid_report_parses_clean() -> None:
    report, diagnostics = _parse(_valid_payload())
    assert report.lock == "acquired"
    assert not report.aborted
    assert diagnostics.clean
    assert report.custodian.findings[0].repo == "RxP"


def test_from_and_to_keywords_survive_the_alias() -> None:
    """``from`` is a Python keyword; the wire name must still round-trip."""
    report, _ = _parse(_valid_payload())
    applied = report.board_unblock.applied[0]
    assert applied.from_state == "Blocked"
    assert applied.to_state == "Ready for AI"
    assert (
        json.loads(report.model_dump_json(by_alias=True))["board_unblock"]["applied"][0]["from"]
        == "Blocked"
    )


# --------------------------------------------------------------------------- #
# The core guarantee: a truncated cycle cannot pass as a clean one
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "section",
    ["custodian", "ghost", "extraction", "watchers", "board_unblock", "executor_investigation"],
)
def test_missing_section_is_rejected_when_the_cycle_ran(section: str) -> None:
    payload = _valid_payload()
    del payload[section]
    with pytest.raises(CollectorReportError) as exc:
        _parse(payload)
    assert section in str(exc.value)


def test_truncated_report_does_not_read_as_healthy() -> None:
    """The whole point: lock alone, without an abort, must not validate.

    A sub-agent that dies after STEP 0 emits exactly this. If it parsed, PHASE 2
    would see zero custodian findings, zero ghosts, zero regressions and call
    the fleet healthy.
    """
    with pytest.raises(CollectorReportError):
        _parse({"lock": "acquired"})


# --------------------------------------------------------------------------- #
# The documented partial: abort
# --------------------------------------------------------------------------- #
def test_abort_may_be_lock_only() -> None:
    report, _ = _parse({"lock": "aborted:live_owner"})
    assert report.aborted
    assert report.abort_reason == "live_owner"
    assert report.custodian is None


def test_abort_requires_a_reason() -> None:
    """A bare ``aborted`` gives PHASE 2 nothing to branch on."""
    with pytest.raises(CollectorReportError):
        _parse({"lock": "aborted"})


def test_unknown_lock_value_is_rejected() -> None:
    with pytest.raises(CollectorReportError) as exc:
        _parse({"lock": "probably_fine"})
    assert "lock" in str(exc.value)


# --------------------------------------------------------------------------- #
# Drift: renamed / invented keys
# --------------------------------------------------------------------------- #
def test_unknown_top_level_key_is_rejected() -> None:
    payload = _valid_payload()
    payload["new_signal"] = {"count": 1}
    with pytest.raises(CollectorReportError) as exc:
        _parse(payload)
    assert "new_signal" in str(exc.value)


def test_renamed_field_is_rejected_not_defaulted() -> None:
    """A renamed key must fail, not silently fall back to the default.

    ``gap_count`` renamed to ``gaps_count`` would otherwise read as 0 gaps.
    """
    payload = _valid_payload()
    payload["extraction"]["gaps_count"] = payload["extraction"].pop("gap_count")
    with pytest.raises(CollectorReportError) as exc:
        _parse(payload)
    assert "gaps_count" in str(exc.value) or "gap_count" in str(exc.value)


# --------------------------------------------------------------------------- #
# Semantic invariants
# --------------------------------------------------------------------------- #
def test_success_rate_is_a_percentage_not_a_fraction() -> None:
    """Pins the same 0..100 invariant the extraction history store enforces.

    A collector switching to fractions would emit 0.87 for 87%, which would read
    as a catastrophic 0.87% and trip a false alarm every cycle.
    """
    payload = _valid_payload()
    payload["extraction"]["success_rate"] = 187.0
    with pytest.raises(CollectorReportError):
        _parse(payload)

    ok = _valid_payload()
    ok["extraction"]["success_rate"] = 0.87
    report, _ = _parse(ok)
    assert report.extraction.success_rate == 0.87


def test_all_zero_contradicting_findings_is_rejected() -> None:
    payload = _valid_payload()
    payload["custodian"]["all_zero"] = True
    with pytest.raises(CollectorReportError) as exc:
        _parse(payload)
    assert "all_zero" in str(exc.value)


def test_extracted_count_cannot_exceed_total() -> None:
    payload = _valid_payload()
    payload["extraction"]["extracted_count"] = 99
    with pytest.raises(CollectorReportError):
        _parse(payload)


def test_negative_counts_are_rejected() -> None:
    payload = _valid_payload()
    payload["ghost"]["total_events"] = -1
    with pytest.raises(CollectorReportError):
        _parse(payload)


# --------------------------------------------------------------------------- #
# Recoverable packaging deviations
# --------------------------------------------------------------------------- #
def test_markdown_fence_is_recovered_and_reported() -> None:
    raw = "```json\n" + json.dumps(_valid_payload()) + "\n```"
    report, diagnostics = parse_report(raw)
    assert report.lock == "acquired"
    assert diagnostics.unfenced
    assert not diagnostics.clean


def test_surrounding_prose_is_recovered_and_reported() -> None:
    raw = "Here is the report:\n" + json.dumps(_valid_payload()) + "\nHope that helps!"
    report, diagnostics = parse_report(raw)
    assert report.lock == "acquired"
    assert diagnostics.stripped_prefix.startswith("Here is the report")
    assert diagnostics.stripped_suffix == "Hope that helps!"


def test_empty_output_is_rejected() -> None:
    with pytest.raises(CollectorReportError) as exc:
        parse_report("   \n  ")
    assert "no output" in str(exc.value)


def test_non_object_json_is_rejected() -> None:
    with pytest.raises(CollectorReportError) as exc:
        parse_report("[1, 2, 3]")
    assert "object" in str(exc.value)


def test_malformed_json_names_the_position() -> None:
    with pytest.raises(CollectorReportError) as exc:
        parse_report('{"lock": "acquired",}')
    assert "line" in str(exc.value)


# --------------------------------------------------------------------------- #
# Error rendering
# --------------------------------------------------------------------------- #
def test_error_message_lists_every_bad_field() -> None:
    """One scannable line per problem — this lands in a cycle summary."""
    payload = _valid_payload()
    payload["ghost"]["total_events"] = -1
    payload["flow"]["gaps"] = -3
    with pytest.raises(CollectorReportError) as exc:
        _parse(payload)
    message = str(exc.value)
    assert "ghost.total_events" in message
    assert "flow.gaps" in message
    assert "2 problems" in message


def test_valid_payload_helper_is_actually_valid() -> None:
    """Guards the other tests: mutations must start from a clean baseline."""
    baseline = _valid_payload()
    report = CollectorReport.model_validate(copy.deepcopy(baseline))
    assert report.lock == "acquired"
    assert not report.aborted
