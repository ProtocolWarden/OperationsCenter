# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Branch/edge coverage for operations_center.run_memory.index.

Complements test_run_memory.py: focuses on the tolerant accessor in
``_record_from_result``, query filter branches, rebuild edge cases, and
artifact iteration directory skips. Uses plain dicts / lightweight fakes
to stay hermetic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

from operations_center.run_memory.index import (
    RunMemoryIndexWriter,
    RunMemoryQueryService,
    _iter_result_artifacts,
    _matches,
    _record_from_result,
    deterministic_record_id,
    rebuild_index_from_artifacts,
    record_execution_result,
)
from operations_center.run_memory.models import (
    RunMemoryQuery,
    RunMemoryRecord,
    SourceType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rec(**overrides) -> RunMemoryRecord:
    base = dict(
        record_id="rmr-x",
        run_id="run-1",
        request_id="req-1",
        result_id="res-1",
        repo_id="repo-1",
        artifact_paths=("a/b.json",),
        contract_kinds=("kindA",),
        status="succeeded",
        summary="hello world",
        tags=("alpha", "beta"),
        created_at="2026-06-01T00:00:00Z",
        source_type=SourceType.EXECUTION_RESULT,
    )
    base.update(overrides)
    return RunMemoryRecord(**base)


class _Status(str, Enum):
    OK = "succeeded"


@dataclass
class _FakeResult:
    run_id: str = "run-99"
    status: object = "running"
    # other optional fields intentionally absent unless set


# ---------------------------------------------------------------------------
# deterministic_record_id
# ---------------------------------------------------------------------------


def test_deterministic_record_id_shape() -> None:
    rid = deterministic_record_id("abc")
    assert rid.startswith("rmr-")
    assert len(rid) == len("rmr-") + 16
    assert rid == deterministic_record_id("abc")
    assert rid != deterministic_record_id("abd")


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


def test_writer_creates_dir_and_path(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "idx"
    w = RunMemoryIndexWriter(target)
    assert target.is_dir()
    assert w.path == target / "records.jsonl"


def test_writer_append_then_truncate(tmp_path: Path) -> None:
    w = RunMemoryIndexWriter(tmp_path)
    w.append(_rec())
    w.append(_rec(record_id="rmr-y"))
    assert len(w.path.read_text().splitlines()) == 2
    w.truncate()
    assert w.path.read_text() == ""


# ---------------------------------------------------------------------------
# _record_from_result tolerant accessor
# ---------------------------------------------------------------------------


def test_record_from_dict_full_fields() -> None:
    data = {
        "run_id": "run-d",
        "request_id": "req-d",
        "decision_id": "dec-d",
        "result_id": "res-explicit",
        "status": "failed",
        "failure_reason": "boom",
    }
    rec = _record_from_result(
        data,
        repo_id="repo-d",
        artifact_paths=("p.json",),
        contract_kinds=("ck",),
        tags=("t",),
        summary=None,
    )
    assert rec.run_id == "run-d"
    assert rec.request_id == "req-d"
    assert rec.result_id == "res-explicit"
    assert rec.status == "failed"
    assert rec.summary == "boom"  # failure_reason wins when no summary
    assert rec.record_id == deterministic_record_id("res-explicit")


def test_record_from_dict_proposal_id_preferred_over_request_id() -> None:
    data = {"run_id": "r", "proposal_id": "prop", "request_id": "req"}
    rec = _record_from_result(
        data,
        repo_id=None,
        artifact_paths=(),
        contract_kinds=(),
        tags=(),
        summary=None,
    )
    assert rec.request_id == "prop"


def test_record_from_dict_lane_decision_id_fallback_and_derived_result_id() -> None:
    # No result_id, no decision_id, but lane_decision_id present.
    data = {"run_id": "rl", "lane_decision_id": "lane-9"}
    rec = _record_from_result(
        data,
        repo_id=None,
        artifact_paths=(),
        contract_kinds=(),
        tags=(),
        summary=None,
    )
    assert rec.result_id == "rl::lane-9"


def test_record_from_dict_no_decision_derives_no_decision_token() -> None:
    data = {"run_id": "rn"}
    rec = _record_from_result(
        data,
        repo_id=None,
        artifact_paths=(),
        contract_kinds=(),
        tags=(),
        summary=None,
    )
    assert rec.result_id == "rn::no-decision"
    # No failure_reason -> synthesized summary uses status + run id.
    assert rec.summary == "unknown: run rn"
    assert rec.status == "unknown"


def test_record_from_object_status_enum_value() -> None:
    obj = _FakeResult(run_id="ro", status=_Status.OK)
    rec = _record_from_result(
        obj,
        repo_id=None,
        artifact_paths=(),
        contract_kinds=(),
        tags=(),
        summary="explicit summary",
    )
    assert rec.status == "succeeded"
    assert rec.summary == "explicit summary"  # explicit summary preserved
    assert rec.source_type is SourceType.EXECUTION_RESULT


def test_record_from_non_dict_non_object_uses_defaults() -> None:
    # A bare value lacking attributes and not a dict exercises the final
    # ``return default`` branch of _get for every field.
    rec = _record_from_result(
        42,
        repo_id=None,
        artifact_paths=(),
        contract_kinds=(),
        tags=(),
        summary=None,
    )
    assert rec.run_id == "None"
    assert rec.status == "unknown"
    assert rec.result_id == "None::no-decision"


# ---------------------------------------------------------------------------
# record_execution_result (single write site)
# ---------------------------------------------------------------------------


def test_record_execution_result_appends_and_returns(tmp_path: Path) -> None:
    rec = record_execution_result(
        {"run_id": "rw", "result_id": "res-w"},
        tmp_path,
        repo_id="repo-w",
        artifact_paths=["x.json"],
        contract_kinds=["ck1"],
        tags=["tg"],
        summary="written",
    )
    svc = RunMemoryQueryService(tmp_path)
    stored = svc.all()
    assert len(stored) == 1
    assert stored[0].record_id == rec.record_id
    assert stored[0].summary == "written"
    assert stored[0].artifact_paths == ("x.json",)


# ---------------------------------------------------------------------------
# Query service
# ---------------------------------------------------------------------------


def test_iter_records_missing_path(tmp_path: Path) -> None:
    svc = RunMemoryQueryService(tmp_path / "absent")
    assert svc.all() == []


def test_iter_records_skips_blank_lines(tmp_path: Path) -> None:
    w = RunMemoryIndexWriter(tmp_path)
    w.append(_rec(record_id="rmr-1", result_id="r1"))
    # Inject blank/whitespace lines.
    with w.path.open("a", encoding="utf-8") as fh:
        fh.write("\n   \n")
    w.append(_rec(record_id="rmr-2", result_id="r2"))
    svc = RunMemoryQueryService(tmp_path)
    assert len(svc.all()) == 2


def test_query_sorts_by_created_at_then_record_id(tmp_path: Path) -> None:
    w = RunMemoryIndexWriter(tmp_path)
    w.append(_rec(record_id="rmr-b", created_at="2026-06-01T00:00:00Z"))
    w.append(_rec(record_id="rmr-a", created_at="2026-06-01T00:00:00Z"))
    w.append(_rec(record_id="rmr-z", created_at="2026-05-01T00:00:00Z"))
    svc = RunMemoryQueryService(tmp_path)
    ids = [r.record_id for r in svc.all()]
    assert ids == ["rmr-z", "rmr-a", "rmr-b"]


# ---------------------------------------------------------------------------
# _matches branches
# ---------------------------------------------------------------------------


def test_matches_empty_query_true() -> None:
    assert _matches(_rec(), RunMemoryQuery()) is True


def test_matches_repo_id_mismatch() -> None:
    assert _matches(_rec(repo_id="a"), RunMemoryQuery(repo_id="b")) is False


def test_matches_run_id_mismatch() -> None:
    assert _matches(_rec(run_id="a"), RunMemoryQuery(run_id="b")) is False


def test_matches_request_id_mismatch() -> None:
    assert _matches(_rec(request_id="a"), RunMemoryQuery(request_id="b")) is False


def test_matches_result_id_mismatch() -> None:
    assert _matches(_rec(result_id="a"), RunMemoryQuery(result_id="b")) is False


def test_matches_status_mismatch() -> None:
    assert _matches(_rec(status="ok"), RunMemoryQuery(status="bad")) is False


def test_matches_contract_kind_not_present() -> None:
    assert _matches(_rec(contract_kinds=("x",)), RunMemoryQuery(contract_kind="y")) is False
    assert _matches(_rec(contract_kinds=("y",)), RunMemoryQuery(contract_kind="y")) is True


def test_matches_tag_not_present() -> None:
    assert _matches(_rec(tags=("x",)), RunMemoryQuery(tag="y")) is False


def test_matches_text_searches_artifact_paths_and_repo() -> None:
    rec = _rec(summary="", repo_id="REPO-special", artifact_paths=("dir/thing.json",))
    assert _matches(rec, RunMemoryQuery(text="repo-special")) is True
    assert _matches(rec, RunMemoryQuery(text="thing.json")) is True
    assert _matches(rec, RunMemoryQuery(text="absent")) is False


def test_matches_text_none_summary_and_repo() -> None:
    # None summary/repo_id exercise the ``or ""`` fallbacks without error.
    rec = _rec(summary="", repo_id=None, run_id="runX", tags=(), artifact_paths=())
    assert _matches(rec, RunMemoryQuery(text="runx")) is True
    assert _matches(rec, RunMemoryQuery(text="nope")) is False


def test_matches_time_range_in_and_out() -> None:
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    inside = _rec(created_at="2026-06-01T12:00:00Z")
    before = _rec(created_at="2026-05-31T12:00:00Z")
    after = _rec(created_at="2026-06-03T12:00:00Z")
    q = RunMemoryQuery(time_range=(start, end))
    assert _matches(inside, q) is True
    assert _matches(before, q) is False
    assert _matches(after, q) is False


def test_matches_time_range_unparseable_created_at() -> None:
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    rec = _rec(created_at="not-a-timestamp")
    assert _matches(rec, RunMemoryQuery(time_range=(start, end))) is False


# ---------------------------------------------------------------------------
# rebuild_index_from_artifacts
# ---------------------------------------------------------------------------


def test_rebuild_missing_dir_returns_zero(tmp_path: Path) -> None:
    idx = tmp_path / "idx"
    n = rebuild_index_from_artifacts(tmp_path / "no-artifacts", idx)
    assert n == 0
    # Truncate still ran, creating an empty file.
    assert (idx / "records.jsonl").read_text() == ""


def test_rebuild_skips_bad_json_and_non_dict(tmp_path: Path) -> None:
    arts = tmp_path / "arts"
    arts.mkdir()
    (arts / "execution_result_bad.json").write_text("{not json", encoding="utf-8")
    (arts / "execution_result_list.json").write_text("[1, 2, 3]", encoding="utf-8")
    (arts / "execution_result_ok.json").write_text(
        json.dumps({"run_id": "rk", "result_id": "res-k", "status": "succeeded"}),
        encoding="utf-8",
    )
    (arts / "ignored.json").write_text(json.dumps({"run_id": "z"}), encoding="utf-8")
    n = rebuild_index_from_artifacts(arts, tmp_path / "idx")
    assert n == 1
    recs = RunMemoryQueryService(tmp_path / "idx").all()
    assert recs[0].run_id == "rk"
    assert recs[0].artifact_paths == (str(arts / "execution_result_ok.json"),)


def test_rebuild_dedupes_same_result_id(tmp_path: Path) -> None:
    arts = tmp_path / "arts"
    sub = arts / "sub"
    sub.mkdir(parents=True)
    payload = json.dumps({"run_id": "rd", "result_id": "dup"})
    (arts / "execution_result_a.json").write_text(payload, encoding="utf-8")
    (sub / "execution_result_b.json").write_text(payload, encoding="utf-8")
    n = rebuild_index_from_artifacts(arts, tmp_path / "idx")
    assert n == 1


def test_rebuild_recurses_and_skips_pycache_and_git(tmp_path: Path) -> None:
    arts = tmp_path / "arts"
    (arts / "__pycache__").mkdir(parents=True)
    (arts / ".git").mkdir()
    deep = arts / "level1" / "level2"
    deep.mkdir(parents=True)
    # Files inside skipped dirs must not be indexed.
    (arts / "__pycache__" / "execution_result_x.json").write_text(
        json.dumps({"run_id": "px", "result_id": "px"}), encoding="utf-8"
    )
    (arts / ".git" / "execution_result_g.json").write_text(
        json.dumps({"run_id": "gx", "result_id": "gx"}), encoding="utf-8"
    )
    (deep / "execution_result_deep.json").write_text(
        json.dumps({"run_id": "deep", "result_id": "deep"}), encoding="utf-8"
    )
    n = rebuild_index_from_artifacts(arts, tmp_path / "idx")
    assert n == 1
    assert RunMemoryQueryService(tmp_path / "idx").all()[0].run_id == "deep"


# ---------------------------------------------------------------------------
# _iter_result_artifacts directly
# ---------------------------------------------------------------------------


def test_iter_result_artifacts_missing_dir(tmp_path: Path) -> None:
    assert list(_iter_result_artifacts(tmp_path / "nope")) == []


def test_iter_result_artifacts_pattern_filter(tmp_path: Path) -> None:
    (tmp_path / "execution_result_1.json").write_text("{}", encoding="utf-8")
    (tmp_path / "execution_result_2.txt").write_text("x", encoding="utf-8")
    (tmp_path / "other.json").write_text("{}", encoding="utf-8")
    found = [p.name for p in _iter_result_artifacts(tmp_path)]
    assert found == ["execution_result_1.json"]
