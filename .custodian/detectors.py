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

import ast
import re
from pathlib import Path

from custodian.audit_kit.detector import LOW, MEDIUM, AuditContext, Detector, DetectorResult

# ── R1: .console/ directory presence ─────────────────────────────────────────

_CONSOLE_REQUIRED_FILES = ["task.md", "guidelines.md", "backlog.md", "log.md", "workers.yaml"]


def _detect_r1_console_presence(ctx: AuditContext) -> DetectorResult:
    """Verify .console/ directory exists and contains all required files."""
    console = ctx.repo_root / ".console"

    if not console.exists():
        return DetectorResult(count=1, samples=[".console/ directory does not exist"])

    if not console.is_dir():
        return DetectorResult(count=1, samples=[".console exists but is not a directory"])

    samples: list[str] = []
    for filename in _CONSOLE_REQUIRED_FILES:
        path = console / filename
        if not path.exists():
            samples.append(f".console/{filename} is missing")
        elif not path.is_file():
            samples.append(f".console/{filename} is not a file")

    return DetectorResult(count=len(samples), samples=samples)


# ── R2: .console/ file budget and structure ───────────────────────────────────

_CONSOLE_SIZE_LIMIT = 200 * 1024  # 200 KB (log.md grows through legitimate operational history)
_TASK_REQUIRED_SECTIONS = ["## Objective", "## Overall Plan", "## Current Stage"]
_BACKLOG_STANDARD_SECTIONS = ["## In Progress", "## Up Next", "## Done"]


def _detect_r2_console_budget(ctx: AuditContext) -> DetectorResult:
    """Verify .console/ files respect size/encoding budgets and structural invariants.

    Silently passes when .console/ is absent — R1 owns presence enforcement.
    """
    import yaml as _yaml  # optional dep — only in dev venv

    console = ctx.repo_root / ".console"
    if not console.exists() or not console.is_dir():
        return DetectorResult(count=0, samples=[])

    samples: list[str] = []
    file_texts: dict[str, str | None] = {}

    for filename in _CONSOLE_REQUIRED_FILES:
        path = console / filename
        if not path.exists() or not path.is_file():
            file_texts[filename] = None
            continue

        if path.stat().st_size > _CONSOLE_SIZE_LIMIT:
            samples.append(
                f".console/{filename} exceeds 200KB budget ({path.stat().st_size} bytes)"
            )

        try:
            file_texts[filename] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            samples.append(f".console/{filename} cannot be read as UTF-8 (corrupted)")
            file_texts[filename] = None

    task_text = file_texts.get("task.md")
    if task_text is not None:
        for section in _TASK_REQUIRED_SECTIONS:
            if section not in task_text:
                samples.append(f".console/task.md is missing required section '{section}'")

    workers_text = file_texts.get("workers.yaml")
    if workers_text is not None:
        try:
            _yaml.safe_load(workers_text)
        except Exception as exc:
            samples.append(f".console/workers.yaml has invalid YAML syntax: {exc}")

    backlog_text = file_texts.get("backlog.md")
    if backlog_text is not None:
        if not any(s in backlog_text for s in _BACKLOG_STANDARD_SECTIONS):
            samples.append(
                ".console/backlog.md is missing standard sections (In Progress/Up Next/Done)"
            )

    return DetectorResult(count=len(samples), samples=samples)


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

    # Budget: 200KB max per file (log.md grows through legitimate operational history)
    max_size_bytes = 200 * 1024
    for filename in ["task.md", "guidelines.md", "backlog.md", "log.md"]:
        filepath = console_root / filename
        if not filepath.exists():
            continue
        try:
            size = filepath.stat().st_size
            if size > max_size_bytes:
                samples.append(f".console/{filename} exceeds 200KB budget ({size} bytes)")
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


# ── OC12: domain-model construction field mismatch ───────────────────────────
#
# Flags constructing a local @dataclass / Pydantic BaseModel with a keyword
# argument that is not one of its fields. This is the observable symptom of a
# divergent definition: PR #269 constructed FlakyTestMetric(failure_entropy=...)
# against a model whose real field was pattern_entropy; commit 0cb06e0e renamed
# CoverageAlert fields while consumers still passed the old names. Both are
# textually clean but raise TypeError at runtime — caught here as a static fact
# before CI/merge.
#
# Conservative by construction (the adversarial review flagged false positives as
# the failure mode for this class of detector). A class is only CHECKED when its
# full field set can be resolved with certainty; anything ambiguous is skipped:
#   - only @dataclass classes and BaseModel subclasses are candidates;
#   - a custom __init__, **kwargs, or Pydantic extra="allow" → skip (accepts more);
#   - an unresolved (non-local, non-terminal) base class → skip (fields unknown);
#   - only bare-Name constructor calls with explicit keyword args are inspected
#     (module.Model(...), Model(**d), positional args are not flagged).
# It does NOT key on name similarity — FlakyTestMetric vs FlakyTestMetrics is an
# intentional, documented pair and must never be flagged.

