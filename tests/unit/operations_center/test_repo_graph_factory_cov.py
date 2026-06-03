# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Hermetic coverage tests for operations_center.repo_graph_factory.

Every collaborator (``load_effective_graph``, ``default_config_path``,
the sibling-source importlib fallback, and ``discover_local_manifest``)
is mocked. No real platform_manifest graph is built, no filesystem layout
beyond ``tmp_path`` is touched, and no network/CLI/git is used.
"""

from __future__ import annotations

import logging
import sys
import types
from pathlib import Path

import pytest

import operations_center.repo_graph_factory as rgf


# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------


class _PMStub:
    """Stand-in for settings.platform_manifest."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        project_slug=None,
        private_manifest_path=None,
        project_manifest_path=None,
        work_scope_manifest_path=None,
        local_manifest_path=None,
    ) -> None:
        self.enabled = enabled
        self.project_slug = project_slug
        self.private_manifest_path = private_manifest_path
        self.project_manifest_path = project_manifest_path
        self.work_scope_manifest_path = work_scope_manifest_path
        self.local_manifest_path = local_manifest_path


class _SettingsStub:
    def __init__(self, pm: _PMStub) -> None:
        self.platform_manifest = pm


def _settings(**kw) -> _SettingsStub:
    return _SettingsStub(_PMStub(**kw))


def _make_recording_leg(*, with_private: bool, ret="GRAPH"):
    """Build a fake load_effective_graph with a real, inspectable signature.

    Returns ``(fn, calls)`` where ``fn`` is the callable to monkeypatch onto
    the module and ``calls`` is the recording list. The signature matters:
    ``_load_effective_graph_compatible`` branches on
    ``inspect.signature(load_effective_graph)``.
    """
    calls: list[dict] = []

    if with_private:

        def _impl(base, *, private=None, project=None, work_scope=None, local=None):
            calls.append(
                {
                    "base": base,
                    "private": private,
                    "project": project,
                    "work_scope": work_scope,
                    "local": local,
                }
            )
            return ret

    else:

        def _impl(base, *, project=None, work_scope=None, local=None):
            calls.append(
                {
                    "base": base,
                    "project": project,
                    "work_scope": work_scope,
                    "local": local,
                }
            )
            return ret

    return _impl, calls


# ===========================================================================
# _load_effective_graph_compatible
# ===========================================================================


class TestLoadEffectiveGraphCompatible:
    def test_private_param_supported_passes_all_layers(self, monkeypatch) -> None:
        leg, calls = _make_recording_leg(with_private=True)
        monkeypatch.setattr(rgf, "load_effective_graph", leg)
        base = Path("/base.yaml")
        out = rgf._load_effective_graph_compatible(
            base,
            private=Path("/p.yaml"),
            project=Path("/proj.yaml"),
            work_scope=None,
            local=Path("/l.yaml"),
        )
        assert out == "GRAPH"
        assert calls == [
            {
                "base": base,
                "private": Path("/p.yaml"),
                "project": Path("/proj.yaml"),
                "work_scope": None,
                "local": Path("/l.yaml"),
            }
        ]

    def test_no_private_support_and_private_none_uses_legacy_signature(self, monkeypatch) -> None:
        leg, calls = _make_recording_leg(with_private=False)
        monkeypatch.setattr(rgf, "load_effective_graph", leg)
        base = Path("/base.yaml")
        out = rgf._load_effective_graph_compatible(
            base,
            private=None,
            project=Path("/proj.yaml"),
            work_scope=None,
            local=None,
        )
        assert out == "GRAPH"
        assert calls == [
            {
                "base": base,
                "project": Path("/proj.yaml"),
                "work_scope": None,
                "local": None,
            }
        ]
        # private was never forwarded (legacy signature lacks it).
        assert "private" not in calls[0]

    def test_no_private_support_but_private_set_falls_back_to_local_impl(self, monkeypatch) -> None:
        leg, calls = _make_recording_leg(with_private=False)
        monkeypatch.setattr(rgf, "load_effective_graph", leg)
        captured: dict = {}

        def _fake_local_impl():
            def _impl(base, *, private, project, work_scope, local):
                captured["args"] = (base, private, project, work_scope, local)
                return "LOCAL_GRAPH"

            return _impl

        monkeypatch.setattr(rgf, "_load_local_platform_manifest_impl", _fake_local_impl)

        out = rgf._load_effective_graph_compatible(
            Path("/base.yaml"),
            private=Path("/p.yaml"),
            project=None,
            work_scope=Path("/ws.yaml"),
            local=None,
        )
        assert out == "LOCAL_GRAPH"
        assert captured["args"] == (
            Path("/base.yaml"),
            Path("/p.yaml"),
            None,
            Path("/ws.yaml"),
            None,
        )
        # The installed (legacy) load_effective_graph must NOT be called here.
        assert calls == []


