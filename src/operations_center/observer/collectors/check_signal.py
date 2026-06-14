# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import configparser
import itertools
import logging
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from operations_center.observer.models import CheckSignal
from operations_center.observer.service import ObserverContext

_TEST_FILE_GLOB_LIMIT = 5
logger = logging.getLogger(__name__)


def latest_matching_file(root: Path, pattern: str) -> tuple[Path, float] | None:
    candidates_with_mtime = []
    for path in root.glob(pattern):
        try:
            mtime = path.stat().st_mtime
            candidates_with_mtime.append((path, mtime))
        except (FileNotFoundError, OSError):
            logger.debug("Skipped file during log discovery: %s", path)
            continue

    if not candidates_with_mtime:
        return None

    latest_path, latest_mtime = max(candidates_with_mtime, key=lambda x: x[1])
    return (latest_path, latest_mtime)


class CheckSignalCollector:
    def collect(self, context: ObserverContext) -> CheckSignal:
        result = latest_matching_file(context.logs_root, "*_test.log")
        if result is not None:
            log_path, observed_mtime = result
            text = log_path.read_text(encoding="utf-8", errors="replace")
            summary = self._extract_summary_line(text)
            status = self._classify_text(text)
            test_name, assertion_message = self._extract_failure_details(text)
            return CheckSignal(
                status=status,
                source=str(log_path),
                observed_at=datetime.fromtimestamp(observed_mtime, tz=UTC),
                summary=summary,
                test_name=test_name,
                assertion_message=assertion_message,
            )
        return self._fallback_discovery(context)

    def _fallback_discovery(self, context: ObserverContext) -> CheckSignal:
        repo_root = context.repo_path
        has_config = self._has_pytest_config(repo_root)
        if not has_config:
            return CheckSignal(status="no_config", source="fallback:no_pytest_config")

        # Prefer repo-local venv pytest; fall back to current interpreter's pytest.
        # Bare "pytest" is not reliable — the subprocess does not inherit venv PATH.
        repo_pytest = repo_root / ".venv" / "bin" / "pytest"
        if repo_pytest.is_file():
            pytest_cmd = [str(repo_pytest)]
        else:
            pytest_cmd = [sys.executable, "-m", "pytest"]

        try:
            result = subprocess.run(
                [*pytest_cmd, "--collect-only", "-q", "--no-header"],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=repo_root,
            )
            if result.returncode not in (0, 5):
                # returncode 5 means no tests collected; other non-zero is an error
                return CheckSignal(status="unknown")

            lines = result.stdout.strip().splitlines()
            # Count test item lines (non-empty lines before the final summary).
            # Test items contain "::" (e.g. path::test_name).
            count = sum(1 for line in lines if "::" in line and line.strip())
            if count > 0:
                return CheckSignal(
                    status="discoverable",
                    test_count=count,
                    source="pytest --collect-only",
                    summary=f"{count} tests discoverable",
                )
            # returncode 5 or 0 but no test items found → unknown
            return CheckSignal(status="unknown")
        except (subprocess.TimeoutExpired, OSError):
            return CheckSignal(status="unknown")

    def _has_pytest_config(self, repo_root: Path) -> bool:
        pyproject = repo_root / "pyproject.toml"
        if pyproject.is_file():
            try:
                text = pyproject.read_text(encoding="utf-8", errors="replace")
                if "[tool.pytest" in text or "[pytest]" in text:
                    return True
            except OSError:
                pass

        pytest_ini = repo_root / "pytest.ini"
        if pytest_ini.is_file():
            return True

        setup_cfg = repo_root / "setup.cfg"
        if setup_cfg.is_file():
            try:
                parser = configparser.ConfigParser()
                parser.read(str(setup_cfg), encoding="utf-8")
                if "tool:pytest" in parser.sections():
                    return True
            except (OSError, configparser.Error):
                pass

        return False

    def _has_test_files(self, repo_root: Path) -> bool:
        candidates = itertools.chain(
            repo_root.rglob("test_*.py"),
            repo_root.rglob("*_test.py"),
        )
        return any(itertools.islice(candidates, _TEST_FILE_GLOB_LIMIT))

    def _extract_summary_line(self, text: str) -> str | None:
        for line in reversed(text.splitlines()):
            stripped = line.strip()
            if stripped and ("passed" in stripped or "failed" in stripped or "error" in stripped):
                return stripped
        return None

    def _classify_text(self, text: str) -> str:
        lowered = text.lower()
        if re.search(r"\b\d+\s+failed\b", lowered) or "error" in lowered:
            return "failed"
        if re.search(r"\b\d+\s+passed\b", lowered):
            return "passed"
        return "unknown"

    def _extract_failure_details(self, text: str) -> tuple[str | None, str | None]:
        """Extract test name and assertion message from test log output.

        Parses pytest-style test logs to extract:
        - test_name: The name of the first failing test
        - assertion_message: The assertion error message from the failure

        Args:
            text: Test log content (pytest output)

        Returns:
            Tuple of (test_name, assertion_message) or (None, None) if not found
        """
        test_name = None
        assertion_message = None

        lines = text.splitlines()
        for i, line in enumerate(lines):
            # Look for FAILED marker to find test name
            if "FAILED" in line and "::" in line:
                # Extract test name from line like: "FAILED tests/unit/test_foo.py::TestClass::test_method"
                parts = line.split()
                for part in parts:
                    if "::" in part:
                        test_name = part.split("::")[-1]  # Get the test function name
                        break

            # Look for AssertionError or assert statement with message
            if test_name and assertion_message is None:
                if "AssertionError" in line or "assert " in line:
                    # Extract assertion message
                    if "AssertionError:" in line:
                        msg = line.split("AssertionError:", 1)[-1].strip()
                        if msg:
                            assertion_message = msg[:200]  # Limit to 200 chars
                    elif i + 1 < len(lines):
                        # Look at next line for assertion details
                        next_line = lines[i + 1].strip()
                        if next_line and not next_line.startswith("_"):
                            assertion_message = next_line[:200]

            # Stop if we have both
            if test_name and assertion_message:
                break

        return test_name, assertion_message
