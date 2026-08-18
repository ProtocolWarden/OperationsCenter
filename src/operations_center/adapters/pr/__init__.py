# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""The PR seam: what the fleet needs from a forge, and one place to build it.

The board got this treatment first, for the same reason it is wanted here: every
caller named the concrete client, so swapping the backend was a change in every
caller rather than in one place. On the PR side that is seventeen files in
``src/`` naming :class:`GitHubPRClient` today.

Thirteen of those seventeen do not want a client at all. They want
``owner_repo_from_clone_url`` — a pure parse of ``host/owner/repo`` that has
nothing to do with GitHub, reached through the GitHub class because that is where
it happened to live. Those callers are the cheapest to migrate and the most
clearly mis-coupled, which is why the helper is a module function here.

This module is the seam:

* :class:`PRClient` — the operations the fleet performs against a forge. Callers
  depend on this name.
* :func:`make_pr_client` — the single construction site.
* :func:`owner_repo_from_clone_url`, :func:`has_thumbs_up` — the two pure helpers
  that were static methods on the client, and needed an instance of nothing.

`GitHubPRClient` satisfies the protocol structurally, so adopting this is a
rename at each call site rather than a behaviour change. That is deliberate: a
migration that is not mechanical is one where regressions hide.

**This seam does not make review portable on its own.** ``docs/specs/forgejo-pr-adapter.md``
records why: Forgejo has no Checks API, and ``enforce_admins`` — which
``_branch_protection_ok`` requires before any self-merge — has no equivalent
there. Those are unresolved design questions, not missing code, and
:func:`make_pr_client` deliberately has no second backend until they are answered.
What the seam buys today is that answering them later is a change here rather
than in seventeen callers.

