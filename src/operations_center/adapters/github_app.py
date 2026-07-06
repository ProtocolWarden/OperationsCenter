# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Per-task GitHub App installation tokens (sandbox token hardening, Track A6).

The board worker used to forward the operator's long-lived OAuth token
(``gho_…``, write-capable across every owned repo, valid until revoked) into
the bwrap sandbox — the environment that runs arbitrary LLM-generated code.
This module mints a **per-task installation token** instead: ``ghs_…``, fixed
~1 hour TTL, scoped to exactly the task's repository with
``contents:write`` + ``pull_requests:write``. If the sandbox leaks it, the
blast radius is one repo for under an hour, and the credential cannot be
renewed from inside (the App private key never enters the sandbox).

Flow (all in the PARENT worker process, before the sandbox spawns):
  1. Build a short-lived RS256 JWT from the App id + private key.
  2. ``GET /repos/{owner}/{repo}/installation`` → installation id.
  3. ``POST /app/installations/{id}/access_tokens`` with the repo +
     permission restriction → the scoped token.

Spec: PlatformManifest docs/architecture/sandbox-token-hardening-spec.md.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

logger = logging.getLogger(__name__)

_API = "https://api.github.com"
# The token must outlive clock skew on GitHub's side; iat is backdated 60s and
# the JWT expires after 9 minutes (GitHub caps at 10).
_JWT_BACKDATE_S = 60
_JWT_TTL_S = 540


class GitHubAppTokenError(RuntimeError):
    """Installation-token minting failed (config, key, or API error)."""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _app_jwt(app_id: str, private_key_pem: bytes) -> str:
    """RS256 App JWT built directly with `cryptography` (no PyJWT dependency)."""
    try:
        key = serialization.load_pem_private_key(private_key_pem, password=None)
    except (ValueError, TypeError) as exc:
        raise GitHubAppTokenError(f"cannot load GitHub App private key: {exc}") from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise GitHubAppTokenError("GitHub App private key must be an RSA key (RS256)")
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, ensure_ascii=False).encode())
    payload = _b64url(
        json.dumps(
            {"iat": now - _JWT_BACKDATE_S, "exp": now + _JWT_TTL_S, "iss": str(app_id)},
            ensure_ascii=False,
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_b64url(signature)}"


def mint_installation_token(
    *,
    app_id: str,
    private_key_path: str | Path,
    owner: str,
    repo: str,
    api_url: str = _API,
) -> str:
    """Mint a repo-scoped, ~1h-TTL installation token for ``owner/repo``.

    Raises GitHubAppTokenError on any failure — the caller decides whether to
    degrade to the configured long-lived token (observable warning) or fail.
    """
    try:
        pem = Path(private_key_path).read_bytes()
    except OSError as exc:
        raise GitHubAppTokenError(
            f"cannot read GitHub App key file {private_key_path}: {exc}"
        ) from exc
    jwt = _app_jwt(app_id, pem)
    headers = {
        "Authorization": f"Bearer {jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        inst = httpx.get(
            f"{api_url}/repos/{owner}/{repo}/installation", headers=headers, timeout=30
        )
        if inst.status_code != 200:
            raise GitHubAppTokenError(
                f"installation lookup for {owner}/{repo} failed: "
                f"{inst.status_code} {inst.text[:200]}"
            )
        installation_id = inst.json()["id"]
        resp = httpx.post(
            f"{api_url}/app/installations/{installation_id}/access_tokens",
            headers=headers,
            json={
                "repositories": [repo],
                "permissions": {"contents": "write", "pull_requests": "write"},
            },
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise GitHubAppTokenError(f"GitHub App token request failed: {exc}") from exc
    if resp.status_code != 201:
        raise GitHubAppTokenError(
            f"installation token mint failed: {resp.status_code} {resp.text[:200]}"
        )
    token = resp.json().get("token")
    if not token:
        raise GitHubAppTokenError("installation token response had no 'token' field")
    logger.info(
        'github_app: minted per-task installation token for %s/%s '
        '{"event": "app_token_minted", "repo": "%s/%s"}',
        owner,
        repo,
        owner,
        repo,
    )
    return token


__all__ = ["GitHubAppTokenError", "mint_installation_token"]
