# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for coverage threshold configuration system.

Covers configuration providers, schema validation, configuration manager,
and integration with CoverageAlertConfig.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from operations_center.observer.coverage_alerting import (
    AlertSeverity,
    AlertType,
    CoverageAlertConfig,
)
from operations_center.observer.coverage_config import (
    AlertChannelConfig,
    AlertChannelRoute,
    CompositeConfigProvider,
    ConfigValidationError,
    CoverageConfigManager,
    CoverageConfigSchema,
    DefaultConfigProvider,
    EnvironmentConfigProvider,
    YamlConfigProvider,
)


class TestDefaultConfigProvider:
    """Tests for DefaultConfigProvider."""

    def test_load_returns_default_values(self) -> None:
        """Test that load returns all default values."""
        provider = DefaultConfigProvider()
        config = provider.load()

        assert config["repo_minimum_threshold"] == 80.0
        assert config["repo_warning_threshold"] == 85.0
        assert config["repo_target_threshold"] == 90.0
        assert config["statement_coverage_minimum"] == 75.0
        assert config["branch_coverage_minimum"] == 65.0
        assert config["line_coverage_minimum"] == 75.0
        assert config["regression_threshold_pct"] == 2.0
        assert config["regression_7day_threshold_pct"] == 3.0
        assert config["regression_30day_threshold_pct"] == 5.0
        assert config["trend_degradation_days"] == 5
        assert config["trend_degradation_velocity_pct"] == 1.0
        assert config["severity_critical_threshold"] == 50.0
        assert config["severity_high_threshold"] == 70.0
        assert config["severity_medium_threshold"] == 80.0
        assert config["module_thresholds"] == {}

    def test_load_contains_all_required_keys(self) -> None:
        """Test that load includes all required configuration keys."""
        provider = DefaultConfigProvider()
        config = provider.load()

        required_keys = {
            "repo_minimum_threshold",
            "repo_warning_threshold",
            "repo_target_threshold",
            "statement_coverage_minimum",
            "branch_coverage_minimum",
            "line_coverage_minimum",
            "regression_threshold_pct",
            "regression_7day_threshold_pct",
            "regression_30day_threshold_pct",
            "trend_degradation_days",
            "trend_degradation_velocity_pct",
            "severity_critical_threshold",
            "severity_high_threshold",
            "severity_medium_threshold",
            "module_thresholds",
        }

        assert required_keys.issubset(set(config.keys()))

    def test_validate_accepts_default_config(self) -> None:
        """Test that validate accepts default configuration."""
        provider = DefaultConfigProvider()
        config = provider.load()
        schema = provider.validate(config)

        assert schema.repo_minimum_threshold == 80.0
        assert schema.repo_target_threshold == 90.0


