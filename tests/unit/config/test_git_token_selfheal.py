# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""git_token() self-heals when the token env var is empty (boot keyring race).

A fleet started at boot (systemd linger) sources .env before the login keyring
is unlocked, so GITHUB_TOKEN/GIT_TOKEN get exported EMPTY and stay empty for
the life of the process. git_token() must fall back to `gh auth token` at call
time and cache the recovered value back into os.environ so all consumers
(including the board-worker env passthrough) heal without a manual restart.
"""

import subprocess
from types import SimpleNamespace

import pytest

from operations_center.config import settings as settings_mod


@pytest.fixture
def settings():
    s = SimpleNamespace(git=SimpleNamespace(token_env="TEST_GIT_TOKEN"))
    return s


def _git_token(s):
    return settings_mod.Settings.git_token(s)


def test_env_var_set_returns_it_without_subprocess(settings, monkeypatch):
    monkeypatch.setenv("TEST_GIT_TOKEN", "tok-from-env")

    def boom(*a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("gh fallback must not run when env var is set")

    monkeypatch.setattr(settings_mod.subprocess, "run", boom)
    assert _git_token(settings) == "tok-from-env"


def test_empty_env_var_recovers_via_gh_and_caches(settings, monkeypatch):
    monkeypatch.setenv("TEST_GIT_TOKEN", "")
    monkeypatch.setattr(settings_mod.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(
        settings_mod.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout="tok-recovered\n", returncode=0),
    )
    assert _git_token(settings) == "tok-recovered"
    # cached back so passthrough/env consumers see it too
    import os

    assert os.environ["TEST_GIT_TOKEN"] == "tok-recovered"


def test_gh_failure_degrades_to_none(settings, monkeypatch):
    monkeypatch.setenv("TEST_GIT_TOKEN", "")
    monkeypatch.setattr(settings_mod.shutil, "which", lambda _: "/usr/bin/gh")

    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="gh", timeout=10)

    monkeypatch.setattr(settings_mod.subprocess, "run", raise_timeout)
    assert _git_token(settings) is None
    import os

    assert os.environ["TEST_GIT_TOKEN"] == ""


def test_gh_missing_degrades_to_none(settings, monkeypatch):
    monkeypatch.setenv("TEST_GIT_TOKEN", "")
    monkeypatch.setattr(settings_mod.shutil, "which", lambda _: None)
    assert _git_token(settings) is None


def test_token_env_none_returns_none(monkeypatch):
    s = SimpleNamespace(git=SimpleNamespace(token_env=None))
    assert _git_token(s) is None
