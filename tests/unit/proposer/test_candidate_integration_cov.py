# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from operations_center.proposer import candidate_integration as ci
from operations_center.proposer.candidate_integration import (
    CandidateProposerIntegrationService,
    ProposerIntegrationContext,
    new_proposer_integration_context,
)


# --------------------------------------------------------------------------- #
# Helpers / fakes                                                             #
# --------------------------------------------------------------------------- #
def _make_outline(title_hint: str = "") -> SimpleNamespace:
    return SimpleNamespace(title_hint=title_hint)


def _make_candidate(
    *,
    candidate_id: str = "cand-1",
    dedup_key: str = "dk-1",
    family: str = "lint_fix",
    subject: str = "Subject",
    status: str = "emit",
    title_hint: str = "",
    repo_key: str | None = None,
    provenance_repo_key: str | None = None,
    changed_files: list[str] | None = None,
    target_paths: list[str] | None = None,
) -> SimpleNamespace:
    provenance = None
    if provenance_repo_key is not None:
        provenance = SimpleNamespace(repo_key=provenance_repo_key)
    return SimpleNamespace(
        candidate_id=candidate_id,
        dedup_key=dedup_key,
        family=family,
        subject=subject,
        status=status,
        proposal_outline=_make_outline(title_hint),
        provenance=provenance,
        repo_key=repo_key,
        changed_files=changed_files or [],
        target_paths=target_paths or [],
    )


def _make_decision_artifact(candidates: list, run_id: str = "dec-run-1") -> SimpleNamespace:
    return SimpleNamespace(
        candidates=candidates,
        run_id=run_id,
        repo=SimpleNamespace(name="repo-name", path=Path("/tmp/repo")),
    )


def _make_draft(
    *,
    name: str = "Drafted Title",
    description: str = "body\nproposer_run_id: run-xyz\nmore",
    state: str = "Backlog",
    label_names: list[str] | None = None,
    task_kind: str = "lint_fix",
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        description=description,
        state=state,
        label_names=label_names or ["auto"],
        task_kind=task_kind,
    )


def _make_settings(*, propose_skip_when_ready_count: int = 0, repos: dict | None = None):
    return SimpleNamespace(
        propose_skip_when_ready_count=propose_skip_when_ready_count,
        repos=repos or {},
    )


def _make_context(
    *,
    run_id: str = "run-1",
    max_create: int = 10,
    dry_run: bool = False,
    repo_filter: str | None = None,
    decision_run_id: str | None = None,
    source_command: str = "propose",
) -> ProposerIntegrationContext:
    return ProposerIntegrationContext(
        repo_filter=repo_filter,
        decision_run_id=decision_run_id,
        run_id=run_id,
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        source_command=source_command,
        max_create=max_create,
        dry_run=dry_run,
    )


def _build_service(
    *,
    settings,
    candidates: list,
    insight_artifact=None,
    decision_artifact=None,
    guardrail_allowed: bool = True,
    guardrail=None,
    mapper_raises: bool = False,
    client=None,
):
    if decision_artifact is None:
        decision_artifact = _make_decision_artifact(candidates)
    loader = MagicMock()
    loader.load.return_value = (decision_artifact, insight_artifact)

    mapper = MagicMock()
    if mapper_raises:
        mapper.map_to_task.side_effect = RuntimeError("map boom")
    else:
        mapper.map_to_task.return_value = _make_draft()

    guardrails = MagicMock()
    if guardrail is None:
        guardrail = SimpleNamespace(allowed=guardrail_allowed, reason=None, evidence=None)
    guardrails.evaluate.return_value = guardrail

    artifact_writer = MagicMock()
    artifact_writer.write.return_value = ["/tmp/out.json"]

    if client is None:
        client = MagicMock()
        client.list_issues.return_value = []
        client.create_issue.return_value = {"id": "issue-99"}

    service = CandidateProposerIntegrationService(
        settings=settings,
        client=client,
        loader=loader,
        mapper=mapper,
        guardrails=guardrails,
        artifact_writer=artifact_writer,
    )
    return service, decision_artifact


