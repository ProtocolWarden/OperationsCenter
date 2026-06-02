# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from operations_center.tuning.applier import (
    TuningApplier,
    _DEFAULTS,
    load_tuning_config,
)
from operations_center.tuning.models import TuningChange, TuningConfig

_GEN_AT = datetime(2026, 6, 2, 12, 0, 0)


def _write_config(path: Path, overrides: dict, version: int = 1) -> None:
    cfg = TuningConfig(version=version, updated_at=_GEN_AT, overrides=overrides)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cfg.model_dump_json(indent=2), encoding="utf-8")


# --- load_tuning_config -------------------------------------------------


def test_load_tuning_config_missing_returns_none(tmp_path):
    assert load_tuning_config(tmp_path / "nope.json") is None


def test_load_tuning_config_valid(tmp_path):
    p = tmp_path / "tuning.json"
    _write_config(p, {"observation_coverage": {"min_consecutive_runs": 3}})
    cfg = load_tuning_config(p)
    assert cfg is not None
    assert cfg.overrides["observation_coverage"]["min_consecutive_runs"] == 3


def test_load_tuning_config_invalid_json_returns_none(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json", encoding="utf-8")
    assert load_tuning_config(p) is None


def test_load_tuning_config_invalid_schema_returns_none(tmp_path):
    p = tmp_path / "schema.json"
    # Missing required updated_at field -> validation error -> None.
    p.write_text(json.dumps({"version": "abc", "overrides": []}), encoding="utf-8")
    assert load_tuning_config(p) is None


def test_load_tuning_config_default_path(monkeypatch, tmp_path):
    # No path given -> uses module default, which does not exist here.
    monkeypatch.chdir(tmp_path)
    assert load_tuning_config() is None


# --- current_value ------------------------------------------------------


def test_current_value_from_file(tmp_path):
    p = tmp_path / "tuning.json"
    _write_config(p, {"test_visibility": {"min_consecutive_runs": 4}})
    applier = TuningApplier(config_path=p)
    assert applier.current_value("test_visibility", "min_consecutive_runs") == 4


def test_current_value_falls_back_to_defaults_when_no_file(tmp_path):
    applier = TuningApplier(config_path=tmp_path / "missing.json")
    assert applier.current_value("observation_coverage", "min_consecutive_runs") == 2
    assert applier.current_value("test_visibility", "min_consecutive_runs") == 3
    assert applier.current_value("dependency_drift", "min_consecutive_runs") == 2


def test_current_value_unknown_family_returns_two(tmp_path):
    applier = TuningApplier(config_path=tmp_path / "missing.json")
    assert applier.current_value("unknown_family", "min_consecutive_runs") == 2


def test_current_value_non_int_override_falls_back(tmp_path):
    p = tmp_path / "tuning.json"
    # Override present but not an int -> isinstance check fails -> defaults.
    _write_config(p, {"observation_coverage": {"min_consecutive_runs": "five"}})
    applier = TuningApplier(config_path=p)
    assert applier.current_value("observation_coverage", "min_consecutive_runs") == 2


def test_current_value_family_present_key_absent(tmp_path):
    p = tmp_path / "tuning.json"
    _write_config(p, {"observation_coverage": {"other_key": 9}})
    applier = TuningApplier(config_path=p)
    # Key not in overrides -> default for family.
    assert applier.current_value("observation_coverage", "min_consecutive_runs") == 2


# --- apply --------------------------------------------------------------


def test_apply_key_not_in_auto_apply_keys_returns_none(tmp_path):
    p = tmp_path / "tuning.json"
    applier = TuningApplier(config_path=p)
    result = applier.apply(
        "observation_coverage", "not_a_key", "loosen_threshold", "because", _GEN_AT
    )
    assert result is None
    assert not p.exists()


def test_apply_invalid_action_returns_none(tmp_path):
    p = tmp_path / "tuning.json"
    applier = TuningApplier(config_path=p)
    # Valid key but action yields new_value None.
    result = applier.apply("observation_coverage", "min_consecutive_runs", "keep", "noop", _GEN_AT)
    assert result is None
    assert not p.exists()


def test_apply_new_value_out_of_range_returns_none(tmp_path):
    p = tmp_path / "tuning.json"
    # current = 1 (min), loosen -> 0 which is below MIN -> None.
    _write_config(p, {"observation_coverage": {"min_consecutive_runs": 1}})
    applier = TuningApplier(config_path=p)
    result = applier.apply(
        "observation_coverage", "min_consecutive_runs", "loosen_threshold", "r", _GEN_AT
    )
    assert result is None


def test_apply_tighten_creates_file_when_none_exists(tmp_path):
    p = tmp_path / "nested" / "tuning.json"
    applier = TuningApplier(config_path=p)
    # Default for observation_coverage is 2; tighten -> 3.
    change = applier.apply(
        "observation_coverage", "min_consecutive_runs", "tighten_threshold", "drift up", _GEN_AT
    )
    assert isinstance(change, TuningChange)
    assert change.before == 2
    assert change.after == 3
    assert change.family == "observation_coverage"
    assert change.key == "min_consecutive_runs"
    assert change.reason == "drift up"
    assert change.applied_at == _GEN_AT

    assert p.exists()
    written = load_tuning_config(p)
    assert written is not None
    assert written.overrides["observation_coverage"]["min_consecutive_runs"] == 3
    assert written.updated_at == _GEN_AT
    # Default version on a freshly created config.
    assert written.version == 1


def test_apply_loosen_existing_config_preserves_other_families(tmp_path):
    p = tmp_path / "tuning.json"
    _write_config(
        p,
        {
            "observation_coverage": {"min_consecutive_runs": 3},
            "test_visibility": {"min_consecutive_runs": 4},
        },
        version=7,
    )
    applier = TuningApplier(config_path=p)
    change = applier.apply(
        "observation_coverage", "min_consecutive_runs", "loosen_threshold", "fewer", _GEN_AT
    )
    assert change is not None
    assert change.before == 3
    assert change.after == 2

    written = load_tuning_config(p)
    assert written is not None
    # Preserves version from existing config.
    assert written.version == 7
    # Untouched family preserved.
    assert written.overrides["test_visibility"]["min_consecutive_runs"] == 4
    assert written.overrides["observation_coverage"]["min_consecutive_runs"] == 2


def test_apply_does_not_mutate_original_config_overrides(tmp_path):
    p = tmp_path / "tuning.json"
    _write_config(p, {"observation_coverage": {"min_consecutive_runs": 2, "extra": 1}})
    applier = TuningApplier(config_path=p)
    change = applier.apply(
        "observation_coverage", "min_consecutive_runs", "tighten_threshold", "r", _GEN_AT
    )
    assert change is not None
    written = load_tuning_config(p)
    assert written is not None
    # Sibling key inside the family override is preserved.
    assert written.overrides["observation_coverage"]["extra"] == 1
    assert written.overrides["observation_coverage"]["min_consecutive_runs"] == 3


def test_apply_adds_new_family_to_existing_config(tmp_path):
    p = tmp_path / "tuning.json"
    _write_config(p, {"test_visibility": {"min_consecutive_runs": 3}})
    applier = TuningApplier(config_path=p)
    # dependency_drift not yet in overrides; default 2, tighten -> 3.
    change = applier.apply(
        "dependency_drift", "min_consecutive_runs", "tighten_threshold", "r", _GEN_AT
    )
    assert change is not None
    assert change.before == 2
    assert change.after == 3
    written = load_tuning_config(p)
    assert written is not None
    assert written.overrides["dependency_drift"]["min_consecutive_runs"] == 3
    assert written.overrides["test_visibility"]["min_consecutive_runs"] == 3


def test_defaults_table_contents():
    # Guard the module-level defaults so behavior is pinned.
    assert _DEFAULTS["observation_coverage"]["min_consecutive_runs"] == 2
    assert _DEFAULTS["test_visibility"]["min_consecutive_runs"] == 3
    assert _DEFAULTS["dependency_drift"]["min_consecutive_runs"] == 2
