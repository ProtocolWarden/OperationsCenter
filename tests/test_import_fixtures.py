# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for import error fixtures."""

import pytest

from operations_center.config import Settings


class TestOptionalImport:
    """Test optional_import fixture."""

    def test_import_existing_module(self, optional_import):
        """Should import a module that exists."""
        json_module = optional_import("json")
        assert json_module is not None
        assert hasattr(json_module, "dumps")

    def test_skip_on_missing_module(self, optional_import):
        """Should skip test when module does not exist."""
        # Use the fixture function form to explicitly trigger import
        with pytest.raises(pytest.skip.Exception):
            optional_import("nonexistent.fake.module.that.does.not.exist")

    @pytest.mark.parametrize("optional_import", ["json", "os"], indirect=True)
    def test_parametrize_with_indirect(self, optional_import):
        """Should work with parametrize + indirect=True."""
        assert optional_import is not None

    @pytest.mark.parametrize(
        "optional_import",
        ["nonexistent_module_xyz", "also_fake_module_abc"],
        indirect=True,
    )
    def test_parametrize_indirect_missing_module(self, optional_import):
        """Should skip when parametrized module doesn't exist."""
        assert False, "optional_import fixture should have triggered pytest.skip"


class TestRequireModule:
    """Test require_module fixture."""

    def test_import_existing_module(self, require_module):
        """Should import a module that exists."""
        json_module = require_module("json")
        assert json_module is not None
        assert hasattr(json_module, "dumps")

    def test_fail_on_missing_module(self, require_module):
        """Should fail test when module does not exist."""
        with pytest.raises(AssertionError, match="Required module.*could not be imported"):
            require_module("nonexistent.fake.module.that.does.not.exist")

    @pytest.mark.parametrize("require_module", ["json", "os"], indirect=True)
    def test_parametrize_with_indirect(self, require_module):
        """Should work with parametrize + indirect=True."""
        assert require_module is not None


class TestModuleWithEnv:
    """Test module_with_env fixture."""

    def test_import_with_env_var(self, module_with_env):
        """Should import module after setting environment variable."""
        # Use a simple module that doesn't depend on environment at import time
        json_module = module_with_env(
            module_path="json",
            env={"TEST_VAR": "test_value"},
        )
        assert json_module is not None
        # Environment is cleaned up after the fixture call, so this should be None
        import os

        assert os.environ.get("TEST_VAR") is None

    def test_module_cache_cleared(self, module_with_env):
        """Should clear module from sys.modules before import."""
        # Add a marker to json module to test cache clearing
        import json

        json.test_marker = "original"

        json_module_1 = module_with_env(
            module_path="json",
            env={"MARKER_TEST": "1"},
            clear_cache=True,
        )

        # After reimport with clear_cache=True, test_marker should not exist
        assert not hasattr(json_module_1, "test_marker")

    def test_clear_cache_false(self, module_with_env):
        """Should not clear module cache when clear_cache=False."""
        import json

        json.test_marker_2 = "should_persist"

        json_module = module_with_env(
            module_path="json",
            env={"MARKER_TEST_2": "1"},
            clear_cache=False,
        )

        # With clear_cache=False, test_marker_2 should still exist
        assert hasattr(json_module, "test_marker_2")


class TestOCModuleImport:
    """Verify fixtures work with real OC source modules."""

    def test_oc_settings_importable(self, require_module):
        """Settings class must be importable via require_module fixture."""
        mod = require_module("operations_center.config")
        assert hasattr(mod, "Settings")
        assert mod.Settings is Settings


class TestAssertModuleUnavailable:
    """Test assert_module_unavailable fixture."""

    def test_assert_unavailable_module(self, assert_module_unavailable):
        """Should not raise when module is unavailable."""
        # Should not raise any exception
        assert_module_unavailable("nonexistent.fake.module.that.does.not.exist")

    def test_assert_available_module_fails(self, assert_module_unavailable):
        """Should raise AssertionError when module IS available."""
        with pytest.raises(AssertionError, match="Expected ModuleNotFoundError"):
            assert_module_unavailable("json")

    def test_multiple_assertions(self, assert_module_unavailable):
        """Should allow multiple assertions in one test."""
        assert_module_unavailable("nonexistent_module_1_xyz")
        assert_module_unavailable("nonexistent_module_2_xyz")
        assert_module_unavailable("nonexistent_module_3_xyz")