_TERMINAL_BASES = {"object", "BaseModel", "Enum", "IntEnum", "StrEnum", "Exception"}
_DATACLASS_DECORATORS = {"dataclass", "dataclasses.dataclass"}


def _decorator_names(node: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return names


def _class_own_fields(node: ast.ClassDef) -> set[str]:
    fields: set[str] = set()
    for stmt in node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            fields.add(stmt.target.id)
    return fields


def _accepts_extra_kwargs(node: ast.ClassDef) -> bool:
    """True if the class can accept keyword args beyond its declared fields:
    a custom __init__, an explicit **kwargs, or Pydantic extra='allow'."""
    for stmt in node.body:
        if isinstance(stmt, ast.FunctionDef) and stmt.name == "__init__":
            return True
        # model_config = ConfigDict(extra="allow")  or  class Config: extra = "allow"
        if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call):
            for kw in stmt.value.keywords:
                if kw.arg == "extra" and isinstance(kw.value, ast.Constant) and kw.value.value == "allow":
                    return True
        if isinstance(stmt, ast.ClassDef) and stmt.name == "Config":
            for s in stmt.body:
                if (
                    isinstance(s, ast.Assign)
                    and isinstance(s.value, ast.Constant)
                    and s.value.value == "allow"
                ):
                    return True
    return False


def _detect_oc12_model_field_mismatch(ctx: AuditContext) -> DetectorResult:
    src = ctx.src_root
    if not src.is_dir():
        return DetectorResult(count=0, samples=[])

    # Pass 1: collect every class def in src/ with its own fields, bases, decorators.
    own_fields: dict[str, set[str]] = {}
    bases: dict[str, list[str]] = {}
    is_dataclass: dict[str, bool] = {}
    inherits_basemodel: dict[str, bool] = {}
    extra_ok: dict[str, bool] = {}
    module_stem: dict[str, str] = {}  # class name → defining file stem (e.g. flaky_test_models)
    defining_file: dict[str, Path] = {}  # class name → defining file path
    duplicate_names: set[str] = set()

    for py in src.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            name = node.name
            if name in own_fields:
                duplicate_names.add(name)  # same name in two modules → can't resolve safely
            base_names = [b.id for b in node.bases if isinstance(b, ast.Name)]
            own_fields[name] = _class_own_fields(node)
            bases[name] = base_names
            decs = _decorator_names(node)
            is_dataclass[name] = bool(decs & _DATACLASS_DECORATORS)
            inherits_basemodel[name] = "BaseModel" in base_names
            extra_ok[name] = _accepts_extra_kwargs(node)
            module_stem[name] = py.stem
            defining_file[name] = py

    def resolve(name: str, seen: set[str]) -> set[str] | None:
        """Full field set incl. local bases, or None if not safely resolvable."""
        if name in seen or name in duplicate_names:
            return None
        seen = seen | {name}
        fields = set(own_fields.get(name, set()))
        for base in bases.get(name, []):
            if base in _TERMINAL_BASES:
                continue
            if base not in own_fields:
                return None  # base is external / unknown → cannot be sure of full field set
            sub = resolve(base, seen)
            if sub is None:
                return None
            fields |= sub
        return fields

    # A class is checkable iff: dataclass or BaseModel subclass, no extra-kwargs
    # escape hatch, and a fully-resolvable field set.
    checkable: dict[str, set[str]] = {}
    for name in own_fields:
        if name in duplicate_names or extra_ok.get(name):
            continue
        model_like = is_dataclass.get(name) or inherits_basemodel.get(name)
        # also treat a class that inherits (transitively, locally) from BaseModel as model-like
        if not model_like:
            continue
        resolved = resolve(name, set())
        if resolved is None:
            continue
        checkable[name] = resolved

    if not checkable:
        return DetectorResult(count=0, samples=[])

    # Pass 2: scan src + tests for bare Model(field=...) calls with unknown kwargs.
    # A construction is only checked when we can confirm the bare Name binds to OUR
    # registered class (and not a same-named class imported from another package —
    # e.g. OC's own ghost_audit.AuditContext vs custodian's AuditContext). The bind
    # is confirmed iff the call-site file imports the name from a module whose final
    # component matches the class's defining file stem, OR the call site is in the
    # defining file itself with no shadowing import.
    samples: list[str] = []
    roots = [src]
    if ctx.tests_root.is_dir():
        roots.append(ctx.tests_root)
    for root in roots:
        for py in root.rglob("*.py"):
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            rel = py.relative_to(ctx.repo_root)
            # Lines inside a `with pytest.raises(...)` / `with raises(...)` block are
            # negative tests asserting the model REJECTS bad input — never flag them.
            raises_lines: set[int] = set()
            for node in ast.walk(tree):
                if not isinstance(node, ast.With):
                    continue
                for item in node.items:
                    ce = item.context_expr
                    fn = ce.func if isinstance(ce, ast.Call) else None
                    fn_name = (
                        fn.attr if isinstance(fn, ast.Attribute)
                        else fn.id if isinstance(fn, ast.Name)
                        else None
                    )
                    if fn_name == "raises":
                        for inner in ast.walk(node):
                            ln = getattr(inner, "lineno", None)
                            if isinstance(ln, int):
                                raises_lines.add(ln)
            # name → imported module's final component (for `from a.b.c import Name`
            # → "c"; for `import a.b as Name` → "b"); None marks "imported but
            # un-stemmable" which we treat as a non-match (skip).
            imported_from_stem: dict[str, str | None] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    stem = node.module.rsplit(".", 1)[-1] if node.module else None
                    for alias in node.names:
                        imported_from_stem[alias.asname or alias.name] = stem
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.asname:
                            imported_from_stem[alias.asname] = alias.name.rsplit(".", 1)[-1]
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                    continue
                cls = node.func.id
                if cls not in checkable:
                    continue
                if node.lineno in raises_lines:
                    continue  # negative test asserting rejection
                if cls in imported_from_stem:
                    if imported_from_stem[cls] != module_stem.get(cls):
                        continue  # same name, different module → not our class
                elif py != defining_file.get(cls):
                    continue  # not imported and not the defining file → can't confirm
                fields = checkable[cls]
                for kw in node.keywords:
                    if kw.arg is None:  # **kwargs expansion — can't tell
                        continue
                    if kw.arg not in fields:
                        samples.append(
                            f"{rel}:{node.lineno}: {cls}({kw.arg}=...) — '{kw.arg}' is not a "
                            f"field of {cls} (fields: {', '.join(sorted(fields)) or 'none'})"
                        )

    return DetectorResult(count=len(samples), samples=samples[:20])