class TestYamlConfigProvider:
    """Tests for YamlConfigProvider."""

    def test_load_valid_yaml_file(self) -> None:
        """Test loading valid YAML configuration file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(
                {
                    "repo_minimum_threshold": 82.0,
                    "repo_warning_threshold": 87.0,
                    "statement_coverage_minimum": 78.0,
                },
                f,
            )
            f.flush()

            try:
                provider = YamlConfigProvider(f.name)
                config = provider.load()

                assert config["repo_minimum_threshold"] == 82.0
                assert config["repo_warning_threshold"] == 87.0
                assert config["statement_coverage_minimum"] == 78.0
            finally:
                Path(f.name).unlink()

    def test_load_yaml_with_module_thresholds(self) -> None:
        """Test loading YAML with module-level threshold overrides."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(
                {
                    "repo_minimum_threshold": 80.0,
                    "module_thresholds": {
                        "src/observer": {
                            "statement_coverage_minimum": 85.0,
                        }
                    },
                },
                f,
            )
            f.flush()

            try:
                provider = YamlConfigProvider(f.name)
                config = provider.load()

                assert (
                    config["module_thresholds"]["src/observer"]["statement_coverage_minimum"]
                    == 85.0
                )
            finally:
                Path(f.name).unlink()

    def test_load_nonexistent_file_raises_error(self) -> None:
        """Test that loading nonexistent file raises ConfigValidationError."""
        provider = YamlConfigProvider("/nonexistent/path/config.yaml")

        with pytest.raises(ConfigValidationError, match="Configuration file not found"):
            provider.load()

    def test_load_invalid_yaml_raises_error(self) -> None:
        """Test that loading invalid YAML raises ConfigValidationError."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("invalid: yaml: content: [")
            f.flush()

            try:
                provider = YamlConfigProvider(f.name)

                with pytest.raises(ConfigValidationError, match="Invalid YAML"):
                    provider.load()
            finally:
                Path(f.name).unlink()

    def test_load_empty_yaml_file(self) -> None:
        """Test loading empty YAML file returns empty dict."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("")
            f.flush()

            try:
                provider = YamlConfigProvider(f.name)
                config = provider.load()

                assert config == {}
            finally:
                Path(f.name).unlink()

    def test_validate_yaml_config(self) -> None:
        """Test validating YAML configuration."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(
                {
                    "repo_minimum_threshold": 82.0,
                    "statement_coverage_minimum": 78.0,
                },
                f,
            )
            f.flush()

            try:
                provider = YamlConfigProvider(f.name)
                config = provider.load()
                schema = provider.validate(config)

                assert schema.repo_minimum_threshold == 82.0
                assert schema.statement_coverage_minimum == 78.0
            finally:
                Path(f.name).unlink()


class TestEnvironmentConfigProvider:
    """Tests for EnvironmentConfigProvider."""

    def test_load_from_environment_variables(self) -> None:
        """Test loading configuration from environment variables."""
        with patch.dict(
            os.environ,
            {
                "COVERAGE_REPO_MINIMUM_THRESHOLD": "82",
                "COVERAGE_REPO_WARNING_THRESHOLD": "87",
                "COVERAGE_STATEMENT_COVERAGE_MINIMUM": "78",
            },
        ):
            provider = EnvironmentConfigProvider()
            config = provider.load()

            assert config["repo_minimum_threshold"] == 82
            assert config["repo_warning_threshold"] == 87
            assert config["statement_coverage_minimum"] == 78

    def test_load_parses_floats(self) -> None:
        """Test that float values are parsed correctly."""
        with patch.dict(
            os.environ,
            {
                "COVERAGE_REPO_MINIMUM_THRESHOLD": "82.5",
                "COVERAGE_REGRESSION_THRESHOLD_PCT": "2.5",
            },
        ):
            provider = EnvironmentConfigProvider()
            config = provider.load()

            assert config["repo_minimum_threshold"] == 82.5
            assert config["regression_threshold_pct"] == 2.5

    def test_load_parses_booleans(self) -> None:
        """Test that boolean values are parsed correctly."""
        with patch.dict(
            os.environ,
            {
                "COVERAGE_SOME_BOOL_TRUE": "true",
                "COVERAGE_SOME_BOOL_FALSE": "false",
            },
        ):
            provider = EnvironmentConfigProvider()
            config = provider.load()

            assert config["some_bool_true"] is True
            assert config["some_bool_false"] is False

    def test_load_ignores_non_coverage_variables(self) -> None:
        """Test that non-COVERAGE_ variables are ignored."""
        with patch.dict(
            os.environ,
            {
                "COVERAGE_REPO_MINIMUM_THRESHOLD": "82",
                "OTHER_VAR": "value",
                "NO_COVERAGE_PREFIX": "value",
            },
        ):
            provider = EnvironmentConfigProvider()
            config = provider.load()

            assert "repo_minimum_threshold" in config
            assert "other_var" not in config
            assert "no_coverage_prefix" not in config

    def test_load_empty_env_returns_empty_dict(self) -> None:
        """Test that no environment variables returns empty dict."""
        with patch.dict(os.environ, {}, clear=True):
            provider = EnvironmentConfigProvider()
            config = provider.load()

            assert config == {}

    def test_load_ignores_empty_values(self) -> None:
        """Test that empty environment variable values are ignored."""
        with patch.dict(
            os.environ,
            {
                "COVERAGE_REPO_MINIMUM_THRESHOLD": "",
                "COVERAGE_REPO_WARNING_THRESHOLD": "87",
            },
        ):
            provider = EnvironmentConfigProvider()
            config = provider.load()

            assert "repo_minimum_threshold" not in config
            assert config["repo_warning_threshold"] == 87


class TestCoverageConfigSchema:
    """Tests for CoverageConfigSchema validation."""

    def test_schema_accepts_valid_percentages(self) -> None:
        """Test that schema accepts valid percentage values (0-100)."""
        schema = CoverageConfigSchema(
            repo_minimum_threshold=80.0,
            statement_coverage_minimum=75.0,
            branch_coverage_minimum=65.0,
        )

        assert schema.repo_minimum_threshold == 80.0
        assert schema.statement_coverage_minimum == 75.0

    def test_schema_rejects_negative_percentage(self) -> None:
        """Test that schema rejects negative percentage values."""
        with pytest.raises(Exception):  # ValidationError from pydantic
            CoverageConfigSchema(repo_minimum_threshold=-5.0)

    def test_schema_rejects_percentage_over_100(self) -> None:
        """Test that schema rejects percentage values over 100."""
        with pytest.raises(Exception):
            CoverageConfigSchema(repo_minimum_threshold=105.0)

    def test_schema_accepts_zero_percentage(self) -> None:
        """Test that schema accepts 0% threshold."""
        schema = CoverageConfigSchema(repo_minimum_threshold=0.0)
        assert schema.repo_minimum_threshold == 0.0

    def test_schema_accepts_100_percentage(self) -> None:
        """Test that schema accepts 100% threshold."""
        schema = CoverageConfigSchema(repo_minimum_threshold=100.0)
        assert schema.repo_minimum_threshold == 100.0

    def test_schema_rejects_invalid_days(self) -> None:
        """Test that schema rejects non-positive days value."""
        with pytest.raises(Exception):
            CoverageConfigSchema(trend_degradation_days=0)

        with pytest.raises(Exception):
            CoverageConfigSchema(trend_degradation_days=-5)

    def test_schema_accepts_positive_days(self) -> None:
        """Test that schema accepts positive days value."""
        schema = CoverageConfigSchema(trend_degradation_days=7)
        assert schema.trend_degradation_days == 7

    def test_schema_accepts_module_thresholds(self) -> None:
        """Test that schema accepts module threshold overrides."""
        schema = CoverageConfigSchema(
            module_thresholds={"src/observer": {"statement_coverage_minimum": 85.0}}
        )

        assert schema.module_thresholds["src/observer"]["statement_coverage_minimum"] == 85.0

    def test_schema_partial_config(self) -> None:
        """Test that schema accepts partial configuration."""
        schema = CoverageConfigSchema(repo_minimum_threshold=82.0)

        assert schema.repo_minimum_threshold == 82.0
        assert schema.repo_warning_threshold is None
        assert schema.statement_coverage_minimum is None


class TestCompositeConfigProvider:
    """Tests for CompositeConfigProvider."""

    def test_composite_merges_providers(self) -> None:
        """Test that composite provider merges configs from all providers."""
        providers = [
            DefaultConfigProvider(),
        ]

        composite = CompositeConfigProvider(providers)
        config = composite.load()

        # Should have all default values
        assert config["repo_minimum_threshold"] == 80.0
        assert config["repo_warning_threshold"] == 85.0

    def test_composite_later_provider_overrides_earlier(self) -> None:
        """Test that later providers override earlier ones."""
        provider1 = DefaultConfigProvider()

        # Create a custom provider that returns specific overrides
        class CustomProvider(DefaultConfigProvider):
            def load(self) -> dict:
                base = super().load()
                base["repo_minimum_threshold"] = 82.0
                return base

        provider2 = CustomProvider()

        composite = CompositeConfigProvider([provider1, provider2])
        config = composite.load()

        # provider2 should override provider1
        assert config["repo_minimum_threshold"] == 82.0

    def test_composite_merges_module_thresholds(self) -> None:
        """Test that composite provider merges module thresholds."""

        class Provider1(DefaultConfigProvider):
            def load(self) -> dict:
                base = super().load()
                base["module_thresholds"] = {"src/observer": {"statement_coverage_minimum": 85.0}}
                return base

        class Provider2(DefaultConfigProvider):
            def load(self) -> dict:
                base = super().load()
                base["module_thresholds"] = {"src/custodian": {"statement_coverage_minimum": 80.0}}
                return base

        composite = CompositeConfigProvider([Provider1(), Provider2()])
        config = composite.load()

        # Both modules should be present
        assert config["module_thresholds"]["src/observer"]["statement_coverage_minimum"] == 85.0
        assert config["module_thresholds"]["src/custodian"]["statement_coverage_minimum"] == 80.0

    def test_composite_module_threshold_override(self) -> None:
        """Test that later provider can override module thresholds."""

        class Provider1(DefaultConfigProvider):
            def load(self) -> dict:
                base = super().load()
                base["module_thresholds"] = {"src/observer": {"statement_coverage_minimum": 85.0}}
                return base

        class Provider2(DefaultConfigProvider):
            def load(self) -> dict:
                base = super().load()
                base["module_thresholds"] = {"src/observer": {"statement_coverage_minimum": 90.0}}
                return base

        composite = CompositeConfigProvider([Provider1(), Provider2()])
        config = composite.load()

        # Provider2 should override provider1's module threshold
        assert config["module_thresholds"]["src/observer"]["statement_coverage_minimum"] == 90.0


class TestCoverageConfigManager:
    """Tests for CoverageConfigManager."""

    def test_create_default(self) -> None:
        """Test creating manager with defaults."""
        manager = CoverageConfigManager.create_default()
        config = manager.load_config()

        assert config["repo_minimum_threshold"] == 80.0
        assert config["repo_target_threshold"] == 90.0

    def test_get_alert_config(self) -> None:
        """Test getting CoverageAlertConfig from manager."""
        manager = CoverageConfigManager.create_default()
        alert_config = manager.get_alert_config()

        assert isinstance(alert_config, CoverageAlertConfig)
        assert alert_config.repo_minimum_threshold == 80.0
        assert alert_config.repo_target_threshold == 90.0

    def test_get_alert_config_with_overrides(self) -> None:
        """Test getting CoverageAlertConfig with configuration overrides."""

        class CustomProvider(DefaultConfigProvider):
            def load(self) -> dict:
                base = super().load()
                base["repo_minimum_threshold"] = 82.0
                return base

        manager = CoverageConfigManager(CustomProvider())
        alert_config = manager.get_alert_config()

        assert alert_config.repo_minimum_threshold == 82.0

    def test_load_config_caches_result(self) -> None:
        """Test that load_config caches result."""
        manager = CoverageConfigManager.create_default()

        config1 = manager.load_config()
        config2 = manager.load_config()

        # Should return same object (cached)
        assert config1 is config2

    def test_get_alert_config_caches_result(self) -> None:
        """Test that get_alert_config caches result."""
        manager = CoverageConfigManager.create_default()

        config1 = manager.get_alert_config()
        config2 = manager.get_alert_config()

        # Should return same object (cached)
        assert config1 is config2

    def test_reload_clears_cache(self) -> None:
        """Test that reload clears cached configuration."""
        manager = CoverageConfigManager.create_default()

        config1 = manager.load_config()
        manager.reload()
        config2 = manager.load_config()

        # Should be different objects after reload
        assert config1 is not config2

    def test_create_with_yaml(self) -> None:
        """Test creating manager with YAML configuration."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(
                {
                    "repo_minimum_threshold": 82.0,
                    "statement_coverage_minimum": 78.0,
                },
                f,
            )
            f.flush()

            try:
                manager = CoverageConfigManager.create_with_yaml(f.name)
                config = manager.load_config()

                # YAML values should override defaults
                assert config["repo_minimum_threshold"] == 82.0
                assert config["statement_coverage_minimum"] == 78.0
                # Other defaults should still be present
                assert config["repo_warning_threshold"] == 85.0
            finally:
                Path(f.name).unlink()

    def test_create_with_yaml_env_override(self) -> None:
        """Test that environment variables override YAML values."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump({"repo_minimum_threshold": 82.0}, f)
            f.flush()

            try:
                with patch.dict(
                    os.environ,
                    {"COVERAGE_REPO_MINIMUM_THRESHOLD": "84"},
                ):
                    manager = CoverageConfigManager.create_with_yaml(f.name)
                    config = manager.load_config()

                    # Environment variable should override YAML
                    assert config["repo_minimum_threshold"] == 84
            finally:
                Path(f.name).unlink()

    def test_create_auto_discovery_with_existing_file(self) -> None:
        """Test auto-discovery when config file exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "coverage-config.yaml"
            yaml.dump({"repo_minimum_threshold": 82.0}, config_path.open("w"))

            manager = CoverageConfigManager.create_auto_discovery([config_path])
            config = manager.load_config()

            assert config["repo_minimum_threshold"] == 82.0

    def test_create_auto_discovery_uses_defaults_when_no_file(self) -> None:
        """Test auto-discovery uses defaults when no config file exists."""
        manager = CoverageConfigManager.create_auto_discovery(
            [Path("/nonexistent/path/config.yaml")]
        )
        config = manager.load_config()

        # Should fall back to defaults
        assert config["repo_minimum_threshold"] == 80.0

    def test_config_with_module_thresholds(self) -> None:
        """Test configuration with module-level threshold overrides."""
        config_dict = {
            "repo_minimum_threshold": 80.0,
            "module_thresholds": {
                "src/observer": {"statement_coverage_minimum": 85.0},
                "src/custodian": {"statement_coverage_minimum": 80.0},
            },
        }

        class CustomProvider(DefaultConfigProvider):
            def load(self) -> dict:
                return config_dict

        manager = CoverageConfigManager(CustomProvider())
        alert_config = manager.get_alert_config()

        assert alert_config.module_thresholds["src/observer"]["statement_coverage_minimum"] == 85.0
        assert alert_config.module_thresholds["src/custodian"]["statement_coverage_minimum"] == 80.0

    def test_invalid_config_raises_error(self) -> None:
        """Test that invalid configuration raises ConfigValidationError."""

        class BadProvider(DefaultConfigProvider):
            def load(self) -> dict:
                return {"repo_minimum_threshold": 150.0}  # Invalid percentage

        manager = CoverageConfigManager(BadProvider())

        with pytest.raises(ConfigValidationError):
            manager.load_config()

    def test_init_with_single_provider(self) -> None:
        """Test initializing manager with single provider."""
        provider = DefaultConfigProvider()
        manager = CoverageConfigManager(provider)

        assert manager.provider is provider

    def test_init_with_provider_list(self) -> None:
        """Test initializing manager with list of providers."""
        providers = [DefaultConfigProvider(), DefaultConfigProvider()]
        manager = CoverageConfigManager(providers)

        assert isinstance(manager.provider, CompositeConfigProvider)

    def test_init_with_invalid_provider_type_raises_error(self) -> None:
        """Test that invalid provider type raises TypeError."""
        with pytest.raises(TypeError):
            CoverageConfigManager("not a provider")  # type: ignore