@pytest.fixture(autouse=True)
def _no_spec_suppression(monkeypatch):
    # Default: spec suppression off and no active campaigns. Individual tests
    # override as needed.
    monkeypatch.setattr(ci, "_spec_suppressed", lambda *a, **k: False)

    # build_provenance is a real collaborator that walks decision/insight
    # artifacts; stub it so the mapper input is hermetic.
    monkeypatch.setattr(ci, "build_provenance", lambda **kwargs: SimpleNamespace())

    # Patch CampaignStateManager at its source so the in-run import resolves
    # to a benign default (no active campaigns).
    import operations_center.spec_author.state as state_mod

    fake_mgr = MagicMock()
    fake_mgr.load.return_value.active_campaigns.return_value = []
    monkeypatch.setattr(state_mod, "CampaignStateManager", lambda: fake_mgr)
    yield


# --------------------------------------------------------------------------- #
# Defaults / constructor                                                      #
# --------------------------------------------------------------------------- #
def test_constructor_defaults_instantiated():
    service = CandidateProposerIntegrationService(settings=_make_settings(), client=MagicMock())
    assert service.loader is not None
    assert service.mapper is not None
    assert service.guardrails is not None
    assert service.artifact_writer is not None


# --------------------------------------------------------------------------- #
# Happy path: create issue                                                    #
# --------------------------------------------------------------------------- #
def test_run_creates_issue_happy_path():
    cand = _make_candidate()
    settings = _make_settings()
    service, dec = _build_service(settings=settings, candidates=[cand])

    artifact, written = service.run(_make_context())

    assert len(artifact.created) == 1
    assert artifact.created[0].status == "created"
    assert artifact.created[0].plane_issue_id == "issue-99"
    assert artifact.created[0].plane_title == "Drafted Title"
    assert artifact.skipped == []
    assert artifact.failed == []
    assert written == ["/tmp/out.json"]
    service.client.create_issue.assert_called_once()
    service.client.comment_issue.assert_called_once()


def test_run_filters_non_emit_candidates():
    cand = _make_candidate(status="suppressed")
    service, _ = _build_service(settings=_make_settings(), candidates=[cand])
    artifact, _ = service.run(_make_context())
    assert artifact.created == []
    assert artifact.skipped == []
    service.client.create_issue.assert_not_called()


# --------------------------------------------------------------------------- #
# Dry run                                                                     #
# --------------------------------------------------------------------------- #
def test_run_dry_run_does_not_call_client_create():
    cand = _make_candidate()
    service, _ = _build_service(settings=_make_settings(), candidates=[cand])
    artifact, _ = service.run(_make_context(dry_run=True))
    assert len(artifact.created) == 1
    assert artifact.created[0].status == "dry_run"
    assert artifact.created[0].plane_issue_id is None
    assert artifact.dry_run is True
    service.client.create_issue.assert_not_called()


# --------------------------------------------------------------------------- #
# Back-pressure / ready-queue saturation                                      #
# --------------------------------------------------------------------------- #
def test_run_queue_saturated_skips_all():
    cand = _make_candidate()
    settings = _make_settings(propose_skip_when_ready_count=2)
    client = MagicMock()
    client.list_issues.return_value = [
        {"state": {"name": "Ready for AI"}},
        {"state": {"name": "ready for ai"}},
        {"state": {"name": "Other"}},
    ]
    service, _ = _build_service(settings=settings, candidates=[cand], client=client)
    artifact, _ = service.run(_make_context())
    assert artifact.created == []
    assert len(artifact.skipped) == 1
    skip = artifact.skipped[0]
    assert skip.reason == "ready_queue_saturated"
    assert skip.evidence == {"ready_count": 2, "cap": 2}


