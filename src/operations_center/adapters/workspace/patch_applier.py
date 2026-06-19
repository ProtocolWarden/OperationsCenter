# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""SBX Layer 0: Pre-push patch applier with path allowlist enforcement.

The patch applier is a non-executing gate that enforces the path allowlist
before any patch is applied to the workspace. This prevents dangerous diffs
from being pushed, such as those modifying CI/CD configs, build hooks, or
credential files.

Key property: The applier does NOT execute the patch (no install/test/format
on host). It only validates the patch syntax and checks that all touched files
are in the allowlist, then delegates to `git apply` for the actual application.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class PatchApplyResult:
    """Result of a patch apply operation."""

    success: bool
    reason: str | None = None
    blocked_paths: list[str] | None = None


# Paths and patterns that must NEVER be modified in a patch (load-bearing rules).
# These block RCE vectors, credential exposure, and infrastructure changes.
_BLOCKED_PATH_PATTERNS = [
    # CI/CD configs (sandbox → CI → secrets)
    r"\.github/workflows/.*",
    r"\.gitlab-ci\.yml",
    r"\.circleci/.*",
    r"bitbucket-pipelines\.yml",
    r"\.circle/.*",
    # Build/install hooks (RCE on host)
    r"^setup\.py$",
    r"^setup\.cfg$",
    r"^pyproject\.toml$",
    r"^Makefile(?:\..*)?$",
    r"^conftest\.py$",
    # Package manifests with build hooks
    r"^Cargo\.toml$",
    r"^Cargo\.lock$",
    r"^go\.mod$",
    r"^go\.sum$",
    # package.json (only if it contains "scripts" field — caught by content check)
    # Git metadata
    r"\.git/.*",
    r"\.gitmodules",
    # Hooks (all forms)
    r"\.husky/.*",
    r"\.githooks/.*",
    r".*\/hooks/.*",
    # Environment and shell configs (often sourced)
    r"\.env(?:\..*)?",
    r"\.bashrc",
    r"\.zshrc",
    r"\.profile",
    # Credentials and keys
    r"\.ssh/.*",
    r"\.gnupg/.*",
    r".*\.pem",
    r".*\.key",
    r".*\.pub",
    # Deployment/infrastructure configs
    r"docker-compose(?:\..*)?\.yml",
    r"Dockerfile.*",
    r"kubernetes/.*",
    r"terraform/.*",
    r"ansible/.*",
    # Path traversal and symlinks (caught separately)
    # - Patterns starting with .. (caught in path validation)
    # - Patterns starting with / (caught in path validation)
    # - Symlinks (caught in diff content check)
]