class TestConfigurationIntegration:
    """Integration tests for configuration system."""

    def test_full_workflow_default_to_alert_config(self) -> None:
        """Test full workflow from defaults to CoverageAlertConfig."""
        manager = CoverageConfigManager.create_default()
        alert_config = manager.get_alert_config()

        # Verify all alert config values are set correctly
        assert alert_config.repo_minimum_threshold == 80.0
        assert alert_config.repo_warning_threshold == 85.0
        assert alert_config.repo_target_threshold == 90.0
        assert alert_config.statement_coverage_minimum == 75.0
        assert alert_config.branch_coverage_minimum == 65.0
        assert alert_config.line_coverage_minimum == 75.0

    def test_full_workflow_yaml_to_alert_config(self) -> None:
        """Test full workflow from YAML to CoverageAlertConfig."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(
                {
                    "repo_minimum_threshold": 82.0,
                    "statement_coverage_minimum": 78.0,
                    "module_thresholds": {"src/observer": {"statement_coverage_minimum": 85.0}},
                },
                f,
            )
            f.flush()

            try:
                manager = CoverageConfigManager.create_with_yaml(f.name)
                alert_config = manager.get_alert_config()

                assert alert_config.repo_minimum_threshold == 82.0
                assert alert_config.statement_coverage_minimum == 78.0
                assert (
                    alert_config.module_thresholds["src/observer"]["statement_coverage_minimum"]
                    == 85.0
                )
            finally:
                Path(f.name).unlink()

    def test_yaml_and_env_override_workflow(self) -> None:
        """Test workflow with YAML and environment variable overrides."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump({"repo_minimum_threshold": 82.0}, f)
            f.flush()

            try:
                with patch.dict(
                    os.environ,
                    {
                        "COVERAGE_REPO_MINIMUM_THRESHOLD": "84",
                        "COVERAGE_STATEMENT_COVERAGE_MINIMUM": "76",
                    },
                ):
                    manager = CoverageConfigManager.create_with_yaml(f.name)
                    alert_config = manager.get_alert_config()

                    # Environment should override YAML
                    assert alert_config.repo_minimum_threshold == 84
                    assert alert_config.statement_coverage_minimum == 76
                    # Other values from YAML or defaults
                    assert alert_config.repo_warning_threshold == 85.0
            finally:
                Path(f.name).unlink()