def test_run_queue_not_saturated_below_cap():
    cand = _make_candidate()
    settings = _make_settings(propose_skip_when_ready_count=5)
    client = MagicMock()
    client.list_issues.return_value = [{"state": {"name": "Ready for AI"}}]
    client.create_issue.return_value = {"id": "i1"}
    service, _ = _build_service(settings=settings, candidates=[cand], client=client)
    artifact, _ = service.run(_make_context())
    assert len(artifact.created) == 1


def test_run_queue_state_not_dict_counts_zero():
    cand = _make_candidate()
    settings = _make_settings(propose_skip_when_ready_count=1)
    client = MagicMock()
    # state is not a dict -> treated as empty string -> not "ready for ai"
    client.list_issues.return_value = [{"state": "Ready for AI"}, {}]
    client.create_issue.return_value = {"id": "i1"}
    service, _ = _build_service(settings=settings, candidates=[cand], client=client)
    artifact, _ = service.run(_make_context())
    assert len(artifact.created) == 1


def test_run_list_issues_raises_falls_through():
    cand = _make_candidate()
    settings = _make_settings(propose_skip_when_ready_count=1)
    client = MagicMock()
    client.list_issues.side_effect = RuntimeError("plane down")
    client.create_issue.return_value = {"id": "i1"}
    service, _ = _build_service(settings=settings, candidates=[cand], client=client)
    artifact, _ = service.run(_make_context())
    # measurement failed -> proceed normally
    assert len(artifact.created) == 1


# --------------------------------------------------------------------------- #
# propose_enabled per-repo gate                                               #
# --------------------------------------------------------------------------- #
def test_run_propose_disabled_repo_skipped_via_provenance():
    cand = _make_candidate(provenance_repo_key="repoX")
    repos = {"repoX": SimpleNamespace(propose_enabled=False)}
    settings = _make_settings(repos=repos)
    service, _ = _build_service(settings=settings, candidates=[cand])
    artifact, _ = service.run(_make_context())
    assert artifact.created == []
    assert len(artifact.skipped) == 1
    assert artifact.skipped[0].reason == "propose_disabled_for_repo"
    assert artifact.skipped[0].evidence == {"repo_key": "repoX"}


def test_run_propose_disabled_repo_resolved_via_repo_key_attr():
    cand = _make_candidate(repo_key="repoY")
    repos = {"repoY": SimpleNamespace(propose_enabled=False)}
    settings = _make_settings(repos=repos)
    service, _ = _build_service(settings=settings, candidates=[cand])
    artifact, _ = service.run(_make_context())
    assert len(artifact.skipped) == 1
    assert artifact.skipped[0].reason == "propose_disabled_for_repo"


def test_run_propose_enabled_repo_proceeds():
    cand = _make_candidate(provenance_repo_key="repoZ")
    repos = {"repoZ": SimpleNamespace(propose_enabled=True)}
    settings = _make_settings(repos=repos)
    service, _ = _build_service(settings=settings, candidates=[cand])
    artifact, _ = service.run(_make_context())
    assert len(artifact.created) == 1


# --------------------------------------------------------------------------- #
# max_create gate                                                             #
# --------------------------------------------------------------------------- #
def test_run_max_create_reached_skips_extra():
    cands = [_make_candidate(candidate_id=f"c{i}", dedup_key=f"d{i}") for i in range(3)]
    service, _ = _build_service(settings=_make_settings(), candidates=cands)
    artifact, _ = service.run(_make_context(max_create=2))
    assert len(artifact.created) == 2
    assert len(artifact.skipped) == 1
    assert artifact.skipped[0].reason == "max_create_reached"
    assert artifact.skipped[0].evidence == {"max_create": 2}


