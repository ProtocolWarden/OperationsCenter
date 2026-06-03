# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from operations_center.entrypoints.propagation_links import main as mod


def _write_record(records_dir: Path, name: str, payload: dict) -> Path:
    records_dir.mkdir(parents=True, exist_ok=True)
    p = records_dir / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _record(**overrides: object) -> dict:
    base = {
        "propagator_run_id": "run-abcdef123456789",
        "triggered_at": "2026-06-01T00:00:00Z",
        "target_repo_id": "cxrp",
        "target_canonical": "cxrp/contract",
        "target_version": "v1",
        "policy_summary": {"k": "v"},
        "impact_summary": {"n": 2},
        "outcomes": [
            {
                "decision_action": "create",
                "consumer_canonical": "consumer-a",
                "decision_reason": "needs update",
                "issue_id": "ISSUE-1",
            },
            {
                "decision_action": "skip",
                "consumer_canonical": "consumer-b",
                "decision_reason": "no change",
            },
        ],
    }
    base.update(overrides)
    return base


def _ns(**overrides: object) -> SimpleNamespace:
    defaults = {"json_output": False}
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# --------------------------------------------------------------------------
# _build_parser
# --------------------------------------------------------------------------


def test_build_parser_list_defaults() -> None:
    args = mod._build_parser().parse_args(["list"])
    assert args.cmd == "list"
    assert args.config == Path("config/operations_center.local.yaml")
    assert args.records_dir is None
    assert args.json_output is False


def test_build_parser_show_requires_run_id() -> None:
    args = mod._build_parser().parse_args(["show", "run-1"])
    assert args.cmd == "show"
    assert args.run_id == "run-1"


def test_build_parser_latest_target_and_json() -> None:
    args = mod._build_parser().parse_args(["--json", "latest", "--target", "cxrp"])
    assert args.cmd == "latest"
    assert args.target == "cxrp"
    assert args.json_output is True


def test_build_parser_subcommand_required() -> None:
    with pytest.raises(SystemExit):
        mod._build_parser().parse_args([])


def test_build_parser_latest_missing_target() -> None:
    with pytest.raises(SystemExit):
        mod._build_parser().parse_args(["latest"])


# --------------------------------------------------------------------------
# _resolve_records_dir
# --------------------------------------------------------------------------


def test_resolve_records_dir_explicit_override() -> None:
    override = Path("/tmp/snapshot")
    args = _ns(records_dir=override, config=Path("ignored.yaml"))
    assert mod._resolve_records_dir(args) == override


def test_resolve_records_dir_config_missing(tmp_path, capsys) -> None:
    args = _ns(records_dir=None, config=tmp_path / "absent.yaml")
    assert mod._resolve_records_dir(args) is None
    assert "config not found" in capsys.readouterr().err


def test_resolve_records_dir_settings_load_failure(tmp_path, capsys, monkeypatch) -> None:
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("x: 1", encoding="utf-8")

    def _boom(_path: object) -> object:
        raise RuntimeError("bad yaml")

    monkeypatch.setattr(mod, "load_settings", _boom)
    args = _ns(records_dir=None, config=cfg)
    assert mod._resolve_records_dir(args) is None
    assert "settings load failed: bad yaml" in capsys.readouterr().err


def test_resolve_records_dir_absolute_from_settings(tmp_path, monkeypatch) -> None:
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("x: 1", encoding="utf-8")
    abs_dir = tmp_path / "abs_records"
    settings = SimpleNamespace(contract_change_propagation=SimpleNamespace(record_dir=abs_dir))
    monkeypatch.setattr(mod, "load_settings", lambda _p: settings)
    args = _ns(records_dir=None, config=cfg)
    assert mod._resolve_records_dir(args) == abs_dir


def test_resolve_records_dir_relative_joined_to_cwd(tmp_path, monkeypatch) -> None:
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("x: 1", encoding="utf-8")
    rel = Path("state/prop")
    settings = SimpleNamespace(contract_change_propagation=SimpleNamespace(record_dir=rel))
    monkeypatch.setattr(mod, "load_settings", lambda _p: settings)
    monkeypatch.chdir(tmp_path)
    args = _ns(records_dir=None, config=cfg)
    assert mod._resolve_records_dir(args) == tmp_path / rel


# --------------------------------------------------------------------------
# _load_all
# --------------------------------------------------------------------------


def test_load_all_missing_dir_returns_empty(tmp_path) -> None:
    assert mod._load_all(tmp_path / "nope") == []


def test_load_all_sorts_newest_first(tmp_path) -> None:
    rd = tmp_path / "records"
    _write_record(rd, "a.json", _record(triggered_at="2026-01-01T00:00:00Z", x="old"))
    _write_record(rd, "b.json", _record(triggered_at="2026-12-31T00:00:00Z", x="new"))
    out = mod._load_all(rd)
    assert [r["x"] for r in out] == ["new", "old"]