class TestAlertChannelRoute:
    """Tests for AlertChannelRoute configuration and matching."""

    def test_route_initialization(self) -> None:
        """Test basic AlertChannelRoute initialization."""
        route = AlertChannelRoute(
            channel_name="slack",
            enabled=True,
            alert_types=["below_threshold"],
            severity_levels=["critical"],
        )

        assert route.channel_name == "slack"
        assert route.enabled is True
        assert route.alert_types == ["below_threshold"]
        assert route.severity_levels == ["critical"]

    def test_route_matches_alert_all_types(self) -> None:
        """Test route matching when alert_types is empty (matches all)."""
        route = AlertChannelRoute(
            channel_name="slack", enabled=True, alert_types=[], severity_levels=[]
        )

        # Should match any alert type when alert_types is empty
        assert route.matches_alert(AlertType.BELOW_THRESHOLD, AlertSeverity.CRITICAL)
        assert route.matches_alert(AlertType.REGRESSION_DETECTED, AlertSeverity.WARNING)

    def test_route_matches_alert_specific_type(self) -> None:
        """Test route matching with specific alert types."""
        route = AlertChannelRoute(
            channel_name="email",
            enabled=True,
            alert_types=["below_threshold", "regression_detected"],
            severity_levels=[],
        )

        # Should match specified types
        assert route.matches_alert(AlertType.BELOW_THRESHOLD, AlertSeverity.INFO)
        assert route.matches_alert(AlertType.REGRESSION_DETECTED, AlertSeverity.INFO)

        # Should not match unspecified types
        assert not route.matches_alert(AlertType.TREND_DEGRADING, AlertSeverity.INFO)

    def test_route_matches_alert_severity_filtering(self) -> None:
        """Test route matching with severity level filtering."""
        route = AlertChannelRoute(
            channel_name="slack",
            enabled=True,
            alert_types=[],
            severity_levels=["critical", "emergency"],
        )

        # Should match specified severity levels
        assert route.matches_alert(AlertType.BELOW_THRESHOLD, AlertSeverity.CRITICAL)
        assert route.matches_alert(AlertType.BELOW_THRESHOLD, AlertSeverity.EMERGENCY)

        # Should not match other severity levels
        assert not route.matches_alert(AlertType.BELOW_THRESHOLD, AlertSeverity.WARNING)
        assert not route.matches_alert(AlertType.BELOW_THRESHOLD, AlertSeverity.INFO)

    def test_route_matches_alert_module_filtering(self) -> None:
        """Test route matching with module filtering."""
        route = AlertChannelRoute(
            channel_name="github",
            enabled=True,
            alert_types=[],
            severity_levels=[],
            enabled_modules=["src/observer", "src/custodian"],
        )

        # Should match specified modules
        assert route.matches_alert(AlertType.MODULE_GAP, AlertSeverity.INFO, "src/observer")
        assert route.matches_alert(AlertType.MODULE_GAP, AlertSeverity.INFO, "src/custodian")

        # Should not match unspecified modules
        assert not route.matches_alert(
            AlertType.MODULE_GAP,
            AlertSeverity.INFO,
            "src/execution",
        )

        # Should match when module not specified and list not empty
        assert not route.matches_alert(AlertType.MODULE_GAP, AlertSeverity.INFO)

    def test_route_disabled_never_matches(self) -> None:
        """Test that disabled routes never match alerts."""
        route = AlertChannelRoute(
            channel_name="slack",
            enabled=False,
            alert_types=[],
            severity_levels=[],
        )

        # Should never match when disabled
        assert not route.matches_alert(AlertType.BELOW_THRESHOLD, AlertSeverity.INFO)
        assert not route.matches_alert(AlertType.REGRESSION_DETECTED, AlertSeverity.EMERGENCY)

    def test_route_combined_matching(self) -> None:
        """Test route matching with combined criteria."""
        route = AlertChannelRoute(
            channel_name="pagerduty",
            enabled=True,
            alert_types=["below_threshold", "trend_degrading"],
            severity_levels=["critical", "emergency"],
            enabled_modules=["src/observer"],
        )

        # Should match all criteria
        assert route.matches_alert(
            AlertType.BELOW_THRESHOLD, AlertSeverity.CRITICAL, "src/observer"
        )

        # Should not match wrong alert type
        assert not route.matches_alert(
            AlertType.REGRESSION_DETECTED, AlertSeverity.CRITICAL, "src/observer"
        )

        # Should not match wrong severity
        assert not route.matches_alert(
            AlertType.BELOW_THRESHOLD, AlertSeverity.WARNING, "src/observer"
        )

        # Should not match wrong module
        assert not route.matches_alert(
            AlertType.BELOW_THRESHOLD, AlertSeverity.CRITICAL, "src/custodian"
        )