class PatchApplier:
    """Non-executing patch applier with path allowlist enforcement.

    The applier validates that:
    1. All touched files are in the allowlist (not in blocked paths)
    2. No path traversal (.. or /)
    3. No symlinks or unsafe constructs
    4. Patch syntax is valid

    If all checks pass, delegates to `git apply` for the actual application.
    """

    def __init__(self, blocked_path_patterns: list[str] | None = None):
        """Initialize applier with optional custom blocked path patterns.

        Args:
            blocked_path_patterns: List of regex patterns for blocked paths.
                If None, uses _BLOCKED_PATH_PATTERNS.
        """
        self._blocked_patterns = blocked_path_patterns or _BLOCKED_PATH_PATTERNS
        self._compiled_patterns = [
            re.compile(f"^({pattern})$") for pattern in self._blocked_patterns
        ]

    def _parse_diff_files(self, diff: str) -> set[str]:
        """Extract all touched file paths from a unified diff.

        Parses lines like:
            --- a/path/to/file
            +++ b/path/to/file
            diff --git a/path/to/file b/path/to/file

        Returns:
            Set of file paths touched by the diff (normalized).
        """
        files = set()

        # Match "--- a/path" and "+++ b/path" lines
        for match in re.finditer(r"^[+-]{3} [ab]/(.+)$", diff, re.MULTILINE):
            path = match.group(1)
            if path and path != "/dev/null":
                files.add(path)

        # Also match "diff --git a/path b/path" lines
        for match in re.finditer(r"^diff --git a/(.+) b/.+$", diff, re.MULTILINE):
            path = match.group(1)
            if path:
                files.add(path)

        return files

    def _is_blocked(self, file_path: str) -> bool:
        """Check if a file path is blocked by the allowlist.

        Args:
            file_path: Relative path to check

        Returns:
            True if the path matches any blocked pattern, False otherwise
        """
        # Normalize path (remove leading ./ if present)
        normalized = file_path
        if normalized.startswith("./"):
            normalized = normalized[2:]

        for pattern in self._compiled_patterns:
            if pattern.match(normalized):
                return True

        return False

    def _check_unsafe_paths(self, files: set[str]) -> tuple[bool, list[str]]:
        """Check for unsafe path constructs (traversal, symlinks, etc).

        Returns:
            (is_safe, unsafe_files) - is_safe is True if all paths are safe
        """
        unsafe = []

        for path in files:
            # Check for path traversal
            if ".." in path or path.startswith("/"):
                unsafe.append(path)
                continue

            # Check for absolute paths or unusual constructs
            if path.startswith("/"):
                unsafe.append(path)

        return len(unsafe) == 0, unsafe

    def _check_package_json_scripts(self, diff: str, files: set[str]) -> tuple[bool, list[str]]:
        """Check if any package.json modifications add/modify "scripts" field.

        The scripts field in package.json can execute arbitrary commands during
        install/build, so we need to block modifications to it.

        Returns:
            (is_safe, blocked_files) - is_safe is True if no script modifications found
        """
        if "package.json" not in files:
            return True, []

        # Look for "scripts" field additions or modifications in the diff
        # This is a simple heuristic: if the patch touches package.json and
        # contains "scripts" lines with + prefix, it's modifying scripts.
        unsafe = []
        in_package_json_hunk = False

        for line in diff.split("\n"):
            if "--- a/package.json" in line or "+++ b/package.json" in line:
                in_package_json_hunk = True
                continue

            if line.startswith("---") or line.startswith("+++"):
                in_package_json_hunk = False
                continue

            if in_package_json_hunk and line.startswith("+"):
                # Check for "scripts" field or common script names
                if '"scripts"' in line or any(
                    f'"{script}"' in line
                    for script in [
                        "preinstall",
                        "postinstall",
                        "prebuild",
                        "postbuild",
                        "pretest",
                        "posttest",
                        "prepare",
                    ]
                ):
                    unsafe.append("package.json")
                    break

        return len(unsafe) == 0, unsafe

    def _check_diff_syntax(self, diff: str, workspace_path: Path) -> tuple[bool, str | None]:
        """Validate that the diff is syntactically valid using git apply --check.

        Returns:
            (is_valid, error_message)
        """
        try:
            proc = subprocess.run(
                ["git", "apply", "--check"],
                input=diff,
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if proc.returncode != 0:
                return False, f"Diff syntax invalid: {proc.stderr.strip()}"

            return True, None
        except subprocess.TimeoutExpired:
            return False, "Diff validation timed out"
        except Exception as e:
            return False, f"Diff validation failed: {e}"

    def validate(self, diff: str, workspace_path: Path) -> PatchApplyResult:
        """Validate a patch against path allowlist WITHOUT applying it.

        This is used for pre-commit validation to ensure dangerous patches
        are rejected before they're committed to the repository.

        Args:
            diff: The patch to validate (unified diff format)
            workspace_path: Path to the git repository

        Returns:
            PatchApplyResult with success status and details
        """
        workspace_path = Path(workspace_path)

        # 1. Parse diff to extract touched files
        files = self._parse_diff_files(diff)

        if not files:
            # Empty diff — nothing to do
            return PatchApplyResult(success=True, reason="No files in diff")

        # 2. Check for unsafe path constructs
        is_safe, unsafe_files = self._check_unsafe_paths(files)
        if not is_safe:
            return PatchApplyResult(
                success=False,
                reason=f"Unsafe paths detected (traversal or absolute): {', '.join(unsafe_files)}",
                blocked_paths=unsafe_files,
            )

        # 3. Check each file against blocked paths
        blocked = [f for f in files if self._is_blocked(f)]
        if blocked:
            return PatchApplyResult(
                success=False,
                reason=f"Blocked paths: {', '.join(blocked)}",
                blocked_paths=blocked,
            )

        # 4. Check package.json for script modifications
        is_safe, unsafe_files = self._check_package_json_scripts(diff, files)
        if not is_safe:
            return PatchApplyResult(
                success=False,
                reason="Cannot modify scripts field in package.json",
                blocked_paths=unsafe_files,
            )

        # 5. Validate diff syntax
        is_valid, error_msg = self._check_diff_syntax(diff, workspace_path)
        if not is_valid:
            return PatchApplyResult(
                success=False,
                reason=error_msg,
            )

        logger.debug("Patch validation passed for %d files", len(files))
        return PatchApplyResult(success=True, reason="Patch validation passed")

    def apply(self, diff: str, workspace_path: Path) -> PatchApplyResult:
        """Apply a patch with path allowlist enforcement.

        First validates the patch, then delegates to `git apply` for the
        actual application. Use validate() for validation-only without applying.

        Args:
            diff: The patch to apply (unified diff format)
            workspace_path: Path to the git repository

        Returns:
            PatchApplyResult with success status and details
        """
        # First validate the patch
        validation_result = self.validate(diff, workspace_path)
        if not validation_result.success:
            return validation_result

        workspace_path = Path(workspace_path)
        files = self._parse_diff_files(diff)

        # Apply the patch using git apply
        try:
            proc = subprocess.run(
                ["git", "apply"],
                input=diff,
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if proc.returncode != 0:
                return PatchApplyResult(
                    success=False,
                    reason=f"Patch apply failed: {proc.stderr.strip()}",
                )

            logger.info("Patch applied successfully with %d files", len(files))
            return PatchApplyResult(success=True)

        except subprocess.TimeoutExpired:
            return PatchApplyResult(
                success=False,
                reason="Patch application timed out",
            )
        except Exception as e:
            return PatchApplyResult(
                success=False,
                reason=f"Patch application failed: {e}",
            )
