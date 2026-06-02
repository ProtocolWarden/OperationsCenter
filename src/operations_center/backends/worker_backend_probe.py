# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Probe live worker-backend models and retract stale cooldowns.

A worker-backend cooldown (see :mod:`operations_center.backends.limit_classifier`)
is recorded with an *estimated* ``reset_at`` parsed from a CLI limit message, and
is never retracted on its own — it only expires when ``reset_at`` passes. When the
underlying limit lifts *earlier* than the estimate (a model recovers, a weekly
window rolls over sooner than guessed), the cooldown lingers: status surfaces show
the model as cooling and, when every model looks cooling, dispatch is deferred for
no reason.

This module closes that gap by *probing* — running a trivial, cheap request against
each cooling model's real CLI. A success proves the model is runnable again, so the
matching cooldown is cleared via
:meth:`UsageStore.clear_worker_backend_cooldown`. Probes never *record* cooldowns:
a probe failure is treated as "still limited / unknown" and left untouched, so a
flaky probe can only ever fail to clear, never falsely block.

The probe command mirrors the controller's session invocation
(``tools/loop/controller.py``) so it exercises the same binary and flags the
executor would use.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from operations_center.backends.limit_classifier import (
    MODEL_WEEKLY,
    WORKER_BACKEND_MODELS,
    classify_limit,
)

# A cheap prompt: one token of output, no tools, no side effects.
PROBE_PROMPT = "Reply with exactly: ok"
DEFAULT_PROBE_TIMEOUT_SECONDS = 90

# claude model aliases accepted by the CLI's --model flag.
_CLAUDE_MODEL_ALIASES = {"sonnet", "opus", "haiku"}


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of probing one (worker_backend, model)."""

    worker_backend: str
    model: str
    ok: bool
    detail: str


def _resolve(command: str) -> str | None:
    """Resolve a CLI binary, mirroring the controller's PATH fallbacks."""
    resolved = shutil.which(command)
    if resolved:
        return resolved
    home = Path.home()
    candidates = [home / ".local" / "bin" / command, home / "bin" / command]
    if command == "codex":
        candidates.extend(sorted((home / ".nvm" / "versions" / "node").glob("*/bin/codex")))
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def _probe_command(worker_backend: str, model: str) -> list[str] | None:
    """Build the probe command for a (worker_backend, model), or None if unsupported."""
    if worker_backend == "claude_code":
        if model not in _CLAUDE_MODEL_ALIASES:
            return None
        executable = _resolve("claude")
        if executable is None:
            return None
        return [
            executable,
            "-p",
            PROBE_PROMPT,
            "--model",
            model,
            "--dangerously-skip-permissions",
            "--output-format",
            "text",
        ]
    if worker_backend == "codex_cli":
        executable = _resolve("codex")
        if executable is None:
            return None
        return [
            executable,
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            PROBE_PROMPT,
        ]
    return None


def probe_model(
    worker_backend: str,
    model: str,
    *,
    timeout: int = DEFAULT_PROBE_TIMEOUT_SECONDS,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> ProbeResult:
    """Run a live probe; ``ok`` only when the model clearly answered without a limit.

    ``ok`` requires exit code 0 *and* no limit signal in the combined output —
    some CLIs print a rate-limit notice and still exit 0. A missing binary,
    timeout, or any error yields ``ok=False`` (never clears a cooldown).
    """
    command = _probe_command(worker_backend, model)
    if command is None:
        return ProbeResult(worker_backend, model, False, "cli unavailable or unsupported model")
    try:
        proc = runner(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ProbeResult(worker_backend, model, False, f"probe timed out after {timeout}s")
    except (OSError, subprocess.SubprocessError) as exc:
        return ProbeResult(worker_backend, model, False, f"probe error: {exc}")
    combined = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    limit_kind, _ = classify_limit(combined)
    if proc.returncode != 0:
        return ProbeResult(worker_backend, model, False, f"exit {proc.returncode}")
    if limit_kind is not None:
        return ProbeResult(worker_backend, model, False, f"limit signal ({limit_kind})")
    return ProbeResult(worker_backend, model, True, "runnable")


def _models_to_probe(
    details: list[dict[str, object]], worker_backend: str
) -> tuple[set[str], bool]:
    """From a backend's cooldown detail list, return (models_to_probe, account_wide_active)."""
    account_wide = False
    per_model: set[str] = set()
    for cd in details:
        if cd.get("limit_kind") == MODEL_WEEKLY and cd.get("model"):
            per_model.add(str(cd["model"]))
        else:
            account_wide = True
    if account_wide:
        # An account-wide cooldown blocks every model; probe them all so we learn
        # which to mark runnable once the account-wide block is disproven.
        per_model.update(WORKER_BACKEND_MODELS.get(worker_backend, ()))
    return per_model, account_wide


def refresh_cooldowns(
    usage_store,
    *,
    now: datetime | None = None,
    backends: tuple[str, ...] = ("claude_code", "codex_cli"),
    probe: Callable[..., ProbeResult] | None = None,
    timeout: int = DEFAULT_PROBE_TIMEOUT_SECONDS,
    logger: Callable[[str], None] | None = None,
) -> dict[str, dict[str, bool]]:
    """Probe each cooling model and clear cooldowns proven stale. Returns a report.

    Only models with an *active* cooldown are probed (no cost when nothing is
    cooling). For each backend, a successful probe clears that model's
    ``model_weekly`` cooldown; the first success also clears any account-wide
    cooldown for the backend (one model running disproves an all-models block).
    """
    current = now or datetime.now(UTC)
    # Resolve the probe by module-global name at call time so callers (and tests)
    # can monkeypatch ``probe_model`` without rebinding a default argument.
    probe = probe or probe_model
    report: dict[str, dict[str, bool]] = {}
    try:
        snapshot = usage_store.current_worker_backend_cooldowns(now=current)
    except Exception:
        return report
    for backend in backends:
        status = snapshot.get(backend, {})
        details = status.get("cooldowns") or []
        if not status.get("cooling_down") and not details:
            continue
        models, account_wide = _models_to_probe(details, backend)
        if not models:
            continue
        backend_report: dict[str, bool] = {}
        for model in sorted(models):
            result = probe(backend, model, timeout=timeout)
            backend_report[model] = result.ok
            if result.ok:
                removed = usage_store.clear_worker_backend_cooldown(
                    worker_backend=backend,
                    model=model,
                    now=current,
                    include_account_wide=account_wide,
                )
                if logger is not None:
                    logger(
                        f"probe: {backend}/{model} runnable — cleared {removed} stale "
                        f"cooldown event(s)"
                    )
                # Account-wide cleared on first success; later models only clear
                # their own per-model entry.
                account_wide = False
            elif logger is not None:
                logger(f"probe: {backend}/{model} still limited ({result.detail})")
        report[backend] = backend_report
    return report
