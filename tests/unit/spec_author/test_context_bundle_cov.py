# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from operations_center.spec_author.context_bundle import (
    ContextBundle,
    ContextBundleBuilder,
)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _issue(
    name: str, state: str | None, updated: str | None = None, created: str | None = None
) -> dict:
    out: dict = {"name": name}
    if state is not None:
        out["state"] = {"name": state}
    if updated is not None:
        out["updated_at"] = updated
    if created is not None:
        out["created_at"] = created
    return out


def test_dataclass_holds_fields() -> None:
    bundle = ContextBundle(
        git_logs={"r": "log"},
        specs_index=[{"slug": "s"}],
        recent_done_tasks=[],
        recent_cancelled_tasks=[],
        open_task_count=3,
        seed_text="seed",
        available_repos=["r"],
    )
    assert bundle.open_task_count == 3
    assert bundle.git_logs == {"r": "log"}
    assert bundle.seed_text == "seed"


def test_build_categorizes_done_cancelled_open() -> None:
    now = datetime.now(UTC)
    recent = _iso(now - timedelta(days=1))
    issues = [
        _issue("d1", "Done", updated=recent),
        _issue("c1", "Cancelled", updated=recent),
        _issue("o1", "In Progress", updated=recent),
        _issue("o2", "Backlog", updated=recent),
    ]
    builder = ContextBundleBuilder()
    bundle = builder.build(
        seed_text="seed",
        board_issues=issues,
        specs_index=[{"slug": "a"}],
        git_logs={"repo": "g"},
        available_repos=["repo"],
    )
    assert [t["name"] for t in bundle.recent_done_tasks] == ["d1"]
    assert [t["name"] for t in bundle.recent_cancelled_tasks] == ["c1"]
    assert bundle.open_task_count == 2
    assert bundle.specs_index == [{"slug": "a"}]
    assert bundle.git_logs == {"repo": "g"}
    assert bundle.available_repos == ["repo"]
    assert bundle.seed_text == "seed"


def test_build_old_done_cancelled_excluded() -> None:
    now = datetime.now(UTC)
    old = _iso(now - timedelta(days=30))
    issues = [
        _issue("d_old", "done", updated=old),
        _issue("c_old", "cancelled", updated=old),
    ]
    bundle = ContextBundleBuilder().build(
        seed_text="",
        board_issues=issues,
        specs_index=[],
        git_logs={},
        available_repos=[],
    )
    # Old done/cancelled tasks are not "open" (state in {done,cancelled}) so open stays 0.
    assert bundle.recent_done_tasks == []
    assert bundle.recent_cancelled_tasks == []
    assert bundle.open_task_count == 0


def test_build_uses_created_at_when_no_updated() -> None:
    now = datetime.now(UTC)
    recent = _iso(now - timedelta(days=2))
    issues = [_issue("d", "done", created=recent)]
    bundle = ContextBundleBuilder().build(
        seed_text="",
        board_issues=issues,
        specs_index=[],
        git_logs={},
        available_repos=[],
    )
    assert [t["name"] for t in bundle.recent_done_tasks] == ["d"]


def test_build_bad_timestamp_falls_back_to_min() -> None:
    issues = [_issue("d", "done", updated="not-a-date")]
    bundle = ContextBundleBuilder().build(
        seed_text="",
        board_issues=issues,
        specs_index=[],
        git_logs={},
        available_repos=[],
    )
    # datetime.min is far before cutoff -> excluded from recent_done
    assert bundle.recent_done_tasks == []
    assert bundle.open_task_count == 0


def test_build_missing_timestamp_uses_empty_string_fallback() -> None:
    issues = [_issue("d", "done")]  # no updated_at/created_at
    bundle = ContextBundleBuilder().build(
        seed_text="",
        board_issues=issues,
        specs_index=[],
        git_logs={},
        available_repos=[],
    )
    assert bundle.recent_done_tasks == []


def test_build_missing_state_treated_as_open() -> None:
    issues = [{"name": "x"}]  # no state key
    bundle = ContextBundleBuilder().build(
        seed_text="",
        board_issues=issues,
        specs_index=[],
        git_logs={},
        available_repos=[],
    )
    assert bundle.open_task_count == 1