class TestAlertChannelConfig:
    """Tests for AlertChannelConfig routing resolution."""

    def test_empty_routes_uses_defaults(self) -> None:
        """Test that alerts with no matching routes use default channels."""
        config = AlertChannelConfig(routes=[], default_channels=["operator"])

        channels = config.get_routes_for_alert(AlertType.BELOW_THRESHOLD, AlertSeverity.INFO)

        assert channels == ["operator"]

    def test_single_matching_route(self) -> None:
        """Test that matching route is returned."""
        route = AlertChannelRoute(
            channel_name="slack",
            enabled=True,
            alert_types=["below_threshold"],
            severity_levels=[],
        )
        config = AlertChannelConfig(routes=[route], default_channels=["operator"])

        channels = config.get_routes_for_alert(AlertType.BELOW_THRESHOLD, AlertSeverity.INFO)

        assert channels == ["slack"]

    def test_first_matching_route_wins(self) -> None:
        """Test that first matching route is returned when multiple match."""
        routes = [
            AlertChannelRoute(
                channel_name="slack",
                enabled=True,
                alert_types=["below_threshold"],
                severity_levels=[],
            ),
            AlertChannelRoute(
                channel_name="email",
                enabled=True,
                alert_types=["below_threshold"],
                severity_levels=[],
            ),
        ]
        config = AlertChannelConfig(routes=routes, default_channels=["operator"])

        channels = config.get_routes_for_alert(AlertType.BELOW_THRESHOLD, AlertSeverity.INFO)

        # All matching routes are returned
        assert "slack" in channels

    def test_no_matching_routes_returns_defaults(self) -> None:
        """Test that no matching routes falls back to defaults."""
        routes = [
            AlertChannelRoute(
                channel_name="slack",
                enabled=True,
                alert_types=["regression_detected"],
                severity_levels=[],
            ),
        ]
        config = AlertChannelConfig(routes=routes, default_channels=["operator", "email"])

        # Alert type doesn't match route
        channels = config.get_routes_for_alert(AlertType.BELOW_THRESHOLD, AlertSeverity.INFO)

        assert channels == ["operator", "email"]

    def test_disabled_route_not_matched(self) -> None:
        """Test that disabled routes are skipped even if they match."""
        routes = [
            AlertChannelRoute(
                channel_name="slack",
                enabled=False,
                alert_types=[],
                severity_levels=[],
            ),
            AlertChannelRoute(
                channel_name="email",
                enabled=True,
                alert_types=[],
                severity_levels=[],
            ),
        ]
        config = AlertChannelConfig(routes=routes, default_channels=["operator"])

        channels = config.get_routes_for_alert(AlertType.BELOW_THRESHOLD, AlertSeverity.INFO)

        # Disabled route should be skipped, email route should match
        assert channels == ["email"]

    def test_severity_based_routing(self) -> None:
        """Test routing based on severity levels."""
        routes = [
            AlertChannelRoute(
                channel_name="pagerduty",
                enabled=True,
                alert_types=[],
                severity_levels=["critical", "emergency"],
            ),
            AlertChannelRoute(
                channel_name="slack",
                enabled=True,
                alert_types=[],
                severity_levels=["warning"],
            ),
        ]
        config = AlertChannelConfig(routes=routes, default_channels=["operator"])

        # Critical should go to PagerDuty
        channels = config.get_routes_for_alert(AlertType.BELOW_THRESHOLD, AlertSeverity.CRITICAL)
        assert channels == ["pagerduty"]

        # Warning should go to Slack
        channels = config.get_routes_for_alert(AlertType.BELOW_THRESHOLD, AlertSeverity.WARNING)
        assert channels == ["slack"]

        # Info should go to default (operator)
        channels = config.get_routes_for_alert(AlertType.BELOW_THRESHOLD, AlertSeverity.INFO)
        assert channels == ["operator"]


