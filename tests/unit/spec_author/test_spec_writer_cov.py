# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from operations_center.spec_author import spec_writer as sw_mod
from operations_center.spec_author.spec_writer import SpecWriter


def _spec_text(
    *,
    slug: str = "my-spec",
    campaign_id: str = "camp-1",
    status: str = "active",
    created_at: str | None = None,
) -> str:
    lines = [
        "---",
        f"campaign_id: {campaign_id}",
        f"slug: {slug}",
        f"status: {status}",
    ]
    if created_at is not None:
        lines.append(f"created_at: {created_at}")
    lines += ["---", "", "# Body", "content"]
    return "\n".join(lines)


def test_init_default_specs_dir() -> None:
    w = SpecWriter()
    assert w.specs_dir == Path("docs/specs")


def test_init_custom_specs_dir(tmp_path: Path) -> None:
    custom = tmp_path / "custom"
    w = SpecWriter(specs_dir=custom)
    assert w.specs_dir == custom


def test_write_creates_canonical_file(tmp_path: Path) -> None:
    specs = tmp_path / "specs"
    w = SpecWriter(specs_dir=specs)
    dest = w.write("alpha", "hello-text")
    assert dest == specs / "alpha.md"
    assert dest.read_text(encoding="utf-8") == "hello-text"


def test_write_creates_nested_specs_dir(tmp_path: Path) -> None:
    specs = tmp_path / "a" / "b" / "specs"
    w = SpecWriter(specs_dir=specs)
    dest = w.write("beta", "body")
    assert dest.exists()
    assert specs.is_dir()


def test_write_no_workspace_does_not_create_copy(tmp_path: Path) -> None:
    specs = tmp_path / "specs"
    ws = tmp_path / "ws"
    w = SpecWriter(specs_dir=specs)
    w.write("gamma", "txt", workspace_path=None)
    assert not (ws / "docs" / "specs" / "gamma.md").exists()


def test_write_with_workspace_copies(tmp_path: Path) -> None:
    specs = tmp_path / "specs"
    ws = tmp_path / "ws"
    w = SpecWriter(specs_dir=specs)
    dest = w.write("delta", "payload", workspace_path=ws)
    ws_copy = ws / "docs" / "specs" / "delta.md"
    assert dest.read_text(encoding="utf-8") == "payload"
    assert ws_copy.read_text(encoding="utf-8") == "payload"


