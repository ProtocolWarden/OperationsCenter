# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Non-blocking drift monitor (the independent critic lane)."""

from __future__ import annotations

import pytest

from operations_center.eval.corpus import Case
from operations_center.eval.critic import run_drift_monitor


def _case(cid="c") -> Case:
    return Case(
        case_id=cid,
        kind="verdict",
        input={"diff": "..."},
        ground_truth={"result": "CONCERNS", "failing": ["code_quality"]},
    )


def _extractor_fixed(checks):
    def _fn(case, *, vote):
        return checks
    return _fn


def test_drift_monitor_agrees_with_answer():
    extractor = _extractor_fixed([{"check_id": "code_quality", "status": "fail"},
                                  {"check_id": "no_tooling_artifacts", "status": "pass"}])
    results = run_drift_monitor([_case()], extractor, votes=3)
    assert results[0].drifted is False
    assert results[0].agree_votes == 3


def test_drift_monitor_flags_disagreement():
    # The model now says everything passes → computes LGTM, but the answer is CONCERNS.
    extractor = _extractor_fixed([{"check_id": "code_quality", "status": "pass"},
                                  {"check_id": "no_tooling_artifacts", "status": "pass"}])
    results = run_drift_monitor([_case()], extractor, votes=3)
    assert results[0].drifted is True
    assert "!= answer" in results[0].detail


def test_drift_monitor_majority_vote():
    def flaky(case, *, vote):
        # 2 of 3 votes say fail (matches answer), 1 says pass.
        if vote == 1:
            return [{"check_id": "code_quality", "status": "pass"},
                    {"check_id": "no_tooling_artifacts", "status": "pass"}]
        return [{"check_id": "code_quality", "status": "fail"},
                {"check_id": "no_tooling_artifacts", "status": "pass"}]

    results = run_drift_monitor([_case()], flaky, votes=3)
    assert results[0].drifted is False  # majority agrees with the answer
    assert results[0].agree_votes == 2


def test_drift_monitor_rejects_zero_votes():
    with pytest.raises(ValueError):
        run_drift_monitor([_case()], _extractor_fixed([]), votes=0)