def test_build_truncates_specs_and_board() -> None:
    now = datetime.now(UTC)
    recent = _iso(now - timedelta(days=1))
    # 60 open issues, but only first 50 are processed.
    issues = [_issue(f"o{i}", "open", updated=recent) for i in range(60)]
    specs = [{"slug": f"s{i}"} for i in range(80)]
    bundle = ContextBundleBuilder().build(
        seed_text="",
        board_issues=issues,
        specs_index=specs,
        git_logs={},
        available_repos=[],
    )
    assert bundle.open_task_count == ContextBundleBuilder._MAX_BOARD_TASKS == 50
    assert len(bundle.specs_index) == ContextBundleBuilder._MAX_SPECS == 50


def test_build_empty_inputs() -> None:
    bundle = ContextBundleBuilder().build(
        seed_text="",
        board_issues=[],
        specs_index=[],
        git_logs={},
        available_repos=[],
    )
    assert bundle.recent_done_tasks == []
    assert bundle.recent_cancelled_tasks == []
    assert bundle.open_task_count == 0


def test_collect_git_log_success() -> None:
    fake = mock.Mock()
    fake.stdout = "  abc123 commit\n"
    with mock.patch(
        "operations_center.spec_author.context_bundle.subprocess.run",
        return_value=fake,
    ) as run:
        out = ContextBundleBuilder.collect_git_log(Path("/repo"), n=5)
    assert out == "abc123 commit"
    args, kwargs = run.call_args
    assert args[0] == ["git", "log", "--oneline", "-5"]
    assert kwargs["cwd"] == Path("/repo")
    assert kwargs["timeout"] == 15


def test_collect_git_log_default_n() -> None:
    fake = mock.Mock()
    fake.stdout = "x"
    with mock.patch(
        "operations_center.spec_author.context_bundle.subprocess.run",
        return_value=fake,
    ) as run:
        ContextBundleBuilder.collect_git_log(Path("/repo"))
    assert run.call_args[0][0] == ["git", "log", "--oneline", "-30"]


def test_collect_git_log_exception_returns_empty() -> None:
    with mock.patch(
        "operations_center.spec_author.context_bundle.subprocess.run",
        side_effect=OSError("boom"),
    ):
        out = ContextBundleBuilder.collect_git_log(Path("/repo"))
    assert out == ""


def test_collect_specs_index_parses_front_matter(tmp_path: Path) -> None:
    (tmp_path / "alpha.md").write_text("alpha-content", encoding="utf-8")
    (tmp_path / "beta.md").write_text("beta-content", encoding="utf-8")

    def _from_text(text: str):
        fm = mock.Mock()
        fm.slug = text.split("-")[0] + "-slug"
        fm.status = "active"
        return fm

    with mock.patch(
        "operations_center.spec_author.models.SpecFrontMatter.from_spec_text",
        side_effect=_from_text,
    ):
        index = ContextBundleBuilder.collect_specs_index(tmp_path)
    # sorted glob -> alpha then beta
    assert index == [
        {"slug": "alpha-slug", "status": "active"},
        {"slug": "beta-slug", "status": "active"},
    ]


def test_collect_specs_index_skips_archive_dir(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    # glob("*.md") on tmp_path won't see nested files; create an archive-named
    # scenario by using a spec whose parent is "archive".
    (archive / "old.md").write_text("x", encoding="utf-8")
    (tmp_path / "live.md").write_text("y", encoding="utf-8")

    with mock.patch(
        "operations_center.spec_author.models.SpecFrontMatter.from_spec_text",
        return_value=mock.Mock(slug="live", status="active"),
    ):
        # Glob the archive dir directly to exercise the parent.name == "archive" skip.
        index = ContextBundleBuilder.collect_specs_index(archive)
    assert index == []


def test_collect_specs_index_parse_failure_fallback(tmp_path: Path) -> None:
    (tmp_path / "broken.md").write_text("garbage", encoding="utf-8")
    with mock.patch(
        "operations_center.spec_author.models.SpecFrontMatter.from_spec_text",
        side_effect=ValueError("bad front matter"),
    ):
        index = ContextBundleBuilder.collect_specs_index(tmp_path)
    assert index == [{"slug": "broken", "status": "unknown"}]


def test_collect_specs_index_empty_dir(tmp_path: Path) -> None:
    assert ContextBundleBuilder.collect_specs_index(tmp_path) == []


def test_collect_specs_index_ignores_non_md(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("x", encoding="utf-8")
    assert ContextBundleBuilder.collect_specs_index(tmp_path) == []
