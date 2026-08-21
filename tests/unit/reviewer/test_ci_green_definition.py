# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""One definition of "CI is green", and the four ways it is not.

The reviewer had four places that decided whether CI was green, and three of
them had re-derived a weaker rule than the self-review precondition. The
weakest — phase 0's advance out of ``ci_fix`` — treated an empty failed-checks
list as a pass, so a still-running build, a head CI had never reported on, and
an API outage all read as "CI green" in the log and advanced the PR to review.

These tests cover the branch that had no coverage, which is why the same defect
was fixed three times upstream (#269, #405/#406, #503) without propagating.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from operations_center.entrypoints.pr_review_watcher import main as watcher

REPO_KEY = "MyRepo"
PR_NUMBER = 42


def _gh(
    *,
    failed: list[str] | None = None,
    pending: list[str] | None = None,
    completed: list[str] | None = None,
    raises: Exception | None = None,
) -> MagicMock:
    """A PR client whose three check queries are set independently.

    Defaults are the settled-green posture: nothing failing, nothing running,
    and at least one check that actually reported on this head.
    """
    gh = MagicMock()
    if raises is not None:
        gh.get_failed_checks.side_effect = raises
        gh.get_incomplete_checks.side_effect = raises
        gh.get_completed_checks.side_effect = raises
    else:
        gh.get_failed_checks.return_value = list(failed or [])
        gh.get_incomplete_checks.return_value = list(pending or [])
        gh.get_completed_checks.return_value = (
            list(completed) if completed is not None else ["Test (pytest)"]
        )
    return gh


def _settings(*, required: list[str] | None = None) -> MagicMock:
    repo_cfg = MagicMock(
        ci_ignored_checks=[],
        required_checks=list(required or []),
        local_path="/nonexistent",
        venv_dir=".venv",
        default_branch="main",
    )
    return MagicMock(reviewer=MagicMock(), repos={REPO_KEY: repo_cfg})


def _pr_data() -> dict[str, Any]:
    return {
        "number": PR_NUMBER,
        "title": "My PR",
        "draft": False,
        "head": {"ref": f"goal/{PR_NUMBER}", "sha": "abc123"},
    }


def _state(tmp_path: Path, **overrides: Any) -> tuple[dict, Path]:
    state = watcher._new_state(REPO_KEY, PR_NUMBER)
    state["phase"] = "ci_fix"
    state.update(overrides)
    sp = watcher._state_path(tmp_path, REPO_KEY, PR_NUMBER)
    watcher._save_state(sp, state)
    return state, sp


def _run_phase0(state: dict, sp: Path, gh: MagicMock, settings: MagicMock, tmp_path: Path) -> None:
    watcher._phase0_ci_fix(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, settings)


# ── _ci_status: the definition itself ────────────────────────────────────────


def test_settled_and_passing_is_green() -> None:
    st = watcher._ci_status(_gh(), "o", "r", PR_NUMBER)
    assert st.green is True
    assert st.why_not_green == "green"


def test_pending_check_is_not_green() -> None:
    """No failure YET is not a pass — this is how #269 merged a red base."""
    st = watcher._ci_status(_gh(pending=["Test (pytest)"]), "o", "r", PR_NUMBER)
    assert st.green is False
    assert "still running" in st.why_not_green


def test_head_with_no_reported_checks_is_not_green() -> None:
    """Zero contexts is the auto-rebase case: the green would belong to an old head."""
    st = watcher._ci_status(_gh(completed=[]), "o", "r", PR_NUMBER)
    assert st.green is False
    assert "no checks have reported" in st.why_not_green


def test_unregistered_required_check_is_not_green() -> None:
    st = watcher._ci_status(
        _gh(completed=["Lint (ruff)"]),
        "o",
        "r",
        PR_NUMBER,
        required=["custodian-audit / audit"],
    )
    assert st.green is False
    assert "required checks not registered" in st.why_not_green


def test_query_error_is_unknown_not_green_and_not_red() -> None:
    """The regression that mattered: an exception used to return [] and read green.

    It must also not read as RED — an empty failed list with an error set means
    there is nothing for a fix pass to act on.
    """
    st = watcher._ci_status(_gh(raises=RuntimeError("502 Bad Gateway")), "o", "r", PR_NUMBER)
    assert st.green is False
    assert st.failed == []
    assert st.error is not None
    assert "RuntimeError" in st.error


def test_failed_check_is_not_green() -> None:
    st = watcher._ci_status(_gh(failed=["Lint (ruff): failure"]), "o", "r", PR_NUMBER)
    assert st.green is False
    assert "1 failing" in st.why_not_green


# ── phase 0: the branch that had no test ─────────────────────────────────────


def test_phase0_advances_on_settled_green(tmp_path: Path) -> None:
    state, sp = _state(tmp_path)
    _run_phase0(state, sp, _gh(), _settings(), tmp_path)
    assert state["phase"] == "self_review"


def test_phase0_does_not_advance_while_ci_is_still_running(tmp_path: Path) -> None:
    state, sp = _state(tmp_path)
    _run_phase0(state, sp, _gh(pending=["Test (pytest)"]), _settings(), tmp_path)
    assert state["phase"] == "ci_fix"
    assert state["ci_settle_cycles"] == 1


def test_phase0_does_not_advance_on_a_head_with_no_ci(tmp_path: Path) -> None:
    state, sp = _state(tmp_path)
    _run_phase0(state, sp, _gh(completed=[]), _settings(), tmp_path)
    assert state["phase"] == "ci_fix"


def test_phase0_does_not_advance_when_the_ci_query_fails(tmp_path: Path) -> None:
    """An API outage must not be spelled "CI green"."""
    state, sp = _state(tmp_path)
    _run_phase0(state, sp, _gh(raises=RuntimeError("boom")), _settings(), tmp_path)
    assert state["phase"] == "ci_fix"


def test_phase0_wait_is_bounded_so_a_repo_without_ci_still_gets_reviewed(
    tmp_path: Path,
) -> None:
    """Liveness: deferring forever would silently park every PR on a CI-less repo.

    Advancing costs a review pass, not a merge — the merge gate re-checks CI.
    """
    state, sp = _state(tmp_path, ci_settle_cycles=watcher._MAX_CI_SETTLE_CYCLES - 1)
    _run_phase0(state, sp, _gh(completed=[]), _settings(), tmp_path)
    assert state["phase"] == "self_review"
    assert "ci_settle_cycles" not in state


def test_phase0_settle_counter_resets_once_ci_reports_a_failure(tmp_path: Path) -> None:
    """A failure is a real answer: the wait budget is for silence, not for red."""
    state, sp = _state(tmp_path, ci_settle_cycles=3)
    settings = _settings()
    settings.reviewer_autofix_audit = False
    _run_phase0(state, sp, _gh(failed=["Lint (ruff): failure"]), settings, tmp_path)
    assert "ci_settle_cycles" not in state
