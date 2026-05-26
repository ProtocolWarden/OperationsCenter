# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import re
from typing import Callable, Generic, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from operations_center.backends._capacity_classifier import (
    classify_capacity_exhaustion,
)

_T = TypeVar("_T")

_SUPPORTED_WORKER_BACKENDS = ("claude_code", "codex_cli")
_TIMEZONE_RESET_RE = re.compile(
    r"resets\s+(\d{1,2}:\d{2}(?:am|pm))\s+\(([^)]+)\)", re.IGNORECASE
)
_ISO_RESET_RE = re.compile(
    r"resets?(?:\s+at)?\s+(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?Z)",
    re.IGNORECASE,
)
_RELATIVE_RESET_RE = re.compile(
    r"(?:try again|retry|resets?|reset|available again)[^\n]{0,80}?\bin\s+"
    r"(?:(?P<hours>\d+)\s*h(?:ours?)?)?\s*"
    r"(?:(?P<minutes>\d+)\s*m(?:in(?:ute)?s?)?)?\s*"
    r"(?:(?P<seconds>\d+)\s*s(?:ec(?:ond)?s?)?)?",
    re.IGNORECASE,
)
_LIMIT_SIGNAL_RE = re.compile(
    r"rate limit|usage limit|weekly limit|quota|too many requests|429",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WorkerBackendSelection:
    preferred_backend: str
    selected_backend: str | None
    cooldowns: dict[str, datetime | None]
    reason: str | None = None


@dataclass(frozen=True)
class WorkerBackendExecution(Generic[_T]):
    selected_backend: str | None
    payload: _T | None
    fallback_used: bool
    selection: WorkerBackendSelection


def worker_backend_candidates(preferred_backend: str) -> tuple[str, ...]:
    if preferred_backend not in _SUPPORTED_WORKER_BACKENDS:
        return (preferred_backend,)
    alternates = tuple(
        backend
        for backend in _SUPPORTED_WORKER_BACKENDS
        if backend != preferred_backend
    )
    return (preferred_backend, *alternates)


def alternate_worker_backend(worker_backend: str) -> str | None:
    for candidate in _SUPPORTED_WORKER_BACKENDS:
        if candidate != worker_backend:
            return candidate
    return None


def _read_worker_backend_cooldown(
    usage_store,
    worker_backend: str,
    *,
    now: datetime,
) -> datetime | None:
    getter = getattr(usage_store, "worker_backend_cooldown_until", None)
    if not callable(getter):
        return None
    try:
        return getter(worker_backend, now=now)
    except TypeError:
        return getter(worker_backend)


def select_worker_backend(
    *,
    preferred_backend: str,
    usage_store,
    dynamic_enabled: bool,
    now: datetime | None = None,
) -> WorkerBackendSelection:
    current = now or datetime.now(UTC)
    candidates = worker_backend_candidates(preferred_backend)
    cooldowns = {
        backend: _read_worker_backend_cooldown(usage_store, backend, now=current)
        for backend in candidates
    }
    if not dynamic_enabled:
        return WorkerBackendSelection(
            preferred_backend=preferred_backend,
            selected_backend=preferred_backend,
            cooldowns=cooldowns,
        )
    for backend in candidates:
        cooldown_until = cooldowns.get(backend)
        if cooldown_until is None or cooldown_until <= current:
            return WorkerBackendSelection(
                preferred_backend=preferred_backend,
                selected_backend=backend,
                cooldowns=cooldowns,
            )
    reason = ", ".join(
        f"{backend} until {cooldowns[backend].isoformat()}"
        for backend in candidates
        if cooldowns.get(backend) is not None
    )
    return WorkerBackendSelection(
        preferred_backend=preferred_backend,
        selected_backend=None,
        cooldowns=cooldowns,
        reason=f"all worker backends cooling down ({reason})",
    )


def parse_worker_backend_reset(
    combined_output: str,
    worker_backend: str,
    *,
    now: datetime | None = None,
) -> datetime | None:
    current = now or datetime.now(UTC)
    match = _TIMEZONE_RESET_RE.search(combined_output)
    if match:
        time_str, tz_name = match.group(1).lower(), match.group(2)
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            return None
        now_local = current.astimezone(tz)
        parsed = datetime.strptime(time_str, "%I:%M%p")  # noqa: DTZ007
        reset_local = now_local.replace(
            hour=parsed.hour,
            minute=parsed.minute,
            second=0,
            microsecond=0,
        )
        if reset_local <= now_local:
            reset_local += timedelta(days=1)
        return reset_local.astimezone(UTC)

    match = _ISO_RESET_RE.search(combined_output)
    if match:
        return datetime.fromisoformat(
            match.group(1).replace("Z", "+00:00")
        ).astimezone(UTC)

    match = _RELATIVE_RESET_RE.search(combined_output)
    if match:
        hours = int(match.group("hours") or 0)
        minutes = int(match.group("minutes") or 0)
        seconds = int(match.group("seconds") or 0)
        delta = timedelta(hours=hours, minutes=minutes, seconds=seconds)
        if delta.total_seconds() > 0:
            return current + delta

    # Avoid classifying arbitrary backend failures as cooldowns.
    if classify_capacity_exhaustion(combined_output) is None and not _LIMIT_SIGNAL_RE.search(
        combined_output
    ):
        return None
    return None


def maybe_record_worker_backend_cooldown(
    *,
    usage_store,
    worker_backend: str,
    combined_output: str | None,
    now: datetime | None = None,
    logger: Callable[[str], None] | None = None,
) -> datetime | None:
    if not combined_output:
        return None
    current = now or datetime.now(UTC)
    reset_at = parse_worker_backend_reset(
        combined_output,
        worker_backend,
        now=current,
    )
    if reset_at is None:
        return None
    recorder = getattr(usage_store, "record_worker_backend_cooldown", None)
    if callable(recorder):
        recorder(worker_backend=worker_backend, reset_at=reset_at, now=current)
    if logger is not None:
        logger(
            f"worker backend {worker_backend} cooling down until {reset_at.isoformat()}"
        )
    return reset_at


def execute_with_worker_backend_round_robin(
    *,
    preferred_backend: str,
    usage_store,
    dynamic_enabled: bool,
    execute_once: Callable[[str], _T],
    failed: Callable[[_T], bool],
    failure_text: Callable[[_T], str | None],
    logger: Callable[[str], None] | None = None,
) -> WorkerBackendExecution[_T]:
    selection = select_worker_backend(
        preferred_backend=preferred_backend,
        usage_store=usage_store,
        dynamic_enabled=dynamic_enabled,
    )
    if selection.selected_backend is None:
        return WorkerBackendExecution(
            selected_backend=None,
            payload=None,
            fallback_used=False,
            selection=selection,
        )

    primary_backend = selection.selected_backend
    primary_payload = execute_once(primary_backend)
    if not failed(primary_payload):
        return WorkerBackendExecution(
            selected_backend=primary_backend,
            payload=primary_payload,
            fallback_used=False,
            selection=selection,
        )

    reset_at = maybe_record_worker_backend_cooldown(
        usage_store=usage_store,
        worker_backend=primary_backend,
        combined_output=failure_text(primary_payload),
        logger=logger,
    )
    if reset_at is None or not dynamic_enabled:
        return WorkerBackendExecution(
            selected_backend=primary_backend,
            payload=primary_payload,
            fallback_used=False,
            selection=selection,
        )

    fallback_selection = select_worker_backend(
        preferred_backend=preferred_backend,
        usage_store=usage_store,
        dynamic_enabled=True,
    )
    if (
        fallback_selection.selected_backend is None
        or fallback_selection.selected_backend == primary_backend
    ):
        return WorkerBackendExecution(
            selected_backend=primary_backend,
            payload=primary_payload,
            fallback_used=False,
            selection=fallback_selection,
        )

    if logger is not None:
        logger(
            f"worker backend {primary_backend} exhausted; retrying with "
            f"{fallback_selection.selected_backend}"
        )
    fallback_backend = fallback_selection.selected_backend
    fallback_payload = execute_once(fallback_backend)
    if failed(fallback_payload):
        maybe_record_worker_backend_cooldown(
            usage_store=usage_store,
            worker_backend=fallback_backend,
            combined_output=failure_text(fallback_payload),
            logger=logger,
        )
    return WorkerBackendExecution(
        selected_backend=fallback_backend,
        payload=fallback_payload,
        fallback_used=True,
        selection=fallback_selection,
    )
