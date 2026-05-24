# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from collections.abc import Sequence

from operations_center.insights.models import DerivedInsight
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.observer.models import RepoStateSnapshot


class DependencyDriftDeriver:
    def __init__(self, normalizer: InsightNormalizer) -> None:
        self.normalizer = normalizer

    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        if not snapshots:
            return []
        current_status = snapshots[0].signals.dependency_drift.status
        insights: list[DerivedInsight] = []
        if current_status == "available":
            available_snapshots = [snapshot for snapshot in snapshots if snapshot.signals.dependency_drift.status == "available"]
            if available_snapshots:
                first_seen = available_snapshots[-1].signals.dependency_drift.observed_at or available_snapshots[-1].observed_at
                last_seen = available_snapshots[0].signals.dependency_drift.observed_at or available_snapshots[0].observed_at
                insights.append(
                    self.normalizer.normalize(
                        kind="dependency_drift_continuity",
                        subject="dependency_drift",
                        status="present",
                        key_parts=["present", "current"],
                        evidence={"current_status": current_status},
                        first_seen_at=first_seen,
                        last_seen_at=last_seen,
                    )
                )
                if len(available_snapshots) >= 2:
                    insights.append(
                        self.normalizer.normalize(
                            kind="dependency_drift_continuity",
                            subject="dependency_drift",
                            status="present",
                            key_parts=["present", "persistent"],
                            evidence={"consecutive_snapshots": len(available_snapshots)},
                            first_seen_at=first_seen,
                            last_seen_at=last_seen,
                        )
                    )
        if len(snapshots) > 1 and current_status != snapshots[1].signals.dependency_drift.status and current_status == "not_available":
            first_seen = snapshots[1].signals.dependency_drift.observed_at or snapshots[1].observed_at
            last_seen = snapshots[0].signals.dependency_drift.observed_at or snapshots[0].observed_at
            insights.append(
                self.normalizer.normalize(
                    kind="dependency_drift_continuity",
                    subject="dependency_drift",
                    status="present",
                    key_parts=["not_available", "transition"],
                    evidence={
                        "previous_status": snapshots[1].signals.dependency_drift.status,
                        "current_status": current_status,
                    },
                    first_seen_at=first_seen,
                    last_seen_at=last_seen,
                )
            )
        return insights
