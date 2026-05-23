# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
import logging
import subprocess

from operations_center.observer.models import LintSignal, LintViolation
from operations_center.observer.service import ObserverContext
from operations_center.observer.validation import LintItemValidator

logger = logging.getLogger(__name__)

_MAX_VIOLATIONS = 20


class LintSignalCollector:
    """Run ruff check and collect lint violations as a first-class observer signal."""

    def collect(self, context: ObserverContext) -> LintSignal:
        try:
            result = subprocess.run(
                ["ruff", "check", "--output-format=json", "--quiet", str(context.repo_path)],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError:
            return LintSignal(status="unavailable", source="ruff_not_found")
        except subprocess.TimeoutExpired:
            return LintSignal(status="unavailable", source="ruff_timeout")
        except Exception as exc:
            return LintSignal(status="unavailable", source=f"ruff_error: {exc}")

        raw = result.stdout.strip()
        return self._parse_ruff_output(raw)

    @staticmethod
    def _parse_ruff_output(raw: str) -> LintSignal:
        if not raw:
            return LintSignal(status="clean", violation_count=0, source="ruff")

        try:
            items = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.debug(
                "Failed to parse ruff output: %s at line %d, col %d",
                e.msg, e.lineno, e.colno
            )
            return LintSignal(status="unavailable", source="ruff_parse_error")

        if not isinstance(items, list):
            logger.warning(
                "ruff output: expected list, got %s", type(items).__name__
            )
            return LintSignal(status="unavailable", source="ruff_unexpected_format")

        distinct_file_count = len({item.get("filename", "") for item in items if isinstance(item, dict) and item.get("filename")})

        violations: list[LintViolation] = []
        for idx, item in enumerate(items[:_MAX_VIOLATIONS]):
            is_valid, error_msg = LintItemValidator.validate(item, idx)
            if not is_valid:
                logger.debug("Skipping invalid lint item: %s", error_msg)
                continue

            try:
                loc = item.get("location", {})
                violations.append(
                    LintViolation(
                        path=str(item.get("filename", "")),
                        line=int(loc.get("row", 0)),
                        col=int(loc.get("column", 0)),
                        code=str(item.get("code", "")),
                        message=str(item.get("message", "")),
                    )
                )
            except (TypeError, ValueError) as e:
                logger.debug("Failed to construct lint violation: %s", e)
                continue

        return LintSignal(
            status="violations" if violations else "clean",
            violation_count=len(items),
            distinct_file_count=distinct_file_count,
            top_violations=violations,
            source="ruff",
        )
