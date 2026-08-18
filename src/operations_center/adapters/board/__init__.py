# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""The board seam: what the fleet needs from a task board, and one place to build it.

OC's board is Plane today and will not be. Replacing it is currently a 37-file
change, not because the surface is large — it is eleven operations — but because
every one of those files imports `PlaneClient` by name, constructs it from the
same four settings fields, and type-hints against the concrete class. Ten of them
have independently hand-rolled the identical `_make_plane_client()` helper, which
is the clearest possible evidence that the missing piece is a shared one.

This module is that piece:

* :class:`BoardClient` — the operations the fleet actually performs, named in
  board terms rather than Plane's. Callers depend on this.
* :func:`make_board_client` — the single construction site. Swapping backends
  changes this function, not every caller.

`PlaneClient` already satisfies the protocol structurally, so adopting this is a
rename at each call site, not a behavioural change. That is deliberate: the
migration should be boring and reviewable, and any behaviour change should be its
own commit.

Nothing outside ``adapters/`` should import `PlaneClient` directly.
``tests/unit/adapters/test_board_seam.py`` enforces that against a shrinking
allowlist, so the boundary tightens instead of eroding.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover
    from operations_center.domain.models import BoardTask

__all__ = ["BoardClient", "make_board_client"]


@runtime_checkable
class BoardClient(Protocol):
    """The board operations the fleet performs.

    Deliberately the *existing* surface, verbatim, rather than an idealised one.
    A protocol that reshapes the API at the same time as introducing the seam
    cannot be adopted mechanically, and a migration that is not mechanical is one
    where regressions hide. Reshaping (for instance, `list_issues` growing a
    filter so backends need not return the whole board) belongs in later commits,
    once callers depend on this name instead of `PlaneClient`.
    """

    # ── reads ────────────────────────────────────────────────────────────────
    def fetch_issue(self, task_id: str) -> dict[str, Any]: ...

    def fetch_project(self) -> dict[str, Any]: ...

    def list_issues(self) -> list[dict[str, Any]]: ...

    def list_states(self) -> list[dict[str, Any]]: ...

    def list_labels(self, *, force_refresh: bool = False) -> list[dict[str, Any]]: ...

    def list_comments(self, task_id: str) -> list[dict[str, Any]]: ...

    # ── translation into the domain ──────────────────────────────────────────
    def to_board_task(self, issue: dict[str, Any]) -> BoardTask: ...

    # ── writes ───────────────────────────────────────────────────────────────
    def transition_issue(self, task_id: str, state: str) -> None: ...

    def create_issue(
        self,
        *,
        name: str,
        description: str,
        state: str | None = None,
        label_names: list[str] | None = None,
    ) -> dict[str, Any]: ...

    def set_priority(self, task_id: str, priority: str) -> None: ...

    def update_issue_description(self, task_id: str, description: str) -> None: ...

    def update_issue_labels(self, task_id: str, label_names: list[str]) -> None: ...

    def comment_issue(self, task_id: str, comment_markdown: str) -> None: ...

    # ── lifecycle ────────────────────────────────────────────────────────────
    def close(self) -> None: ...


def make_board_client(settings: Any) -> BoardClient:
    """Build the configured board client.

    The one place that names a concrete backend. Every caller that used to
    construct `PlaneClient` from `settings.plane.*` calls this instead, so
    pointing the fleet at a different board is a change here and nowhere else.

    Kept byte-compatible with the ten hand-rolled `_make_plane_client()` helpers
    it replaces — same four fields, same token accessor — so adopting it cannot
    change behaviour.
    """
    backend = getattr(settings, "board_backend", "plane")
    if not isinstance(backend, str):
        # A test double (`MagicMock()`) answers every attribute with a child
        # mock, so `getattr` never reaches its default and `backend` becomes a
        # Mock — which matches neither "plane" nor "forgejo" and raised
        # "unknown board_backend <MagicMock ...>" from deep inside the factory.
        # Real Settings validates this field as a str, so a non-string here means
        # "nothing configured this", not "someone chose backend 42".
        backend = "plane"

    if backend == "forgejo":
        from operations_center.adapters.forgejo import ForgejoClient

        cfg = settings.forgejo
        if cfg is None:
            raise RuntimeError(
                "board_backend is 'forgejo' but no `forgejo:` settings block is "
                "configured — refusing to fall back to Plane, because a silent "
                "fallback would point the fleet at the board it is migrating off"
            )
        return ForgejoClient(
            base_url=cfg.base_url,
            api_token=settings.forgejo_token(),
            owner=cfg.owner,
            repo=cfg.repo,
        )

    if backend != "plane":
        raise RuntimeError(f"unknown board_backend {backend!r} (plane, forgejo)")

    from operations_center.adapters.plane import PlaneClient

    board = settings.plane
    return PlaneClient(
        base_url=board.base_url,
        api_token=settings.plane_token(),
        workspace_slug=board.workspace_slug,
        project_id=board.project_id,
    )