class TestCoverageConfigManagerAlertChannels:
    """Tests for CoverageConfigManager alert channel configuration loading."""

    def test_get_alert_channel_config_default(self) -> None:
        """Test getting alert channel config with defaults."""
        manager = CoverageConfigManager.create_default()
        channel_config = manager.get_alert_channel_config()

        assert channel_config is not None
        assert len(channel_config.routes) >= 1
        assert channel_config.default_channels == ["operator"]

    def test_get_alert_channel_config_from_yaml(self) -> None:
        """Test loading alert channel config from YAML file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(
                {
                    "alert_channels": {
                        "routes": [
                            {
                                "channel_name": "slack",
                                "enabled": True,
                                "alert_types": ["below_threshold"],
                                "severity_levels": ["critical"],
                                "enabled_modules": [],
                            },
                        ],
                        "default_channels": ["operator"],
                    }
                },
                f,
            )
            f.flush()

            try:
                manager = CoverageConfigManager.create_with_yaml(f.name)
                channel_config = manager.get_alert_channel_config()

                assert len(channel_config.routes) == 1
                assert channel_config.routes[0].channel_name == "slack"
                assert "below_threshold" in channel_config.routes[0].alert_types
                assert "critical" in channel_config.routes[0].severity_levels
            finally:
                Path(f.name).unlink()

    def test_alert_channel_config_caching(self) -> None:
        """Test that alert channel config is cached after first load."""
        manager = CoverageConfigManager.create_default()

        config1 = manager.get_alert_channel_config()
        config2 = manager.get_alert_channel_config()

        # Should be the same object (cached)
        assert config1 is config2

    def test_reload_clears_alert_channel_cache(self) -> None:
        """Test that reload() clears the alert channel config cache."""
        manager = CoverageConfigManager.create_default()

        config1 = manager.get_alert_channel_config()
        manager.reload()
        config2 = manager.get_alert_channel_config()

        # Should be different objects after reload
        assert config1 is not config2

    def test_alert_channel_config_invalid_yaml(self) -> None:
        """Test error handling for invalid alert channel config (wrong field type)."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(
                {
                    "alert_channels": {
                        "routes": [
                            {
                                "channel_name": "slack",
                                "enabled": "not-a-valid-boolean-type-for-pydantic",
                                "alert_types": "should-be-a-list-not-a-string",
                            }
                        ]
                    }
                },
                f,
            )
            f.flush()

            try:
                manager = CoverageConfigManager.create_with_yaml(f.name)

                # Should raise error when trying to get config due to invalid field types
                with pytest.raises(ConfigValidationError):
                    manager.get_alert_channel_config()
            finally:
                Path(f.name).unlink()
