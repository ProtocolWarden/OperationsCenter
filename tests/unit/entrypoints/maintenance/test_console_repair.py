# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Self-heal: auto-repair dropped .console/task.md sections on open goal PRs."""

from __future__ import annotations

from operations_center.entrypoints.maintenance.console_repair import (
    missing_required_sections,
    repair_console_structure,
    repair_task_md,
)

_GOOD = "# Current Task\n\n## Objective\n\nx\n\n## Overall Plan\n\ny\n\n## Current Stage\n\nz\n"
_MISSING_OBJ = "# Current Task\n\n## Overall Plan\n\ny\n\n## Current Stage\n\nz\n"


def test_missing_sections_detects_dropped_objective():
    assert missing_required_sections(_MISSING_OBJ) == ["Objective"]
    assert missing_required_sections(_GOOD) == []


def test_repair_inserts_missing_heading_after_h1():
    out = repair_task_md(_MISSING_OBJ)
    assert out is not None
    assert "## Objective" in out
    assert missing_required_sections(out) == []  # now structurally valid
    # inserted right after the H1, body preserved
    assert out.splitlines()[0] == "# Current Task"
    assert "## Overall Plan" in out and "## Current Stage" in out


def test_repair_noop_on_valid_task_md():
    assert repair_task_md(_GOOD) is None


def test_repair_handles_all_missing():
    out = repair_task_md("# Current Task\n\nsome prose\n")
    assert out is not None
    assert missing_required_sections(out) == []


class _FakeGH:
    def __init__(self, files: dict[str, tuple[str, str]]):
        self.files = files  # branch -> (text, sha)
        self.updates: list[dict] = []

    def get_file_content(self, owner, repo, path, ref):
        return self.files.get(ref)

    def update_file(self, owner, repo, path, *, new_text, message, branch, blob_sha):
        self.updates.append({"branch": branch, "text": new_text, "sha": blob_sha})
        return True


def _pr(number, ref):
    return {"number": number, "head": {"ref": ref}}


def test_repair_console_structure_fixes_goal_pr():
    gh = _FakeGH({"goal/abc": (_MISSING_OBJ, "sha1")})
    actions = repair_console_structure(gh, "o", "r", [_pr(10, "goal/abc")])
    assert len(actions) == 1
    a = actions[0]
    assert a["pr_number"] == 10 and a["missing_sections"] == ["Objective"] and a["repaired"] is True
    assert len(gh.updates) == 1
    assert "## Objective" in gh.updates[0]["text"]


def test_repair_skips_non_goal_branches():
    gh = _FakeGH({"feature/x": (_MISSING_OBJ, "s")})
    assert repair_console_structure(gh, "o", "r", [_pr(1, "feature/x")]) == []
    assert gh.updates == []


def test_repair_skips_valid_and_missing_console():
    gh = _FakeGH({"goal/ok": (_GOOD, "s")})  # valid → no repair
    assert repair_console_structure(gh, "o", "r", [_pr(2, "goal/ok")]) == []
    # branch with no .console/task.md (get returns None) → skipped, no crash
    gh2 = _FakeGH({})
    assert repair_console_structure(gh2, "o", "r", [_pr(3, "goal/none")]) == []


def test_repair_handles_improve_prefix():
    gh = _FakeGH({"improve/y": (_MISSING_OBJ, "s")})
    actions = repair_console_structure(gh, "o", "r", [_pr(4, "improve/y")])
    assert len(actions) == 1 and actions[0]["repaired"] is True
