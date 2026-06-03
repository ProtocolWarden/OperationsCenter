# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from operations_center.entrypoints.worker_backend_probe.main import app
from operations_center.execution.usage_store import UsageStore


def test_probe_cli_noop_when_nothing_cooling(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    result = CliRunner().invoke(app, ["--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["report"] == {}


def test_probe_cli_clears_stale_cooldown(monkeypatch, tmp_path: Path) -> None:
    # Force the probe to report runnable so the CLI clears a stale cooldown end-to-end.
    usage_path = tmp_path / "usage.json"
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(usage_path))
    now = datetime.now(UTC)
    UsageStore().record_worker_backend_cooldown(
        worker_backend="claude_code",
        reset_at=now + timedelta(days=3),
        now=now,
        limit_kind="model_weekly",
        model="sonnet",
    )

    from operations_center.backends import worker_backend_probe as mod

    monkeypatch.setattr(
        mod,
        "probe_model",
        lambda backend, model, *, timeout: mod.ProbeResult(backend, model, True, "stub"),
    )

    result = CliRunner().invoke(app, ["--json"])
    assert result.exit_code == 0, result.output

    remaining = UsageStore().worker_backend_cooldown_details("claude_code", now=now)
    assert remaining == []
