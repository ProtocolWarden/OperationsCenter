# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Claude subscription budget guard (operator directive 2026-07-13).

The fleet, the PseudoOperator loop, and the supervising session all share one
Claude subscription metered in a rolling 5h window. On 2026-07-13 the combined
system consumed a full window (session_5h limit at 17:43Z): the reviewer
parked, the loop fell back to codex, and the operator had nothing left for
interactive use. Directive: the system must leave ~25% of every window free.

This module estimates current consumption from the Claude CLI's own transcripts
(``~/.claude/projects/*/*.jsonl`` — every session, including the loop's ``-p``
sessions and reviewer/executor spawns, records per-message ``usage`` there) and
reports when the system should stop spending.

Estimation model (deliberately approximate — the real meter is opaque):

- **Weighted tokens**: input + 5*output + 0.1*cache_read + 1.25*cache_create,
  scaled by a per-model multiplier (opus/fable-class 5x, sonnet 1x, haiku
  0.33x — mirroring relative pricing). Calibrated 2026-07-13 against the
  observed limit hit: one full 5h window ≈ 42M weighted tokens.
- **Window**: a fixed trailing 5h. ``used`` is the weighted sum of every event
  in ``[now - 5h, now]``. This is deliberately conservative — it counts the
  true trailing 5h and can never collapse toward zero under sustained load (an
  earlier boundary-chaining model did exactly that and failed open under the
  heavy load it existed to catch).
- **Exhausted**: ``used >= cap * (1 - reserve)``.
- **Reset horizon** (``bucket_end``): when enough of the *oldest* still-counted
  usage ages out of the trailing window to bring ``used`` back under the
  threshold. Always strictly in the future while there is recent usage, so a
  cooldown built from it meaningfully diverts the ladder to codex; re-evaluated
  every iteration, so a conservative (early) horizon just rechecks sooner.

The 25% reserve absorbs the estimate's error bars; the guard aims to stop the
SYSTEM before the meter stops EVERYTHING. Unknown token classes and unknown
model ids are counted toward the *expensive* side so the guard fires early
rather than late.

Env overrides (documented in .env.operations-center.example):
  OC_CLAUDE_BUDGET_CAP_WEIGHTED  — window capacity in weighted tokens; overrides the
                                   learned cap (default: learned from observed limits,
                                   else the 42_000_000 cold-start seed)
  OC_CLAUDE_BUDGET_RESERVE       — fraction to leave unspent (default 0.25, clamped [0, 0.95])
  OC_CLAUDE_BUDGET_DISABLED      — truthy (1/true/yes/on) → guard reports never-exhausted
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

BUCKET_SPAN = timedelta(hours=5)
DEFAULT_CAP_WEIGHTED = 42_000_000.0
DEFAULT_RESERVE = 0.25
_MAX_RESERVE = 0.95

# (usage-field, weight) — relative cost of each token class.
_PART_WEIGHTS = (
    ("input_tokens", 1.0),
    ("output_tokens", 5.0),
    ("cache_read_input_tokens", 0.1),
    ("cache_creation_input_tokens", 1.25),
)

# Substring family multipliers, mirroring relative subscription pricing. Matched
# as substrings (not prefixes) so region/vendor-prefixed ids like
# ``us.anthropic.claude-opus-4`` and legacy ``claude-3-5-sonnet`` still resolve.
_MODEL_FAMILY_WEIGHTS = (
    ("opus", 5.0),
    ("fable", 5.0),
    ("sonnet", 1.0),
    ("haiku", 0.33),
)
# Fail-safe: a non-empty model id we don't recognise is counted as the most
# expensive class, so an unrecognised model makes the guard fire early, not late.
_UNKNOWN_MODEL_WEIGHT = 5.0

_TRUTHY = {"1", "true", "yes", "on"}


def _model_weight(model: str) -> float:
    m = (model or "").lower()
    if not m:
        return 1.0  # no model info on this row — stay neutral, don't overcount
    for token, weight in _MODEL_FAMILY_WEIGHTS:
        if token in m:
            return weight
    return _UNKNOWN_MODEL_WEIGHT


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        # Loud: a mistyped knob must never silently disable the guard.
        logger.warning("usage_budget: %s=%r is not a number; using default %s", name, raw, default)
        return default


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in _TRUTHY


def _learned_cap() -> float | None:
    """Best-effort cap learned from observed account-wide limit events (audit D4).

    Lazy import so this module stays dependency-light and importable without the
    usage store; any failure falls back to the default cap.
    """
    try:
        from operations_center.execution.usage_store import UsageStore

        return UsageStore().learned_budget_cap()
    except Exception:  # noqa: BLE001 — calibration is best-effort
        return None


def _resolve_cap() -> float:
    """Cap precedence: explicit env override > learned-from-observed-limits > default.

    The 42M default is only a cold-start seed; once the fleet has observed a
    couple of real account-wide limits, the learned median replaces it and the
    magic constant retires (audit F17).
    """
    raw = os.environ.get("OC_CLAUDE_BUDGET_CAP_WEIGHTED")
    if raw is not None and raw.strip() != "":
        cap = _env_float("OC_CLAUDE_BUDGET_CAP_WEIGHTED", DEFAULT_CAP_WEIGHTED)
    else:
        cap = _learned_cap() or DEFAULT_CAP_WEIGHTED
    if cap <= 0:
        logger.warning("usage_budget: resolved cap %s <= 0; using default %s", cap, DEFAULT_CAP_WEIGHTED)
        cap = DEFAULT_CAP_WEIGHTED
    return cap


def _claude_projects_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "projects"


@dataclass(frozen=True)
class BudgetStatus:
    bucket_start: datetime
    bucket_end: datetime
    used_weighted: float
    cap_weighted: float
    reserve_fraction: float
    exhausted: bool
    disabled: bool = False

    @property
    def threshold_weighted(self) -> float:
        return self.cap_weighted * (1.0 - self.reserve_fraction)

    def as_dict(self) -> dict:
        return {
            "bucket_start": self.bucket_start.isoformat(),
            "bucket_end": self.bucket_end.isoformat(),
            "used_weighted": round(self.used_weighted),
            "cap_weighted": round(self.cap_weighted),
            "reserve_fraction": self.reserve_fraction,
            "threshold_weighted": round(self.threshold_weighted),
            "exhausted": self.exhausted,
            "disabled": self.disabled,
        }


def _iter_usage_events(projects_dir: Path, not_before: datetime):
    """Yield (timestamp, weighted_tokens) from transcripts touched since not_before.

    File-level mtime pruning keeps the scan cheap: a transcript last written
    before the horizon cannot contain in-horizon events.
    """
    if not projects_dir.is_dir():
        return
    for f in projects_dir.glob("*/*.jsonl"):
        try:
            if datetime.fromtimestamp(f.stat().st_mtime, timezone.utc) < not_before:
                continue
            with f.open(errors="replace") as fh:
                for line in fh:
                    if '"usage"' not in line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    raw_ts = entry.get("timestamp")
                    message = entry.get("message") or {}
                    usage = message.get("usage") or {}
                    if not raw_ts or not usage:
                        continue
                    try:
                        ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        continue
                    if ts.tzinfo is None:  # a naive stamp must not crash the aware compare
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts < not_before:
                        continue
                    weighted = sum(
                        float(usage.get(field, 0) or 0) * w for field, w in _PART_WEIGHTS
                    ) * _model_weight(str(message.get("model", "")))
                    if weighted > 0:
                        yield ts, weighted
        except OSError:
            continue


def _reset_horizon(
    in_window: list[tuple[datetime, float]], excess: float, now: datetime
) -> datetime:
    """When the trailing window drops back under threshold.

    ``excess`` is ``used - threshold``. Usage ages out of the rolling window
    oldest-first; each event leaves ``BUCKET_SPAN`` after its timestamp. We shed
    the oldest events until strictly more than ``excess`` weight has left, and
    return when that pivotal event exits the window. When not over budget
    (``excess < 0``) this is informational: when the oldest event ages out.
    """
    ordered = sorted(in_window, key=lambda e: e[0])
    if excess < 0:
        return (ordered[0][0] if ordered else now) + BUCKET_SPAN
    shed = 0.0
    for ts, weight in ordered:
        shed += weight
        if shed > excess:
            return ts + BUCKET_SPAN
    return now + BUCKET_SPAN  # fallback; unreachable while used >= threshold


def budget_status(now: datetime | None = None) -> BudgetStatus:
    now = now or datetime.now(timezone.utc)
    cap = _resolve_cap()
    reserve = min(max(_env_float("OC_CLAUDE_BUDGET_RESERVE", DEFAULT_RESERVE), 0.0), _MAX_RESERVE)
    disabled = _truthy(os.environ.get("OC_CLAUDE_BUDGET_DISABLED"))

    window_start = now - BUCKET_SPAN
    in_window = [
        (ts, weighted)
        for ts, weighted in _iter_usage_events(_claude_projects_dir(), window_start)
        if ts >= window_start
    ]
    used = sum(weighted for _, weighted in in_window)
    threshold = cap * (1.0 - reserve)
    exhausted = (not disabled) and used >= threshold

    bucket_start = min((ts for ts, _ in in_window), default=now)
    bucket_end = _reset_horizon(in_window, used - threshold, now)
    return BudgetStatus(
        bucket_start=bucket_start,
        bucket_end=bucket_end,
        used_weighted=used,
        cap_weighted=cap,
        reserve_fraction=reserve,
        exhausted=exhausted,
        disabled=disabled,
    )


__all__ = [
    "BudgetStatus",
    "budget_status",
    "BUCKET_SPAN",
    "DEFAULT_CAP_WEIGHTED",
    "DEFAULT_RESERVE",
]