# ===========================================================================
# _load_local_platform_manifest_impl
# ===========================================================================


class TestLoadLocalPlatformManifestImpl:
    def test_missing_sibling_source_raises_runtimeerror(self, monkeypatch) -> None:
        # Point __file__-derived workspace root at an empty tree.
        fake_file = Path("/nope/a/b/c/repo_graph_factory.py")
        monkeypatch.setattr(rgf, "__file__", str(fake_file))
        with pytest.raises(RuntimeError, match="does not support it"):
            rgf._load_local_platform_manifest_impl()

    def test_loads_sibling_module_and_returns_callable(self, tmp_path, monkeypatch) -> None:
        # Build a fake workspace:
        #   <ws>/PlatformManifest/src/platform_manifest/__init__.py
        # __file__ is at parents[3] == <ws>, so module lives 3 dirs deep.
        ws = tmp_path / "ws"
        pkg = ws / "PlatformManifest" / "src" / "platform_manifest"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(
            "def load_effective_graph(*a, **k):\n    return 'SIBLING'\n",
            encoding="utf-8",
        )
        # repo_graph_factory.__file__ -> parents[3] must == ws
        fake_file = ws / "d0" / "d1" / "d2" / "repo_graph_factory.py"
        monkeypatch.setattr(rgf, "__file__", str(fake_file))

        # Swap sys.path for a copy so we can observe/clean inserts without
        # mutating the real interpreter path.
        path_copy = list(sys.path)
        monkeypatch.setattr(sys, "path", path_copy)
        monkeypatch.delitem(sys.modules, "platform_manifest_local_fallback", raising=False)

        package_dir = str(pkg.parent)
        try:
            fn = rgf._load_local_platform_manifest_impl()
            assert callable(fn)
            assert fn() == "SIBLING"
            # The package's parent (the src dir) was put on sys.path.
            assert package_dir in sys.path
            assert "platform_manifest_local_fallback" in sys.modules
        finally:
            sys.modules.pop("platform_manifest_local_fallback", None)

    def test_path_not_reinserted_when_already_present(self, tmp_path, monkeypatch) -> None:
        ws = tmp_path / "ws"
        pkg = ws / "PlatformManifest" / "src" / "platform_manifest"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(
            "def load_effective_graph(*a, **k):\n    return 'SIBLING'\n",
            encoding="utf-8",
        )
        fake_file = ws / "d0" / "d1" / "d2" / "repo_graph_factory.py"
        monkeypatch.setattr(rgf, "__file__", str(fake_file))

        package_dir = str(pkg.parent)
        # Pre-seed sys.path so the `if package_dir not in sys.path` branch is False.
        monkeypatch.setattr(sys, "path", [package_dir, *sys.path])
        monkeypatch.delitem(sys.modules, "platform_manifest_local_fallback", raising=False)

        before = sys.path.count(package_dir)
        try:
            fn = rgf._load_local_platform_manifest_impl()
            assert fn() == "SIBLING"
            # Not inserted a second time.
            assert sys.path.count(package_dir) == before
        finally:
            sys.modules.pop("platform_manifest_local_fallback", None)

    def test_spec_none_raises_runtimeerror(self, tmp_path, monkeypatch) -> None:
        ws = tmp_path / "ws"
        pkg = ws / "PlatformManifest" / "src" / "platform_manifest"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("x = 1\n", encoding="utf-8")
        fake_file = ws / "d0" / "d1" / "d2" / "repo_graph_factory.py"
        monkeypatch.setattr(rgf, "__file__", str(fake_file))
        monkeypatch.setattr(rgf.importlib.util, "spec_from_file_location", lambda *a, **k: None)
        with pytest.raises(RuntimeError, match="could not load sibling"):
            rgf._load_local_platform_manifest_impl()

    def test_spec_loader_none_raises_runtimeerror(self, tmp_path, monkeypatch) -> None:
        ws = tmp_path / "ws"
        pkg = ws / "PlatformManifest" / "src" / "platform_manifest"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("x = 1\n", encoding="utf-8")
        fake_file = ws / "d0" / "d1" / "d2" / "repo_graph_factory.py"
        monkeypatch.setattr(rgf, "__file__", str(fake_file))

        fake_spec = types.SimpleNamespace(name="x", loader=None)
        monkeypatch.setattr(
            rgf.importlib.util, "spec_from_file_location", lambda *a, **k: fake_spec
        )
        with pytest.raises(RuntimeError, match="could not load sibling"):
            rgf._load_local_platform_manifest_impl()


