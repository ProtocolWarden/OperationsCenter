# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for automatic `.console/log.md` rotation.

The behaviour that matters most here is the refusal: a rotation rewrites the
whole file, and a wholesale rewrite is the operation that silently destroys log
history when rebased across another change to the same file. On 2026-08-17 that
nearly erased six entries, and nothing caught it — no conflict, and no finding,
because OC2 measures size and a rotation is meant to shrink the file. So the
census that gates every write is tested directly, not just the happy path.
"""

from __future__ import annotations

import pytest

from operations_center.entrypoints.maintenance import console_log_rotate as clr


def _log(n_entries: int, body_bytes: int = 1000, start_day: int = 1) -> str:
    out = []
    for i in range(n_entries):
        day = start_day + (i % 27)
        out.append(f"## 2026-06-{day:02d} — entry {i}\n\n" + ("x" * body_bytes) + "\n\n")
    return "".join(out)


def _write(tmp_path, text):
    p = tmp_path / ".console"
    p.mkdir(parents=True, exist_ok=True)
    f = p / "log.md"
    f.write_text(text, encoding="utf-8")
    return f


def test_no_rotation_below_the_threshold(tmp_path):
    f = _write(tmp_path, _log(5))
    p = clr.plan(f, budget=512_000)
    assert not p.needed
    assert p.headroom > 0


def test_rotation_is_due_above_the_threshold(tmp_path):
    f = _write(tmp_path, _log(60, body_bytes=1000))
    p = clr.plan(f, budget=50_000, warn_ratio=0.80)
    assert p.needed
    assert p.archive, "nothing selected for archiving"
    assert p.keep, "rotation must not empty the log"


def test_every_entry_survives_rotation(tmp_path):
    """The core guarantee: rotation moves history, it never drops it."""
    text = _log(60, body_bytes=1000)
    f = _write(tmp_path, text)
    before = {clr._heading(e) for e in clr._entries(text)}

    p = clr.plan(f, budget=50_000)
    clr.apply(f, p, repo_root=tmp_path)

    after_log = {clr._heading(e) for e in clr._entries(f.read_text(encoding="utf-8"))}
    archive = tmp_path / p.archive_path
    after_arch = {clr._heading(e) for e in clr._entries(archive.read_text(encoding="utf-8"))}

    assert before == (after_log | after_arch), "entries were lost or invented"
    assert not (after_log & after_arch), "an entry is duplicated across log and archive"


def test_rotation_brings_the_file_under_budget(tmp_path):
    f = _write(tmp_path, _log(60, body_bytes=1000))
    p = clr.plan(f, budget=50_000)
    clr.apply(f, p, repo_root=tmp_path)
    assert f.stat().st_size < 50_000


def test_refuses_to_write_when_an_entry_would_be_lost(tmp_path, monkeypatch):
    """If the census fails, nothing is written and the log is left untouched.

    Modelled on the real hazard rather than an artificial one: a bug in the
    retained-log construction drops entries, and the result still looks
    plausible — smaller, well-formed, no conflict, no finding. The census is the
    only thing standing between that and lost history.
    """
    f = _write(tmp_path, _log(60, body_bytes=1000))
    original = f.read_text(encoding="utf-8")
    p = clr.plan(f, budget=50_000)

    # Retained entries silently vanish while the archive is written correctly.
    monkeypatch.setattr(clr, "_pointer_block", lambda existing, path, count: "")

    with pytest.raises(RuntimeError, match="refusing to rotate"):
        clr.apply(f, p, repo_root=tmp_path)

    assert f.read_text(encoding="utf-8") == original, "log was modified despite the refusal"


def test_archive_is_linked_so_it_is_not_an_orphan(tmp_path):
    """An unlinked archive under docs/ is a DC7 orphan and fails the audit."""
    f = _write(tmp_path, _log(60, body_bytes=1000))
    p = clr.plan(f, budget=50_000)
    clr.apply(f, p, repo_root=tmp_path)
    log_text = f.read_text(encoding="utf-8")
    assert p.archive_path.name in log_text, "archive is not linked from the log"


def test_second_rotation_is_additive(tmp_path):
    """Rotating again must not orphan the first archive's link."""
    f = _write(tmp_path, _log(60, body_bytes=1000))
    p1 = clr.plan(f, budget=50_000)
    clr.apply(f, p1, repo_root=tmp_path)
    first = p1.archive_path.name

    f.write_text(f.read_text(encoding="utf-8") + _log(40, body_bytes=1000, start_day=1),
                 encoding="utf-8")
    p2 = clr.plan(f, budget=50_000)
    if p2.needed:
        clr.apply(f, p2, repo_root=tmp_path)
        assert first in f.read_text(encoding="utf-8"), "first archive lost its link"


def test_budget_matches_the_detector_that_enforces_it():
    """A drifted constant would rotate to the wrong target and still fail OC2."""
    assert clr.BUDGET_BYTES == 512_000
    assert 0 < clr.TARGET_RATIO < clr.WARN_RATIO < 1.0
