# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Sandbox token hardening (audit Track A6).

The long-lived operator git token must never enter the executor sandbox when a
GitHub App is configured — a per-task installation token (repo-scoped, ~1h
TTL) replaces it in the forwarded env. Minting failure fails CLOSED per task
unless explicitly opted out.
"""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest

from operations_center.adapters import github_app
from operations_center.entrypoints.board_worker import _subprocess
from operations_center.entrypoints.board_worker.sandbox import ContainmentRequiredError

CLONE_URL = "https://github.com/acme/widgets.git"


def _settings(app_id=None, key_path=None):
    return SimpleNamespace(
        git=SimpleNamespace(github_app_id=app_id, github_app_key_path=key_path)
    )


def test_unconfigured_app_leaves_env_and_warns_once(monkeypatch, caplog):
    monkeypatch.setattr(_subprocess, "_warned_long_lived_token", False)
    monkeypatch.setenv("OC_BWRAP_SANDBOX", "1")
    env = {"GITHUB_TOKEN": "gho_longlived"}
    with caplog.at_level("WARNING"):
        out = _subprocess.harden_git_token(env, settings=_settings(), clone_url=CLONE_URL)
    assert out["GITHUB_TOKEN"] == "gho_longlived"
    assert any("long_lived_token_in_sandbox" in r.message for r in caplog.records)


def test_no_token_in_env_is_a_noop():
    out = _subprocess.harden_git_token(
        {"PATH": "/bin"}, settings=_settings("1", "/k"), clone_url=CLONE_URL
    )
    assert out == {"PATH": "/bin"}


def test_configured_app_swaps_all_token_vars(monkeypatch):
    monkeypatch.setattr(
        "operations_center.adapters.github_app.mint_installation_token",
        lambda **kw: "ghs_ephemeral",
    )
    env = {"GITHUB_TOKEN": "gho_longlived", "GIT_TOKEN": "gho_longlived", "PATH": "/bin"}
    out = _subprocess.harden_git_token(
        env, settings=_settings("12345", "/tmp/key.pem"), clone_url=CLONE_URL
    )
    assert out["GITHUB_TOKEN"] == "ghs_ephemeral"
    assert out["GIT_TOKEN"] == "ghs_ephemeral"
    assert "gho_longlived" not in out.values()
    # original env untouched (hardened copy returned)
    assert env["GITHUB_TOKEN"] == "gho_longlived"


def test_mint_failure_fails_closed_by_default(monkeypatch):
    def boom(**kw):
        raise github_app.GitHubAppTokenError("api down")

    monkeypatch.setattr("operations_center.adapters.github_app.mint_installation_token", boom)
    monkeypatch.delenv("OC_APP_TOKEN_REQUIRED", raising=False)
    with pytest.raises(ContainmentRequiredError):
        _subprocess.harden_git_token(
            {"GITHUB_TOKEN": "gho_x"}, settings=_settings("1", "/k"), clone_url=CLONE_URL
        )


def test_mint_failure_degrades_when_opted_out(monkeypatch, caplog):
    def boom(**kw):
        raise github_app.GitHubAppTokenError("api down")

    monkeypatch.setattr("operations_center.adapters.github_app.mint_installation_token", boom)
    monkeypatch.setenv("OC_APP_TOKEN_REQUIRED", "0")
    with caplog.at_level("ERROR"):
        out = _subprocess.harden_git_token(
            {"GITHUB_TOKEN": "gho_x"}, settings=_settings("1", "/k"), clone_url=CLONE_URL
        )
    assert out["GITHUB_TOKEN"] == "gho_x"
    assert any("token_hardening_degraded" in r.message for r in caplog.records)


# ── github_app adapter ────────────────────────────────────────────────────────


def _rsa_pem() -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def test_app_jwt_shape():
    token = github_app._app_jwt("42", _rsa_pem())
    header_b64, payload_b64, sig_b64 = token.split(".")

    def _decode(part: str) -> dict:
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))

    assert _decode(header_b64) == {"alg": "RS256", "typ": "JWT"}
    payload = _decode(payload_b64)
    assert payload["iss"] == "42"
    assert payload["exp"] - payload["iat"] == 600  # 60s backdate + 540s ttl
    assert sig_b64


def test_mint_installation_token_happy_path(monkeypatch, tmp_path):
    key_file = tmp_path / "app.pem"
    key_file.write_bytes(_rsa_pem())

    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(("GET", url))
        return SimpleNamespace(status_code=200, json=lambda: {"id": 77}, text="")

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(("POST", url, json))
        assert json["repositories"] == ["widgets"]
        assert json["permissions"] == {"contents": "write", "pull_requests": "write"}
        return SimpleNamespace(
            status_code=201, json=lambda: {"token": "ghs_new"}, text=""
        )

    monkeypatch.setattr(github_app.httpx, "get", fake_get)
    monkeypatch.setattr(github_app.httpx, "post", fake_post)
    token = github_app.mint_installation_token(
        app_id="42", private_key_path=key_file, owner="acme", repo="widgets"
    )
    assert token == "ghs_new"
    assert calls[0][1].endswith("/repos/acme/widgets/installation")
    assert "/app/installations/77/access_tokens" in calls[1][1]


def test_mint_installation_token_missing_key_raises(tmp_path):
    with pytest.raises(github_app.GitHubAppTokenError):
        github_app.mint_installation_token(
            app_id="42", private_key_path=tmp_path / "nope.pem", owner="a", repo="b"
        )