def test_write_logs_events(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    specs = tmp_path / "specs"
    ws = tmp_path / "ws"
    w = SpecWriter(specs_dir=specs)
    with caplog.at_level("INFO", logger=sw_mod.logger.name):
        w.write("eps", "x", workspace_path=ws)
    text = caplog.text
    assert "spec_written" in text
    assert "spec_workspace_copy" in text


def test_archive_expired_moves_old_complete(tmp_path: Path) -> None:
    specs = tmp_path / "specs"
    specs.mkdir(parents=True)
    old = (datetime.now(UTC) - timedelta(days=200)).isoformat()
    (specs / "done.md").write_text(
        _spec_text(slug="done", status="complete", created_at=old),
        encoding="utf-8",
    )
    w = SpecWriter(specs_dir=specs)
    archived = w.archive_expired(retention_days=90)
    assert archived == [specs / "archive" / "done.md"]
    assert not (specs / "done.md").exists()
    assert (specs / "archive" / "done.md").exists()


def test_archive_expired_moves_old_cancelled(tmp_path: Path) -> None:
    specs = tmp_path / "specs"
    specs.mkdir(parents=True)
    old = (datetime.now(UTC) - timedelta(days=200)).isoformat()
    (specs / "stop.md").write_text(
        _spec_text(slug="stop", status="cancelled", created_at=old),
        encoding="utf-8",
    )
    w = SpecWriter(specs_dir=specs)
    archived = w.archive_expired()
    assert len(archived) == 1
    assert archived[0].name == "stop.md"


def test_archive_expired_skips_active_status(tmp_path: Path) -> None:
    specs = tmp_path / "specs"
    specs.mkdir(parents=True)
    old = (datetime.now(UTC) - timedelta(days=200)).isoformat()
    (specs / "live.md").write_text(
        _spec_text(slug="live", status="active", created_at=old),
        encoding="utf-8",
    )
    w = SpecWriter(specs_dir=specs)
    archived = w.archive_expired()
    assert archived == []
    assert (specs / "live.md").exists()


def test_archive_expired_skips_recent_complete(tmp_path: Path) -> None:
    specs = tmp_path / "specs"
    specs.mkdir(parents=True)
    recent = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    (specs / "fresh.md").write_text(
        _spec_text(slug="fresh", status="complete", created_at=recent),
        encoding="utf-8",
    )
    w = SpecWriter(specs_dir=specs)
    archived = w.archive_expired(retention_days=90)
    assert archived == []
    assert (specs / "fresh.md").exists()


def test_archive_expired_skips_unparseable(tmp_path: Path) -> None:
    specs = tmp_path / "specs"
    specs.mkdir(parents=True)
    (specs / "bad.md").write_text("no front matter here", encoding="utf-8")
    w = SpecWriter(specs_dir=specs)
    archived = w.archive_expired()
    assert archived == []
    assert (specs / "bad.md").exists()


def test_archive_expired_no_created_at_archives(tmp_path: Path) -> None:
    specs = tmp_path / "specs"
    specs.mkdir(parents=True)
    # created_at defaults to "" → falsy → skip the date check, archive it.
    (specs / "nodate.md").write_text(
        _spec_text(slug="nodate", status="complete", created_at=None),
        encoding="utf-8",
    )
    w = SpecWriter(specs_dir=specs)
    archived = w.archive_expired()
    assert len(archived) == 1
    assert (specs / "archive" / "nodate.md").exists()


def test_archive_expired_invalid_created_at_archives(tmp_path: Path) -> None:
    specs = tmp_path / "specs"
    specs.mkdir(parents=True)
    # Non-ISO created_at → ValueError caught → falls through and archives.
    (specs / "weird.md").write_text(
        _spec_text(slug="weird", status="complete", created_at="not-a-date"),
        encoding="utf-8",
    )
    w = SpecWriter(specs_dir=specs)
    archived = w.archive_expired()
    assert len(archived) == 1
    assert (specs / "archive" / "weird.md").exists()


def test_archive_expired_empty_dir(tmp_path: Path) -> None:
    specs = tmp_path / "specs"
    specs.mkdir(parents=True)
    w = SpecWriter(specs_dir=specs)
    archived = w.archive_expired()
    assert archived == []
    assert not (specs / "archive").exists()


def test_archive_expired_logs_event(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    specs = tmp_path / "specs"
    specs.mkdir(parents=True)
    old = (datetime.now(UTC) - timedelta(days=200)).isoformat()
    (specs / "done.md").write_text(
        _spec_text(slug="done", status="complete", created_at=old),
        encoding="utf-8",
    )
    w = SpecWriter(specs_dir=specs)
    with caplog.at_level("INFO", logger=sw_mod.logger.name):
        w.archive_expired()
    assert "spec_archived" in caplog.text


def test_update_front_matter_status_missing_file_noop(tmp_path: Path) -> None:
    specs = tmp_path / "specs"
    specs.mkdir(parents=True)
    w = SpecWriter(specs_dir=specs)
    # Should not raise and should not create the file.
    w.update_front_matter_status("ghost", "complete")
    assert not (specs / "ghost.md").exists()


def test_update_front_matter_status_replaces(tmp_path: Path) -> None:
    specs = tmp_path / "specs"
    specs.mkdir(parents=True)
    (specs / "s.md").write_text(_spec_text(slug="s", status="active"), encoding="utf-8")
    w = SpecWriter(specs_dir=specs)
    w.update_front_matter_status("s", "complete")
    new_text = (specs / "s.md").read_text(encoding="utf-8")
    assert "status: complete" in new_text
    assert "status: active" not in new_text


def test_update_front_matter_status_only_first_occurrence(tmp_path: Path) -> None:
    specs = tmp_path / "specs"
    specs.mkdir(parents=True)
    body = "---\nstatus: active\n---\nbody status: active here\n"
    (specs / "m.md").write_text(body, encoding="utf-8")
    w = SpecWriter(specs_dir=specs)
    w.update_front_matter_status("m", "complete")
    new_text = (specs / "m.md").read_text(encoding="utf-8")
    # Only the first "status: active" is replaced.
    assert new_text.count("status: active") == 1
    assert "status: complete" in new_text


def test_update_front_matter_status_no_match_unchanged(tmp_path: Path) -> None:
    specs = tmp_path / "specs"
    specs.mkdir(parents=True)
    original = "---\nstatus: complete\n---\nbody\n"
    (specs / "u.md").write_text(original, encoding="utf-8")
    w = SpecWriter(specs_dir=specs)
    w.update_front_matter_status("u", "cancelled")
    assert (specs / "u.md").read_text(encoding="utf-8") == original