# ===========================================================================
# build_effective_repo_graph
# ===========================================================================


class TestBuildEffectiveRepoGraph:
    def test_delegates_to_compatible_with_default_base(self, monkeypatch) -> None:
        monkeypatch.setattr(rgf, "default_config_path", lambda: Path("/bundled.yaml"))
        captured: dict = {}

        def _fake_compat(base, *, private, project, work_scope, local):
            captured.update(
                base=base,
                private=private,
                project=project,
                work_scope=work_scope,
                local=local,
            )
            return "OUT"

        monkeypatch.setattr(rgf, "_load_effective_graph_compatible", _fake_compat)

        out = rgf.build_effective_repo_graph(
            private_manifest_path=Path("/pr.yaml"),
            project_manifest_path=Path("/pj.yaml"),
            work_scope_manifest_path=Path("/ws.yaml"),
            local_manifest_path=Path("/lc.yaml"),
        )
        assert out == "OUT"
        assert captured == {
            "base": Path("/bundled.yaml"),
            "private": Path("/pr.yaml"),
            "project": Path("/pj.yaml"),
            "work_scope": Path("/ws.yaml"),
            "local": Path("/lc.yaml"),
        }

    def test_defaults_are_all_none(self, monkeypatch) -> None:
        monkeypatch.setattr(rgf, "default_config_path", lambda: Path("/b.yaml"))
        captured: dict = {}

        def _fake_compat(base, *, private, project, work_scope, local):
            captured.update(private=private, project=project, work_scope=work_scope, local=local)
            return "OK"

        monkeypatch.setattr(rgf, "_load_effective_graph_compatible", _fake_compat)
        assert rgf.build_effective_repo_graph() == "OK"
        assert captured == {
            "private": None,
            "project": None,
            "work_scope": None,
            "local": None,
        }


# ===========================================================================
# _resolve_private_manifest_path
# ===========================================================================


