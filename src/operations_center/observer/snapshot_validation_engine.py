# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Snapshot validation engine with comprehensive error reporting and orchestration.

Coordinates snapshot loading, validation, and result reporting with:
- Structured error reporting with validation context
- Configurable validation layer selection
- Tolerance and accuracy configuration
- Baseline comparison for regression detection
- Output serialization (JSON/text)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from operations_center.observer.models import RepoStateSnapshot
from operations_center.observer.snapshot_loader import SnapshotLoadError, SnapshotLoader
from operations_center.observer.snapshot_validator import (
    SnapshotValidator,
    SnapshotValidationReport,
)

logger = logging.getLogger(__name__)


@dataclass
class ValidationConfig:
    """Configuration for validation execution."""

    layers: list[int] | None = None
    tolerance: dict[str, float] | None = None
    repo_path: Path | None = None
    timeout: int = 60
    retry_on_transient: bool = False
    max_retries: int = 3

    def get_layers(self) -> list[int]:
        """Get layers to validate.

        Returns:
            List of layer numbers (1-5), defaults to 1,2,3 (fast path)
        """
        if self.layers is None:
            return [1, 2, 3]
        return self.layers

    def get_tolerance(self) -> dict[str, float]:
        """Get tolerance configuration.

        Returns:
            Tolerance dict mapping signal names to tolerance values
        """
        if self.tolerance is None:
            return {
                "test_count": 0.01,  # 1%
                "coverage": 0.05,  # 5%
            }
        return self.tolerance


class ValidationError(Exception):
    """Raised when validation orchestration fails."""

    def __init__(
        self,
        message: str,
        context: dict[str, Any] | None = None,
        source_error: Exception | None = None,
    ):
        """Initialize validation error.

        Args:
            message: Error message
            context: Additional context dict
            source_error: Original exception that caused this error
        """
        self.message = message
        self.context = context or {}
        self.source_error = source_error
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for reporting."""
        result = {"error": self.message, "context": self.context}
        if self.source_error:
            result["source"] = {
                "type": type(self.source_error).__name__,
                "message": str(self.source_error),
            }
        return result


class SnapshotValidationEngine:
    """High-level validation orchestration with error handling."""

    def __init__(self, loader: SnapshotLoader | None = None):
        """Initialize validation engine.

        Args:
            loader: Snapshot loader instance. If None, a default is created.
        """
        self.loader = loader or SnapshotLoader()

    def validate(
        self,
        source: str,
        config: ValidationConfig | None = None,
        baseline_source: str | None = None,
    ) -> SnapshotValidationReport:
        """Validate a snapshot with comprehensive error handling.

        Args:
            source: Snapshot source (file path, run_id, or storage location)
            config: Validation configuration. If None, defaults are used.
            baseline_source: Baseline snapshot source for regression detection (layer 5)

        Returns:
            SnapshotValidationReport with results

        Raises:
            ValidationError: If validation cannot be performed
        """
        if config is None:
            config = ValidationConfig()

        try:
            snapshot = self.loader.load(source)
        except SnapshotLoadError as e:
            raise ValidationError(
                f"Failed to load snapshot from '{source}'",
                context=e.to_dict(),
                source_error=e,
            ) from e

        try:
            validator = SnapshotValidator(
                snapshot,
                repo_path=config.repo_path or Path.cwd(),
            )

            layers = config.get_layers()

            baseline = None
            if 5 in layers and baseline_source:
                try:
                    baseline = self.loader.load(baseline_source)
                except SnapshotLoadError as e:
                    logger.warning("Failed to load baseline: %s", e.message)

            report = validator.validate_all_layers(
                layers=layers,
                baseline=baseline,
            )

            return report

        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(
                "Validation failed with unexpected error",
                context={
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                },
                source_error=e,
            ) from e

    def validate_with_retry(
        self,
        source: str,
        config: ValidationConfig | None = None,
        baseline_source: str | None = None,
    ) -> tuple[SnapshotValidationReport, bool]:
        """Validate with automatic retry on transient errors.

        Args:
            source: Snapshot source
            config: Validation configuration
            baseline_source: Baseline snapshot source

        Returns:
            Tuple of (report, was_retried)

        Raises:
            ValidationError: If validation fails after retries
        """
        if config is None:
            config = ValidationConfig()

        if not config.retry_on_transient:
            report = self.validate(source, config, baseline_source)
            return report, False

        last_error = None
        for attempt in range(config.max_retries):
            try:
                report = self.validate(source, config, baseline_source)

                retryable_errors = report.get_retryable_errors()
                if not retryable_errors:
                    return report, attempt > 0

                if attempt < config.max_retries - 1:
                    logger.info(
                        "Attempt %d: Found %d transient errors, retrying",
                        attempt + 1,
                        len(retryable_errors),
                    )
                    continue

                return report, True

            except ValidationError as e:
                last_error = e
                if attempt < config.max_retries - 1:
                    logger.debug("Validation attempt %d failed: %s", attempt + 1, e.message)
                    continue
                raise

        if last_error:
            raise last_error

        raise ValidationError("Validation failed after all retries")

    def load_snapshot(self, source: str) -> RepoStateSnapshot:
        """Load snapshot without validation.

        Args:
            source: Snapshot source (file path, run_id, or storage location)

        Returns:
            Loaded RepoStateSnapshot

        Raises:
            ValidationError: If snapshot cannot be loaded
        """
        try:
            return self.loader.load(source)
        except SnapshotLoadError as e:
            raise ValidationError(
                f"Failed to load snapshot from '{source}'",
                context=e.to_dict(),
                source_error=e,
            ) from e

    def export_report(
        self,
        report: SnapshotValidationReport,
        output_path: Path,
    ) -> None:
        """Export validation report to file.

        Args:
            report: Validation report
            output_path: Where to save the report

        Raises:
            ValidationError: If export fails
        """
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                self._to_json(report.to_dict()),
                encoding="utf-8",
            )
            logger.info("Exported validation report to %s", output_path)
        except (OSError, IOError) as e:
            raise ValidationError(
                f"Failed to export report to {output_path}",
                context={"error": str(e)},
                source_error=e,
            ) from e

    @staticmethod
    def _to_json(obj: Any) -> str:
        """Convert object to JSON with proper serialization.

        Args:
            obj: Object to serialize

        Returns:
            JSON string
        """
        import json
        from datetime import datetime

        def serializer(o: Any) -> Any:
            if isinstance(o, datetime):
                return o.isoformat()
            raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")

        return json.dumps(obj, indent=2, default=serializer, ensure_ascii=False)
