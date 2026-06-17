# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from operations_center.entrypoints.graph_doctor import main as gd

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeEnum:
    """Stand-in for an enum member exposing a ``.value`` string."""

    def __init__(self, value: str) -> None:
        self.value = value


class _FakeNode:
    def __init__(self, source: str, visibility: str) -> None:
        self.source = _FakeEnum(source)
        self.visibility = _FakeEnum(visibility)


class _FakeEdge:
    def __init__(self, source: str) -> None:
        self.source = _FakeEnum(source)


class _FakeGraph:
    def __init__(
        self,
        nodes: list[_FakeNode] | None = None,
        edges: list[_FakeEdge] | None = None,
    ) -> None:
        self._nodes = nodes or []
        self.edges = edges or []

    def list_nodes(self) -> list[_FakeNode]:
        return self._nodes


def _make_pm(
    *,
    enabled: bool = True,
    project_slug: str | None = None,
    private_manifest_path: Path | None = None,
    project_manifest_path: Path | None = None,
    work_scope_manifest_path: Path | None = None,
    local_manifest_path: Path | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        enabled=enabled,
        project_slug=project_slug,
        private_manifest_path=private_manifest_path,
        project_manifest_path=project_manifest_path,
        work_scope_manifest_path=work_scope_manifest_path,
        local_manifest_path=local_manifest_path,
    )


def _make_settings(pm: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(platform_manifest=pm)


def _existing_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "operations_center.local.yaml"
    cfg.write_text("# config\n", encoding="utf-8")
    return cfg


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    settings: SimpleNamespace,
    graph: _FakeGraph | None,
    version: str | None = "1.2.3",
    breakdown: list | None = None,
) -> dict[str, mock.Mock]:
    """Patch every external collaborator of ``main`` and return the mocks."""
    load_settings = mock.Mock(return_value=settings)
    build = mock.Mock(return_value=graph)
    resolve_version = mock.Mock(return_value=version)
    compute = mock.Mock(return_value=breakdown if breakdown is not None else [])
    monkeypatch.setattr(gd, "load_settings", load_settings)
    monkeypatch.setattr(gd, "build_effective_repo_graph_from_settings", build)
    monkeypatch.setattr(gd, "_resolve_platform_manifest_version", resolve_version)
    monkeypatch.setattr(gd, "_compute_per_include_breakdown", compute)
    return {
        "load_settings": load_settings,
        "build": build,
        "resolve_version": resolve_version,
        "compute": compute,
    }


# ---------------------------------------------------------------------------
# _build_parser
# ---------------------------------------------------------------------------


class TestBuildParser:
    def test_defaults(self) -> None:
        args = gd._build_parser().parse_args([])
        assert args.config == Path("config/operations_center.local.yaml")
        assert args.repo_root is None
        assert args.json_output is False

    def test_all_flags(self) -> None:
        args = gd._build_parser().parse_args(["--config", "x.yaml", "--repo-root", "/r", "--json"])
        assert args.config == Path("x.yaml")
        assert args.repo_root == Path("/r")
        assert args.json_output is True


# ---------------------------------------------------------------------------
# main — invocation errors (exit 2)
# ---------------------------------------------------------------------------


class TestMainInvocationErrors:
    def test_config_missing_json(self, tmp_path, monkeypatch, capsys) -> None:
        missing = tmp_path / "nope.yaml"
        rc = gd.main(["--config", str(missing), "--json"])
        assert rc == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "error"
        assert payload["exit_code"] == 2
        assert "config not found" in payload["message"]

    def test_config_missing_human(self, tmp_path, capsys) -> None:
        missing = tmp_path / "nope.yaml"
        rc = gd.main(["--config", str(missing)])
        assert rc == 2
        out = capsys.readouterr().out
        assert "graph-doctor: error" in out
        assert "config not found" in out

    def test_settings_load_failure(self, tmp_path, monkeypatch, capsys) -> None:
        cfg = _existing_config(tmp_path)
        monkeypatch.setattr(gd, "load_settings", mock.Mock(side_effect=RuntimeError("boom")))
        rc = gd.main(["--config", str(cfg), "--json"])
        assert rc == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "error"
        assert "settings load failed: boom" in payload["message"]
        assert payload["config"] == str(cfg.resolve())


# ---------------------------------------------------------------------------
# main — mode selection + status
# ---------------------------------------------------------------------------


