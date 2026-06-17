# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import logging

from operations_center.spec_author import suppressor
from operations_center.spec_author.models import CampaignRecord


def _make_campaign(spec_file: str = "spec.md") -> CampaignRecord:
    return CampaignRecord(
        campaign_id="camp-1",
        slug="my-slug",
        spec_file=spec_file,
        status="active",
        created_at="2026-01-01T00:00:00",
    )


def _write_spec(path, keywords: list[str]) -> None:
    kw_yaml = "\n".join(f"  - {k}" for k in keywords)
    text = f"---\ncampaign_id: camp-1\nslug: my-slug\narea_keywords:\n{kw_yaml}\n---\n# body\n"
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# is_suppressed
# ---------------------------------------------------------------------------


def test_is_suppressed_no_active_campaigns_returns_false():
    assert suppressor.is_suppressed("title", ["path/a.py"], active_campaigns=None) is False


def test_is_suppressed_empty_active_campaigns_returns_false():
    assert suppressor.is_suppressed("title", ["path/a.py"], active_campaigns=[]) is False


def test_is_suppressed_match_via_title(tmp_path, caplog):
    spec = tmp_path / "spec.md"
    _write_spec(spec, ["billing"])
    campaign = _make_campaign("spec.md")
    with caplog.at_level(logging.INFO):
        result = suppressor.is_suppressed(
            "Fix the Billing bug", ["src/x.py"], [campaign], specs_dir=tmp_path
        )
    assert result is True
    assert "spec_suppressed" in caplog.text
    assert "camp-1" in caplog.text


def test_is_suppressed_match_via_path(tmp_path):
    spec = tmp_path / "spec.md"
    _write_spec(spec, ["payments"])
    campaign = _make_campaign("spec.md")
    result = suppressor.is_suppressed(
        "Unrelated title", ["src/payments/core.py"], [campaign], specs_dir=tmp_path
    )
    assert result is True


def test_is_suppressed_no_match_returns_false(tmp_path):
    spec = tmp_path / "spec.md"
    _write_spec(spec, ["billing"])
    campaign = _make_campaign("spec.md")
    result = suppressor.is_suppressed("Unrelated", ["src/other.py"], [campaign], specs_dir=tmp_path)
    assert result is False


def test_is_suppressed_returns_true_on_first_matching_of_many(tmp_path):
    spec_a = tmp_path / "a.md"
    spec_b = tmp_path / "b.md"
    _write_spec(spec_a, ["nomatch"])
    _write_spec(spec_b, ["billing"])
    c_a = _make_campaign("a.md")
    c_b = _make_campaign("b.md")
    assert suppressor.is_suppressed("billing fix", ["x.py"], [c_a, c_b], specs_dir=tmp_path) is True


def test_is_suppressed_missing_spec_file_no_match(tmp_path):
    # No spec written -> keywords load fails gracefully -> no match.
    campaign = _make_campaign("missing.md")
    assert suppressor.is_suppressed("billing", ["x.py"], [campaign], specs_dir=tmp_path) is False


# ---------------------------------------------------------------------------
# _load_area_keywords
# ---------------------------------------------------------------------------


def test_load_area_keywords_via_specs_dir_filename(tmp_path):
    spec = tmp_path / "spec.md"
    _write_spec(spec, ["alpha", "beta"])
    campaign = _make_campaign("nested/dir/spec.md")
    kws = suppressor._load_area_keywords(campaign, tmp_path)
    assert kws == ["alpha", "beta"]


def test_load_area_keywords_absolute_path_when_not_in_specs_dir(tmp_path):
    # specs_dir given but candidate (by name) does not exist; stored path is
    # absolute and exists -> use stored absolute path.
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    spec = real_dir / "spec.md"
    _write_spec(spec, ["gamma"])
    empty_specs = tmp_path / "empty"
    empty_specs.mkdir()
    campaign = _make_campaign(str(spec))  # absolute path
    kws = suppressor._load_area_keywords(campaign, empty_specs)
    assert kws == ["gamma"]


def test_load_area_keywords_best_guess_candidate_fails_gracefully(tmp_path):
    # specs_dir given, candidate by name missing, path not absolute ->
    # falls to candidate best guess which does not exist -> [].
    specs_dir = tmp_path / "specs"
    specs_dir.mkdir()
    campaign = _make_campaign("relative.md")
    kws = suppressor._load_area_keywords(campaign, specs_dir)
    assert kws == []


def test_load_area_keywords_no_specs_dir_uses_stored_path(tmp_path, monkeypatch):
    spec = tmp_path / "spec.md"
    _write_spec(spec, ["delta"])
    monkeypatch.chdir(tmp_path)
    campaign = _make_campaign("spec.md")  # relative to cwd
    kws = suppressor._load_area_keywords(campaign, None)
    assert kws == ["delta"]


def test_load_area_keywords_unparseable_spec_returns_empty(tmp_path, caplog):
    spec = tmp_path / "spec.md"
    spec.write_text("no front matter here", encoding="utf-8")
    campaign = _make_campaign("spec.md")
    with caplog.at_level(logging.DEBUG):
        kws = suppressor._load_area_keywords(campaign, tmp_path)
    assert kws == []
    assert "spec_keywords_load_failed" in caplog.text


def test_load_area_keywords_missing_file_returns_empty(tmp_path):
    campaign = _make_campaign("nope.md")
    assert suppressor._load_area_keywords(campaign, tmp_path) == []


def test_load_area_keywords_empty_keywords_field(tmp_path):
    spec = tmp_path / "spec.md"
    spec.write_text("---\ncampaign_id: c\nslug: s\n---\nbody\n", encoding="utf-8")
    campaign = _make_campaign("spec.md")
    assert suppressor._load_area_keywords(campaign, tmp_path) == []


# ---------------------------------------------------------------------------
# _any_keyword_matches
# ---------------------------------------------------------------------------


def test_any_keyword_matches_empty_keywords_false():
    assert suppressor._any_keyword_matches([], "title", ["p"]) is False


def test_any_keyword_matches_title_case_insensitive():
    assert suppressor._any_keyword_matches(["FOO"], "a foo b", []) is True


def test_any_keyword_matches_path_case_insensitive():
    assert suppressor._any_keyword_matches(["Bar"], "title", ["src/BAR/x.py"]) is True


def test_any_keyword_matches_no_match():
    assert suppressor._any_keyword_matches(["zzz"], "title", ["src/a.py"]) is False


def test_any_keyword_matches_second_keyword_matches():
    assert suppressor._any_keyword_matches(["nope", "yes"], "the yes here", []) is True


def test_any_keyword_matches_empty_paths_list():
    assert suppressor._any_keyword_matches(["x"], "no match", []) is False
