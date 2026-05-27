# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from collections.abc import Sequence

from operations_center.insights.models import DerivedInsight
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.observer.models import RepoStateSnapshot


class DependencyDriftDeriver:
    """Derive dependency-related insights from observer snapshots.

    Fires on:
    - dependency_drift_continuity/present/current: current snapshot has available dependencies.
    - dependency_drift_continuity/present/persistent: dependencies have remained available across multiple snapshots.
    - dependency_drift_continuity/present/transition: transition to unavailable status.
    - dependency_drift_continuity/present/recovery: transition from unavailable to available status.
    """

    def __init__(self, normalizer: InsightNormalizer) -> None:
        self.normalizer = normalizer

    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        if not snapshots:
            return []
        current_status = snapshots[0].signals.dependency_drift.status
        insights: list[DerivedInsight] = []
        if current_status == "available":
            available_snapshots = [snapshot for snapshot in snapshots if snapshot.signals.dependency_drift.status == "available"]
            insights.append(
                self.normalizer.normalize(
                    kind="dependency_drift_continuity",
                    subject="dependency_drift",
                    status="present",
                    key_parts=["present", "current"],
                    evidence={"current_status": current_status},
                    first_seen_at=available_snapshots[-1].observed_at,
                    last_seen_at=available_snapshots[0].observed_at,
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
                        first_seen_at=available_snapshots[-1].observed_at,
                        last_seen_at=available_snapshots[0].observed_at,
                    )
                )
        if len(snapshots) > 1:
            previous_status = snapshots[1].signals.dependency_drift.status
            if current_status != previous_status:
                if current_status == "not_available":
                    insights.append(
                        self.normalizer.normalize(
                            kind="dependency_drift_continuity",
                            subject="dependency_drift",
                            status="present",
                            key_parts=["not_available", "transition"],
                            evidence={
                                "previous_status": previous_status,
                                "current_status": current_status,
                            },
                            first_seen_at=snapshots[1].observed_at,
                            last_seen_at=snapshots[0].observed_at,
                        )
                    )
                elif current_status == "available" and previous_status == "not_available":
                    insights.append(
                        self.normalizer.normalize(
                            kind="dependency_drift_continuity",
                            subject="dependency_drift",
                            status="present",
                            key_parts=["available", "recovery"],
                            evidence={
                                "previous_status": previous_status,
                                "current_status": current_status,
                            },
                            first_seen_at=snapshots[1].observed_at,
                            last_seen_at=snapshots[0].observed_at,
                        )
                    )
        return insights