class TestMainModes:
    def test_disabled_mode_ok_disabled(self, tmp_path, monkeypatch, capsys) -> None:
        cfg = _existing_config(tmp_path)
        pm = _make_pm(enabled=False)
        _patch_pipeline(monkeypatch, settings=_make_settings(pm), graph=None)
        rc = gd.main(["--config", str(cfg), "--json"])
        assert rc == 0
        report = json.loads(capsys.readouterr().out)
        assert report["status"] == "ok_disabled"
        assert report["platform_manifest"]["mode"] == "disabled"
        assert report["graph_built"] is False

    def test_work_scope_mode(self, tmp_path, monkeypatch, capsys) -> None:
        cfg = _existing_config(tmp_path)
        ws = tmp_path / "ws.yaml"
        pm = _make_pm(work_scope_manifest_path=ws)
        graph = _FakeGraph(
            nodes=[_FakeNode("platform", "public"), _FakeNode("project", "private")],
            edges=[_FakeEdge("platform")],
        )
        breakdown = [{"name": "a", "path": "/p", "nodes_contributed": 1, "edges_contributed": 0}]
        mocks = _patch_pipeline(
            monkeypatch, settings=_make_settings(pm), graph=graph, breakdown=breakdown
        )
        rc = gd.main(["--config", str(cfg), "--json"])
        assert rc == 0
        report = json.loads(capsys.readouterr().out)
        assert report["platform_manifest"]["mode"] == "work_scope"
        assert report["includes"] == breakdown
        mocks["compute"].assert_called_once_with(ws)

    def test_project_mode(self, tmp_path, monkeypatch, capsys) -> None:
        cfg = _existing_config(tmp_path)
        pm = _make_pm(project_manifest_path=tmp_path / "proj.yaml")
        graph = _FakeGraph(nodes=[_FakeNode("platform", "public")])
        mocks = _patch_pipeline(monkeypatch, settings=_make_settings(pm), graph=graph)
        rc = gd.main(["--config", str(cfg), "--json"])
        assert rc == 0
        report = json.loads(capsys.readouterr().out)
        assert report["platform_manifest"]["mode"] == "project"
        # project mode does not compute per-include breakdown
        mocks["compute"].assert_not_called()
        assert "includes" not in report

    def test_platform_only_mode(self, tmp_path, monkeypatch, capsys) -> None:
        cfg = _existing_config(tmp_path)
        pm = _make_pm()
        graph = _FakeGraph(nodes=[_FakeNode("platform", "public")])
        _patch_pipeline(monkeypatch, settings=_make_settings(pm), graph=graph)
        rc = gd.main(["--config", str(cfg), "--json"])
        assert rc == 0
        report = json.loads(capsys.readouterr().out)
        assert report["platform_manifest"]["mode"] == "platform_only"
        assert report["status"] == "ok"

    def test_enabled_graph_none_fails(self, tmp_path, monkeypatch, capsys) -> None:
        cfg = _existing_config(tmp_path)
        pm = _make_pm()
        _patch_pipeline(monkeypatch, settings=_make_settings(pm), graph=None)
        rc = gd.main(["--config", str(cfg), "--json"])
        assert rc == 1
        report = json.loads(capsys.readouterr().out)
        assert report["status"] == "fail_graph_none"
        assert report["graph_built"] is False

    def test_work_scope_without_path_skips_includes(self, tmp_path, monkeypatch, capsys) -> None:
        # mode resolves to platform_only (no ws path) but graph present;
        # ensures the includes branch guard on work_scope_manifest_path holds.
        cfg = _existing_config(tmp_path)
        pm = _make_pm()
        graph = _FakeGraph(nodes=[_FakeNode("platform", "public")])
        mocks = _patch_pipeline(monkeypatch, settings=_make_settings(pm), graph=graph)
        rc = gd.main(["--config", str(cfg), "--json"])
        assert rc == 0
        mocks["compute"].assert_not_called()


# ---------------------------------------------------------------------------
# main — report content & path serialization
# ---------------------------------------------------------------------------


