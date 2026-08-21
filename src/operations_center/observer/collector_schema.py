# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Capture schema for the watchdog loop's Haiku data-collection sub-agent.

``.console/watchdog_loop_prompt.md`` PHASE 1 spawns a Haiku sub-agent running
``.console/haiku_collector_prompt.md``, then hands its stdout to PHASE 2 where
Sonnet analyses it and *acts* — transitioning board tasks, committing fixes,
scheduling the next wake. Until now the contract between those halves was prose
("Emit exactly this JSON (no fences, no extra text)") and PHASE 2 simply parsed
whatever came back. A sub-agent that truncated a section, renamed a field, or
wrapped its answer in a markdown fence still produced something Sonnet would
reason over — with the missing signal reading as "nothing to see" rather than as
a failure. That is the worst failure mode available to a watchdog: silence and
health become indistinguishable.

This module makes the handoff load-bearing:

- ``CollectorReport`` is the OUTPUT SCHEMA as a fail-closed model
  (``extra="forbid"``) — a renamed or invented key is an error, not silent drift.
- ``parse_report`` takes raw sub-agent stdout and either returns a validated
  report or raises ``CollectorReportError`` naming the offending field. There is
  no partial-credit path.
- The one legitimate partial is the abort. STEP 0 of the collector prompt tells
  the sub-agent to "emit partial JSON with lock field only" when another live
  owner holds the loop lock, so ``lock`` carries the whole report's completeness
  contract — see ``_require_full_report_unless_aborted``.

Captured output is UNTRUSTED. The report carries task titles, error strings,
ghost ids and PR text harvested from 17 repos, any of which can contain text
aimed at the model reading it downstream — the threat ``injection.py`` exists
for. Callers must fence it before it reaches PHASE 2; see
``operations_center.injection.wrap_untrusted_report``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

# A fenced ```json ... ``` block. The prompt forbids fences, but a model that
# adds one has still done the collection work correctly; failing the whole cycle
# over cosmetic packaging would trade a real signal for a style point. The strip
# is reported (``ParseDiagnostics.unfenced``) so prompt drift stays visible
# instead of being absorbed silently.
_MD_FENCE_RE = re.compile(
    r"\A\s*```(?:json|JSON)?\s*\n(?P<body>.*?)\n?\s*```\s*\Z",
    re.DOTALL,
)

# The sections a non-aborted report must carry. Kept as an explicit tuple rather
# than derived from the model fields, so that making a section optional later is
# a deliberate edit here and not an accident of field ordering.
_REQUIRED_SECTIONS: tuple[str, ...] = (
    "cycle_ts",
    "repo_sync",
    "preflight",
    "custodian",
    "ghost",
    "flow",
    "graph",
    "reaudit",
    "regressions",
    "triage",
    "board_unblock",
    "running_tasks",
    "extraction",
    "watchers",
    "executor_investigation",
)


class CollectorReportError(ValueError):
    """Raised when sub-agent output is not a valid collector report."""


class _Section(BaseModel):
    """Base for every report section: unknown keys are a hard error."""

    model_config = ConfigDict(extra="forbid")


class RepoSync(_Section):
    behind: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class Preflight(_Section):
    plane: str
    switchboard: str
    watchers_running: int = Field(ge=0)
    watchers_total: int = Field(default=8, ge=0)


class CustodianFinding(_Section):
    repo: str
    check: str
    delta: int


