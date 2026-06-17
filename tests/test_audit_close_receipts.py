# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from operations_center.entrypoints.maintenance import audit_close_receipts


def test_audit_close_receipts_flags_unreceipted_salvage(monkeypatch, capsys, tmp_path) -> None:
    settings = SimpleNamespace(
        git_token=lambda: "tok",
        repos={
            "MyRepo": SimpleNamespace(
                clone_url="https://github.com/owner/repo.git",
            )
        },
    )
    gh = MagicMock()
    gh.list_closed_prs.return_value = [
        {
            "number": 42,
            "merged_at": None,
            "head": {"ref": "goal/demo"},
            "html_url": "https://github.com/owner/repo/pull/42",
        }
    ]
    gh.list_pr_comments.return_value = [
        {"body": "Closing without merge. Work preserved in the branch for later pickup."}
    ]

    monkeypatch.setattr(audit_close_receipts, "load_settings", lambda _: settings)
    mock_cls = MagicMock(return_value=gh)
    mock_cls.owner_repo_from_clone_url = (
        audit_close_receipts.GitHubPRClient.owner_repo_from_clone_url
    )
    monkeypatch.setattr(audit_close_receipts, "GitHubPRClient", mock_cls)
    monkeypatch.setattr(
        "sys.argv",
        ["audit_close_receipts", "--config", str(tmp_path / "cfg.yaml")],
    )

    rc = audit_close_receipts.main()

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["closed_unmerged_count"] == 1
    assert out["finding_count"] == 1
    assert out["findings"][0]["reason"] == "closed_unmerged_without_durable_receipt"


def test_audit_close_receipts_accepts_durable_receipt_comment(
    monkeypatch, capsys, tmp_path
) -> None:
    settings = SimpleNamespace(
        git_token=lambda: "tok",
        repos={
            "MyRepo": SimpleNamespace(
                clone_url="https://github.com/owner/repo.git",
            )
        },
    )
    gh = MagicMock()
    gh.list_closed_prs.return_value = [
        {
            "number": 42,
            "merged_at": None,
            "head": {"ref": "goal/demo"},
            "html_url": "https://github.com/owner/repo/pull/42",
        }
    ]
    gh.list_pr_comments.return_value = [
        {
            "body": (
                "Durable receipt recorded on Plane task `task-abc` for "
                "`refs/pull/42/head` and `docs/specs/queue-drain.md`."
            )
        }
    ]

    monkeypatch.setattr(audit_close_receipts, "load_settings", lambda _: settings)
    mock_cls = MagicMock(return_value=gh)
    mock_cls.owner_repo_from_clone_url = (
        audit_close_receipts.GitHubPRClient.owner_repo_from_clone_url
    )
    monkeypatch.setattr(audit_close_receipts, "GitHubPRClient", mock_cls)
    monkeypatch.setattr(
        "sys.argv",
        ["audit_close_receipts", "--config", str(tmp_path / "cfg.yaml")],
    )

    rc = audit_close_receipts.main()

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["closed_unmerged_count"] == 1
    assert out["finding_count"] == 0