class TestMainReportContent:
    def test_paths_and_counts_serialized(self, tmp_path, monkeypatch, capsys) -> None:
        cfg = _existing_config(tmp_path)
        pm = _make_pm(
            project_slug="demo",
            private_manifest_path=tmp_path / "priv.yaml",
            project_manifest_path=tmp_path / "proj.yaml",
            local_manifest_path=tmp_path / "local.yaml",
        )
        graph = _FakeGraph(
            nodes=[
                _FakeNode("platform", "public"),
                _FakeNode("platform", "private"),
                _FakeNode("project", "public"),
            ],
            edges=[_FakeEdge("platform"), _FakeEdge("project"), _FakeEdge("project")],
        )
        _patch_pipeline(monkeypatch, settings=_make_settings(pm), graph=graph, version="9.9.9")
        rc = gd.main(["--config", str(cfg), "--repo-root", "/somewhere", "--json"])
        assert rc == 0
        report = json.loads(capsys.readouterr().out)
        pmr = report["platform_manifest"]
        assert pmr["project_slug"] == "demo"
        assert pmr["version"] == "9.9.9"
        assert pmr["private_manifest_path"] == str(tmp_path / "priv.yaml")
        assert pmr["project_manifest_path"] == str(tmp_path / "proj.yaml")
        assert pmr["local_manifest_path"] == str(tmp_path / "local.yaml")
        assert pmr["work_scope_manifest_path"] is None
        assert report["repo_root"] == "/somewhere"
        assert report["nodes_total"] == 3
        assert report["edges_total"] == 3
        assert report["nodes_by_source"] == {"platform": 2, "project": 1}
        assert report["edges_by_source"] == {"platform": 1, "project": 2}
        assert report["nodes_by_visibility"] == {"public": 2, "private": 1}

    def test_none_paths_serialize_to_none(self, tmp_path, monkeypatch, capsys) -> None:
        cfg = _existing_config(tmp_path)
        pm = _make_pm()
        graph = _FakeGraph(nodes=[_FakeNode("platform", "public")])
        _patch_pipeline(monkeypatch, settings=_make_settings(pm), graph=graph, version=None)
        rc = gd.main(["--config", str(cfg), "--json"])
        assert rc == 0
        report = json.loads(capsys.readouterr().out)
        pmr = report["platform_manifest"]
        assert pmr["private_manifest_path"] is None
        assert pmr["project_manifest_path"] is None
        assert pmr["local_manifest_path"] is None
        assert pmr["version"] is None
        assert report["repo_root"] is None

    def test_repo_root_forwarded_to_factory(self, tmp_path, monkeypatch, capsys) -> None:
        cfg = _existing_config(tmp_path)
        pm = _make_pm()
        graph = _FakeGraph(nodes=[_FakeNode("platform", "public")])
        mocks = _patch_pipeline(monkeypatch, settings=_make_settings(pm), graph=graph)
        gd.main(["--config", str(cfg), "--repo-root", "/r", "--json"])
        _, kwargs = mocks["build"].call_args
        assert kwargs["repo_root"] == Path("/r")


# ---------------------------------------------------------------------------
# main — warning capture
# ---------------------------------------------------------------------------


class TestMainWarningCapture:
    def test_factory_warnings_captured(self, tmp_path, monkeypatch, capsys) -> None:
        cfg = _existing_config(tmp_path)
        pm = _make_pm()
        graph = _FakeGraph(nodes=[_FakeNode("platform", "public")])

        def _build_emitting_warning(_settings, repo_root=None):  # noqa: ANN001
            logging.getLogger("operations_center.repo_graph_factory").warning("danger ahead")
            return graph

        monkeypatch.setattr(gd, "load_settings", mock.Mock(return_value=_make_settings(pm)))
        monkeypatch.setattr(gd, "build_effective_repo_graph_from_settings", _build_emitting_warning)
        monkeypatch.setattr(gd, "_resolve_platform_manifest_version", lambda: "1")
        rc = gd.main(["--config", str(cfg), "--json"])
        assert rc == 0
        report = json.loads(capsys.readouterr().out)
        assert any("danger ahead" in w for w in report["warnings"])

    def test_logger_level_restored_after_run(self, tmp_path, monkeypatch, capsys) -> None:
        cfg = _existing_config(tmp_path)
        logger = logging.getLogger("operations_center.repo_graph_factory")
        original_level = logger.level
        logger.setLevel(logging.ERROR)
        prior_handlers = list(logger.handlers)
        pm = _make_pm()
        _patch_pipeline(
            monkeypatch,
            settings=_make_settings(pm),
            graph=_FakeGraph(nodes=[_FakeNode("platform", "public")]),
        )
        try:
            gd.main(["--config", str(cfg), "--json"])
            assert logger.level == logging.ERROR
            assert list(logger.handlers) == prior_handlers
        finally:
            # Restore the level we forced so later caplog-based tests on this
            # logger aren't silenced by a leaked ERROR threshold.
            logger.setLevel(original_level)


