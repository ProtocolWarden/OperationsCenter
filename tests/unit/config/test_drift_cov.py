# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from pathlib import Path

import pytest

from operations_center.config.drift import detect_config_drift


def _write(p: Path, text: str) -> Path:
    p.write_text(text, encoding="utf-8")
    return p


def test_no_drift_when_identical(tmp_path):
    cfg = _write(tmp_path / "cfg.yaml", "a: 1\nb:\n  x: 2\n")
    ex = _write(tmp_path / "ex.yaml", "a: 1\nb:\n  x: 2\n")
    assert detect_config_drift(cfg, ex) == []


def test_missing_top_level_key(tmp_path):
    cfg = _write(tmp_path / "cfg.yaml", "a: 1\n")
    ex = _write(tmp_path / "ex.yaml", "a: 1\nescalation: {}\n")
    assert detect_config_drift(cfg, ex) == ["escalation"]


def test_missing_nested_key(tmp_path):
    cfg = _write(tmp_path / "cfg.yaml", "escalation:\n  enabled: true\n")
    ex = _write(
        tmp_path / "ex.yaml",
        "escalation:\n  enabled: true\n  webhook_url: http://x\n",
    )
    assert detect_config_drift(cfg, ex) == ["escalation.webhook_url"]


def test_multiple_missing_top_and_nested(tmp_path):
    cfg = _write(tmp_path / "cfg.yaml", "a:\n  x: 1\n")
    ex = _write(
        tmp_path / "ex.yaml",
        "a:\n  x: 1\n  y: 2\nb: 3\n",
    )
    gaps = detect_config_drift(cfg, ex)
    assert set(gaps) == {"a.y", "b"}


def test_dynamic_top_level_skipped(tmp_path):
    # repos present but with different sub-keys -> still no nested drift
    cfg = _write(tmp_path / "cfg.yaml", "repos:\n  myrepo: {}\n")
    ex = _write(
        tmp_path / "ex.yaml",
        "repos:\n  example-repo:\n    branch: main\n",
    )
    assert detect_config_drift(cfg, ex) == []


def test_scheduled_tasks_dynamic_skipped(tmp_path):
    cfg = _write(tmp_path / "cfg.yaml", "scheduled_tasks:\n  job_a: {}\n")
    ex = _write(
        tmp_path / "ex.yaml",
        "scheduled_tasks:\n  job_b:\n    cron: '* * * * *'\n",
    )
    assert detect_config_drift(cfg, ex) == []


def test_dynamic_top_level_missing_still_reported(tmp_path):
    cfg = _write(tmp_path / "cfg.yaml", "a: 1\n")
    ex = _write(tmp_path / "ex.yaml", "a: 1\nrepos: {}\n")
    assert detect_config_drift(cfg, ex) == ["repos"]


def test_non_dict_example_value_not_recursed(tmp_path):
    # top-level scalar present in both -> no recursion
    cfg = _write(tmp_path / "cfg.yaml", "a: hello\n")
    ex = _write(tmp_path / "ex.yaml", "a: world\n")
    assert detect_config_drift(cfg, ex) == []


def test_example_dict_but_config_scalar_not_recursed(tmp_path):
    # example_val is dict, config_sub is not a dict -> skip nested check
    cfg = _write(tmp_path / "cfg.yaml", "a: scalar\n")
    ex = _write(tmp_path / "ex.yaml", "a:\n  x: 1\n")
    assert detect_config_drift(cfg, ex) == []


def test_config_file_missing_returns_empty(tmp_path):
    ex = _write(tmp_path / "ex.yaml", "a: 1\n")
    missing = tmp_path / "does_not_exist.yaml"
    assert detect_config_drift(missing, ex) == []


def test_example_file_missing_returns_empty(tmp_path):
    cfg = _write(tmp_path / "cfg.yaml", "a: 1\n")
    missing = tmp_path / "does_not_exist.yaml"
    assert detect_config_drift(cfg, missing) == []


def test_unparseable_yaml_returns_empty(tmp_path):
    cfg = _write(tmp_path / "cfg.yaml", "a: 1\n")
    bad = _write(tmp_path / "bad.yaml", "a: [unterminated\n")
    assert detect_config_drift(cfg, bad) == []


def test_empty_files_treated_as_empty_dict(tmp_path):
    cfg = _write(tmp_path / "cfg.yaml", "")
    ex = _write(tmp_path / "ex.yaml", "")
    assert detect_config_drift(cfg, ex) == []


def test_empty_config_reports_all_example_keys(tmp_path):
    cfg = _write(tmp_path / "cfg.yaml", "")
    ex = _write(tmp_path / "ex.yaml", "a: 1\nb: 2\n")
    assert set(detect_config_drift(cfg, ex)) == {"a", "b"}


def test_config_not_a_dict_returns_empty(tmp_path):
    cfg = _write(tmp_path / "cfg.yaml", "- item1\n- item2\n")
    ex = _write(tmp_path / "ex.yaml", "a: 1\n")
    assert detect_config_drift(cfg, ex) == []


def test_example_not_a_dict_returns_empty(tmp_path):
    cfg = _write(tmp_path / "cfg.yaml", "a: 1\n")
    ex = _write(tmp_path / "ex.yaml", "- item1\n")
    assert detect_config_drift(cfg, ex) == []


def test_empty_example_dict_value_no_nested_gaps(tmp_path):
    # example_val is empty dict -> the for-loop over sub_keys runs zero times
    cfg = _write(tmp_path / "cfg.yaml", "section:\n  present: 1\n")
    ex = _write(tmp_path / "ex.yaml", "section: {}\n")
    assert detect_config_drift(cfg, ex) == []


def test_accepts_str_and_path_args(tmp_path):
    cfg = _write(tmp_path / "cfg.yaml", "a: 1\n")
    ex = _write(tmp_path / "ex.yaml", "a: 1\nb: 2\n")
    via_str = detect_config_drift(str(cfg), str(ex))
    via_path = detect_config_drift(cfg, ex)
    assert via_str == via_path == ["b"]


def test_raises_nothing_on_directory_path(tmp_path):
    # Reading a directory raises -> caught -> empty list
    ex = _write(tmp_path / "ex.yaml", "a: 1\n")
    assert detect_config_drift(tmp_path, ex) == []


@pytest.mark.parametrize("dynamic_key", ["repos", "scheduled_tasks"])
def test_both_dynamic_keys_present_no_recursion(tmp_path, dynamic_key):
    cfg = _write(tmp_path / "cfg.yaml", f"{dynamic_key}:\n  a: {{}}\n")
    ex = _write(tmp_path / "ex.yaml", f"{dynamic_key}:\n  b:\n    c: 1\n")
    assert detect_config_drift(cfg, ex) == []
