# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Spec authoring shared library.

This package was historically called ``spec_director`` and powered a
standalone watcher (``operations_center.entrypoints.spec_director``).

ADR 0007 Phase F (2026-05-22) retired that watcher: trigger detection now
lives in ``operations_center.entrypoints.spec_trigger``, hygiene + active.json
projection in ``operations_center.entrypoints.spec_hygiene``, and phase
orchestration runs inside the ``spec-author`` task-kind handler in
``operations_center.entrypoints.board_worker``.

The package was renamed to ``spec_author`` to match the task-kind it now
fronts; it is shared infrastructure, not a watcher. See
``docs/architecture/adr/0007-spec-director-refactor.md``.
"""
