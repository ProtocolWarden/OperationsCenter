# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from operations_center.entrypoints.maintenance import close_stale_prs


def test_no_salvage_close_comment_uses_required_phrase() -> None:
    comment = close_stale_prs._no_salvage_close_comment(age_days=9.4, threshold_days=7)
    assert "no salvage value" in comment
    assert "9.4d" in comment
    assert "7d" in comment
    assert "preserved on origin" not in comment


def test_main_posts_no_salvage_comment_before_close(monkeypatch, capsys, tmp_path) -> None:
    settings = SimpleNamespace(
        git_token=lambda: "tok",
        repos={
            "MyRepo": SimpleNamespace(
                stale_pr_days=7,
                clone_url="https://github.com/owner/repo.git",
            )
        },
    )
    gh = MagicMock()
    gh.list_open_prs.return_value = [
        {
            "number": 42,
            "head": {"ref": "goal/demo"},
            "labels": [],
            "updated_at": "2026-05-01T00:00:00Z",
            "html_url": "https://github.com/owner/repo/pull/42",
        }
    ]

    monkeypatch.setattr(close_stale_prs, "load_settings", lambda _: settings)
    mock_cls = MagicMock(return_value=gh)
    mock_cls.owner_repo_from_clone_url = close_stale_prs.GitHubPRClient.owner_repo_from_clone_url
    monkeypatch.setattr(close_stale_prs, "GitHubPRClient", mock_cls)
    monkeypatch.setattr(
        "sys.argv",
        ["close_stale_prs", "--config", str(tmp_path / "cfg.yaml")],
    )

    rc = close_stale_prs.main()

    assert rc == 0
    gh.post_comment.assert_called_once()
    comment = gh.post_comment.call_args.args[3]
    assert "no salvage value" in comment
    assert "preserved on origin" not in comment
    gh.close_pr.assert_called_once_with("owner", "repo", 42)
    out = json.loads(capsys.readouterr().out)
    assert out["closed_count"] == 1


def test_main_blocks_close_when_comment_lacks_receipt_and_no_salvage(
    monkeypatch, capsys, tmp_path
) -> None:
    settings = SimpleNamespace(
        git_token=lambda: "tok",
        repos={
            "MyRepo": SimpleNamespace(
                stale_pr_days=7,
                clone_url="https://github.com/owner/repo.git",
            )
        },
    )
    gh = MagicMock()
    gh.list_open_prs.return_value = [
        {
            "number": 42,
            "head": {"ref": "goal/demo"},
            "labels": [],
            "updated_at": "2026-05-01T00:00:00Z",
            "html_url": "https://github.com/owner/repo/pull/42",
        }
    ]

    monkeypatch.setattr(close_stale_prs, "load_settings", lambda _: settings)
    mock_cls = MagicMock(return_value=gh)
    mock_cls.owner_repo_from_clone_url = close_stale_prs.GitHubPRClient.owner_repo_from_clone_url
    monkeypatch.setattr(close_stale_prs, "GitHubPRClient", mock_cls)
    monkeypatch.setattr(close_stale_prs, "_no_salvage_close_comment", lambda **_: "stale PR")
    monkeypatch.setattr(
        "sys.argv",
        ["close_stale_prs", "--config", str(tmp_path / "cfg.yaml")],
    )

    rc = close_stale_prs.main()

    assert rc == 0
    gh.post_comment.assert_not_called()
    gh.close_pr.assert_not_called()
    out = json.loads(capsys.readouterr().out)
    assert out["closed_count"] == 0
    assert out["skipped_count"] == 1
    assert (
        out["skipped"][0]["error"]
        == "close_invariant_blocked_missing_receipt_or_no_salvage_comment"
    )
