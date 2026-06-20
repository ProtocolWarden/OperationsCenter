# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Code-computed reviewer verdict (INJ Phase 1, D-INJ-1 — the root fix).

The reviewer used to emit a free-text ``{"result": "LGTM"|"CONCERNS"}`` that the
*model* authored — so any prompt injection in the diff/spec/findings contended
directly for the merge decision (suppress a CONCERNS, forge an LGTM). Per
HARNESS_TRUST_HARDENING.md §2.2/§2.3 the capability itself is removed: the model
fills a TYPED schema (one ``{check_id, status, evidence_span}`` per enumerated
review check) and **code** — here — computes LGTM/CONCERNS from it. Injection can
at most flip a single check's ``status`` enum (each independently re-checkable via
its ``evidence_span``); it cannot author the gated decision, and there is no
free-text "merge me" channel.

Fail-safe by construction (also satisfies D-INJ-2 degrade-to-stricter): anything
missing, unknown, or malformed computes to CONCERNS — never an auto-LGTM.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

# A status the model may assign to a check. Anything outside this set is treated
# as a failure (an injected/garbage status must never read as "pass").
_PASS = "pass"
_FAIL = "fail"
_NA = "n/a"
VALID_STATUS = frozenset({_PASS, _FAIL, _NA})


@dataclass(frozen=True)
class ReviewCheck:
    """One enumerated review check the model must report a status for.

    ``optional`` checks are conditional (e.g. only apply when a campaign spec or
    Custodian findings are present): absent/``n/a`` is fine, only an explicit
    ``fail`` raises a concern. ``required`` checks MUST be present and ``pass``.
    """

    check_id: str
    prompt: str
    optional: bool = False


# The enumerated checklist. check_ids are a stable contract shared with the
# review prompt (main.py) and the Phase-4 replay corpus — do not rename casually.
REVIEW_CHECKS: tuple[ReviewCheck, ...] = (
    ReviewCheck(
        "spec_compliance",
        "If a campaign spec is provided, the diff implements EXACTLY what it "
        "requires (filenames, member names, member count, exports, tests, "
        "version bumps).",
        optional=True,
    ),
    ReviewCheck(
        "custodian_findings",
        "If Custodian findings are listed, each is resolved by the diff.",
        optional=True,
    ),
    ReviewCheck(
        "code_quality",
        "Standard code quality: correctness, style, no potential bugs.",
        optional=False,
    ),
    ReviewCheck(
        "no_tooling_artifacts",
        "No tooling artifacts (.baseline-validation.json, run-status.md, etc.) "
        "in the diff.",
        optional=False,
    ),
)

_REQUIRED_IDS = frozenset(c.check_id for c in REVIEW_CHECKS if not c.optional)
_KNOWN_IDS = frozenset(c.check_id for c in REVIEW_CHECKS)

LGTM = "LGTM"
CONCERNS = "CONCERNS"


def compute_verdict(checks: object) -> tuple[str, list[str]]:
    """Compute (result, failing_check_ids) from the model's typed checks.

    ``checks`` is whatever came out of the model's ``verdict.json`` — validated
    here, never trusted. Returns ``("LGTM", [])`` ONLY when every required check
    is present and ``pass`` and no known check is ``fail``; otherwise
    ``("CONCERNS", [...])`` with the offending/missing check_ids. Unknown status
    values and unknown check_ids cannot produce an LGTM (the former counts as a
    failure for its check; the latter is ignored — it is not a decision channel).
    """
    if not isinstance(checks, list):
        return CONCERNS, ["malformed_no_checks"]

    status_by_id: dict[str, str] = {}
    for entry in checks:
        if not isinstance(entry, dict):
            continue
        d = cast("dict[str, Any]", entry)
        cid = d.get("check_id")
        status = d.get("status")
        if cid in _KNOWN_IDS and isinstance(cid, str):
            # Last write wins; a non-string / out-of-enum status -> fail-safe.
            status_by_id[cid] = status if status in VALID_STATUS else _FAIL

    failing: list[str] = []
    # Required checks must be present and pass.
    for cid in (c.check_id for c in REVIEW_CHECKS if not c.optional):
        st = status_by_id.get(cid)
        if st != _PASS:
            failing.append(cid)
    # Optional checks: only an explicit fail (or invalid status) raises a concern.
    for cid in (c.check_id for c in REVIEW_CHECKS if c.optional):
        if status_by_id.get(cid) == _FAIL:
            failing.append(cid)

    return (LGTM, []) if not failing else (CONCERNS, failing)


def verdict_schema_prompt() -> str:
    """The review-prompt fragment describing the typed verdict the model must
    write. Kept here so the schema and the computation stay in lockstep."""
    lines = [
        "Write your review as JSON to a file named `verdict.json` in the current "
        "working directory. Report a status for EACH enumerated check — do NOT "
        "write an overall verdict; the merge decision is computed by code from "
        "your per-check statuses, not from any free-text field.",
        "",
        '{"checks": [',
    ]
    for i, c in enumerate(REVIEW_CHECKS):
        tail = "," if i < len(REVIEW_CHECKS) - 1 else ""
        applic = ' (use "n/a" if not applicable)' if c.optional else ""
        lines.append(
            f'  {{"check_id": "{c.check_id}", "status": "pass|fail{"|n/a" if c.optional else ""}", '
            f'"evidence_span": "<quote the diff line(s) justifying this status>"}}{tail}'
            f"   // {c.prompt}{applic}"
        )
    lines.append("], \"summary\": \"short human-readable note (NOT the decision)\"}")
    lines.append("")
    lines.append(
        'Set status "pass" only when the check genuinely holds. Use "fail" with a '
        "concrete evidence_span for anything wrong."
    )
    return "\n".join(lines)


__all__ = [
    "CONCERNS",
    "LGTM",
    "REVIEW_CHECKS",
    "ReviewCheck",
    "VALID_STATUS",
    "compute_verdict",
    "verdict_schema_prompt",
]
