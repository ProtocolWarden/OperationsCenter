# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import pytest

from operations_center.application.scope_policy import ChangedFilePolicyChecker


@pytest.fixture
def checker() -> ChangedFilePolicyChecker:
    return ChangedFilePolicyChecker()


# ---------------------------------------------------------------------------
# find_violations
# ---------------------------------------------------------------------------
def test_empty_allowed_paths_returns_empty(checker: ChangedFilePolicyChecker) -> None:
    # No allowed paths -> short circuit, everything permitted.
    assert checker.find_violations(["src/foo.py", "bar.py"], []) == []


def test_no_changed_files_returns_empty(checker: ChangedFilePolicyChecker) -> None:
    assert checker.find_violations([], ["src"]) == []


def test_exact_file_match_no_violation(checker: ChangedFilePolicyChecker) -> None:
    assert checker.find_violations(["src/foo.py"], ["src/foo.py"]) == []


def test_directory_pattern_matches_contents(checker: ChangedFilePolicyChecker) -> None:
    # "src" pattern should match "src/foo.py" via startswith(pattern + "/").
    assert checker.find_violations(["src/foo.py"], ["src"]) == []


def test_violation_outside_allowed_dir(checker: ChangedFilePolicyChecker) -> None:
    result = checker.find_violations(["docs/readme.md"], ["src"])
    assert result == ["docs/readme.md"]


def test_glob_pattern_match(checker: ChangedFilePolicyChecker) -> None:
    # fnmatch path: "*.py" matches a top-level python file.
    assert checker.find_violations(["foo.py"], ["*.py"]) == []


def test_glob_pattern_non_match_is_violation(checker: ChangedFilePolicyChecker) -> None:
    assert checker.find_violations(["foo.txt"], ["*.py"]) == ["foo.txt"]


def test_results_are_sorted_and_deduped(checker: ChangedFilePolicyChecker) -> None:
    # Duplicate after normalization ("b.py" and "./b.py") plus ordering.
    result = checker.find_violations(["z.py", "a.py", "./z.py"], ["src"])
    assert result == ["a.py", "z.py"]


def test_mixed_allowed_and_violations(checker: ChangedFilePolicyChecker) -> None:
    result = checker.find_violations(
        ["src/keep.py", "other/drop.py", "src/sub/deep.py"],
        ["src"],
    )
    assert result == ["other/drop.py"]


def test_trailing_slash_pattern_becomes_glob(checker: ChangedFilePolicyChecker) -> None:
    # "src/" -> "src/*" which matches one level deep via fnmatch.
    assert checker.find_violations(["src/foo.py"], ["src/"]) == []


def test_trailing_slash_pattern_deep_via_startswith(checker: ChangedFilePolicyChecker) -> None:
    # "src/" -> "src/*"; deep path matched by startswith("src/*" + "/")? No.
    # fnmatch("src/a/b.py", "src/*") is True because * matches across slashes.
    assert checker.find_violations(["src/a/b.py"], ["src/"]) == []


def test_multiple_patterns_any_match(checker: ChangedFilePolicyChecker) -> None:
    result = checker.find_violations(
        ["lib/x.py", "tests/y.py", "bad/z.py"],
        ["lib", "tests"],
    )
    assert result == ["bad/z.py"]


# ---------------------------------------------------------------------------
# _normalize_pattern
# ---------------------------------------------------------------------------
def test_normalize_pattern_trailing_slash() -> None:
    assert ChangedFilePolicyChecker._normalize_pattern("src/") == "src/*"


def test_normalize_pattern_backslashes() -> None:
    assert ChangedFilePolicyChecker._normalize_pattern("src\\app") == "src/app"


def test_normalize_pattern_strips_leading_dot_slash() -> None:
    # lstrip("./") removes leading '.' and '/' characters.
    assert ChangedFilePolicyChecker._normalize_pattern("./src/foo") == "src/foo"


def test_normalize_pattern_collapses_path() -> None:
    # Path() normalization collapses redundant components.
    assert ChangedFilePolicyChecker._normalize_pattern("src//foo") == "src/foo"


def test_normalize_pattern_whitespace_stripped() -> None:
    assert ChangedFilePolicyChecker._normalize_pattern("  src/foo  ") == "src/foo"


def test_normalize_pattern_backslash_trailing_becomes_glob() -> None:
    # Backslash converted to slash first, trailing slash -> glob.
    assert ChangedFilePolicyChecker._normalize_pattern("src\\") == "src/*"


# ---------------------------------------------------------------------------
# _normalize_changed_path
# ---------------------------------------------------------------------------
def test_normalize_changed_rename_arrow() -> None:
    # Git rename "old -> new" keeps the new path only.
    assert ChangedFilePolicyChecker._normalize_changed_path("old.py -> new.py") == "new.py"


def test_normalize_changed_rename_arrow_only_first_split() -> None:
    # maxsplit=1 -> remainder after first arrow kept verbatim (then normpath).
    assert ChangedFilePolicyChecker._normalize_changed_path("a -> b -> c") == "b -> c"


def test_normalize_changed_normpath_collapses_dotdot() -> None:
    # normpath runs before backslash replacement, so use forward slashes here.
    assert ChangedFilePolicyChecker._normalize_changed_path("src/../foo.py") == "foo.py"


def test_normalize_changed_backslashes_replaced_after_normpath() -> None:
    # On POSIX, normpath does not treat "\\" as a separator, so ".." is not
    # collapsed; backslashes are merely swapped for slashes afterwards.
    assert ChangedFilePolicyChecker._normalize_changed_path("src\\..\\foo.py") == "src/../foo.py"


def test_normalize_changed_strips_leading_dot_slash() -> None:
    assert ChangedFilePolicyChecker._normalize_changed_path("./src/foo.py") == "src/foo.py"


def test_normalize_changed_whitespace() -> None:
    assert ChangedFilePolicyChecker._normalize_changed_path("  src/foo.py  ") == "src/foo.py"


def test_rename_changed_path_in_find_violations(checker: ChangedFilePolicyChecker) -> None:
    # End-to-end: a rename line resolving to an allowed dir is no violation.
    assert checker.find_violations(["src/old.py -> src/new.py"], ["src"]) == []


def test_rename_changed_path_violation(checker: ChangedFilePolicyChecker) -> None:
    result = checker.find_violations(["src/old.py -> docs/new.py"], ["src"])
    assert result == ["docs/new.py"]
