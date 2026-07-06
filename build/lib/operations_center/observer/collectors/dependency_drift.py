# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from operations_center.observer.models import DependencyDriftSignal
from operations_center.observer.service import ObserverContext
from operations_center.observer.validation import (
    ArtifactValidator,
    DependencyReportValidator,
)

logger = logging.getLogger(__name__)


class DependencyDriftCollector:
    def collect(self, context: ObserverContext) -> DependencyDriftSignal:
        result = self._latest_dependency_report(context.settings.report_root)
        if result is None:
            return DependencyDriftSignal(status="not_available")

        candidate, observed_mtime = result

        try:
            text = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            ArtifactValidator.log_io_error(
                candidate,
                e,
                context={"collector": "DependencyDriftCollector"},
                metrics_exporter=context.metrics_exporter,
            )
            return DependencyDriftSignal(status="not_available")

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as e:
            ArtifactValidator.log_parse_error(
                candidate,
                e,
                context={"collector": "DependencyDriftCollector"},
                metrics_exporter=context.metrics_exporter,
            )
            return DependencyDriftSignal(status="not_available")

        is_valid, error_msg = DependencyReportValidator.validate(payload)
        if not is_valid:
            ArtifactValidator.log_structure_error(
                candidate,
                error_msg,
                expected_schema="dependency_report.json",
                context={"collector": "DependencyDriftCollector"},
                metrics_exporter=context.metrics_exporter,
            )
            return DependencyDriftSignal(status="not_available")

        statuses = payload.get("statuses", [])
        created_task_ids = payload.get("created_task_ids", [])
        actionable = [
            status for status in statuses if isinstance(status, dict) and status.get("notes")
        ]
        summary = (
            f"actionable_statuses={len(actionable)} created_task_ids={len(created_task_ids)}"
            if statuses
            else "dependency report present with no statuses"
        )
        return DependencyDriftSignal(
            status="available",
            source=str(candidate),
            observed_at=datetime.fromtimestamp(observed_mtime, tz=UTC),
            summary=summary,
        )

    def _latest_dependency_report(self, report_root: Path) -> tuple[Path, float] | None:
        candidates_with_mtime = []
        for path in report_root.glob("*/dependency_report.json"):
            try:
                mtime = path.stat().st_mtime
                candidates_with_mtime.append((path, mtime))
            except (FileNotFoundError, OSError):
                logger.debug("Skipped file during dependency report discovery: %s", path)
                continue

        if not candidates_with_mtime:
            return None

        latest_path, latest_mtime = max(candidates_with_mtime, key=lambda x: x[1])
        return (latest_path, latest_mtime)