# --------------------------------------------------------------------------- #
# Spec suppression                                                            #
# --------------------------------------------------------------------------- #
def test_run_spec_suppressed_skips(monkeypatch):
    cand = _make_candidate(title_hint="Some Title", changed_files=["a.py"], target_paths=["b.py"])
    captured = {}

    def fake_suppressed(title, paths, campaigns, *, specs_dir):
        captured["title"] = title
        captured["paths"] = paths
        captured["specs_dir"] = specs_dir
        return True

    monkeypatch.setattr(ci, "_spec_suppressed", fake_suppressed)
    service, _ = _build_service(settings=_make_settings(), candidates=[cand])
    artifact, _ = service.run(_make_context())
    assert len(artifact.skipped) == 1
    assert artifact.skipped[0].reason == "active_spec_campaign"
    assert captured["title"] == "Some Title"
    assert captured["paths"] == ["a.py", "b.py"]
    assert captured["specs_dir"] == Path("docs/specs")


def test_run_title_falls_back_to_subject_when_no_hint(monkeypatch):
    cand = _make_candidate(title_hint="", subject="The Subject")
    seen = {}

    def fake_suppressed(title, paths, campaigns, *, specs_dir):
        seen["title"] = title
        return False

    monkeypatch.setattr(ci, "_spec_suppressed", fake_suppressed)
    service, _ = _build_service(settings=_make_settings(), candidates=[cand])
    service.run(_make_context())
    assert seen["title"] == "The Subject"


# --------------------------------------------------------------------------- #
# Campaign state manager exception path                                       #
# --------------------------------------------------------------------------- #
def test_run_campaign_state_manager_exception_handled(monkeypatch):
    import operations_center.spec_author.state as state_mod

    def boom():
        raise RuntimeError("state unavailable")

    monkeypatch.setattr(state_mod, "CampaignStateManager", boom)

    captured = {}

    def fake_suppressed(title, paths, campaigns, *, specs_dir):
        captured["campaigns"] = campaigns
        return False

    monkeypatch.setattr(ci, "_spec_suppressed", fake_suppressed)
    cand = _make_candidate()
    service, _ = _build_service(settings=_make_settings(), candidates=[cand])
    artifact, _ = service.run(_make_context())
    assert len(artifact.created) == 1
    assert captured["campaigns"] == []


# --------------------------------------------------------------------------- #
# Mapper failure                                                              #
# --------------------------------------------------------------------------- #
def test_run_mapper_failure_records_failed():
    cand = _make_candidate()
    service, _ = _build_service(settings=_make_settings(), candidates=[cand], mapper_raises=True)
    artifact, _ = service.run(_make_context())
    assert artifact.created == []
    assert len(artifact.failed) == 1
    assert artifact.failed[0].reason == "candidate_mapping_failed"
    assert "map boom" in artifact.failed[0].error


# --------------------------------------------------------------------------- #
# Guardrail blocked                                                           #
# --------------------------------------------------------------------------- #
def test_run_guardrail_blocked_with_reason_and_evidence():
    cand = _make_candidate()
    guardrail = SimpleNamespace(allowed=False, reason="cooldown_active", evidence={"x": 1})
    service, _ = _build_service(settings=_make_settings(), candidates=[cand], guardrail=guardrail)
    artifact, _ = service.run(_make_context())
    assert len(artifact.skipped) == 1
    assert artifact.skipped[0].reason == "cooldown_active"
    assert artifact.skipped[0].evidence == {"x": 1}


def test_run_guardrail_blocked_defaults_when_reason_evidence_none():
    cand = _make_candidate()
    guardrail = SimpleNamespace(allowed=False, reason=None, evidence=None)
    service, _ = _build_service(settings=_make_settings(), candidates=[cand], guardrail=guardrail)
    artifact, _ = service.run(_make_context())
    assert artifact.skipped[0].reason == "guardrail_blocked"
    assert artifact.skipped[0].evidence == {}


