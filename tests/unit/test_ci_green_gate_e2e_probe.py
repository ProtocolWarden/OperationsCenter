# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""E2E probe — a deliberately-failing test used ONLY on a throwaway branch to
verify the reviewer's CI-green merge gate refuses to self-merge a red PR.

This is never merged to main; the branch is deleted after the verification.
"""


def test_ci_green_gate_e2e_probe() -> None:
    # Intentional failure to make the "Test (pytest)" check RED so we can confirm
    # _merge_and_done refuses to merge a PR whose CI is not green.
    assert False, "intentional e2e probe failure (CI-green merge-gate verification)"