class TestResolvePrivateManifestPath:
    def test_explicit_override_returned(self) -> None:
        pm = _PMStub(private_manifest_path=Path("/explicit.yaml"))
        assert rgf._resolve_private_manifest_path(pm, repo_root=None) == Path("/explicit.yaml")

    def test_no_slug_returns_none(self) -> None:
        pm = _PMStub(project_slug=None)
        assert rgf._resolve_private_manifest_path(pm, repo_root=None) is None

    def test_discovers_candidate_via_repo_root_parent(self, tmp_path) -> None:
        parent = tmp_path / "parent"
        repo_root = parent / "managed"
        repo_root.mkdir(parents=True)
        cand = parent / "store" / "manifests" / "slug-x" / "private_manifest.yaml"
        cand.parent.mkdir(parents=True)
        cand.write_text("x: 1\n", encoding="utf-8")
        pm = _PMStub(project_slug="slug-x")
        out = rgf._resolve_private_manifest_path(pm, repo_root=repo_root)
        assert out == cand

    def test_discovers_via_cwd_when_repo_root_none(self, tmp_path, monkeypatch) -> None:
        cand = tmp_path / "store" / "manifests" / "slug-y" / "private_manifest.yaml"
        cand.parent.mkdir(parents=True)
        cand.write_text("x: 1\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        pm = _PMStub(project_slug="slug-y")
        out = rgf._resolve_private_manifest_path(pm, repo_root=None)
        assert out == cand

    def test_no_candidate_found_returns_none(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        pm = _PMStub(project_slug="missing-slug")
        assert rgf._resolve_private_manifest_path(pm, repo_root=None) is None

    def test_glob_match_that_is_not_a_file_is_skipped(self, tmp_path, monkeypatch) -> None:
        # The glob can match a directory named private_manifest.yaml; the
        # `if candidate.is_file()` guard must skip it (the loop falls through).
        monkeypatch.chdir(tmp_path)
        not_a_file = tmp_path / "store" / "manifests" / "slug-d" / "private_manifest.yaml"
        not_a_file.mkdir(parents=True)
        pm = _PMStub(project_slug="slug-d")
        assert rgf._resolve_private_manifest_path(pm, repo_root=None) is None

    def test_duplicate_roots_deduplicated(self, tmp_path, monkeypatch) -> None:
        # When cwd == repo_root.parent the same root would be scanned twice;
        # the `seen` set must skip the duplicate. Exercise that branch.
        parent = tmp_path / "parent"
        repo_root = parent / "managed"
        repo_root.mkdir(parents=True)
        monkeypatch.chdir(parent)
        pm = _PMStub(project_slug="none-here")
        # No candidate exists -> None, but the dedup branch is traversed.
        assert rgf._resolve_private_manifest_path(pm, repo_root=repo_root) is None


# ===========================================================================
# _resolve_project_manifest_path
# ===========================================================================


class TestResolveProjectManifestPath:
    def test_explicit_override_returned(self) -> None:
        pm = _PMStub(project_manifest_path=Path("/proj.yaml"))
        assert rgf._resolve_project_manifest_path(pm, repo_root=None) == Path("/proj.yaml")

    def test_topology_convention_used_when_file_present(self, tmp_path) -> None:
        (tmp_path / "topology").mkdir()
        cand = tmp_path / "topology" / "project_manifest.yaml"
        cand.write_text("x: 1\n", encoding="utf-8")
        pm = _PMStub()
        assert rgf._resolve_project_manifest_path(pm, repo_root=tmp_path) == cand

    def test_topology_absent_returns_none(self, tmp_path) -> None:
        pm = _PMStub()
        assert rgf._resolve_project_manifest_path(pm, repo_root=tmp_path) is None

    def test_no_repo_root_returns_none(self) -> None:
        pm = _PMStub()
        assert rgf._resolve_project_manifest_path(pm, repo_root=None) is None


# ===========================================================================
# _resolve_local_manifest_path
# ===========================================================================


class TestResolveLocalManifestPath:
    def test_explicit_override_returned(self) -> None:
        pm = _PMStub(local_manifest_path=Path("/local.yaml"))
        logger = logging.getLogger("t")
        assert rgf._resolve_local_manifest_path(pm, None, logger) == Path("/local.yaml")

    def test_no_slug_returns_none(self) -> None:
        pm = _PMStub(project_slug=None)
        logger = logging.getLogger("t")
        assert rgf._resolve_local_manifest_path(pm, None, logger) is None

    def test_import_error_returns_none_and_debug_logs(self, monkeypatch) -> None:
        # Ensure the import inside the function fails.
        monkeypatch.setitem(sys.modules, "platform_deployment_cli", None)
        monkeypatch.setitem(sys.modules, "platform_deployment_cli.local_manifest", None)
        pm = _PMStub(project_slug="slug")
        logger = logging.getLogger("t")
        assert rgf._resolve_local_manifest_path(pm, None, logger) is None

    def test_discover_returns_path(self, monkeypatch) -> None:
        mod = types.ModuleType("platform_deployment_cli.local_manifest")

        def _discover(slug, *, repo_root=None):
            assert slug == "slug-z"
            return Path("/discovered/local.yaml")

        mod.discover_local_manifest = _discover
        parent = types.ModuleType("platform_deployment_cli")
        monkeypatch.setitem(sys.modules, "platform_deployment_cli", parent)
        monkeypatch.setitem(sys.modules, "platform_deployment_cli.local_manifest", mod)
        pm = _PMStub(project_slug="slug-z")
        logger = logging.getLogger("t")
        out = rgf._resolve_local_manifest_path(pm, repo_root=None, logger=logger)
        assert out == Path("/discovered/local.yaml")

    def test_discover_raises_returns_none(self, monkeypatch) -> None:
        mod = types.ModuleType("platform_deployment_cli.local_manifest")

        def _discover(slug, *, repo_root=None):
            raise ValueError("boom")

        mod.discover_local_manifest = _discover
        parent = types.ModuleType("platform_deployment_cli")
        monkeypatch.setitem(sys.modules, "platform_deployment_cli", parent)
        monkeypatch.setitem(sys.modules, "platform_deployment_cli.local_manifest", mod)
        pm = _PMStub(project_slug="slug-z")
        logger = logging.getLogger("t")
        assert rgf._resolve_local_manifest_path(pm, repo_root=None, logger=logger) is None


# ===========================================================================
# build_effective_repo_graph_from_settings
# ===========================================================================


class TestBuildFromSettings:
    def test_disabled_returns_none_without_building(self, monkeypatch) -> None:
        called = {"n": 0}

        def _boom(**kw):
            called["n"] += 1
            raise AssertionError("should not build")

        monkeypatch.setattr(rgf, "build_effective_repo_graph", _boom)
        assert rgf.build_effective_repo_graph_from_settings(_settings(enabled=False)) is None
        assert called["n"] == 0

    def test_happy_path_forwards_resolved_layers(self, monkeypatch) -> None:
        captured: dict = {}

        def _fake_build(**kw):
            captured.update(kw)
            return "GRAPH"

        monkeypatch.setattr(rgf, "build_effective_repo_graph", _fake_build)
        monkeypatch.setattr(rgf, "_resolve_private_manifest_path", lambda pm, rr: Path("/pr"))
        monkeypatch.setattr(rgf, "_resolve_project_manifest_path", lambda pm, rr: Path("/pj"))
        monkeypatch.setattr(rgf, "_resolve_local_manifest_path", lambda pm, rr, lg: Path("/lc"))
        out = rgf.build_effective_repo_graph_from_settings(_settings())
        assert out == "GRAPH"
        assert captured == {
            "private_manifest_path": Path("/pr"),
            "project_manifest_path": Path("/pj"),
            "work_scope_manifest_path": None,
            "local_manifest_path": Path("/lc"),
        }

    def test_work_scope_mode_skips_project_resolution(self, monkeypatch) -> None:
        captured: dict = {}
        project_resolver_called = {"n": 0}

        def _fake_build(**kw):
            captured.update(kw)
            return "GRAPH"

        def _proj_resolver(pm, rr):
            project_resolver_called["n"] += 1
            return Path("/should-not-be-used")

        monkeypatch.setattr(rgf, "build_effective_repo_graph", _fake_build)
        monkeypatch.setattr(rgf, "_resolve_private_manifest_path", lambda pm, rr: None)
        monkeypatch.setattr(rgf, "_resolve_project_manifest_path", _proj_resolver)
        monkeypatch.setattr(rgf, "_resolve_local_manifest_path", lambda pm, rr, lg: None)

        s = _settings(work_scope_manifest_path=Path("/ws.yaml"))
        out = rgf.build_effective_repo_graph_from_settings(s)
        assert out == "GRAPH"
        assert captured["work_scope_manifest_path"] == Path("/ws.yaml")
        assert captured["project_manifest_path"] is None
        # Project resolution is short-circuited in work-scope mode.
        assert project_resolver_called["n"] == 0

    def test_config_error_swallowed_returns_none_with_warning(self, monkeypatch, caplog) -> None:
        from platform_manifest import RepoGraphConfigError

        def _fake_build(**kw):
            raise RepoGraphConfigError("bad manifest")

        monkeypatch.setattr(rgf, "build_effective_repo_graph", _fake_build)
        monkeypatch.setattr(rgf, "_resolve_private_manifest_path", lambda pm, rr: None)
        monkeypatch.setattr(rgf, "_resolve_project_manifest_path", lambda pm, rr: None)
        monkeypatch.setattr(rgf, "_resolve_local_manifest_path", lambda pm, rr, lg: None)

        with caplog.at_level(logging.WARNING):
            out = rgf.build_effective_repo_graph_from_settings(_settings())
        assert out is None
        assert any("graph construction failed" in r.message.lower() for r in caplog.records)

    def test_unexpected_error_swallowed_returns_none_with_warning(
        self, monkeypatch, caplog
    ) -> None:
        def _fake_build(**kw):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(rgf, "build_effective_repo_graph", _fake_build)
        monkeypatch.setattr(rgf, "_resolve_private_manifest_path", lambda pm, rr: None)
        monkeypatch.setattr(rgf, "_resolve_project_manifest_path", lambda pm, rr: None)
        monkeypatch.setattr(rgf, "_resolve_local_manifest_path", lambda pm, rr, lg: None)

        with caplog.at_level(logging.WARNING):
            out = rgf.build_effective_repo_graph_from_settings(_settings())
        assert out is None
        assert any("unexpected error" in r.message.lower() for r in caplog.records)