``tests/unit/adapters/test_pr_seam.py`` holds the boundary against a shrinking
allowlist.
"""

from __future__ import annotations

import re
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "PRClient",
    "has_thumbs_up",
    "make_pr_client",
    "owner_repo_from_clone_url",
]


@runtime_checkable
class PRClient(Protocol):
    """The forge operations the fleet performs.

    Deliberately the *existing* surface, verbatim, rather than an idealised one —
    the same choice :class:`~operations_center.adapters.board.BoardClient` made,
    for the same reason. Reshaping the API and introducing the seam in one step
    produces a migration that cannot be reviewed mechanically.

    Two known-GitHub-shaped operations are declared here as they exist rather
    than as they ought to be: :meth:`get_check_runs` (GitHub Checks, which Forgejo
    does not implement) and :meth:`get_branch_protection` (whose return shape the
    reviewer reads for ``required_status_checks.contexts`` and
    ``enforce_admins.enabled``). Declaring them honestly keeps the protocol a
    description of what callers actually depend on. Changing them is the work the
    Forgejo spec scopes, and it belongs in its own commit.
    """

    # ── pull requests ────────────────────────────────────────────────────────
    def create_pr(
        self,
        owner: str,
        repo: str,
        *,
        head: str,
        base: str,
        title: str,
        body: str = "",
    ) -> dict: ...

    def get_pr(self, owner: str, repo: str, pr_number: int) -> dict: ...

    def merge_pr(
        self, owner: str, repo: str, pr_number: int, *, merge_method: str = "squash"
    ) -> dict: ...

    def close_pr(self, owner: str, repo: str, pr_number: int) -> dict: ...

    def list_open_prs(self, owner: str, repo: str) -> list[dict]: ...

    def list_closed_prs(self, owner: str, repo: str) -> list[dict]: ...

    def find_pr_by_head(self, owner: str, repo: str, head_ref: str) -> dict | None: ...

    def get_mergeable(self, owner: str, repo: str, pr_number: int) -> bool | None: ...

    def update_pr_description(
        self, owner: str, repo: str, pr_number: int, body: str
    ) -> dict: ...

    def create_and_merge(
        self,
        owner: str,
        repo: str,
        *,
        head: str,
        base: str,
        title: str,
        body: str = "",
        merge_method: str = "squash",
    ) -> str: ...

    # ── diffs and files ──────────────────────────────────────────────────────
    def list_pr_files(self, owner: str, repo: str, pr_number: int) -> list[str]: ...

    def get_pr_diff(self, owner: str, repo: str, pr_number: int) -> str: ...

    def get_file_content(
        self, owner: str, repo: str, path: str, ref: str
    ) -> tuple[str, str] | None: ...

    def update_file(
        self,
        owner: str,
        repo: str,
        path: str,
        *,
        new_text: str,
        message: str,
        branch: str,
        blob_sha: str,
    ) -> bool: ...

    # ── review ───────────────────────────────────────────────────────────────
    def list_pr_comments(self, owner: str, repo: str, pr_number: int) -> list[dict]: ...

    def list_pr_review_comments(
        self, owner: str, repo: str, pr_number: int
    ) -> list[dict]: ...

    def list_pr_reviews(self, owner: str, repo: str, pr_number: int) -> list[dict]: ...

    def pr_has_changes_requested(
        self, owner: str, repo: str, pr_number: int
    ) -> bool: ...

    def post_comment(self, owner: str, repo: str, pr_number: int, body: str) -> dict: ...

    def update_comment(self, owner: str, repo: str, comment_id: int, body: str) -> dict: ...

    def get_pr_reactions(self, owner: str, repo: str, pr_number: int) -> list[dict]: ...

    def get_comment_reactions(
        self, owner: str, repo: str, comment_id: int
    ) -> list[dict]: ...

    # ── CI signal ────────────────────────────────────────────────────────────
    def set_commit_status(
        self,
        owner: str,
        repo: str,
        sha: str,
        *,
        state: str,
        context: str,
        description: str = "",
        target_url: str | None = None,
    ) -> dict: ...

    def get_check_runs(self, owner: str, repo: str, ref: str) -> list[dict]: ...

    def get_failed_checks(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        *,
        pr_data: dict | None = None,
        ignored_checks: list[str] | None = None,
    ) -> list[str]: ...

    def get_incomplete_checks(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        *,
        pr_data: dict | None = None,
        ignored_checks: list[str] | None = None,
    ) -> list[str]: ...

    def get_completed_checks(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        *,
        pr_data: dict | None = None,
        ignored_checks: list[str] | None = None,
    ) -> list[str]: ...

    # ── branches ─────────────────────────────────────────────────────────────
    def get_branch_head(self, owner: str, repo: str, branch: str) -> str | None: ...

    def get_branch_protection(
        self, owner: str, repo: str, branch: str
    ) -> dict | None: ...

    def delete_branch(self, owner: str, repo: str, branch: str) -> None: ...


# ── pure helpers ─────────────────────────────────────────────────────────────
#
# Both were static methods on `GitHubPRClient`. Neither touches the network, the
# token, or `self`; callers were importing a client class to reach a function.


_CLONE_URL_RE = re.compile(r"[:/]([^/]+)/([^/]+?)(?:\.git)?$")


def owner_repo_from_clone_url(clone_url: str) -> tuple[str, str]:
    """Parse owner/repo from an https or ssh clone URL.

    Forge-agnostic by construction — it matches the last two path segments, so a
    Forgejo or self-hosted URL parses exactly as a GitHub one does::

        git@github.com:owner/repo.git
        https://github.com/owner/repo.git
        https://forge.internal/owner/repo.git
    """
    m = _CLONE_URL_RE.search(clone_url)
    if not m:
        raise ValueError(f"Cannot parse owner/repo from clone URL: {clone_url!r}")
    return m.group(1), m.group(2)


def has_thumbs_up(reactions: list[dict]) -> bool:
    """True if any reaction is a 👍."""
    return any(r["content"] == "+1" for r in reactions)


# ── construction ─────────────────────────────────────────────────────────────


def make_pr_client(settings: Any) -> PRClient:
    """Build the configured PR client.

    The one place that names a concrete forge. Kept byte-compatible with the
    construction every caller already performs — `settings.git_token()`, then the
    client — so adopting it cannot change behaviour.

    There is intentionally no backend switch yet. The board factory has one
    because a Forgejo board client exists; no Forgejo PR client does, and
    ``docs/specs/forgejo-pr-adapter.md`` argues it should not be written before
    the ``enforce_admins`` question is settled. A config knob selecting a backend
    that cannot be built would advertise a capability the fleet does not have.
    """
    token = settings.git_token()
    if not token:
        raise RuntimeError("no git token — set GIT_TOKEN in .env")

    from operations_center.adapters.github_pr import GitHubPRClient

    return GitHubPRClient(token)
