# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json

import pytest

from operations_center import quality_alerts as qa


# ── _comment_markdown ────────────────────────────────────────────────────────


def test_comment_markdown_headline_only():
    out = qa._comment_markdown(headline="  Hello  ")
    assert out == "<!-- operations-center:bot -->\n\n**Hello**"


def test_comment_markdown_with_bullets_filters_blank():
    out = qa._comment_markdown(
        headline="Title",
        bullets=["one", "  ", "", "two"],
    )
    lines = out.split("\n")
    assert "- one" in lines
    assert "- two" in lines
    # only two bullet lines (blank/whitespace dropped)
    assert sum(1 for ln in lines if ln.startswith("- ")) == 2


def test_comment_markdown_with_none_bullet_entry():
    out = qa._comment_markdown(headline="T", bullets=[None, "kept"])  # type: ignore[list-item]
    assert "- kept" in out
    bullet_lines = [ln for ln in out.split("\n") if ln.startswith("- ")]
    assert bullet_lines == ["- kept"]


def test_comment_markdown_with_code_block():
    out = qa._comment_markdown(headline="T", code_block="  print(1)  ")
    assert "```\nprint(1)\n```" in out


def test_comment_markdown_custom_marker():
    out = qa._comment_markdown(headline="T", bot_marker="<!-- x -->")
    assert out.startswith("<!-- x -->")


def test_comment_markdown_empty_bullets_list_no_section():
    out = qa._comment_markdown(headline="T", bullets=[])
    assert out == "<!-- operations-center:bot -->\n\n**T**"


# ── _extract_rejection_patterns ──────────────────────────────────────────────


def test_extract_rejection_empty():
    assert qa._extract_rejection_patterns([]) == []


def test_extract_rejection_counts_and_orders():
    records = [
        {"reason": "Flaky Test"},
        {"reason": "flaky test"},
        {"reason": "style"},
        {"reason": "STYLE"},
        {"reason": "style"},
    ]
    out = qa._extract_rejection_patterns(records)
    # "style" appears 3x, "flaky test" 2x -> style first
    assert out[0] == "style"
    assert "flaky test" in out


def test_extract_rejection_skips_non_dict_and_blank():
    records = ["bad", 5, {}, {"reason": ""}, {"reason": "  "}, {"reason": "real"}]
    assert qa._extract_rejection_patterns(records) == ["real"]


def test_extract_rejection_top5_cap():
    records = [{"reason": f"r{i}"} for i in range(8)]
    out = qa._extract_rejection_patterns(records)
    assert len(out) == 5


def test_extract_rejection_missing_reason_key():
    assert qa._extract_rejection_patterns([{"other": "x"}]) == []


# ── _load_rejection_patterns_for_proposal ────────────────────────────────────


def _chdir(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)


def test_load_rejection_missing_catalog(monkeypatch, tmp_path):
    _chdir(monkeypatch, tmp_path)
    assert qa._load_rejection_patterns_for_proposal() == []


def test_load_rejection_unreadable_json(monkeypatch, tmp_path):
    _chdir(monkeypatch, tmp_path)
    cat = tmp_path / "state" / "proposal_rejections.json"
    cat.parent.mkdir(parents=True)
    cat.write_text("{ not json", encoding="utf-8")
    assert qa._load_rejection_patterns_for_proposal() == []


def test_load_rejection_dict_with_records(monkeypatch, tmp_path):
    _chdir(monkeypatch, tmp_path)
    cat = tmp_path / "state" / "proposal_rejections.json"
    cat.parent.mkdir(parents=True)
    cat.write_text(
        json.dumps({"records": [{"reason": "x"}, {"reason": "x"}, {"reason": "y"}]}),
        encoding="utf-8",
    )
    out = qa._load_rejection_patterns_for_proposal()
    assert out[0] == "x"


def test_load_rejection_bare_list(monkeypatch, tmp_path):
    _chdir(monkeypatch, tmp_path)
    cat = tmp_path / "state" / "proposal_rejections.json"
    cat.parent.mkdir(parents=True)
    cat.write_text(json.dumps([{"reason": "z"}]), encoding="utf-8")
    assert qa._load_rejection_patterns_for_proposal() == ["z"]


def test_load_rejection_records_not_list(monkeypatch, tmp_path):
    _chdir(monkeypatch, tmp_path)
    cat = tmp_path / "state" / "proposal_rejections.json"
    cat.parent.mkdir(parents=True)
    cat.write_text(json.dumps({"records": "nope"}), encoding="utf-8")
    assert qa._load_rejection_patterns_for_proposal() == []


