# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import UTC, datetime

from operations_center.execution.usage_store import UsageStore

_TIER_PROFILES = {
    "budget": {
        "claude_code": {"model": "claude-haiku-4-5-20251001", "effort": "low"},
        "codex_cli": {"model": "gpt-5.4-mini", "effort": "low"},
    },
    "standard": {
        "claude_code": {"model": "claude-sonnet-5", "effort": "medium"},
        "codex_cli": {"model": "gpt-5.4", "effort": "medium"},
    },
    "premium": {
        "claude_code": {"model": "claude-opus-4-7", "effort": "high"},
        "codex_cli": {"model": "gpt-5.4", "effort": "high"},
    },
}


def normalize_tier_name(tier: str | None) -> str | None:
    if tier is None:
        return None
    lowered = str(tier).strip().lower()
    if lowered in _TIER_PROFILES:
        return lowered
    return lowered


def tier_name_for_runtime_binding(runtime_binding) -> str | None:
    config_ref = getattr(runtime_binding, "config_ref", None)
    if config_ref:
        lowered_ref = str(config_ref).strip().lower()
        for team_name in ("budget", "standard", "premium"):
            if lowered_ref.endswith(f":{team_name}") or lowered_ref == team_name:
                return normalize_tier_name(team_name)

    model = getattr(runtime_binding, "model", None)
    if not model:
        return None
    lowered = str(model).strip().lower()
    if any(token in lowered for token in ("haiku", "mini")):
        return "budget"
    if "opus" in lowered:
        return "premium"
    if any(token in lowered for token in ("sonnet", "gpt-5.4", "gpt-5")):
        return "standard"
    return None


def budget_pressure(usage_store: UsageStore) -> float:
    now = datetime.now(UTC)
    remaining = max(0, usage_store.remaining_exec_capacity(now=now))
    limits = [
        limit
        for limit in (
            usage_store.settings.max_exec_per_hour,
            usage_store.settings.max_exec_per_day,
        )
        if limit > 0
    ]
    if not limits:
        return 0.0
    capacity = min(limits)
    if capacity <= 0:
        return 1.0
    consumed = max(0, capacity - remaining)
    return min(1.0, consumed / capacity)


def downgrade_tier(tier: str) -> str:
    tier = normalize_tier_name(tier) or "budget"
    if tier == "premium":
        return "standard"
    if tier == "standard":
        return "budget"
    return tier


def select_tier(
    *,
    configured: str,
    runtime_binding,
    usage_store: UsageStore,
    dynamic_enabled: bool,
    pressure_threshold: float,
) -> str:
    configured = normalize_tier_name(configured) or "budget"
    if not dynamic_enabled:
        return configured

    inferred = normalize_tier_name(tier_name_for_runtime_binding(runtime_binding)) or configured
    if budget_pressure(usage_store) >= pressure_threshold:
        return downgrade_tier(inferred)
    return inferred


def tier_profile(tier: str) -> dict[str, dict[str, str]]:
    return _TIER_PROFILES.get(normalize_tier_name(tier) or "standard", _TIER_PROFILES["standard"])
