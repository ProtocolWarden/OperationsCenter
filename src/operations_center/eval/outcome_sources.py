# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Production outcome sources for the Component-2 flagger (§4.2, D-EVAL-1).

The flagger correlates reviewer decisions with downstream outcomes. The cleanest
*real* correlation needs no separate decision log: **a merged PR necessarily passed
the required ``reviewer-verdict`` check**, i.e. the reviewer said LGTM. So a
post-merge regression on a merged PR *is* an LGTM-then-regression — a candidate
reviewer miss — recoverable straight from GitHub via
:func:`detect_post_merge_regressions`.

``GitHubOutcomeSource`` turns those regression signals into :class:`ReviewOutcome`
records. The ``requeue_to_death`` signal (worker non-convergence, D-EVAL-4) lives in
board state, not GitHub, and is sourced separately; this adapter only owns the
merge→regression half. The detector is injectable so the join is unit-testable
without a live GitHub client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from operations_center.eval.outcome_flagger import LGTM, ReviewOutcome
from operations_center.post_merge_regression import (
    RegressionSignal,
    detect_post_merge_regressions,
)

if TYPE_CHECKING:
    from operations_center.adapters.github_pr import GitHubPRClient

# (owner, repo) targets to scan. Defaults to OC's own repo (the one the reviewer
# self-reviews and where the eval lives); extend as the flagger is pointed wider.
DEFAULT_TARGETS: tuple[tuple[str, str], ...] = (("ProtocolWarden", "OperationsCenter"),)

# A detector with the signature of detect_post_merge_regressions, injectable for tests.
Detector = Callable[..., list[RegressionSignal]]


class GitHubOutcomeSource:
    """Yield review outcomes from post-merge regressions (merged ⟹ LGTM, regressed)."""

    def __init__(
        self,
        gh_client: GitHubPRClient,
        *,
        targets: tuple[tuple[str, str], ...] = DEFAULT_TARGETS,
        base_branch: str = "main",
        lookback_hours: int = 24,
        detector: Detector | None = None,
    ) -> None:
        self._gh = gh_client
        self._targets = targets
        self._base_branch = base_branch
        self._lookback_hours = lookback_hours
        self._detector = detector or detect_post_merge_regressions

    def __call__(self) -> list[ReviewOutcome]:
        outcomes: list[ReviewOutcome] = []
        for owner, repo in self._targets:
            try:
                signals = self._detector(
                    self._gh,
                    owner,
                    repo,
                    base_branch=self._base_branch,
                    lookback_hours=self._lookback_hours,
                )
            except Exception:  # noqa: BLE001 — a flaky GitHub call must not break the join
                continue
            for sig in signals:
                if sig.pr_number is None:
                    # Can't attribute to a PR → can't make it a reviewer ticket.
                    continue
                outcomes.append(
                    ReviewOutcome(
                        pr_number=sig.pr_number,
                        decision=LGTM,  # merge required reviewer-verdict=success
                        merged=True,
                        main_regressed=True,
                        repo=repo,
                    )
                )
        return outcomes


def make_github_outcome_source(settings: Any, gh_client: GitHubPRClient) -> GitHubOutcomeSource:
    """Build the source from settings (targets/lookback overridable later)."""
    return GitHubOutcomeSource(gh_client)


__all__ = ["DEFAULT_TARGETS", "GitHubOutcomeSource", "make_github_outcome_source"]
