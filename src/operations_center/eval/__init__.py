# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Capability EVAL — self-healing agent-quality guard (HARNESS_TRUST_HARDENING §4).

The exam/answer-key split that lets the fleet grade itself without a human in the
per-correction loop:

* ``corpus``      — append-only, hash-chained case ledger (tamper-evident).
* ``signing``     — Ed25519 operator answer-key signatures (the one human anchor).
* ``replay``      — deterministic blocking gate vs the code-computed verdict.
* ``critic``      — non-blocking, different-family-model drift monitor.
* ``constitution``— monotonic baseline floor + report-only→blocking graduation.
* ``verify``      — the required CI check tying them together.

Build status: scaffolding complete and exercised by unsigned candidate cases; the
gate is in report-only mode until the operator anchors a key and signs the seed
cases (the only deferred, irreducibly-human step)."""
