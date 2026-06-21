# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Host-side wheelhouse pre-provisioning for the sandboxed executor.

The bwrap sandbox has no pypi egress — the L7/SNI allowlist permits only the
model endpoint + github + localhost (D-SBX-2) — so the executor's in-sandbox
dev-install (`pip install -e .[dev]`) can't reach pypi and fails at workspace
bootstrap. Rather than loosen the allowlist (pip deps are a supply-chain
inject/exfil vector), we PRE-PROVISION: build a wheelhouse of the repo's full
dependency closure (pypi packages AND git deps, all baked into wheels) on the
HOST while egress is unrestricted, so the in-sandbox install runs fully offline
via ``pip install --no-index --find-links <wheelhouse>``.

Event-driven + self-maintaining (no cron / no system scheduler): the executor
calls :func:`ensure_wheelhouse` on the host right before dispatching the
sandboxed run. It rebuilds ONLY when the repo's dependency fingerprint (a hash of
its pyproject/lock files) changed or the wheelhouse is missing — a fresh
wheelhouse is a fast no-op. **Fail-open (§0.1):** any build failure returns
``None`` and the caller proceeds without provisioning (the in-sandbox install
will then fail and the task degrades/requeues, never a halt).
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Files whose contents define the dependency closure — any change triggers a
# rebuild. Lock files (if present) pin git SHAs too, so they catch git-dep drift
# that a bare pyproject hash would miss; pyproject alone is the common case.
_DEP_FILES = (
    "pyproject.toml",
    "uv.lock",
    "poetry.lock",
    "requirements.txt",
    "requirements-dev.txt",
)

# Built once per host; bound read-only into the sandbox. Under ~/.cache so it
# survives across runs and is naturally per-user.
_CACHE_ROOT = Path.home() / ".cache" / "oc-wheelhouse"

# What to wheel: the package + its dev extra, plus the build backend (the
# editable install runs --no-build-isolation in the sandbox, so setuptools/wheel
# must be present as wheels too).
_EXTRA_BUILD_REQS = ("setuptools", "wheel", "pip")

# tiktoken downloads its BPE encoding files from openaipublic.blob.core.windows.net
# on first use — a host the egress allowlist denies — so the executor's token
# accounting fails in the sandbox. Pre-populate the encodings into a host cache
# bound into the sandbox (TIKTOKEN_CACHE_DIR) so they resolve offline.
_TIKTOKEN_CACHE = Path.home() / ".cache" / "oc-tiktoken"
_TIKTOKEN_ENCODINGS = ("cl100k_base", "o200k_base", "p50k_base", "r50k_base")


def wheelhouse_dir(repo_key: str) -> Path:
    """The wheelhouse directory for a repo (stable path, bound into the sandbox)."""
    return _CACHE_ROOT / repo_key


def _fingerprint(repo_path: Path) -> str:
    """Hash of the repo's dependency-defining files; changes ⇒ rebuild."""
    h = hashlib.sha256()
    for name in _DEP_FILES:
        f = repo_path / name
        if f.exists():
            h.update(name.encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:16]


def _is_fresh(wh: Path, fingerprint: str) -> bool:
    fp_file = wh / ".fingerprint"
    return (
        wh.is_dir()
        and fp_file.is_file()
        and fp_file.read_text(encoding="utf-8").strip() == fingerprint
        and any(wh.glob("*.whl"))
    )


def ensure_wheelhouse(
    repo_key: str,
    repo_local_path: str | None,
    *,
    python_bin: str,
    extras: str = ".[dev]",
    timeout: int = 1200,
) -> Path | None:
    """Ensure a fresh wheelhouse exists for ``repo_key``; return its dir or None.

    Builds ``pip wheel <extras> setuptools wheel pip`` from the host repo at
    ``repo_local_path`` (online, unsandboxed) when missing or when the dependency
    fingerprint changed. A fresh wheelhouse is a fast no-op. ``None`` on any
    failure or when the repo has no ``pyproject.toml`` (fail-open).
    """
    if not repo_local_path:
        return None
    repo_path = Path(repo_local_path)
    if not (repo_path / "pyproject.toml").is_file():
        return None  # nothing to provision

    fingerprint = _fingerprint(repo_path)
    wh = wheelhouse_dir(repo_key)
    if _is_fresh(wh, fingerprint):
        return wh

    try:
        wh.mkdir(parents=True, exist_ok=True)
        for stale in wh.glob("*.whl"):  # drop the previous closure before rebuild
            stale.unlink()
        logger.info("wheelhouse: building for %s (deps changed or missing)", repo_key)
        subprocess.run(
            [python_bin, "-m", "pip", "wheel", extras, *_EXTRA_BUILD_REQS, "-w", str(wh)],
            cwd=repo_path,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        (wh / ".fingerprint").write_text(fingerprint, encoding="utf-8")
        logger.info("wheelhouse: built %d wheels for %s", len(list(wh.glob("*.whl"))), repo_key)
        return wh
    except Exception as exc:  # noqa: BLE001 — provisioning is best-effort, never fatal
        logger.warning("wheelhouse: build failed for %s — %s", repo_key, exc)
        return None


def ensure_tiktoken_cache(python_bin: str) -> Path | None:
    """Pre-populate tiktoken's encoding cache on the host so the sandboxed
    executor's token accounting works offline. Idempotent: a non-empty cache is a
    no-op; otherwise it loads the encodings once (online) in a subprocess with
    ``TIKTOKEN_CACHE_DIR`` pointed at the cache. ``None`` on failure (fail-open)."""
    cache = _TIKTOKEN_CACHE
    if cache.is_dir() and any(cache.glob("*")):
        return cache
    try:
        cache.mkdir(parents=True, exist_ok=True)
        code = (
            "import tiktoken\n"
            f"for e in {list(_TIKTOKEN_ENCODINGS)!r}:\n"
            "    try: tiktoken.get_encoding(e)\n"
            "    except Exception: pass\n"
        )
        subprocess.run(
            [python_bin, "-c", code],
            env={**os.environ, "TIKTOKEN_CACHE_DIR": str(cache)},
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as exc:  # noqa: BLE001 — provisioning is best-effort, never fatal
        logger.warning("tiktoken cache provision failed — %s", exc)
    return cache if cache.is_dir() and any(cache.glob("*")) else None


def provision_env(
    repo_key: str, repo_local_path: str | None, *, python_bin: str
) -> dict[str, str]:
    """Host-side pre-provisioning env to merge into the executor env when the
    bwrap sandbox is on (else ``{}``, fail-open). Wires the offline wheelhouse
    (``OC_WHEELHOUSE``) and the tiktoken encoding cache (``TIKTOKEN_CACHE_DIR``) —
    both bound into the sandbox — so the executor needs no pypi / CDN egress.
    """
    if os.environ.get("OC_BWRAP_SANDBOX") != "1":
        return {}
    out: dict[str, str] = {}
    wh = ensure_wheelhouse(repo_key, repo_local_path, python_bin=python_bin)
    if wh is not None:
        out["OC_WHEELHOUSE"] = str(wh)
    tk = ensure_tiktoken_cache(python_bin)
    if tk is not None:
        out["TIKTOKEN_CACHE_DIR"] = str(tk)
    return out


__all__ = [
    "ensure_tiktoken_cache",
    "ensure_wheelhouse",
    "provision_env",
    "wheelhouse_dir",
]