# --------------------------------------------------------------------------- #
# Plane create failure                                                        #
# --------------------------------------------------------------------------- #
def test_run_create_issue_failure_records_failed():
    cand = _make_candidate()
    client = MagicMock()
    client.list_issues.return_value = []
    client.create_issue.side_effect = RuntimeError("create boom")
    service, _ = _build_service(settings=_make_settings(), candidates=[cand], client=client)
    artifact, _ = service.run(_make_context())
    assert artifact.created == []
    assert len(artifact.failed) == 1
    assert artifact.failed[0].reason == "plane_create_failed"
    assert "create boom" in artifact.failed[0].error


def test_run_comment_issue_failure_records_failed():
    cand = _make_candidate()
    client = MagicMock()
    client.list_issues.return_value = []
    client.create_issue.return_value = {"id": "i1"}
    client.comment_issue.side_effect = RuntimeError("comment boom")
    service, _ = _build_service(settings=_make_settings(), candidates=[cand], client=client)
    artifact, _ = service.run(_make_context())
    assert artifact.created == []
    assert len(artifact.failed) == 1
    assert artifact.failed[0].reason == "plane_create_failed"


# --------------------------------------------------------------------------- #
# Artifact assembly fields                                                    #
# --------------------------------------------------------------------------- #
def test_run_artifact_top_level_fields():
    cand = _make_candidate()
    dec = _make_decision_artifact([cand], run_id="dec-99")
    service, _ = _build_service(settings=_make_settings(), candidates=[cand], decision_artifact=dec)
    ctx = _make_context(run_id="myrun", source_command="cmd-x")
    artifact, _ = service.run(ctx)
    assert artifact.run_id == "myrun"
    assert artifact.source_command == "cmd-x"
    assert artifact.source_decision_run_id == "dec-99"
    assert artifact.repo.name == "repo-name"
    assert artifact.repo.path == Path("/tmp/repo")


# --------------------------------------------------------------------------- #
# _created_comment                                                            #
# --------------------------------------------------------------------------- #
def test_created_comment_extracts_proposer_run_id():
    cand = _make_candidate(family="type_fix", candidate_id="cc", dedup_key="dd")
    draft = _make_draft(
        task_kind="type_fix",
        description="header\nproposer_run_id: abc123\ntrailer",
    )
    out = CandidateProposerIntegrationService._created_comment(
        candidate=cand, draft=draft, decision_run_id="dr-1"
    )
    assert "task_kind: type_fix" in out
    assert "result_status: created" in out
    assert "source_family: type_fix" in out
    assert "candidate_id: cc" in out
    assert "dedup_key: dd" in out
    assert "decision_run_id: dr-1" in out
    assert "proposer_run_id: abc123" in out
    assert "handoff_reason: proposer_candidate_type_fix" in out


def test_created_comment_unknown_proposer_run_id_when_absent():
    cand = _make_candidate()
    draft = _make_draft(description="no run id here")
    out = CandidateProposerIntegrationService._created_comment(
        candidate=cand, draft=draft, decision_run_id="dr-2"
    )
    assert "proposer_run_id: unknown" in out


# --------------------------------------------------------------------------- #
# new_proposer_integration_context                                            #
# --------------------------------------------------------------------------- #
def test_new_proposer_integration_context_builds_run_id():
    ctx = new_proposer_integration_context(
        repo_filter="r",
        decision_run_id="d",
        max_create=4,
        dry_run=True,
        source_command="src",
    )
    assert ctx.repo_filter == "r"
    assert ctx.decision_run_id == "d"
    assert ctx.max_create == 4
    assert ctx.dry_run is True
    assert ctx.source_command == "src"
    assert ctx.run_id.startswith("prop_")
    assert len(ctx.run_id) <= 32
    assert ctx.generated_at.tzinfo is UTC


def test_loader_called_with_context_filters():
    cand = _make_candidate()
    service, _ = _build_service(settings=_make_settings(), candidates=[cand])
    ctx = _make_context(repo_filter="myrepo", decision_run_id="drun")
    service.run(ctx)
    service.loader.load.assert_called_once_with(repo="myrepo", decision_run_id="drun")
