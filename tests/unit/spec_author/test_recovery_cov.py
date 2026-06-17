# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

from operations_center.spec_author.models import CampaignRecord
from operations_center.spec_author.recovery import RecoveryService


def _make_campaign(
    created_at: str,
    campaign_id: str = "camp-1",
    slug: str = "my-campaign",
) -> CampaignRecord:
    return CampaignRecord(
        campaign_id=campaign_id,
        slug=slug,
        spec_file=f"{slug}.md",
        status="active",
        created_at=created_at,
    )


def _make_service(abandon_hours: int = 72) -> tuple[RecoveryService, MagicMock, MagicMock]:
    client = MagicMock()
    state = MagicMock()
    svc = RecoveryService(client=client, state_manager=state, abandon_hours=abandon_hours)
    return svc, client, state


# --------------------------------------------------------------------------
# should_abandon
# --------------------------------------------------------------------------


def test_should_abandon_true_when_elapsed_exceeds_window() -> None:
    svc, _, _ = _make_service(abandon_hours=72)
    old = (datetime.now(UTC) - timedelta(hours=100)).isoformat()
    campaign = _make_campaign(old)
    assert svc.should_abandon(campaign) is True


def test_should_abandon_false_when_within_window() -> None:
    svc, _, _ = _make_service(abandon_hours=72)
    recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    campaign = _make_campaign(recent)
    assert svc.should_abandon(campaign) is False


def test_should_abandon_false_just_under_boundary() -> None:
    # elapsed slightly less than abandon_hours should NOT abandon (strict >).
    svc, _, _ = _make_service(abandon_hours=72)
    just_under = (datetime.now(UTC) - timedelta(hours=71, minutes=59)).isoformat()
    campaign = _make_campaign(just_under)
    assert svc.should_abandon(campaign) is False


def test_should_abandon_respects_custom_window() -> None:
    svc, _, _ = _make_service(abandon_hours=1)
    elapsed = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    campaign = _make_campaign(elapsed)
    assert svc.should_abandon(campaign) is True


def test_should_abandon_true_on_unparseable_created_at() -> None:
    svc, _, _ = _make_service(abandon_hours=72)
    campaign = _make_campaign("not-a-date")
    assert svc.should_abandon(campaign) is True


# --------------------------------------------------------------------------
# self_cancel
# --------------------------------------------------------------------------


def _matching_issue(campaign_id: str, state_name: str) -> dict:
    return {
        "id": 101,
        "labels": [{"name": f"campaign-id: {campaign_id}"}],
        "state": {"name": state_name},
    }


def test_self_cancel_transitions_open_matching_issue(tmp_path: Path) -> None:
    svc, client, state = _make_service()
    campaign = _make_campaign((datetime.now(UTC)).isoformat())
    client.list_issues.return_value = [_matching_issue(campaign.campaign_id, "In Progress")]

    svc.self_cancel(campaign, "stale", tmp_path)

    client.transition_issue.assert_called_once_with("101", "Cancelled")
    state.mark_cancelled.assert_called_once_with(campaign.campaign_id)


def test_self_cancel_skips_already_done_or_cancelled_issues(tmp_path: Path) -> None:
    svc, client, state = _make_service()
    campaign = _make_campaign((datetime.now(UTC)).isoformat())
    client.list_issues.return_value = [
        _matching_issue(campaign.campaign_id, "Done"),
        _matching_issue(campaign.campaign_id, "Cancelled"),
    ]

    svc.self_cancel(campaign, "stale", tmp_path)

    client.transition_issue.assert_not_called()
    state.mark_cancelled.assert_called_once_with(campaign.campaign_id)


def test_self_cancel_ignores_non_matching_campaign_label(tmp_path: Path) -> None:
    svc, client, _ = _make_service()
    campaign = _make_campaign((datetime.now(UTC)).isoformat())
    client.list_issues.return_value = [
        {
            "id": 5,
            "labels": [{"name": "campaign-id: other"}],
            "state": {"name": "In Progress"},
        }
    ]

    svc.self_cancel(campaign, "stale", tmp_path)

    client.transition_issue.assert_not_called()


def test_self_cancel_handles_missing_labels_and_state(tmp_path: Path) -> None:
    svc, client, _ = _make_service()
    campaign = _make_campaign((datetime.now(UTC)).isoformat())
    # labels None -> [] ; state None -> {} ; should not match, no error.
    client.list_issues.return_value = [{"id": 9, "labels": None, "state": None}]

    svc.self_cancel(campaign, "stale", tmp_path)

    client.transition_issue.assert_not_called()


def test_self_cancel_swallows_list_issues_exception(tmp_path: Path) -> None:
    svc, client, state = _make_service()
    campaign = _make_campaign((datetime.now(UTC)).isoformat())
    client.list_issues.side_effect = RuntimeError("api down")

    # Should not raise; still marks cancelled afterwards.
    svc.self_cancel(campaign, "stale", tmp_path)

    client.transition_issue.assert_not_called()
    state.mark_cancelled.assert_called_once_with(campaign.campaign_id)


def test_self_cancel_updates_spec_front_matter_when_present(tmp_path: Path) -> None:
    svc, client, _ = _make_service()
    client.list_issues.return_value = []
    campaign = _make_campaign((datetime.now(UTC)).isoformat(), slug="my-campaign")
    spec = tmp_path / "my-campaign.md"
    spec.write_text("---\nstatus: active\n---\nbody status: active\n", encoding="utf-8")

    svc.self_cancel(campaign, "stale", tmp_path)

    text = spec.read_text(encoding="utf-8")
    # Only the first occurrence is replaced.
    assert "status: cancelled" in text
    assert text.count("status: active") == 1


def test_self_cancel_noop_on_missing_spec_file(tmp_path: Path) -> None:
    svc, client, state = _make_service()
    client.list_issues.return_value = []
    campaign = _make_campaign((datetime.now(UTC)).isoformat(), slug="absent")

    # No file exists; should proceed without error and still mark cancelled.
    svc.self_cancel(campaign, "stale", tmp_path)

    assert not (tmp_path / "absent.md").exists()
    state.mark_cancelled.assert_called_once_with(campaign.campaign_id)


def test_self_cancel_marks_state_even_when_no_issues(tmp_path: Path) -> None:
    svc, client, state = _make_service()
    client.list_issues.return_value = []
    campaign = _make_campaign((datetime.now(UTC)).isoformat())

    svc.self_cancel(campaign, "done", tmp_path)

    state.mark_cancelled.assert_called_once_with(campaign.campaign_id)


def test_self_cancel_label_match_is_case_insensitive(tmp_path: Path) -> None:
    svc, client, _ = _make_service()
    campaign = _make_campaign((datetime.now(UTC)).isoformat())
    client.list_issues.return_value = [
        {
            "id": 77,
            "labels": [{"name": f"Campaign-ID: {campaign.campaign_id}".upper()}],
            "state": {"name": "OPEN"},
        }
    ]

    svc.self_cancel(campaign, "stale", tmp_path)

    client.transition_issue.assert_called_once_with("77", "Cancelled")
