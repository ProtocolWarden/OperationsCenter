# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""ExtractionHistoryCollector — Records extraction health metrics snapshots for trend analysis.

Captures ExtractionHealth data from signals at collection time, storing snapshots
for historical trend analysis and anomaly detection.

Usage:
    collector = ExtractionHistoryCollector(storage_root="/var/extraction-history")
    snapshot = collector.collect_snapshot(extraction_health, signal_id="snap-123")
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from operations_center.observer.extraction_health_history import (
    ExtractionHealthSnapshot,
    ExtractionHistoryStorage,
)

logger = logging.getLogger(__name__)


class ExtractionHistoryCollector:
    """Collects and records extraction health snapshots for trend analysis.

    Captures extraction metrics (success_rate, extraction counts, edge cases)
    at collection time, storing them for historical analysis.
    """

    def __init__(self, storage_root: str | Path) -> None:
        """Initialize collector.

        Args:
            storage_root: Root directory for snapshot storage.
        """
        self.storage = ExtractionHistoryStorage.create_local(str(storage_root))

    def collect_snapshot(
        self,
        success_rate: float,
        complete_extraction: int,
        partial_extraction: int,
        no_extraction: int,
        total_flaky_tests: int,
        edge_case_summary: dict[str, int] | None = None,
        snapshot_id: str | None = None,
        collection_run_id: str | None = None,
    ) -> ExtractionHealthSnapshot:
        """Record a snapshot of current extraction health.

        Args:
            success_rate: Percentage of flaky tests with extraction (0-100).
            complete_extraction: Count of tests with both test_name and assertion_message.
            partial_extraction: Count of tests with one field only.
            no_extraction: Count of tests with neither field.
            total_flaky_tests: Total count of flaky tests analyzed.
            edge_case_summary: Dict of edge case counts (optional).
            snapshot_id: Reference to source signal (optional).
            collection_run_id: Unique identifier for collection cycle (optional).

        Returns:
            The created and stored ExtractionHealthSnapshot.
        """
        if edge_case_summary is None:
            edge_case_summary = {}

        snapshot = ExtractionHealthSnapshot(
            observed_at=datetime.now(UTC),
            success_rate=success_rate,
            complete_extraction=complete_extraction,
            partial_extraction=partial_extraction,
            no_extraction=no_extraction,
            total_flaky_tests=total_flaky_tests,
            extracted_count=complete_extraction + partial_extraction,
            edge_case_summary=edge_case_summary,
            snapshot_id=snapshot_id,
            collection_run_id=collection_run_id,
        )

        try:
            self.storage.save_snapshot(snapshot)
            logger.debug(
                "Recorded extraction health snapshot: success_rate=%.1f%%, extracted=%d/%d",
                success_rate,
                complete_extraction + partial_extraction,
                total_flaky_tests,
            )
        except OSError as e:
            logger.error("Failed to save extraction health snapshot: %s", e)
            raise

        return snapshot
