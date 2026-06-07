# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""OperationsCenter plugin detectors for Custodian.

Remaining OC-specific detectors (cannot be expressed by native Custodian config):

  OC3  Orphaned entrypoints  — module namespaces under operations_center.entrypoints.*
                               that are never imported or referenced anywhere outside
                               their own directory. OC-specific: knows the entrypoints/
                               directory contract.

  OC8  Doc phantom symbols   — K1 equivalent but also matches field-definition syntax
                               (``name: TypeAnnotation``) in addition to ``def``/``class``,
                               reducing false positives for DTO field references in docs.
                               Migrates fully once K1 gains field-def awareness.

  OC10 team_executor max_concurrent must be 1 — reads
                               config/operations_center.local.yaml (if present) and confirms
                               backend_caps.team_executor.max_concurrent == 1. Prevents
                               inadvertent concurrency widening from the watchdog loop or
                               autonomy-cycle. Silently passes when the local config is
                               absent (CI / fresh clone).

Superseded and removed (native Custodian covers them):
  OC1  → U1–U3 (stub/unimplemented detector family)
  OC2  → C1 (deferred-aware TODO; domain-path exclusions in audit.exclude_paths.C1)
  OC4  → RUFF adapter
  OC5  → T3 (unconditional skips; OC hints in audit.t3_env_gate_hints)
  OC6  → dead placeholder (removed)
  OC7  → F3 (Pydantic BaseModel field liveness)
  OC9  → K2 (doc value drift; OC known_values in audit.known_values)