class Custodian(_Section):
    all_zero: bool
    findings: list[CustodianFinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def _all_zero_agrees_with_findings(self) -> Custodian:
        """``all_zero`` is what PHASE 2 branches on; findings are what it reads.

        Both come from the same sweep, so a report claiming all_zero while
        carrying non-zero deltas is self-contradictory — and the contradiction
        resolves the dangerous way, with Sonnet trusting the boolean and never
        reading the findings.
        """
        if self.all_zero and self.findings:
            raise ValueError(
                f"custodian.all_zero is true but {len(self.findings)} finding(s) present"
            )
        return self


class Ghost(_Section):
    total_events: int = Field(ge=0)
    active: list[str] = Field(default_factory=list)
    fixed: list[str] = Field(default_factory=list)


class Flow(_Section):
    gaps: int = Field(ge=0)


class Graph(_Section):
    ok: bool
    error: str | None = None


class Reaudit(_Section):
    repos_needing_audit: list[str] = Field(default_factory=list)


class Regressions(_Section):
    count: int = Field(ge=0)
    # Element shape is unspecified in the OUTPUT SCHEMA (always emitted as []).
    # Left permissive deliberately rather than guessed at.
    findings: list[dict[str, Any]] = Field(default_factory=list)


class TriageHealed(_Section):
    task_id: str
    transition: str


class Triage(_Section):
    escalation_commented: list[str] = Field(default_factory=list)
    healed: list[TriageHealed] = Field(default_factory=list)


class BoardUnblockApplied(_Section):
    # ``from`` is a Python keyword, so the wire name arrives via an alias. ``to``
    # is aliased too, purely so the pair reads symmetrically at the call site
    # (``item.from_state`` / ``item.to_state``).
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    task_id: str
    title: str
    rule: str
    from_state: str = Field(alias="from")
    to_state: str = Field(alias="to")


class BoardUnblockSkipped(_Section):
    task_id: str
    rule: str
    reason: str


class BoardUnblock(_Section):
    applied: list[BoardUnblockApplied] = Field(default_factory=list)
    skipped: list[BoardUnblockSkipped] = Field(default_factory=list)


class ExtractionEdgeCase(_Section):
    test_id: str
    issue: str


class Extraction(_Section):
    # Percentage, NOT a fraction — pinned to the same 0..100 invariant the
    # history store enforces (observer/extraction_health_history.py). A collector
    # that starts emitting 0.87 for "87%" must fail here rather than quietly read
    # as a catastrophic 0.87% and trip a false extraction-health alarm.
    success_rate: float = Field(ge=0.0, le=100.0)
    extracted_count: int = Field(ge=0)
    total_count: int = Field(ge=0)
    gap_count: int = Field(ge=0)
    edge_case_count: int = Field(ge=0)
    gaps: list[str] = Field(default_factory=list)
    edge_cases: list[ExtractionEdgeCase] = Field(default_factory=list)

    @model_validator(mode="after")
    def _counts_are_consistent(self) -> Extraction:
        if self.extracted_count > self.total_count:
            raise ValueError(
                f"extraction.extracted_count ({self.extracted_count}) exceeds "
                f"total_count ({self.total_count})"
            )
        return self


class Watcher(_Section):
    role: str
    running: bool
    exit_code: int | None = None
    consecutive_non143: int = Field(ge=0)
    last_error: str | None = None


class ExecutorSigkill(_Section):
    task_id: str
    context: str


class ExecutorInvestigation(_Section):
    triggered: bool
    oom_signals: bool
    recent_sigkills: list[ExecutorSigkill] = Field(default_factory=list)
    memory_free_gb: float | None = None


class CollectorReport(BaseModel):
    """One watchdog cycle's collected signals, as PHASE 2 receives them."""

    model_config = ConfigDict(extra="forbid")

    lock: str
    cycle_ts: str | None = None
    repo_sync: RepoSync | None = None
    preflight: Preflight | None = None
    custodian: Custodian | None = None
    ghost: Ghost | None = None
    flow: Flow | None = None
    graph: Graph | None = None
    reaudit: Reaudit | None = None
    regressions: Regressions | None = None
    triage: Triage | None = None
    board_unblock: BoardUnblock | None = None
    running_tasks: list[str] | None = None
    extraction: Extraction | None = None
    watchers: list[Watcher] | None = None
    executor_investigation: ExecutorInvestigation | None = None

    @property
    def aborted(self) -> bool:
        """True when STEP 0 bailed out (lock contention or preflight refusal)."""
        return self.lock.startswith("aborted:")

    @property
    def abort_reason(self) -> str:
        """The text after ``aborted:``; empty string when not aborted."""
        return self.lock.split(":", 1)[1] if self.aborted else ""

    @model_validator(mode="after")
    def _lock_is_known(self) -> CollectorReport:
        if self.lock in {"acquired", "reclaimed"}:
            return self
        if self.aborted and self.abort_reason:
            return self
        raise ValueError(
            f"lock must be 'acquired', 'reclaimed', or 'aborted:<reason>', got {self.lock!r}"
        )

    @model_validator(mode="after")
    def _require_full_report_unless_aborted(self) -> CollectorReport:
        """A completed cycle must carry every section.

        Without this, a sub-agent that died halfway still yields a model-valid
        report, and each absent section reads downstream as a clean signal. Only
        the documented abort path may be partial.
        """
        if self.aborted:
            return self
        missing = [name for name in _REQUIRED_SECTIONS if getattr(self, name) is None]
        if missing:
            raise ValueError(
                f"lock={self.lock!r} means the cycle ran, so the report must be "
                f"complete; missing section(s): {', '.join(missing)}"
            )
        return self


class ParseDiagnostics(BaseModel):
    """What ``parse_report`` had to do to the raw text before it validated.

    Non-empty diagnostics mean the sub-agent drifted from the prompt contract in
    a recoverable way. They are surfaced rather than swallowed so the drift shows
    up in the cycle log and can be fixed in the prompt.
    """

    model_config = ConfigDict(extra="forbid")

    unfenced: bool = False
    stripped_prefix: str = ""
    stripped_suffix: str = ""

    @property
    def clean(self) -> bool:
        return not (self.unfenced or self.stripped_prefix or self.stripped_suffix)


def _extract_json_text(raw: str) -> tuple[str, ParseDiagnostics]:
    """Recover the JSON object from sub-agent stdout.

    Handles the two recoverable deviations from "no fences, no extra text": a
    markdown fence around the object, and prose before or after it. Anything
    else is left alone so ``json.loads`` reports the real problem.
    """
    diagnostics = ParseDiagnostics()
    text = raw.strip()

    fenced = _MD_FENCE_RE.match(text)
    if fenced:
        text = fenced.group("body").strip()
        diagnostics.unfenced = True

    start = text.find("{")
    end = text.rfind("}")
    if start > 0:
        diagnostics.stripped_prefix = text[:start].strip()
    if 0 <= end < len(text) - 1:
        diagnostics.stripped_suffix = text[end + 1 :].strip()
    if start >= 0 and end > start:
        text = text[start : end + 1]

    return text, diagnostics


def parse_report(raw: str) -> tuple[CollectorReport, ParseDiagnostics]:
    """Validate raw collector stdout into a ``CollectorReport``.

    Raises ``CollectorReportError`` with a field-level explanation on any
    failure — malformed JSON, unknown key, missing section, or a violated
    invariant. Never returns a partially-valid report.
    """
    if not raw or not raw.strip():
        raise CollectorReportError("collector produced no output")

    text, diagnostics = _extract_json_text(raw)

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CollectorReportError(
            f"collector output is not valid JSON: {exc.msg} (line {exc.lineno}, col {exc.colno})"
        ) from exc

    if not isinstance(payload, dict):
        raise CollectorReportError(
            f"collector output must be a JSON object, got {type(payload).__name__}"
        )

    try:
        report = CollectorReport.model_validate(payload)
    except ValidationError as exc:
        raise CollectorReportError(_format_validation_error(exc)) from exc

    return report, diagnostics


def _format_validation_error(exc: ValidationError) -> str:
    """Render a pydantic error as one scannable line per offending field.

    The default repr buries the field path in a multi-line block; this output
    lands in a cycle summary where one line per problem is what the next reader
    (human or model) actually needs.
    """
    lines = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err["loc"]) or "<root>"
        lines.append(f"  {loc}: {err['msg']}")
    count = len(lines)
    plural = "s" if count != 1 else ""
    return "\n".join([f"collector report failed validation ({count} problem{plural}):", *lines])
