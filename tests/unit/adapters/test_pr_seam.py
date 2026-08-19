# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Hold the PR seam, and make the remaining coupling shrink rather than drift.

Seventeen files in ``src/`` named :class:`GitHubPRClient` directly. Thirteen of
them only to reach ``owner_repo_from_clone_url``, a pure URL parse that
never needed a client. That is the shape the board seam had before it was
migrated, and the same ratchet applies:

* pin the seam — the protocol matches what the fleet actually calls, and the
  concrete client still satisfies it;
* ratchet the migration — ``STILL_IMPORTING_GITHUB_PR`` is the accepted
  remainder, and it may only shrink. A new file reaching past the boundary fails
  here, which is the difference between a boundary and a suggestion.

Unlike the board, there is no second backend to migrate *to* yet, and
``docs/specs/forgejo-pr-adapter.md`` argues there should not be one until the
``enforce_admins`` question is answered. "Finished" therefore means what it meant
for the board: every caller goes through the seam, so a future backend is a
change in one module. ``test_the_migration_is_finished`` pins that state.
"""

from __future__ import annotations

import pathlib
import re

import pytest

SRC = pathlib.Path(__file__).resolve().parents[3] / "src" / "operations_center"

#: Files that still name the concrete client. Empty — the burn-down list of 17
#: reached zero when `pr_review_watcher/main.py` moved (last, and under K=3
#: council review, because it is a guardrail path). The set stays so the ratchet
#: keeps holding: unlike the board's `PLANE_SPECIFIC_BY_DESIGN`, there is no
#: file with a design reason to name a forge client directly.
STILL_IMPORTING_GITHUB_PR: set[str] = set()

#: Migrated in the sweep. Pinned so the boundary cannot quietly erode back.
MIGRATED = [
    "entrypoints/board_worker/_subprocess.py",
    "entrypoints/board_worker/claim.py",
    "entrypoints/ci_monitor/main.py",
    "entrypoints/maintenance/audit_close_receipts.py",
    "entrypoints/maintenance/board_unblock.py",
    "entrypoints/maintenance/board_unblock_task.py",
    "entrypoints/maintenance/check_regressions.py",
    "entrypoints/maintenance/close_stale_prs.py",
    "entrypoints/maintenance/console_repair.py",
    "entrypoints/maintenance/orphan_branch_check.py",
    "entrypoints/maintenance/outcome_flagger_task.py",
    "entrypoints/maintenance/reconcile_merged_tasks.py",
    "entrypoints/pr_review_watcher/main.py",
    "eval/outcome_sources.py",
    "execution/workspace.py",
    "observer/collectors/ci_history.py",
    "post_merge_regression.py",
]

_IMPORTS_GITHUB_PR = re.compile(
    r"^[ \t]*from operations_center\.adapters\.github_pr import",
    re.M,
)


def _importers() -> set[str]:
    """Files outside adapters/ that import the concrete client."""
    found = set()
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("adapters/") or "__pycache__" in rel:
            continue
        if _IMPORTS_GITHUB_PR.search(path.read_text(encoding="utf-8", errors="replace")):
            found.add(rel)
    return found


# ── the seam ─────────────────────────────────────────────────────────────────


#: The forge operations, named once. Derived from the concrete client's public
#: surface, not from taste — a protocol narrower than real usage pushes callers
#: back to the concrete class, which is the failure the board seam already hit
#: once with `set_priority`.
PR_OPERATIONS = frozenset({
    # pull requests
    "create_pr", "get_pr", "merge_pr", "close_pr", "list_open_prs",
    "list_closed_prs", "find_pr_by_head", "get_mergeable",
    "update_pr_description", "create_and_merge",
    # diffs and files
    "list_pr_files", "get_pr_diff", "get_file_content", "update_file",
    # review
    "list_pr_comments", "list_pr_review_comments", "list_pr_reviews",
    "pr_has_changes_requested", "post_comment", "update_comment",
    "get_pr_reactions", "get_comment_reactions",
    # CI signal
    "set_commit_status", "get_check_runs", "get_failed_checks",
    "get_incomplete_checks", "get_completed_checks",
    # branches
    "get_branch_head", "get_branch_protection", "delete_branch",
})


def _declared_operations(proto: type) -> set[str]:
    """Public members a Protocol declares.

    Deliberately not `__protocol_attrs__`: that is a CPython internal added in
    3.12. Using it made the board seam's tests pass on a 3.12 developer machine
    and fail on CI's 3.11 with `AttributeError`. `dir()` is stable across both.
    """
    return {n for n in dir(proto) if not n.startswith("_")}


def test_the_concrete_client_satisfies_the_protocol():
    """GitHubPRClient must remain usable as a PRClient.

    If it stops, callers type-hinting the protocol are lying about what they
    accept, and the seam is decorative.
    """
    from operations_center.adapters.github_pr import GitHubPRClient

    missing = sorted(op for op in PR_OPERATIONS if not hasattr(GitHubPRClient, op))
    assert not missing, f"GitHubPRClient no longer provides: {missing}"


def test_protocol_declares_every_operation_the_fleet_calls():
    """The protocol must not be narrower than actual usage."""
    from operations_center.adapters.pr import PRClient

    declared = _declared_operations(PRClient)
    missing = sorted(PR_OPERATIONS - declared)
    assert not missing, f"PRClient does not declare: {missing}"


def test_protocol_declares_nothing_the_client_lacks():
    """And not wider, either — a declared-but-absent operation fails at runtime."""
    from operations_center.adapters.github_pr import GitHubPRClient
    from operations_center.adapters.pr import PRClient

    phantom = sorted(
        op for op in _declared_operations(PRClient) if not hasattr(GitHubPRClient, op)
    )
    assert not phantom, f"PRClient declares operations GitHubPRClient lacks: {phantom}"


# ── construction ─────────────────────────────────────────────────────────────


def test_factory_builds_from_settings_without_naming_a_backend(monkeypatch):
    """make_pr_client is the one place a concrete forge is named."""
    from operations_center.adapters import pr

    captured = {}

    class _Fake:
        def __init__(self, token):
            captured["token"] = token

    monkeypatch.setattr(
        "operations_center.adapters.github_pr.GitHubPRClient", _Fake, raising=False
    )

    class _Settings:
        def git_token(self):
            return "tok"

    pr.make_pr_client(_Settings())
    assert captured == {"token": "tok"}, (
        "the factory changed the construction contract the callers relied on"
    )


def test_factory_refuses_without_a_token():
    """Same failure the seventeen callers already produce, in one place.

    Silently building a tokenless client would turn a startup error into an
    authentication failure inside the first API call, which is far harder to read
    in a daemon log.
    """
    from operations_center.adapters import pr

    class _Settings:
        def git_token(self):
            return None

    with pytest.raises(RuntimeError, match="no git token"):
        pr.make_pr_client(_Settings())


def test_factory_builds_forgejo_when_selected(monkeypatch):
    from operations_center.adapters import pr

    built = {}

    class _Fake:
        def __init__(self, base_url, token):
            built["base_url"] = base_url
            built["token"] = token

    monkeypatch.setattr(
        "operations_center.adapters.forgejo.pr_client.ForgejoPRClient",
        _Fake,
        raising=False,
    )

    class _Forgejo:
        base_url = "http://forge.local"

    class _Settings:
        pr_backend = "forgejo"
        forgejo = _Forgejo()

        def forgejo_token(self):
            return "ftok"

    pr.make_pr_client(_Settings())
    assert built == {"base_url": "http://forge.local", "token": "ftok"}


def test_factory_refuses_forgejo_backend_without_a_block():
    from operations_center.adapters import pr

    class _Settings:
        pr_backend = "forgejo"
        forgejo = None

    with pytest.raises(RuntimeError, match="no `forgejo:` settings block"):
        pr.make_pr_client(_Settings())


def test_factory_rejects_an_unknown_pr_backend():
    from operations_center.adapters import pr

    class _Settings:
        pr_backend = "gitlab"

    with pytest.raises(RuntimeError, match="unknown pr_backend"):
        pr.make_pr_client(_Settings())


# ── the pure helpers ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/ProtocolWarden/OperationsCenter.git", ("ProtocolWarden", "OperationsCenter")),
        ("https://github.com/ProtocolWarden/OperationsCenter", ("ProtocolWarden", "OperationsCenter")),
        ("git@github.com:ProtocolWarden/OperationsCenter.git", ("ProtocolWarden", "OperationsCenter")),
        # the reason this helper belongs at the seam and not on the GitHub client
        ("https://forge.internal/ProtocolWarden/OperationsCenter.git", ("ProtocolWarden", "OperationsCenter")),
        ("git@forge.internal:ProtocolWarden/OperationsCenter.git", ("ProtocolWarden", "OperationsCenter")),
    ],
)
def test_owner_repo_parses_any_forge(url, expected):
    from operations_center.adapters.pr import owner_repo_from_clone_url

    assert owner_repo_from_clone_url(url) == expected


def test_owner_repo_rejects_unparseable():
    from operations_center.adapters.pr import owner_repo_from_clone_url

    with pytest.raises(ValueError, match="Cannot parse owner/repo"):
        owner_repo_from_clone_url("not-a-url")


def test_has_thumbs_up():
    from operations_center.adapters.pr import has_thumbs_up

    assert has_thumbs_up([{"content": "+1"}])
    assert has_thumbs_up([{"content": "eyes"}, {"content": "+1"}])
    assert not has_thumbs_up([{"content": "-1"}])
    assert not has_thumbs_up([])


def test_the_class_helpers_still_delegate():
    """The thirteen unmigrated callers must keep working while they migrate.

    Moving the implementation to the seam and leaving the static methods behind
    as delegates is what makes this migration incremental rather than a
    seventeen-file atomic change.
    """
    from operations_center.adapters.github_pr import GitHubPRClient
    from operations_center.adapters import pr

    url = "git@github.com:owner/repo.git"
    assert GitHubPRClient.owner_repo_from_clone_url(url) == pr.owner_repo_from_clone_url(url)
    assert GitHubPRClient.has_thumbs_up([{"content": "+1"}]) is True
    assert GitHubPRClient.has_thumbs_up([{"content": "-1"}]) is False


def test_token_factory_builds_the_client(monkeypatch):
    """pr_client_from_token is the entry point for callers holding a raw token.

    Twelve of the seventeen resolve their own token from four different
    environment variables, a constructor argument, or `self._token`, and each
    reports a missing one differently. Forcing them through `make_pr_client`
    would have unified error handling too — a behaviour change disguised as a
    refactor.
    """
    from operations_center.adapters import pr

    seen = {}

    class _Fake:
        def __init__(self, token):
            seen["token"] = token

    monkeypatch.setattr(
        "operations_center.adapters.github_pr.GitHubPRClient", _Fake, raising=False
    )
    pr.pr_client_from_token("raw-tok")
    assert seen == {"token": "raw-tok"}


def test_settings_factory_goes_through_the_token_factory(monkeypatch):
    """One construction path, so swapping the forge stays a one-place change."""
    from operations_center.adapters import pr

    calls = []
    monkeypatch.setattr(pr, "pr_client_from_token", lambda t: calls.append(t))

    class _Settings:
        def git_token(self):
            return "tok"

    pr.make_pr_client(_Settings())
    assert calls == ["tok"], "make_pr_client no longer routes through the token factory"


@pytest.mark.parametrize("migrated", MIGRATED)
def test_migrated_files_stay_migrated(migrated):
    """Pin the sweep so it cannot quietly regress."""
    text = (SRC / migrated).read_text(encoding="utf-8")
    assert "GitHubPRClient" not in text, f"{migrated} names GitHubPRClient again"
    assert "adapters.pr" in text, f"{migrated} no longer uses the seam"


def test_the_migration_is_finished():
    """No caller is left to migrate.

    The seam existed to make swapping the forge a one-place change. That is only
    true once every caller goes through it — a seam with stragglers still forces
    a per-caller change at cutover, which is the cost it was built to remove.
    Same end state the board seam reached.
    """
    actual = _importers()
    assert not actual, (
        f"{len(actual)} caller(s) import GitHubPRClient again: {sorted(actual)}"
    )


# ── the ratchet ──────────────────────────────────────────────────────────────


def test_no_new_file_reaches_past_the_boundary():
    """The accepted remainder may shrink, never grow."""
    actual = _importers()
    added = sorted(actual - STILL_IMPORTING_GITHUB_PR)
    assert not added, (
        f"{len(added)} file(s) import GitHubPRClient directly without being on the "
        f"accepted list: {added}. Use "
        "`from operations_center.adapters.pr import PRClient, make_pr_client` "
        "instead — or, if all you need is the clone-URL parse, "
        "`from operations_center.adapters.pr import owner_repo_from_clone_url`."
    )


def test_allowlist_has_no_stale_entries():
    """A migrated file must be struck off, so the list measures real remaining work."""
    actual = _importers()
    stale = sorted(STILL_IMPORTING_GITHUB_PR - actual)
    assert not stale, (
        f"{len(stale)} file(s) no longer import GitHubPRClient but are still listed: "
        f"{stale}. Remove them from STILL_IMPORTING_GITHUB_PR."
    )


def test_the_seam_itself_does_not_import_the_concrete_client_at_module_scope():
    """Import-time coupling would defeat the point and risk a cycle.

    `make_pr_client` imports `GitHubPRClient` inside the function body, the same
    way `make_board_client` does, so importing the protocol costs nothing and
    `github_pr` can delegate back to the seam's helpers without a cycle.
    """
    text = (SRC / "adapters" / "pr" / "__init__.py").read_text(encoding="utf-8")
    module_scope = [
        line for line in text.splitlines()
        if line.startswith("from operations_center.adapters.github_pr")
        or line.startswith("import operations_center.adapters.github_pr")
    ]
    assert not module_scope, (
        f"the seam imports the concrete client at module scope: {module_scope}"
    )