def test_load_all_skips_invalid_json(tmp_path) -> None:
    rd = tmp_path / "records"
    rd.mkdir()
    (rd / "bad.json").write_text("{not json", encoding="utf-8")
    _write_record(rd, "good.json", _record(x="ok"))
    out = mod._load_all(rd)
    assert len(out) == 1
    assert out[0]["x"] == "ok"


def test_load_all_skips_non_dict_payload(tmp_path) -> None:
    rd = tmp_path / "records"
    rd.mkdir()
    (rd / "list.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert mod._load_all(rd) == []


def test_load_all_missing_triggered_at_treated_as_empty(tmp_path) -> None:
    rd = tmp_path / "records"
    rec_no_ts = _record(x="nots")
    del rec_no_ts["triggered_at"]
    _write_record(rd, "a.json", rec_no_ts)
    _write_record(rd, "b.json", _record(triggered_at="2026-05-01T00:00:00Z", x="ts"))
    out = mod._load_all(rd)
    # the one with a timestamp sorts ahead of the empty-string one
    assert out[0]["x"] == "ts"
    assert out[1]["x"] == "nots"


# --------------------------------------------------------------------------
# _cmd_list
# --------------------------------------------------------------------------


def test_cmd_list_human(capsys) -> None:
    rc = mod._cmd_list([_record()], _ns(json_output=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "target=cxrp/contract" in out
    assert "version=v1" in out
    assert "fired=1/2" in out


def test_cmd_list_json(capsys) -> None:
    rec = _record()
    rc = mod._cmd_list([rec], _ns(json_output=True))
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed[0]["propagator_run_id"] == rec["propagator_run_id"]


def test_cmd_list_human_missing_fields(capsys) -> None:
    rc = mod._cmd_list([{}], _ns(json_output=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "target=?" in out
    assert "fired=0/0" in out


# --------------------------------------------------------------------------
# _cmd_show
# --------------------------------------------------------------------------


def test_cmd_show_no_match(capsys) -> None:
    rc = mod._cmd_show([_record()], "zzz", _ns(json_output=False))
    assert rc == 1
    assert "no run matches" in capsys.readouterr().out


def test_cmd_show_no_match_json(capsys) -> None:
    rc = mod._cmd_show([_record()], "zzz", _ns(json_output=True))
    assert rc == 1
    assert json.loads(capsys.readouterr().out)["error"].startswith("no run matches")


def test_cmd_show_ambiguous_prefix(capsys) -> None:
    recs = [
        _record(propagator_run_id="run-1aaa"),
        _record(propagator_run_id="run-1bbb"),
    ]
    rc = mod._cmd_show(recs, "run-1", _ns(json_output=False))
    assert rc == 1
    assert "ambiguous: 2 matches" in capsys.readouterr().out


def test_cmd_show_ambiguous_prefix_json(capsys) -> None:
    recs = [
        _record(propagator_run_id="run-1aaa"),
        _record(propagator_run_id="run-1bbb"),
    ]
    rc = mod._cmd_show(recs, "run-1", _ns(json_output=True))
    assert rc == 1
    assert "ambiguous prefix" in json.loads(capsys.readouterr().out)["error"]


def test_cmd_show_unique_human(capsys) -> None:
    rec = _record(propagator_run_id="run-unique-xyz")
    rc = mod._cmd_show([rec], "run-unique", _ns(json_output=False))
    assert rc == 0
    assert "propagation run: run-unique-xyz" in capsys.readouterr().out


def test_cmd_show_unique_json(capsys) -> None:
    rec = _record(propagator_run_id="run-unique-json")
    rc = mod._cmd_show([rec], "run-unique-json", _ns(json_output=True))
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["propagator_run_id"] == "run-unique-json"


# --------------------------------------------------------------------------
# _cmd_latest
# --------------------------------------------------------------------------


def test_cmd_latest_no_match(capsys) -> None:
    rc = mod._cmd_latest([_record()], "nope", _ns(json_output=False))
    assert rc == 1
    assert "no records for 'nope'" in capsys.readouterr().out


def test_cmd_latest_no_match_json(capsys) -> None:
    rc = mod._cmd_latest([_record()], "nope", _ns(json_output=True))
    assert rc == 1
    assert "no records for target" in json.loads(capsys.readouterr().out)["error"]


def test_cmd_latest_match_by_repo_id(capsys) -> None:
    rec = _record(target_repo_id="CXRP", target_canonical="other")
    rc = mod._cmd_latest([rec], "cxrp", _ns(json_output=False))
    assert rc == 0
    assert "propagation run:" in capsys.readouterr().out


def test_cmd_latest_match_by_canonical(capsys) -> None:
    rec = _record(target_repo_id="x", target_canonical="MyCanon")
    rc = mod._cmd_latest([rec], "mycanon", _ns(json_output=True))
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["target_canonical"] == "MyCanon"


def test_cmd_latest_returns_first_newest(capsys) -> None:
    recs = [
        _record(propagator_run_id="newest"),
        _record(propagator_run_id="older"),
    ]
    rc = mod._cmd_latest(recs, "cxrp", _ns(json_output=True))
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["propagator_run_id"] == "newest"


# --------------------------------------------------------------------------
# _print_human
# --------------------------------------------------------------------------


def test_print_human_with_issue_and_error(capsys) -> None:
    rec = _record(
        outcomes=[
            {
                "decision_action": "create",
                "consumer_canonical": "c1",
                "decision_reason": "go",
                "issue_id": "ISS-9",
                "error": "boom",
            },
        ]
    )
    mod._print_human(rec)
    out = capsys.readouterr().out
    assert "→ issue=ISS-9" in out
    assert "(error: boom)" in out
    assert "[create] c1: go" in out


def test_print_human_no_issue_no_error(capsys) -> None:
    rec = _record(
        outcomes=[
            {
                "decision_action": "skip",
                "consumer_canonical": "c2",
                "decision_reason": "nope",
            }
        ]
    )
    mod._print_human(rec)
    out = capsys.readouterr().out
    assert "[skip] c2: nope" in out
    assert "issue=" not in out
    assert "error:" not in out


def test_print_human_empty_outcomes(capsys) -> None:
    rec = _record(outcomes=[])
    mod._print_human(rec)
    out = capsys.readouterr().out
    assert "propagation run:" in out
    assert "[" not in out.split("impact:")[-1]


# --------------------------------------------------------------------------
# _emit
# --------------------------------------------------------------------------


def test_emit_plain(capsys) -> None:
    mod._emit(_ns(json_output=False), {"error": "x"}, plain="plain-msg")
    assert capsys.readouterr().out.strip() == "plain-msg"


def test_emit_json(capsys) -> None:
    mod._emit(_ns(json_output=True), {"error": "x"}, plain="plain-msg")
    assert json.loads(capsys.readouterr().out)["error"] == "x"


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def test_main_resolve_returns_none_exit_2(monkeypatch) -> None:
    monkeypatch.setattr(mod, "_resolve_records_dir", lambda _a: None)
    assert mod.main(["list"]) == 2


def test_main_no_records_exit_1(tmp_path, monkeypatch, capsys) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(mod, "_resolve_records_dir", lambda _a: empty)
    rc = mod.main(["list"])
    assert rc == 1
    assert "no records in" in capsys.readouterr().out


def test_main_no_records_json_exit_1(tmp_path, monkeypatch, capsys) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(mod, "_resolve_records_dir", lambda _a: empty)
    rc = mod.main(["--json", "list"])
    assert rc == 1
    assert "no records found" in json.loads(capsys.readouterr().out)["error"]


def test_main_list_dispatch(tmp_path, monkeypatch, capsys) -> None:
    rd = tmp_path / "rec"
    _write_record(rd, "a.json", _record())
    monkeypatch.setattr(mod, "_resolve_records_dir", lambda _a: rd)
    rc = mod.main(["list"])
    assert rc == 0
    assert "fired=1/2" in capsys.readouterr().out


def test_main_show_dispatch(tmp_path, monkeypatch, capsys) -> None:
    rd = tmp_path / "rec"
    _write_record(rd, "a.json", _record(propagator_run_id="run-show-me"))
    monkeypatch.setattr(mod, "_resolve_records_dir", lambda _a: rd)
    rc = mod.main(["show", "run-show-me"])
    assert rc == 0
    assert "run-show-me" in capsys.readouterr().out


def test_main_latest_dispatch(tmp_path, monkeypatch, capsys) -> None:
    rd = tmp_path / "rec"
    _write_record(rd, "a.json", _record())
    monkeypatch.setattr(mod, "_resolve_records_dir", lambda _a: rd)
    rc = mod.main(["latest", "--target", "cxrp"])
    assert rc == 0
    assert "propagation run:" in capsys.readouterr().out


def test_main_unknown_cmd_returns_2(tmp_path, monkeypatch) -> None:
    rd = tmp_path / "rec"
    _write_record(rd, "a.json", _record())
    monkeypatch.setattr(mod, "_resolve_records_dir", lambda _a: rd)

    real_parse = mod._build_parser

    def _fake_parser() -> object:
        parser = real_parse()
        orig = parser.parse_args

        def _patched(argv: object = None) -> object:
            ns = orig(["list"])
            ns.cmd = "bogus"
            return ns

        parser.parse_args = _patched  # type: ignore[assignment]
        return parser

    monkeypatch.setattr(mod, "_build_parser", _fake_parser)
    assert mod.main(["list"]) == 2