# ---------------------------------------------------------------------------
# main — human output paths
# ---------------------------------------------------------------------------


class TestHumanOutput:
    def test_human_full_report(self, tmp_path, monkeypatch, capsys) -> None:
        cfg = _existing_config(tmp_path)
        ws = tmp_path / "ws.yaml"
        pm = _make_pm(project_slug="demo", work_scope_manifest_path=ws)
        graph = _FakeGraph(
            nodes=[_FakeNode("platform", "public"), _FakeNode("project", "private")],
            edges=[_FakeEdge("platform")],
        )
        breakdown = [
            {"name": "alpha", "path": "/p/a", "nodes_contributed": 2, "edges_contributed": 1},
            {"index": 1, "error": "bad include"},
        ]

        def _build_warns(_s, repo_root=None):  # noqa: ANN001
            logging.getLogger("operations_center.repo_graph_factory").warning("w1")
            return graph

        monkeypatch.setattr(gd, "load_settings", mock.Mock(return_value=_make_settings(pm)))
        monkeypatch.setattr(gd, "build_effective_repo_graph_from_settings", _build_warns)
        monkeypatch.setattr(gd, "_resolve_platform_manifest_version", lambda: "7.0")
        monkeypatch.setattr(gd, "_compute_per_include_breakdown", lambda _p: breakdown)
        rc = gd.main(["--config", str(cfg)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "✓ graph-doctor: ok" in out
        assert "mode:                     work_scope" in out
        assert "project_slug:             demo" in out
        assert "nodes_total:            2" in out
        assert "nodes_by_source" in out
        assert "includes (2):" in out
        assert "+2 nodes" in out
        assert "ERROR bad include" in out
        assert "warnings (1):" in out
        assert "- w1" in out

    def test_human_failure_icon_and_no_counts(self, tmp_path, monkeypatch, capsys) -> None:
        cfg = _existing_config(tmp_path)
        pm = _make_pm()
        _patch_pipeline(monkeypatch, settings=_make_settings(pm), graph=None)
        rc = gd.main(["--config", str(cfg)])
        assert rc == 1
        out = capsys.readouterr().out
        assert "✗ graph-doctor: fail_graph_none" in out
        assert "nodes_total" not in out
        assert "includes" not in out

    def test_human_unknown_version_and_none_slug(self, tmp_path, monkeypatch, capsys) -> None:
        cfg = _existing_config(tmp_path)
        pm = _make_pm()
        graph = _FakeGraph(nodes=[_FakeNode("platform", "public")])
        _patch_pipeline(monkeypatch, settings=_make_settings(pm), graph=graph, version=None)
        rc = gd.main(["--config", str(cfg)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "(unknown)" in out
        assert "project_slug:             (none)" in out

    def test_print_human_non_dict_pm_and_includes_entries(self, capsys) -> None:
        # Directly exercise _print_human defensive branches: pm not a dict,
        # includes containing a non-dict entry (skipped).
        report = {
            "status": "ok",
            "config": "/c",
            "platform_manifest": "not-a-dict",
            "repo_root": None,
            "graph_built": True,
            "nodes_total": 1,
            "edges_total": 0,
            "nodes_by_source": {},
            "edges_by_source": {},
            "nodes_by_visibility": {},
            "includes": [
                "not-a-dict",
                {"name": "ok", "nodes_contributed": 0, "edges_contributed": 0, "path": "/x"},
            ],
            "warnings": [],
        }
        gd._print_human(report, 0)
        out = capsys.readouterr().out
        assert "enabled:                  None" in out
        assert "includes (2):" in out
        assert "- ok:" in out


# ---------------------------------------------------------------------------
# _resolve_platform_manifest_version
# ---------------------------------------------------------------------------


class TestResolveVersion:
    def test_returns_version(self, monkeypatch) -> None:
        monkeypatch.setattr("importlib.metadata.version", lambda name: "3.1.4")
        assert gd._resolve_platform_manifest_version() == "3.1.4"

    def test_package_not_found_returns_none(self, monkeypatch) -> None:
        from importlib import metadata as md

        def _raise(_name):  # noqa: ANN001
            raise md.PackageNotFoundError("platform-manifest")

        monkeypatch.setattr("importlib.metadata.version", _raise)
        assert gd._resolve_platform_manifest_version() is None


# ---------------------------------------------------------------------------
# _emit_error
# ---------------------------------------------------------------------------


class TestEmitError:
    def test_json_payload(self, capsys) -> None:
        gd._emit_error(True, "oops", exit_code=2, config="/c")
        payload = json.loads(capsys.readouterr().out)
        assert payload == {
            "status": "error",
            "message": "oops",
            "exit_code": 2,
            "config": "/c",
        }

    def test_human_payload(self, capsys) -> None:
        gd._emit_error(False, "oops", exit_code=2)
        out = capsys.readouterr().out
        assert "✗ graph-doctor: error — oops" in out


# ---------------------------------------------------------------------------
# _compute_per_include_breakdown
# ---------------------------------------------------------------------------


def _install_pm_stubs(monkeypatch, *, load_effective, load_repo, base="/base") -> None:
    """Install a fake ``platform_manifest`` package + submodule in sys.modules
    so the lazy imports inside _compute_per_include_breakdown resolve."""
    import sys
    import types

    class _RepoGraphConfigError(Exception):
        pass

    pm_mod = types.ModuleType("platform_manifest")
    pm_mod.RepoGraphConfigError = _RepoGraphConfigError
    pm_mod.default_config_path = lambda: Path(base)
    pm_mod.load_effective_graph = load_effective
    pm_mod.load_repo_graph = load_repo

    models_mod = types.ModuleType("platform_manifest.models")
    models_mod.ManifestKind = SimpleNamespace(PLATFORM="platform")
    pm_mod.models = models_mod

    monkeypatch.setitem(sys.modules, "platform_manifest", pm_mod)
    monkeypatch.setitem(sys.modules, "platform_manifest.models", models_mod)
    return _RepoGraphConfigError


class TestComputePerIncludeBreakdown:
    def test_yaml_read_error(self, tmp_path, monkeypatch) -> None:
        ws = tmp_path / "missing.yaml"  # does not exist -> OSError on read_text
        self_err = _install_pm_stubs(monkeypatch, load_effective=mock.Mock(), load_repo=mock.Mock())
        assert self_err is not None
        result = gd._compute_per_include_breakdown(ws)
        assert len(result) == 1
        assert "failed to read work-scope manifest" in result[0]["error"]

    def test_root_not_mapping(self, tmp_path, monkeypatch) -> None:
        ws = tmp_path / "ws.yaml"
        ws.write_text("- just\n- a list\n", encoding="utf-8")
        _install_pm_stubs(monkeypatch, load_effective=mock.Mock(), load_repo=mock.Mock())
        result = gd._compute_per_include_breakdown(ws)
        assert result == [{"error": "work-scope manifest root is not a mapping"}]

    def test_empty_yaml_treated_as_empty_mapping(self, tmp_path, monkeypatch) -> None:
        ws = tmp_path / "ws.yaml"
        ws.write_text("", encoding="utf-8")
        platform_graph = _FakeGraph(nodes=[_FakeNode("platform", "public")], edges=[])
        _install_pm_stubs(
            monkeypatch,
            load_effective=mock.Mock(),
            load_repo=mock.Mock(return_value=platform_graph),
        )
        # No includes key -> empty list -> loop runs zero times.
        result = gd._compute_per_include_breakdown(ws)
        assert result == []

    def test_includes_not_a_list(self, tmp_path, monkeypatch) -> None:
        ws = tmp_path / "ws.yaml"
        ws.write_text("includes: {a: 1}\n", encoding="utf-8")
        _install_pm_stubs(monkeypatch, load_effective=mock.Mock(), load_repo=mock.Mock())
        result = gd._compute_per_include_breakdown(ws)
        assert result == [{"error": "work-scope manifest 'includes' is not a list"}]

    def test_platform_base_load_failure(self, tmp_path, monkeypatch) -> None:
        ws = tmp_path / "ws.yaml"
        ws.write_text("includes:\n  - name: a\n", encoding="utf-8")
        err = _install_pm_stubs(
            monkeypatch,
            load_effective=mock.Mock(),
            load_repo=mock.Mock(),
        )

        def _raise(_path, expected_kind=None):  # noqa: ANN001
            raise err("base broken")

        import sys

        sys.modules["platform_manifest"].load_repo_graph = _raise
        result = gd._compute_per_include_breakdown(ws)
        assert result == [{"error": "platform base load failed: base broken"}]

    def test_include_entry_not_mapping(self, tmp_path, monkeypatch) -> None:
        ws = tmp_path / "ws.yaml"
        ws.write_text("includes:\n  - just-a-string\n", encoding="utf-8")
        platform_graph = _FakeGraph(nodes=[_FakeNode("platform", "public")], edges=[])
        _install_pm_stubs(
            monkeypatch,
            load_effective=mock.Mock(),
            load_repo=mock.Mock(return_value=platform_graph),
        )
        result = gd._compute_per_include_breakdown(ws)
        assert result == [{"index": 0, "error": "include entry is not a mapping"}]

    def test_include_missing_path(self, tmp_path, monkeypatch) -> None:
        ws = tmp_path / "ws.yaml"
        ws.write_text("includes:\n  - name: nopath\n", encoding="utf-8")
        platform_graph = _FakeGraph(nodes=[_FakeNode("platform", "public")], edges=[])
        _install_pm_stubs(
            monkeypatch,
            load_effective=mock.Mock(),
            load_repo=mock.Mock(return_value=platform_graph),
        )
        result = gd._compute_per_include_breakdown(ws)
        assert result == [
            {"name": "nopath", "error": "missing or non-string 'project_manifest_path'"}
        ]

    def test_include_unnamed_uses_index_label(self, tmp_path, monkeypatch) -> None:
        ws = tmp_path / "ws.yaml"
        ws.write_text("includes:\n  - project_manifest_path: 5\n", encoding="utf-8")
        platform_graph = _FakeGraph(nodes=[_FakeNode("platform", "public")], edges=[])
        _install_pm_stubs(
            monkeypatch,
            load_effective=mock.Mock(),
            load_repo=mock.Mock(return_value=platform_graph),
        )
        result = gd._compute_per_include_breakdown(ws)
        # path_raw=5 is non-string -> error; name falls back to include[0]
        assert result == [
            {"name": "include[0]", "error": "missing or non-string 'project_manifest_path'"}
        ]

    def test_include_load_effective_error(self, tmp_path, monkeypatch) -> None:
        ws = tmp_path / "ws.yaml"
        ws.write_text(
            "includes:\n  - name: a\n    project_manifest_path: rel/p.yaml\n",
            encoding="utf-8",
        )
        platform_graph = _FakeGraph(nodes=[_FakeNode("platform", "public")], edges=[])
        err = _install_pm_stubs(
            monkeypatch,
            load_effective=mock.Mock(side_effect=OSError("disk gone")),
            load_repo=mock.Mock(return_value=platform_graph),
        )
        assert err is not None
        result = gd._compute_per_include_breakdown(ws)
        assert result[0]["name"] == "a"
        assert result[0]["error"] == "disk gone"
        assert result[0]["path"].endswith("rel/p.yaml")

    def test_include_success_contribution_counts(self, tmp_path, monkeypatch) -> None:
        ws = tmp_path / "ws.yaml"
        ws.write_text(
            "includes:\n  - name: a\n    project_manifest_path: rel/p.yaml\n",
            encoding="utf-8",
        )
        platform_graph = _FakeGraph(
            nodes=[_FakeNode("platform", "public")], edges=[_FakeEdge("platform")]
        )
        effective_graph = _FakeGraph(
            nodes=[_FakeNode("platform", "public"), _FakeNode("project", "public")],
            edges=[_FakeEdge("platform"), _FakeEdge("project"), _FakeEdge("project")],
        )
        load_eff = mock.Mock(return_value=effective_graph)
        _install_pm_stubs(
            monkeypatch,
            load_effective=load_eff,
            load_repo=mock.Mock(return_value=platform_graph),
        )
        result = gd._compute_per_include_breakdown(ws)
        assert len(result) == 1
        entry = result[0]
        assert entry["name"] == "a"
        assert entry["nodes_contributed"] == 1
        assert entry["edges_contributed"] == 2
        # include path resolved relative to ws parent
        assert entry["path"] == str((ws.parent / Path("rel/p.yaml")).resolve())
        _, kwargs = load_eff.call_args
        assert kwargs["project"] == (ws.parent / Path("rel/p.yaml")).resolve()


# ---------------------------------------------------------------------------
# __main__ guard
# ---------------------------------------------------------------------------


class TestEntrypoint:
    def test_main_callable_via_module(self, tmp_path, monkeypatch, capsys) -> None:
        cfg = _existing_config(tmp_path)
        pm = _make_pm(enabled=False)
        _patch_pipeline(monkeypatch, settings=_make_settings(pm), graph=None)
        rc = gd.main(["--config", str(cfg), "--json"])
        assert isinstance(rc, int)
        assert rc == 0