def test_load_rejection_top_level_not_dict_or_list(monkeypatch, tmp_path):
    _chdir(monkeypatch, tmp_path)
    cat = tmp_path / "state" / "proposal_rejections.json"
    cat.parent.mkdir(parents=True)
    cat.write_text(json.dumps("scalar"), encoding="utf-8")
    assert qa._load_rejection_patterns_for_proposal() == []


def test_load_rejection_filters_by_repo_key(monkeypatch, tmp_path):
    _chdir(monkeypatch, tmp_path)
    cat = tmp_path / "state" / "proposal_rejections.json"
    cat.parent.mkdir(parents=True)
    cat.write_text(
        json.dumps(
            {
                "records": [
                    {"reason": "a", "repo_key": "r1"},
                    {"reason": "b", "repo_key": "r2"},
                    "junk",
                ]
            }
        ),
        encoding="utf-8",
    )
    out = qa._load_rejection_patterns_for_proposal(repo_key="r1")
    assert out == ["a"]


# ── _escalate_to_human ────────────────────────────────────────────────────────


def test_escalate_writes_jsonl(monkeypatch, tmp_path):
    log = tmp_path / "state" / "escalations.jsonl"
    monkeypatch.setattr(qa, "_ESCALATION_LOG", log)
    ok = qa._escalate_to_human(task_id="T1", reason="bad", detail="d", severity="crit")
    assert ok is True
    rec = json.loads(log.read_text(encoding="utf-8").strip())
    assert rec["task_id"] == "T1"
    assert rec["reason"] == "bad"
    assert rec["detail"] == "d"
    assert rec["severity"] == "crit"
    assert "ts" in rec


def test_escalate_appends_multiple(monkeypatch, tmp_path):
    log = tmp_path / "state" / "escalations.jsonl"
    monkeypatch.setattr(qa, "_ESCALATION_LOG", log)
    qa._escalate_to_human(task_id="A", reason="r")
    qa._escalate_to_human(task_id="B", reason="r")
    lines = log.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2


def test_escalate_truncates_detail(monkeypatch, tmp_path):
    log = tmp_path / "state" / "escalations.jsonl"
    monkeypatch.setattr(qa, "_ESCALATION_LOG", log)
    qa._escalate_to_human(task_id="T", reason="r", detail="x" * 5000)
    rec = json.loads(log.read_text(encoding="utf-8").strip())
    assert len(rec["detail"]) == 1000


def test_escalate_defaults(monkeypatch, tmp_path):
    log = tmp_path / "state" / "escalations.jsonl"
    monkeypatch.setattr(qa, "_ESCALATION_LOG", log)
    qa._escalate_to_human(task_id="T", reason="r")
    rec = json.loads(log.read_text(encoding="utf-8").strip())
    assert rec["detail"] == ""
    assert rec["severity"] == "warn"


def test_escalate_write_failure_returns_false(monkeypatch, tmp_path):
    log = tmp_path / "state" / "escalations.jsonl"
    monkeypatch.setattr(qa, "_ESCALATION_LOG", log)

    def boom(*a, **k):
        raise OSError("disk full")

    # mkdir succeeds, open raises OSError
    monkeypatch.setattr(type(log), "open", boom)
    assert qa._escalate_to_human(task_id="T", reason="r") is False


# ── _process_self_review ──────────────────────────────────────────────────────


def test_process_self_review_none():
    res, summ = qa._process_self_review(None)
    assert res == "CONCERNS"
    assert summ == "(verdict missing or malformed)"


def test_process_self_review_not_dict():
    res, summ = qa._process_self_review("nope")  # type: ignore[arg-type]
    assert res == "CONCERNS"


def test_process_self_review_lgtm():
    res, summ = qa._process_self_review({"result": " lgtm ", "summary": "ok"})
    assert res == "LGTM"
    assert summ == "ok"


def test_process_self_review_concerns_explicit():
    res, summ = qa._process_self_review({"result": "concerns", "summary": "issues"})
    assert res == "CONCERNS"
    assert summ == "issues"


def test_process_self_review_unknown_result_fails_closed():
    res, _ = qa._process_self_review({"result": "maybe", "summary": "x"})
    assert res == "CONCERNS"


def test_process_self_review_missing_summary():
    res, summ = qa._process_self_review({"result": "LGTM"})
    assert summ == "(no summary provided)"


def test_process_self_review_truncates_summary():
    res, summ = qa._process_self_review({"result": "LGTM", "summary": "y" * 500}, max_summary=400)
    assert len(summ) == 400
    assert summ.endswith("...")


def test_process_self_review_non_string_fields():
    res, summ = qa._process_self_review({"result": 123, "summary": 456})
    assert res == "CONCERNS"
    assert summ == "456"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
