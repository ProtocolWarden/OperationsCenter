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


def wheelhouse_env(
    repo_key: str, repo_local_path: str | None, *, python_bin: str
) -> dict[str, str]:
    """``{"OC_WHEELHOUSE": <dir>}`` to merge into the executor env when the bwrap
    sandbox is enabled and provisioning succeeds, else ``{}`` (fail-open).

    Self-contained so dispatch wires it in one line: reads ``OC_BWRAP_SANDBOX``
    (provisioning is only needed when the executor is sandboxed) and runs the
    host-side :func:`ensure_wheelhouse`.
    """
    if os.environ.get("OC_BWRAP_SANDBOX") != "1":
        return {}
    wh = ensure_wheelhouse(repo_key, repo_local_path, python_bin=python_bin)
    return {"OC_WHEELHOUSE": str(wh)} if wh is not None else {}


__all__ = ["ensure_wheelhouse", "wheelhouse_dir", "wheelhouse_env"]
