# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
# src/operations_center/entrypoints/spec_trigger/main.py
"""Spec trigger watcher (ADR 0007 Phase B).

Detects the conditions under which a new spec should be authored
(operator drop-file present, or board fully drained) and emits a
single Plane task with ``task-kind: spec-author`` for the
``board_worker`` to pick up.

This watcher MUST NOT invoke any LLM. The entire point of the Phase
B extraction is to push spec-author execution through the normal
backend executor pipeline via Plane — see ADR 0007 for the contract.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from operations_center.adapters.plane import PlaneClient
from operations_center.config import load_settings
from operations_center.spec_author.models import TriggerSource
from operations_center.spec_author.spec_author_task import (
    LABEL_SOURCE as _LABEL_SOURCE,
    LABEL_TASK_KIND as _LABEL_TASK_KIND,
    SpecAuthorPayload,
    create_spec_author_task,
)
from operations_center.spec_author.trigger import TriggerDetector, TriggerResult

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

_SPECS_DIR = Path("docs/specs")
_ACTIVE_CAMPAIGNS_PATH = Path("state/campaigns/active.json")

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_GIT_LOG_COMMITS = 10


def _count_state(issues: list[dict[str, Any]], state_name: str) -> int:
    """Count issues whose Plane state matches *state_name* (case-insensitive)."""
    target = state_name.lower()
    return sum(
        1
        for i in issues
        if str((i.get("state") or {}).get("name", "")).lower() == target
    )


def _has_active_campaign(state_path: Path = _ACTIVE_CAMPAIGNS_PATH) -> bool:
    """Read the spec_hygiene-owned projection at *state_path* and report
    whether any campaign is currently active.

    The file is Phase A's Plane-derived projection (single writer:
    spec_hygiene). We only *read* it here.
    """
    if not state_path.exists():
        return False
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    campaigns = data.get("campaigns") or [] if isinstance(data, dict) else []
    for record in campaigns:
        if isinstance(record, dict) and record.get("status") == "active":
            return True
    return False


def _existing_spec_author_in_flight(issues: list[dict[str, Any]]) -> str | None:
    """Return the issue id of any non-Done spec-author task, else None."""
    src = _LABEL_SOURCE.lower()
    kind = _LABEL_TASK_KIND.lower()
    for issue in issues:
        state_name = str((issue.get("state") or {}).get("name", "")).lower()
        if state_name == "done":
            continue
        names: list[str] = []
        for label in issue.get("labels", []) or []:
            if isinstance(label, dict):
                names.append(str(label.get("name", "")).lower())
            else:
                names.append(str(label).lower())
        if src in names and kind in names:
            return str(issue.get("id", ""))
    return None


def _slugify(text: str, fallback: str) -> str:
    """Best-effort slug from arbitrary text; *fallback* used if empty."""
    first_line = (text or "").splitlines()[0] if text else ""
    lowered = first_line.strip().lower()
    cleaned = _SLUG_RE.sub("-", lowered).strip("-")
    cleaned = cleaned[:60].strip("-")
    return cleaned or fallback


def _derive_spec_slug(trigger: TriggerResult) -> str:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    if trigger.source == TriggerSource.DROP_FILE and trigger.seed_text:
        return _slugify(trigger.seed_text, fallback=f"spec-{ts}")
    return f"queue-drain-{ts}"


def _collect_git_log(repo_path: Path, n: int = _GIT_LOG_COMMITS) -> str:
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", f"-{n}"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _collect_existing_specs(specs_dir: Path) -> list[str]:
    if not specs_dir.exists():
        return []
    return sorted(
        p.stem
        for p in specs_dir.glob("*.md")
        if p.is_file() and p.parent.name != "archive"
    )


def _build_payload(
    trigger: TriggerResult,
    settings: Any,
    ready_count: int,
    running_count: int,
) -> SpecAuthorPayload:
    slug = _derive_spec_slug(trigger)
    git_logs: dict[str, str] = {}
    for repo_key, repo_cfg in (settings.repos or {}).items():
        local_path = getattr(repo_cfg, "local_path", None)
        if not local_path:
            continue
        log = _collect_git_log(Path(local_path))
        if log:
            git_logs[repo_key] = log
    return SpecAuthorPayload(
        spec_slug=slug,
        trigger_source=trigger.source.value,
        target_path=f"docs/specs/{slug}.md",
        seed_text=trigger.seed_text,
        recent_git_log_repos=git_logs,
        existing_specs=_collect_existing_specs(_SPECS_DIR),
        ready_count=ready_count,
        running_count=running_count,
        drained=(ready_count == 0 and running_count == 0),
    )


def run_once(settings: Any, client: PlaneClient) -> None:
    sd = settings.spec_author
    if not sd.enabled:
        return

    logger.info(json.dumps({"event": "spec_trigger_cycle_start"}, ensure_ascii=False))

    try:
        all_issues = client.list_issues()
    except Exception as exc:  # noqa: BLE001 — log and skip cycle
        logger.error(json.dumps(
            {"event": "spec_trigger_board_fetch_failed", "error": str(exc)},
            ensure_ascii=False,
        ))
        return

    # Dedupe: one in-flight spec-author task at a time.
    existing = _existing_spec_author_in_flight(all_issues)
    if existing is not None:
        logger.info(json.dumps(
            {"event": "spec_trigger_skip_in_flight", "issue_id": existing},
            ensure_ascii=False,
        ))
        return

    ready_count = _count_state(all_issues, "ready for ai")
    running_count = _count_state(all_issues, "in progress")
    has_active = _has_active_campaign()

    # Detection — TriggerDetector already encodes the
    # drop_file > queue_drain priority and the has_active_campaign gate.
    detector = TriggerDetector(drop_file_path=Path(sd.drop_file_path))
    trigger = detector.detect(
        ready_count=ready_count,
        running_count=running_count,
        has_active_campaign=has_active,
    )
    if trigger is None:
        logger.info(json.dumps(
            {
                "event": "spec_trigger_no_fire",
                "ready_count": ready_count,
                "running_count": running_count,
                "has_active": has_active,
            },
            ensure_ascii=False,
        ))
        return

    payload = _build_payload(trigger, settings, ready_count, running_count)
    try:
        issue_id = create_spec_author_task(client, payload)
    except Exception as exc:  # noqa: BLE001 — log and skip; drop-file stays so we can retry
        logger.error(json.dumps(
            {
                "event": "spec_trigger_create_failed",
                "trigger_source": payload.trigger_source,
                "spec_slug": payload.spec_slug,
                "error": str(exc),
            },
            ensure_ascii=False,
        ))
        return

    logger.info(json.dumps(
        {
            "event": "spec_trigger_task_created",
            "issue_id": issue_id,
            "trigger_source": payload.trigger_source,
            "spec_slug": payload.spec_slug,
            "target_path": payload.target_path,
        },
        ensure_ascii=False,
    ))

    # Archive the drop-file only after the Plane task is safely on the board,
    # so a creation failure leaves the operator's seed in place for the next cycle.
    if trigger.source == TriggerSource.DROP_FILE:
        try:
            detector.archive_drop_file()
        except OSError as exc:
            logger.error(json.dumps(
                {"event": "spec_trigger_archive_failed", "error": str(exc)},
                ensure_ascii=False,
            ))


def _write_heartbeat(status_dir: Path | None) -> None:
    if status_dir is None:
        return
    try:
        hb = status_dir / "heartbeat_spec_trigger.json"
        hb.write_text(
            json.dumps(
                {
                    "role": "spec_trigger",
                    "at": datetime.now(UTC).isoformat(),
                    "status": "idle",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Spec trigger watcher — detect drop-file / queue-drain and "
                    "emit a single spec-author Plane task per cycle (ADR 0007 Phase B).",
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument(
        "--status-dir",
        type=Path,
        default=None,
        help="Directory for heartbeat_spec_trigger.json",
    )
    args = parser.parse_args()

    settings = load_settings(args.config)
    client = PlaneClient(
        base_url=settings.plane.base_url,
        api_token=settings.plane_token(),
        workspace_slug=settings.plane.workspace_slug,
        project_id=settings.plane.project_id,
    )
    sd = settings.spec_author

    try:
        if args.once:
            run_once(settings, client)
            return
        cycle = 0
        while True:
            _write_heartbeat(args.status_dir)
            try:
                run_once(settings, client)
            except Exception as exc:  # noqa: BLE001 — watcher must not die on cycle errors
                logger.error(json.dumps(
                    {
                        "event": "spec_trigger_cycle_error",
                        "cycle": cycle,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                ))
            cycle += 1
            time.sleep(sd.poll_interval_seconds)
    finally:
        client.close()


if __name__ == "__main__":
    main()
