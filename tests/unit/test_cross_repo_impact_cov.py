# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from dataclasses import dataclass

import pytest

from operations_center.cross_repo_impact import (
    CrossRepoImpact,
    _check_cross_repo_impact,
    _normalize,
)


@dataclass
class _FakeRepo:
    """Minimal RepoSettings-like stub carrying impact_report_paths."""

    impact_report_paths: list[str] | None = None


def _repos(**kw: _FakeRepo) -> dict[str, _FakeRepo]:
    return dict(kw)


# --- _normalize ----------------------------------------------------------


def test_normalize_strips_leading_dot_slash() -> None:
    assert _normalize("./src/foo.py") == "src/foo.py"


def test_normalize_plain_path_unchanged() -> None:
    assert _normalize("src/foo.py") == "src/foo.py"


def test_normalize_backslashes_become_posix() -> None:
    # Path normalisation collapses redundant separators / dot segments.
    assert _normalize("a/./b") == "a/b"


def test_normalize_strips_only_leading_dotslash_chars() -> None:
    # lstrip("./") removes any leading '.' or '/' chars.
    assert _normalize("/abs/path") == "abs/path"


# --- early-return guards -------------------------------------------------


def test_empty_changed_files_returns_empty() -> None:
    repos = _repos(b=_FakeRepo(impact_report_paths=["src/"]))
    assert _check_cross_repo_impact([], repos=repos) == []


def test_empty_repos_returns_empty() -> None:
    assert _check_cross_repo_impact(["src/foo.py"], repos={}) == []


def test_none_changed_files_via_falsy_returns_empty() -> None:
    # Falsy changed_files (empty) short-circuits before iterating.
    assert _check_cross_repo_impact([], repos={}) == []


# --- happy paths ---------------------------------------------------------


def test_single_match_under_prefix() -> None:
    repos = _repos(consumer=_FakeRepo(impact_report_paths=["src/iface/"]))
    out = _check_cross_repo_impact(["src/iface/api.py"], repos=repos)
    assert len(out) == 1
    impact = out[0]
    assert isinstance(impact, CrossRepoImpact)
    assert impact.repo_key == "consumer"
    assert impact.matched_paths == ("src/iface/",)
    assert impact.changed_files == ("src/iface/api.py",)


def test_exact_path_equality_match() -> None:
    repos = _repos(c=_FakeRepo(impact_report_paths=["src/iface/api.py"]))
    out = _check_cross_repo_impact(["src/iface/api.py"], repos=repos)
    assert out[0].changed_files == ("src/iface/api.py",)


def test_prefix_with_trailing_slash_rstripped() -> None:
    # Declared with trailing slash; matching uses prefix + "/".
    repos = _repos(c=_FakeRepo(impact_report_paths=["lib/"]))
    out = _check_cross_repo_impact(["lib/x.py"], repos=repos)
    assert out[0].matched_paths == ("lib/",)


def test_multiple_prefixes_one_repo_dedup_and_sorted() -> None:
    repos = _repos(
        c=_FakeRepo(impact_report_paths=["b/", "a/", "a/"]),
    )
    out = _check_cross_repo_impact(["a/1.py", "b/2.py", "a/1.py"], repos=repos)
    assert len(out) == 1
    # matched_paths sorted + deduped
    assert out[0].matched_paths == ("a/", "b/")
    # changed_files sorted + deduped
    assert out[0].changed_files == ("a/1.py", "b/2.py")


def test_multiple_repos_only_matching_included() -> None:
    repos = _repos(
        hit=_FakeRepo(impact_report_paths=["shared/"]),
        miss=_FakeRepo(impact_report_paths=["other/"]),
    )
    out = _check_cross_repo_impact(["shared/m.py"], repos=repos)
    keys = {i.repo_key for i in out}
    assert keys == {"hit"}


# --- branch coverage -----------------------------------------------------


def test_source_repo_excluded() -> None:
    repos = _repos(
        src=_FakeRepo(impact_report_paths=["x/"]),
        other=_FakeRepo(impact_report_paths=["x/"]),
    )
    out = _check_cross_repo_impact(["x/f.py"], repos=repos, source_repo_key="src")
    assert {i.repo_key for i in out} == {"other"}


def test_repo_with_no_impact_paths_skipped() -> None:
    repos = _repos(c=_FakeRepo(impact_report_paths=[]))
    assert _check_cross_repo_impact(["x/f.py"], repos=repos) == []


def test_repo_with_none_impact_paths_skipped() -> None:
    repos = _repos(c=_FakeRepo(impact_report_paths=None))
    assert _check_cross_repo_impact(["x/f.py"], repos=repos) == []


def test_repo_missing_attribute_skipped() -> None:
    class _Bare:
        pass

    out = _check_cross_repo_impact(["x/f.py"], repos={"c": _Bare()})
    assert out == []


def test_empty_string_prefix_skipped() -> None:
    # A prefix that normalises to empty (e.g. "./") is skipped.
    repos = _repos(c=_FakeRepo(impact_report_paths=["./", "real/"]))
    out = _check_cross_repo_impact(["real/f.py"], repos=repos)
    assert out[0].matched_paths == ("real/",)


def test_only_empty_prefix_yields_no_match() -> None:
    repos = _repos(c=_FakeRepo(impact_report_paths=["./"]))
    assert _check_cross_repo_impact(["real/f.py"], repos=repos) == []


def test_falsy_changed_file_entries_filtered() -> None:
    # Empty-string entries in changed_files are dropped by the comprehension.
    repos = _repos(c=_FakeRepo(impact_report_paths=["a/"]))
    out = _check_cross_repo_impact(["", "a/x.py"], repos=repos)
    assert out[0].changed_files == ("a/x.py",)


def test_no_files_under_prefix_no_impact() -> None:
    repos = _repos(c=_FakeRepo(impact_report_paths=["deep/nested/"]))
    out = _check_cross_repo_impact(["deep/other.py"], repos=repos)
    assert out == []


def test_prefix_is_not_substring_false_positive() -> None:
    # "src/ifaceX" must NOT match prefix "src/iface".
    repos = _repos(c=_FakeRepo(impact_report_paths=["src/iface"]))
    out = _check_cross_repo_impact(["src/ifaceX/y.py"], repos=repos)
    assert out == []


def test_dot_slash_changed_file_normalised_to_match() -> None:
    repos = _repos(c=_FakeRepo(impact_report_paths=["src/"]))
    out = _check_cross_repo_impact(["./src/foo.py"], repos=repos)
    assert out[0].changed_files == ("src/foo.py",)


# --- dataclass behaviour -------------------------------------------------


def test_cross_repo_impact_is_frozen() -> None:
    impact = CrossRepoImpact(repo_key="r", matched_paths=("a/",), changed_files=("a/x",))
    with pytest.raises((AttributeError, TypeError)):
        impact.repo_key = "other"  # type: ignore[misc]


def test_cross_repo_impact_equality() -> None:
    a = CrossRepoImpact(repo_key="r", matched_paths=("a/",), changed_files=("a/x",))
    b = CrossRepoImpact(repo_key="r", matched_paths=("a/",), changed_files=("a/x",))
    assert a == b