# ── OC13: test re-implements a metric formula instead of calling production ───
#
# Flags a test function that computes a metric formula INLINE (currently the
# log-based entropy signature: math.log / log2 / log10) and asserts on it, while
# never calling any production metric function. This is the #269 anti-pattern: its
# tests recomputed Shannon entropy inline and asserted hardcoded constants that did
# not even match the formula (asserted 0.081296; correct value 0.080793) — and the
# production code was never exercised, so the test validated nothing real.
#
# It deliberately does NOT fire on the legitimate golden-value cross-check pattern,
# where a test CALLS the production function and compares it to an independently
# computed reference, e.g.:
#     entropy = reporter._compute_pattern_entropy(runs)   # production exercised
#     assert abs(entropy - (-math.log(0.5) * 0.5 * 2)) < 1e-3
# Here the inline math is a reference value, not a replacement — so a call to a
# production metric symbol in the same function suppresses the finding. The rule
# keys on import/call-absence and inline-formula presence, never on literal values.

_INLINE_METRIC_FNS = {"log", "log2", "log10"}  # math.* entropy signature
_PRODUCTION_METRIC_RE = re.compile(r"^_compute_[a-z_]+$")


def _flaky_metric_symbols(src_root: Path) -> set[str]:
    """Public function names exported by observer/flaky_metrics.py (the canonical
    metric implementations a test should call instead of re-deriving)."""
    fm = src_root / "observer" / "flaky_metrics.py"
    names: set[str] = set()
    try:
        tree = ast.parse(fm.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return names
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            names.add(node.name)
    return names


def _calls_production_metric(fn: ast.AST, production: set[str]) -> bool:
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        name = (
            callee.attr if isinstance(callee, ast.Attribute)
            else callee.id if isinstance(callee, ast.Name)
            else None
        )
        if name and (name in production or _PRODUCTION_METRIC_RE.match(name)):
            return True
    return False


def _computes_metric_inline(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        if isinstance(callee, ast.Attribute) and callee.attr in _INLINE_METRIC_FNS:
            if isinstance(callee.value, ast.Name) and callee.value.id == "math":
                return True
        if isinstance(callee, ast.Name) and callee.id in _INLINE_METRIC_FNS:
            return True  # `from math import log2`
    return False


def _has_assert(fn: ast.AST) -> bool:
    return any(isinstance(n, ast.Assert) for n in ast.walk(fn))


def _detect_oc13_test_reimplements_metric(ctx: AuditContext) -> DetectorResult:
    tests = ctx.tests_root
    if not tests.is_dir():
        return DetectorResult(count=0, samples=[])
    production = _flaky_metric_symbols(ctx.src_root)
    samples: list[str] = []
    for py in tests.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        rel = py.relative_to(ctx.repo_root)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.FunctionDef) and node.name.startswith("test")):
                continue
            if (
                _computes_metric_inline(node)
                and _has_assert(node)
                and not _calls_production_metric(node, production)
            ):
                samples.append(
                    f"{rel}:{node.lineno}: {node.name}() computes a metric inline "
                    f"(math.log*) and asserts on it without calling a production metric "
                    f"function — import observer.flaky_metrics (or call the reporter) instead"
                )
    return DetectorResult(count=len(samples), samples=samples[:20])


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
        Detector(
            "OC12",
            "domain-model constructed with a non-field keyword argument",
            "open",
            _detect_oc12_model_field_mismatch,
            MEDIUM,
        ),
        Detector(
            "OC13",
            "test re-implements a metric formula inline without calling production",
            "open",
            _detect_oc13_test_reimplements_metric,
            LOW,
        ),
    ]
