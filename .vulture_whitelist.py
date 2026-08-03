# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Vulture whitelist — names vulture reports as dead that a signature requires.

Custodian's vulture adapter passes this file as an extra scan path when it
exists (see `custodian/adapters/vulture.py`), so every name referenced here
counts as used. Vulture flags unused *parameters* at 100% confidence, which is
correct for genuinely dead code but wrong whenever an external contract dictates
the signature — a protocol method, a pytest hook, a fixture requested purely for
its side effect, or a lambda that must mirror the callee it replaces.

Scope discipline: this file is for names a contract forces us to accept, plus
compat shims the source itself already documents as deliberate. Genuinely unused
parameters are NOT listed here — they are left visible so the gate reports them.

Vulture matches on the bare NAME, not the location, so an entry suppresses that
identifier repo-wide. Keep the list minimal and justified for that reason.
"""

# --- Language / stdlib protocols ------------------------------------------
# `__exit__(self, exc_type, exc_val, tb)` — the context-manager protocol fixes
# the signature; this implementation only needs to release the lock.
exc_val  # noqa: B018  # src/operations_center/audit_dispatch/locks.py:103

# --- pytest hook specifications -------------------------------------------
# `pytest_sessionfinish(session, exitstatus)` — pytest calls hooks by keyword
# against its own hookspec, so the parameter must exist whether or not it is read.
exitstatus  # noqa: B018  # tests/conftest.py:301, observer/pytest_flaky_plugin.py:91

# --- pytest fixtures requested for their side effects ---------------------
# Naming the fixture in the signature is what activates it; the body has no
# reason to reference the value.
valid_console_dir  # noqa: B018  # tests/unit/detectors/test_r{1,2}_console_*.py
no_cl_env  # noqa: B018  # tests/unit/execution/test_coordinator_cl_wrap.py:78
monkeypatch_modules  # noqa: B018  # tests/unit/execution/test_workspace_cov.py:695

# --- Test doubles that must mirror the signature they replace -------------
# monkeypatch/lambda stubs are called with the real callee's arguments, so they
# have to accept them even when the stub ignores them.
lg  # noqa: B018  # lambda pm, rr, lg — test_repo_graph_factory_cov.py
indent  # noqa: B018  # lambda indent=2 mirroring model_dump_json — *_cov.py
expected_kind  # noqa: B018  # def _raise(_path, expected_kind=None) — graph_doctor

# --- Compat shims the source already documents as deliberate --------------
# Both carry an in-source comment stating the parameter is retained on purpose
# to avoid churning callers; one already suppresses ruff ARG002. Listed here so
# the two linters agree rather than one of them staying permanently red.
max_rewrite_attempts  # noqa: B018  # spec_author/phase_orchestrator.py:141
queue_threshold  # noqa: B018  # spec_author/trigger.py:26
