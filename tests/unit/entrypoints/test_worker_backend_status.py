# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from operations_center.entrypoints.worker_backend_status.main import app
from operations_center.execution.usage_store import UsageStore


def test_worker_backend_status_json_reports_cooldowns(
    monkeypatch, tmp_path: Path
) -> None:
    usage_path = tmp_path / "usage.json"
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(usage_path))
    store = UsageStore()
    now = datetime.now(UTC)
    store.record_worker_backend_cooldown(
        worker_backend="claude_code",
        reset_at=now + timedelta(hours=1),
        now=now,
    )

    result = CliRunner().invoke(app, ["--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["worker_backends"]["claude_code"]["cooling_down"] is True
    assert payload["worker_backends"]["codex_cli"]["cooling_down"] is False


def test_worker_backend_status_reports_per_model_kind(
    monkeypatch, tmp_path: Path
) -> None:
    usage_path = tmp_path / "usage.json"
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(usage_path))
    store = UsageStore()
    now = datetime.now(UTC)
    # Sonnet weekly burnt, but opus/haiku untouched — must show model-scoped.
    store.record_worker_backend_cooldown(
        worker_backend="claude_code",
        reset_at=now + timedelta(days=3),
        now=now,
        limit_kind="model_weekly",
        model="sonnet",
    )

    result = CliRunner().invoke(app, ["--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    cooldowns = payload["worker_backends"]["claude_code"]["cooldowns"]
    assert len(cooldowns) == 1
    assert cooldowns[0]["limit_kind"] == "model_weekly"
    assert cooldowns[0]["model"] == "sonnet"
    # Human (non-JSON) render must not error.
    assert CliRunner().invoke(app, []).exit_code == 0