"""

from __future__ import annotations

import re
from pathlib import Path

from custodian.audit_kit.detector import LOW, MEDIUM, AuditContext, Detector, DetectorResult

# ── helpers ──────────────────────────────────────────────────────────────────


def _py_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


# ── R1: .console/ directory presence validator ───────────────────────────────


def _detect_r1_console_presence(ctx: AuditContext) -> DetectorResult:
    """Validate .console/ directory and required core files exist.

    R1 is the presence validator for .console/ reconciliation — verifies that
    the .console/ directory exists and contains all required core files:
    task.md, guidelines.md, backlog.md, log.md, and workers.yaml.
    """
    console_root = ctx.repo_root / ".console"
    required_files = {"task.md", "guidelines.md", "backlog.md", "log.md", "workers.yaml"}

    # Check if .console/ directory exists
    if not console_root.exists():
        return DetectorResult(
            count=1,
            samples=[".console/ directory does not exist (CRITICAL)"],
        )

    if not console_root.is_dir():
        return DetectorResult(
            count=1,
            samples=[".console/ exists but is not a directory (CRITICAL)"],
        )

    # Check for missing required files
    missing_files = []
    for filename in sorted(required_files):
        file_path = console_root / filename
        if not file_path.exists():
            missing_files.append(filename)
        elif not file_path.is_file():
            missing_files.append(f"{filename} (not a file)")

    if missing_files:
        samples = [f".console/ missing required file: {f}" for f in missing_files]
        return DetectorResult(count=len(missing_files), samples=samples)

    return DetectorResult(count=0, samples=[])


# ── R2: .console/ budget and structure validator ─────────────────────────────


def _detect_r2_console_budget(ctx: AuditContext) -> DetectorResult:
    """Validate .console/ files have proper structure, size, and content.

    R2 is the budget validator for .console/ reconciliation — verifies that
    .console/ files have valid structure, required sections, and reasonable
    sizes. Complements R1 (which just checks presence).

    Validates:
    - File sizes within budget (each <100KB)
    - Files are valid UTF-8 (not corrupted)
    - task.md has required sections (Objective, Overall Plan, Current Stage)
    - backlog.md has required sections
    - workers.yaml is valid YAML
    """
    import yaml as _yaml

    console_root = ctx.repo_root / ".console"
    samples: list[str] = []

    if not console_root.exists() or not console_root.is_dir():
        return DetectorResult(count=0, samples=[])

    # Budget: 100KB max per file
    max_size_bytes = 100 * 1024
    for filename in ["task.md", "guidelines.md", "backlog.md", "log.md"]:
        filepath = console_root / filename
        if not filepath.exists():
            continue
        try:
            size = filepath.stat().st_size
            if size > max_size_bytes:
                samples.append(f".console/{filename} exceeds 100KB budget ({size} bytes)")
        except OSError:
            samples.append(f".console/{filename} cannot be read (permission denied)")

    # Validate UTF-8 encoding for all markdown files
    for filename in ["task.md", "guidelines.md", "backlog.md", "log.md"]:
        filepath = console_root / filename
        if not filepath.exists():
            continue
        try:
            filepath.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            samples.append(f".console/{filename} is not valid UTF-8 (corrupted)")
        except OSError:
            samples.append(f".console/{filename} cannot be read (permission denied)")

    # Validate task.md structure
    task_md = console_root / "task.md"
    if task_md.exists():
        try:
            content = task_md.read_text(encoding="utf-8")
            required_sections = ["Objective", "Overall Plan", "Current Stage"]
            missing_sections = []
            for section in required_sections:
                if f"## {section}" not in content:
                    missing_sections.append(section)
            if missing_sections:
                for section in missing_sections:
                    samples.append(f".console/task.md missing required section: ## {section}")
        except (OSError, UnicodeDecodeError):
            pass  # Already caught above

    # Validate backlog.md structure (at least one required section)
    backlog_md = console_root / "backlog.md"
    if backlog_md.exists():
        try:
            content = backlog_md.read_text(encoding="utf-8")
            # Must have at least one of the standard sections
            has_section = any(
                f"## {s}" in content for s in ["In Progress", "Up Next", "Done", "Backlog"]
            )
            if not has_section:
                samples.append(
                    ".console/backlog.md has no standard sections (In Progress/Up Next/Done)"
                )
        except (OSError, UnicodeDecodeError):
            pass  # Already caught above

    # Validate workers.yaml is valid YAML
    workers_yaml = console_root / "workers.yaml"
    if workers_yaml.exists():
        try:
            content = workers_yaml.read_text(encoding="utf-8")
            _yaml.safe_load(content)
        except _yaml.YAMLError:
            samples.append(".console/workers.yaml has YAML syntax error")
        except (OSError, UnicodeDecodeError):
            pass  # Already caught above

    return DetectorResult(count=len(samples), samples=samples[:10])


# ── OC3: orphaned entrypoints ─────────────────────────────────────────────────


def _detect_oc3_orphaned_entrypoints(ctx: AuditContext) -> DetectorResult:
    ep_root = ctx.src_root / "entrypoints"
    if not ep_root.is_dir():
        return DetectorResult(count=0, samples=[])
    samples: list[str] = []
    for sub in sorted(ep_root.iterdir()):
        if not sub.is_dir() or sub.name.startswith("_"):
            continue
        ref_count = 0
        rx = f"operations_center.entrypoints.{sub.name}"
        for root in (
            ctx.src_root,
            ctx.tests_root,
            ctx.repo_root / "scripts",
            ctx.repo_root / "docs",
        ):
            if not root.exists():
                continue
            for f in root.rglob("*"):
                if not f.is_file() or "__pycache__" in f.parts:
                    continue
                if str(f.relative_to(ctx.repo_root)).startswith(
                    f"src/operations_center/entrypoints/{sub.name}"
                ):
                    continue
                try:
                    if rx in f.read_text(errors="replace"):
                        ref_count += 1
                        break
                except OSError:
                    continue
            if ref_count:
                break
        pyproj = ctx.repo_root / "pyproject.toml"
        if pyproj.exists():
            try:
                if rx in pyproj.read_text():
                    ref_count += 1
            except OSError:
                pass
        if ref_count == 0:
            samples.append(f"entrypoints/{sub.name}/")
    return DetectorResult(count=len(samples), samples=samples[:10])


# ── OC8: doc phantom symbols (field-def aware) ────────────────────────────────


def _detect_oc8_phantom_symbols(ctx: AuditContext) -> DetectorResult:
    docs_root = ctx.repo_root / "docs"
    readme = ctx.repo_root / "README.md"
    files: list[Path] = [readme] if readme.exists() else []
    for sub in ("design", "architecture"):
        d = docs_root / sub
        if d.exists():
            files.extend(d.rglob("*.md"))

    sym_re = re.compile(r"`(_?[a-z][a-z0-9_]{7,})`")
    impl_marker_re = re.compile(
        r"\*\*Files:\*\*|\bImplementation:|see\s+`|defined in `|"
        r"\b(?:def|class)\s+|`\s*\(.*?\)\s*",
        re.IGNORECASE,
    )
    value_context_re = re.compile(
        r"(?:status|state|kind|name|value|id|type|family|key|column)s?\s*[:=]|"
        r"(?:enum|constant|literal)|"
        r"\bset\s+to\s+`|"
        r"\bone\s+of\s+",
        re.IGNORECASE,
    )

    src_text = ""
    src_test_text = ""
    for f in _py_files(ctx.src_root):
        try:
            src_text += f.read_text(errors="replace") + "\n"
        except OSError:
            continue
    if ctx.tests_root.exists():
        for f in _py_files(ctx.tests_root):
            try:
                src_test_text += f.read_text(errors="replace") + "\n"
            except OSError:
                continue

    audit_cfg = ctx.config.get("audit", {}) or {}
    common_words = set(audit_cfg.get("common_words", []) or [])
    stale_handlers = set(audit_cfg.get("stale_handlers", []) or [])

    field_def_re_template = r"^\s+{name}\s*:\s*[A-Za-z]"

    def _exists(name: str) -> bool:
        if name in common_words:
            return True
        if re.search(rf"\b(def|class)\s+{re.escape(name)}\b", src_text):
            return True
        if re.search(field_def_re_template.format(name=re.escape(name)), src_text, re.MULTILINE):
            return True
        if re.search(rf"\b(def|class)\s+{re.escape(name)}\b", src_test_text):
            return True
        if re.search(rf'"event"\s*:\s*"{re.escape(name)}"', src_text):
            return True
        return False

    deferred_words = ("deferred", "out of scope", "not yet implemented", "future:", "deprecated")
    seen: dict[str, tuple[Path, int]] = {}
    for f in files:
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        rel = str(f.relative_to(ctx.repo_root))
        if "/history/" in rel or "/audits/" in rel or "/archive/" in rel:
            continue
        current_section_deferred = False
        for i, line in enumerate(text.splitlines(), 1):
            lower = line.lower()
            if line.startswith("#"):
                current_section_deferred = any(w in lower for w in deferred_words)
                continue
            if current_section_deferred:
                continue
            if any(w in lower for w in deferred_words):
                continue
            if not impl_marker_re.search(line):
                continue
            if value_context_re.search(line):
                continue
            for m in sym_re.finditer(line):
                name = m.group(1)
                if name in seen or name in stale_handlers or _exists(name):
                    continue
                seen[name] = (f, i)

    samples = [
        f"{path.relative_to(ctx.repo_root)}:{ln}: `{name}` referenced but no def/class"
        for name, (path, ln) in sorted(seen.items())
    ]
    return DetectorResult(count=len(seen), samples=samples[:8])


# ── OC10: team_executor max_concurrent must be 1 ──────────────────────────────


def _detect_oc10_team_executor_max_concurrent(ctx: AuditContext) -> DetectorResult:
    """Confirm backend_caps.team_executor.max_concurrent == 1 in the local config.

    Silently passes when the local config is absent (CI / fresh clone) so this
    never blocks the test suite on machines that haven't run setup.
    """
    import yaml as _yaml  # optional dep — only installed in dev venv

    local_cfg = ctx.repo_root / "config" / "operations_center.local.yaml"
    if not local_cfg.exists():
        return DetectorResult(count=0, samples=[])
    try:
        data = _yaml.safe_load(local_cfg.read_text())
    except Exception:
        return DetectorResult(count=0, samples=[])

    actual = (data or {}).get("backend_caps", {}).get("team_executor", {}).get("max_concurrent")
    if actual is None:
        # Key absent — not a violation; may be inheriting default.
        return DetectorResult(count=0, samples=[])
    if actual != 1:
        return DetectorResult(
            count=1,
            samples=[
                f"config/operations_center.local.yaml: "
                f"backend_caps.team_executor.max_concurrent={actual!r} — must be 1 "
                f"(watchdog loop invariant; concurrent executor teams fight for host RAM)"
            ],
        )
    return DetectorResult(count=0, samples=[])


# ── OC11: managed-repo config schema sync ────────────────────────────────────


def _detect_oc11_schema_sync(ctx: AuditContext) -> DetectorResult:
    """Every Pydantic field in models.py must appear as a YAML key in example_managed_repo.yaml.

    Catches the case where a field is added to ManagedRepoConfig (or any nested
    model) but the operator-facing example template is not updated to match.
    """
    import ast as _ast

    import yaml as _yaml

    models_path = ctx.src_root / "operations_center" / "managed_repos" / "models.py"
    example_path = ctx.repo_root / "config" / "managed_repos" / "example_managed_repo.yaml"

    if not models_path.exists() or not example_path.exists():
        return DetectorResult(count=0, samples=[])

    # Collect all annotated field names from Pydantic model classes.
    try:
        tree = _ast.parse(models_path.read_text())
    except SyntaxError:
        return DetectorResult(count=0, samples=[])

    pydantic_bases = {"BaseModel"}
    model_class_names: set[str] = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.ClassDef):
            if any(
                (isinstance(b, _ast.Name) and b.id in pydantic_bases)
                or (isinstance(b, _ast.Attribute) and b.attr in pydantic_bases)
                for b in node.bases
            ):
                model_class_names.add(node.name)

    field_names: set[str] = set()
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.ClassDef) or node.name not in model_class_names:
            continue
        for stmt in node.body:
            if isinstance(stmt, _ast.AnnAssign) and isinstance(stmt.target, _ast.Name):
                name = stmt.target.id
                if not name.startswith("_"):
                    field_names.add(name)

    # Collect all YAML keys recursively (including nested dicts and list items).
    try:
        raw = _yaml.safe_load(example_path.read_text())
    except Exception:
        return DetectorResult(count=0, samples=[])

    yaml_keys: set[str] = set()

    def _walk_yaml(obj: object) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                yaml_keys.add(str(k))
                _walk_yaml(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk_yaml(item)

    _walk_yaml(raw)

    missing = sorted(field_names - yaml_keys)
    samples = [
        f"models.py field `{name}` has no matching key in example_managed_repo.yaml"
        for name in missing
    ]
    return DetectorResult(count=len(missing), samples=samples[:10])


# ── contributor entry point ───────────────────────────────────────────────────


def build_oc_detectors() -> list[Detector]:
    return [
        Detector("R1", ".console/ presence validator", "open", _detect_r1_console_presence, MEDIUM),
        Detector(
            "R2",
            ".console/ budget and structure validator",
            "open",
            _detect_r2_console_budget,
            MEDIUM,
        ),
        Detector("OC3", "orphaned entrypoints", "open", _detect_oc3_orphaned_entrypoints, MEDIUM),
        Detector(
            "OC8",
            "docs reference a symbol that doesn't exist",
            "open",
            _detect_oc8_phantom_symbols,
            LOW,
        ),
        Detector(
            "OC10",
            "team_executor max_concurrent must be 1",
            "open",
            _detect_oc10_team_executor_max_concurrent,
            MEDIUM,
        ),
        Detector(
            "OC11", "managed-repo config schema sync", "open", _detect_oc11_schema_sync, MEDIUM
        ),
    ]
