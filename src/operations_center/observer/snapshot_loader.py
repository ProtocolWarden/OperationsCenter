# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Snapshot loading and parsing from multiple formats and sources.

Provides unified interface for loading snapshots from:
- Local files (JSON, YAML)
- Storage backends (local filesystem, S3, HTTP)
- Run IDs (from configured storage backend)

Handles automatic format detection and graceful error reporting.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml

from operations_center.observer.models import RepoStateSnapshot
from operations_center.observer.snapshot_repository import (
    LocalSnapshotRepository,
    SnapshotFormat,
    SnapshotRepository,
)

logger = logging.getLogger(__name__)


class SnapshotLoadError(Exception):
    """Raised when snapshot loading fails."""

    def __init__(self, message: str, source: str | None = None, details: dict[str, Any] | None = None):
        """Initialize snapshot load error.

        Args:
            message: Human-readable error message
            source: Where the error occurred (file path, run_id, etc.)
            details: Additional context for debugging
        """
        self.message = message
        self.source = source
        self.details = details or {}
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for reporting."""
        return {
            "error": self.message,
            "source": self.source,
            "details": self.details,
        }


class SnapshotLoader:
    """Unified snapshot loading from multiple sources and formats."""

    def __init__(self, storage_backend: SnapshotRepository | None = None):
        """Initialize loader with optional storage backend.

        Args:
            storage_backend: Repository instance for loading snapshots by run_id.
                           If None, local filesystem is used.
        """
        self.storage_backend = storage_backend or LocalSnapshotRepository()

    def load(self, source: str) -> RepoStateSnapshot:
        """Load snapshot from various sources.

        Attempts to load from:
        1. Local file (if source is a valid file path)
        2. Storage backend by run_id (if source looks like a run_id)

        Args:
            source: File path, run_id, or storage location

        Returns:
            Loaded RepoStateSnapshot

        Raises:
            SnapshotLoadError: If snapshot cannot be loaded
        """
        source_path = Path(source)

        if source_path.is_file():
            return self.load_from_file(source_path)

        try:
            return self.storage_backend.load(source)
        except Exception as e:
            raise SnapshotLoadError(
                f"Failed to load snapshot from source '{source}'",
                source=source,
                details={"error_type": type(e).__name__, "error": str(e)},
            ) from e

    def load_from_file(self, file_path: Path | str) -> RepoStateSnapshot:
        """Load snapshot from a local file.

        Auto-detects format from file extension or content.

        Args:
            file_path: Path to snapshot file

        Returns:
            Loaded RepoStateSnapshot

        Raises:
            SnapshotLoadError: If file cannot be read or parsed
        """
        file_path = Path(file_path)

        if not file_path.exists():
            raise SnapshotLoadError(
                f"File not found: {file_path}",
                source=str(file_path),
            )

        if not file_path.is_file():
            raise SnapshotLoadError(
                f"Path is not a file: {file_path}",
                source=str(file_path),
            )

        try:
            content = file_path.read_text(encoding="utf-8")
        except (OSError, IOError) as e:
            raise SnapshotLoadError(
                f"Cannot read file: {e}",
                source=str(file_path),
                details={"error_type": type(e).__name__},
            ) from e

        format = self._detect_format(file_path, content)
        return self._parse_snapshot(content, format, str(file_path))

    def load_from_json(self, content: str) -> RepoStateSnapshot:
        """Load snapshot from JSON string.

        Args:
            content: JSON content

        Returns:
            Loaded RepoStateSnapshot

        Raises:
            SnapshotLoadError: If JSON is invalid
        """
        return self._parse_snapshot(content, SnapshotFormat.JSON, "json_string")

    def load_from_yaml(self, content: str) -> RepoStateSnapshot:
        """Load snapshot from YAML string.

        Args:
            content: YAML content

        Returns:
            Loaded RepoStateSnapshot

        Raises:
            SnapshotLoadError: If YAML is invalid
        """
        return self._parse_snapshot(content, SnapshotFormat.YAML, "yaml_string")

    def _detect_format(self, file_path: Path, content: str) -> SnapshotFormat:
        """Detect snapshot format from file extension or content.

        Args:
            file_path: Path to file
            content: File content

        Returns:
            Detected SnapshotFormat

        Raises:
            SnapshotLoadError: If format cannot be determined
        """
        suffix = file_path.suffix.lower()

        if suffix == ".json":
            return SnapshotFormat.JSON
        if suffix == ".yaml" or suffix == ".yml":
            return SnapshotFormat.YAML
        if suffix == ".jsonl":
            return SnapshotFormat.JSONL

        try:
            json.loads(content)
            return SnapshotFormat.JSON
        except (json.JSONDecodeError, ValueError):
            pass

        try:
            yaml.safe_load(content)
            return SnapshotFormat.YAML
        except yaml.YAMLError:
            pass

        raise SnapshotLoadError(
            f"Cannot detect snapshot format from file '{file_path.name}' and content",
            source=str(file_path),
            details={"suffix": suffix},
        )

    def _parse_snapshot(
        self, content: str, format: SnapshotFormat, source: str
    ) -> RepoStateSnapshot:
        """Parse snapshot from content in specified format.

        Args:
            content: Raw content
            format: Expected format
            source: Source identifier for error reporting

        Returns:
            Parsed RepoStateSnapshot

        Raises:
            SnapshotLoadError: If parsing fails
        """
        try:
            if format == SnapshotFormat.JSON:
                data = json.loads(content)
            elif format == SnapshotFormat.YAML:
                data = yaml.safe_load(content)
            elif format == SnapshotFormat.JSONL:
                data = self._parse_jsonl(content)
            else:
                raise SnapshotLoadError(
                    f"Unsupported format: {format}",
                    source=source,
                )

            snapshot = RepoStateSnapshot.model_validate(data)
            logger.info("Loaded snapshot %s from %s", snapshot.run_id, source)
            return snapshot

        except json.JSONDecodeError as e:
            raise SnapshotLoadError(
                f"Invalid JSON: {e}",
                source=source,
                details={"line": e.lineno, "col": e.colno},
            ) from e
        except yaml.YAMLError as e:
            raise SnapshotLoadError(
                f"Invalid YAML: {e}",
                source=source,
            ) from e
        except Exception as e:
            raise SnapshotLoadError(
                f"Failed to parse snapshot: {e}",
                source=source,
                details={"error_type": type(e).__name__},
            ) from e

    def _parse_jsonl(self, content: str) -> dict[str, Any]:
        """Parse JSONL format (one JSON object per line).

        For snapshots, expects last non-empty line to be the snapshot object.

        Args:
            content: JSONL content

        Returns:
            Parsed JSON object

        Raises:
            SnapshotLoadError: If JSONL is invalid
        """
        lines = [line.strip() for line in content.split("\n") if line.strip()]

        if not lines:
            raise SnapshotLoadError(
                "JSONL file is empty",
                source="jsonl_content",
            )

        last_line = lines[-1]
        try:
            return json.loads(last_line)
        except json.JSONDecodeError as e:
            raise SnapshotLoadError(
                f"Invalid JSON in JSONL: {e}",
                source="jsonl_content",
                details={"line": len(lines)},
            ) from e
