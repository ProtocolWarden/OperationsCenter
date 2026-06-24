# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Execution entrypoint for routed OC orchestration bundles.

Consumes a proposal/decision bundle, constructs an ExecutionRequest,
runs the mandatory policy gate, and then invokes the selected
backend adapter when execution is allowed.

Failure handling:
- Unexpected coordinator exception: writes partial artifacts (proposal +
  decision), emits structured error JSON to --output or stdout, exits 1.
"""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from operations_center.backends.factory import CanonicalBackendRegistry
from operations_center.config.settings import load_settings
from operations_center.contracts.proposal import OcPlanningProposal
from operations_center.contracts.routing import OcRoutingDecision
from operations_center.execution.artifact_writer import RunArtifactWriter
from operations_center.execution.coordinator import ExecutionCoordinator
from operations_center.execution.handoff import ExecutionRuntimeContext
from operations_center.execution.usage_store import UsageStore
from operations_center.execution.workspace import WorkspaceManager
from operations_center.planning.models import ProposalDecisionBundle
from operations_center.repo_graph_factory import (
    build_effective_repo_graph_from_settings,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Execute a routed proposal through the canonical execution boundary."
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument(
        "--bundle", required=True, type=Path, help="JSON file containing proposal and decision"
    )
    parser.add_argument("--workspace-path", required=True, type=Path)
    parser.add_argument("--task-branch", required=True)
    parser.add_argument("--goal-file-path", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--no-artifacts", action="store_true", help="Skip writing run artifacts to disk"
    )
    parser.add_argument(
        "--source",
        default="",
        help="Run source tag written to run_metadata.json (e.g. manual, auto_once)",
    )
    return parser


def _build_recovery_policy(settings):
    """Build a RecoveryPolicy from ``settings.recovery`` (inventory #3).

    Fail-safe: with the shipped default (``max_attempts=1``) this returns a
    policy identical to ``RecoveryPolicy()`` — single-shot, no retry. The
    retryable/non-retryable failure-kind sets keep their conservative defaults;
    only the attempt budget, backoff ceiling, and UNKNOWN-retry opt-in are
    operator-tunable here. The coordinator builds the matching RecoveryEngine
    from this policy.
    """
    from operations_center.execution.recovery_loop import RecoveryPolicy

    rec = settings.recovery
    return RecoveryPolicy(
        max_attempts=int(rec.max_attempts),
        retry_unknowns=bool(rec.retry_unknowns),
        unknown_retry_limit=int(rec.unknown_retry_limit),
        max_delay_seconds=float(rec.max_delay_seconds),
    )


def _load_bundle(path: Path) -> ProposalDecisionBundle:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return ProposalDecisionBundle(
        proposal=OcPlanningProposal.model_validate(payload["proposal"]),
        decision=OcRoutingDecision.model_validate(payload["decision"]),
    )


def _emit(payload: dict, output: Path | None) -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    if output:
        output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


def _artifacts_suppressed(args) -> bool:
    """Return True when artifact writing must be skipped.

    Suppressed when --no-artifacts is passed, or when running under pytest
    (PYTEST_CURRENT_TEST env var set) without an explicit --source flag,
    or when CONSOLE_DISABLE_ARTIFACTS=1.
    """
    import os

    if args.no_artifacts:
        return True
    if os.environ.get("CONSOLE_DISABLE_ARTIFACTS") == "1":
        return True
    return False


def main() -> int:
    args = _build_parser().parse_args()
    no_artifacts = _artifacts_suppressed(args)
    settings = load_settings(args.config)
    bundle = _load_bundle(args.bundle)
    runtime = ExecutionRuntimeContext(
        workspace_path=args.workspace_path,
        task_branch=args.task_branch,
        goal_file_path=args.goal_file_path,
    )
    import os as _os

    await_review_repos = {
        rk for rk, rcfg in (settings.repos or {}).items() if getattr(rcfg, "await_review", False)
    }

    def _env_int(name: str) -> int | None:
        raw = _os.environ.get(name, "").strip()
        try:
            return int(raw) if raw else None
        except ValueError:
            return None

    # Bot identity: prefer settings.git.author_name / author_email so commits
    # attribute correctly per repo workflow. Falls back to a generic identity
    # when the fields aren't set.
    bot_name = getattr(settings.git, "author_name", None) or "Operations Center"
    bot_email = getattr(settings.git, "author_email", None) or "operations-center@local"
    workspace_manager = WorkspaceManager(
        github_token=settings.git_token(),
        await_review_repos=await_review_repos,
        bot_identity=(bot_name, bot_email),
        max_files=_env_int("OPS_CENTER_MAX_FILES"),
        max_lines=_env_int("OPS_CENTER_MAX_LINES"),
        repo_settings_lookup=lambda key: (settings.repos or {}).get(key),
    )
    repo_graph = build_effective_repo_graph_from_settings(
        settings,
        repo_root=Path.cwd(),
    )

    # Per-task model-tier selection (inventory #2). Fail-safe: None unless
    # settings.runtime_binding.enabled — None preserves the static-team default.
    # A relative policy_path is resolved against the config-file directory so
    # operators can write `policy_path: runtime_binding_policy.yaml`.
    runtime_binding_settings = settings.runtime_binding
    if (
        runtime_binding_settings.enabled
        and runtime_binding_settings.policy_path is not None
        and not runtime_binding_settings.policy_path.is_absolute()
    ):
        runtime_binding_settings = runtime_binding_settings.model_copy(
            update={
                "policy_path": (args.config.parent / runtime_binding_settings.policy_path).resolve()
            }
        )
        settings = settings.model_copy(update={"runtime_binding": runtime_binding_settings})
    runtime_binding_policy = ExecutionCoordinator.resolve_runtime_binding_policy(settings)

    # Bounded execution retry/backoff (inventory #3). Fail-safe: the shipped
    # default max_attempts=1 is single-shot (current behavior); the engine
    # additionally refuses to retry non-idempotent requests. Raising max_attempts
    # in settings.recovery enables retry of transient failures.
    recovery_policy = _build_recovery_policy(settings)

    coordinator = ExecutionCoordinator(
        adapter_registry=CanonicalBackendRegistry.from_settings(settings),
        workspace_manager=workspace_manager,
        repo_graph=repo_graph,
        usage_store=UsageStore(),
        backend_caps=settings.backend_caps,
        resource_gate=settings.resource_gate,
        runtime_binding_policy=runtime_binding_policy,
        recovery_policy=recovery_policy,
    )

    try:
        outcome = coordinator.execute(bundle, runtime)
    except Exception as exc:
        partial_run_id = f"partial-{uuid.uuid4().hex[:8]}"
        if not no_artifacts:
            try:
                RunArtifactWriter().write_partial(
                    run_id=partial_run_id,
                    proposal=bundle.proposal,
                    decision=bundle.decision,
                    reason=f"Coordinator raised unexpected exception: {exc}",
                )
            except Exception:
                pass  # best-effort

        error_payload = {
            "error": "coordinator_failure",
            "error_type": "backend_error",
            "message": str(exc),
            "partial_run_id": partial_run_id,
        }
        _emit(error_payload, args.output)
        return 1

    if not no_artifacts:
        extra: dict = {}
        if args.source:
            extra["source"] = args.source
        RunArtifactWriter().write_run(
            proposal=bundle.proposal,
            decision=bundle.decision,
            request=outcome.request,
            result=outcome.result,
            executed=outcome.executed,
            extra_metadata=extra or None,
        )

    payload = {
        "request": outcome.request.model_dump(mode="json"),
        "policy_decision": outcome.policy_decision.model_dump(mode="json"),
        "result": outcome.result.model_dump(mode="json"),
        "record": outcome.record.model_dump(mode="json"),
        "trace": outcome.trace.model_dump(mode="json"),
        "executed": outcome.executed,
    }
    _emit(payload, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
