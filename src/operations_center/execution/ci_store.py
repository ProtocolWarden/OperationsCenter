# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
ci_store.py — Persistent store for CI lineage index entries and execution state.

Backed by ``state/ci_lineage.json``. Stores lightweight ``OcLineageIndexEntry``
records keyed by lineage_id, and ``OcContinuousImprovementState`` records keyed
by proposal_id.

The full ImprovementLineage artifact lives at the CLP-native path
``<lineage_artifact_path>`` (e.g. .context/capsules/<id>/lineage.json in the
target repo). This store holds only the index pointer and mutable state.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from operations_center.contracts.ci import (
    ImprovementLineage,
    OcContinuousImprovementState,
    OcLineageIndexEntry,
)
from operations_center.contracts.enums import RefinementStatus

logger = logging.getLogger(__name__)

_DEFAULT_STORE_PATH = Path("state/ci_lineage.json")


class CiStore:
    """Thread-safe store for CI lineage index entries and improvement state."""

    def __init__(self, path: Path = _DEFAULT_STORE_PATH) -> None:
        self._path = path
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Lineage index

    def upsert_index_entry(self, entry: OcLineageIndexEntry) -> None:
        with self._lock:
            data = self._load()
            data["lineage_index"][entry.lineage_id] = json.loads(entry.model_dump_json())
            self._save(data)

    def get_index_entry(self, lineage_id: str) -> OcLineageIndexEntry | None:
        with self._lock:
            raw = self._load()["lineage_index"].get(lineage_id)
        if raw is None:
            return None
        return OcLineageIndexEntry.model_validate(raw)

    def list_index_entries(
        self,
        *,
        proposal_id: str | None = None,
        status: RefinementStatus | None = None,
    ) -> list[OcLineageIndexEntry]:
        with self._lock:
            rows = list(self._load()["lineage_index"].values())
        entries = [OcLineageIndexEntry.model_validate(r) for r in rows]
        if proposal_id is not None:
            entries = [e for e in entries if e.proposal_id == proposal_id]
        if status is not None:
            entries = [e for e in entries if e.status == status]
        return sorted(entries, key=lambda e: e.last_updated_at, reverse=True)

    # ------------------------------------------------------------------
    # CI state (mutable execution state per proposal)

    def upsert_state(self, state: OcContinuousImprovementState) -> None:
        with self._lock:
            data = self._load()
            data["ci_state"][state.proposal_id] = json.loads(state.model_dump_json())
            self._save(data)

    def get_state(self, proposal_id: str) -> OcContinuousImprovementState | None:
        with self._lock:
            raw = self._load()["ci_state"].get(proposal_id)
        if raw is None:
            return None
        return OcContinuousImprovementState.model_validate(raw)

    # ------------------------------------------------------------------
    # Lineage artifact (CLP-native JSON at lineage_artifact_path)
    # These helpers read/write the ImprovementLineage from the target
    # repo's .context/capsules/ directory. The store itself only holds
    # the index pointer.

    @staticmethod
    def load_lineage(lineage_artifact_path: Path) -> ImprovementLineage | None:
        if not lineage_artifact_path.exists():
            return None
        try:
            return ImprovementLineage.model_validate_json(
                lineage_artifact_path.read_text(encoding="utf-8")
            )
        except Exception as exc:
            logger.warning(
                '{"event": "ci_store_load_lineage_failed", "path": "%s", "error": "%s"}',
                lineage_artifact_path,
                exc,
            )
            return None

    @staticmethod
    def save_lineage(lineage: ImprovementLineage, lineage_artifact_path: Path) -> None:
        lineage_artifact_path.parent.mkdir(parents=True, exist_ok=True)
        lineage_artifact_path.write_text(
            lineage.model_dump_json(indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Internal

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                data.setdefault("lineage_index", {})
                data.setdefault("ci_state", {})
                return data
            except Exception as exc:
                logger.warning(
                    '{"event": "ci_store_load_failed", "path": "%s", "error": "%s"}',
                    self._path,
                    exc,
                )
        return {"lineage_index": {}, "ci_state": {}}

    def _save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
