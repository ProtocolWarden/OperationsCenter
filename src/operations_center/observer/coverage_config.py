# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Coverage threshold configuration system for loading and managing configuration from multiple sources.

Supports YAML files, environment variables, and defaults with composition and precedence.
Includes alert routing configuration for specifying which channels receive which alert types.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from operations_center.observer.coverage_alerting import (
    AlertSeverity,
    AlertType,
    CoverageAlertConfig,
)


class ConfigValidationError(ValueError):
    """Raised when configuration validation fails."""

    pass


class AlertChannelRoute(BaseModel):
    """Route configuration for a specific alert channel."""

    channel_name: str = Field(
        description="Name of the alert channel (e.g., 'slack', 'email', 'github')"
    )
    enabled: bool = Field(default=True, description="Whether this route is enabled")
    alert_types: list[str] = Field(
        default_factory=list,
        description="Alert types this channel receives (empty = all types)",
    )
    severity_levels: list[str] = Field(
        default_factory=list,
        description="Severity levels this channel receives (empty = all levels)",
    )
    enabled_modules: list[str] = Field(
        default_factory=list,
        description="Modules this channel alerts for (empty = all modules)",
    )

    def matches_alert(
        self,
        alert_type: AlertType,
        severity: AlertSeverity,
        module: str | None = None,
    ) -> bool:
        """Check if this route should receive the given alert.

        Args:
            alert_type: Type of the alert
            severity: Severity level of the alert
            module: Module the alert is for (optional)

        Returns:
            True if this route should receive the alert
        """
        if not self.enabled:
            return False

        # Check alert type (empty list = all types)
        if self.alert_types and alert_type.value not in self.alert_types:
            return False

        # Check severity (empty list = all levels)
        if self.severity_levels and severity.value not in self.severity_levels:
            return False

        # Check module (empty list = all modules; if modules required but none provided, no match)
        if self.enabled_modules and (not module or module not in self.enabled_modules):
            return False

        return True


class AlertChannelConfig(BaseModel):
    """Configuration for coverage-specific alert routing."""

    routes: list[AlertChannelRoute] = Field(
        default_factory=list,
        description="List of alert channel routes for coverage alerts",
    )
    default_channels: list[str] = Field(
        default_factory=lambda: ["operator"],
        description="Default channels to use if no specific routes match",
    )

    def get_routes_for_alert(
        self,
        alert_type: AlertType,
        severity: AlertSeverity,
        module: str | None = None,
    ) -> list[str]:
        """Get channels that should receive the given alert.

        Args:
            alert_type: Type of the alert
            severity: Severity level of the alert
            module: Module the alert is for (optional)

        Returns:
            List of channel names that should receive this alert
        """
        matching_channels = [
            route.channel_name
            for route in self.routes
            if route.matches_alert(alert_type, severity, module)
        ]

        # Fall back to default channels if no matches
        if not matching_channels:
            return self.default_channels

        return matching_channels


class CoverageConfigSchema(BaseModel):
    """Schema for coverage configuration validation."""

    # Repository-level thresholds
    repo_minimum_threshold: float | None = None
    repo_warning_threshold: float | None = None
    repo_target_threshold: float | None = None

    # Coverage type specific thresholds
    statement_coverage_minimum: float | None = None
    branch_coverage_minimum: float | None = None
    line_coverage_minimum: float | None = None

    # Regression thresholds
    regression_threshold_pct: float | None = None
    regression_7day_threshold_pct: float | None = None
    regression_30day_threshold_pct: float | None = None

    # Trend detection
    trend_degradation_days: int | None = None
    trend_degradation_velocity_pct: float | None = None

    # Severity mapping
    severity_critical_threshold: float | None = None
    severity_high_threshold: float | None = None
    severity_medium_threshold: float | None = None

    # Module-level thresholds
    module_thresholds: dict[str, dict[str, float]] | None = Field(
        default=None, description="Per-module threshold overrides"
    )

    # Alert routing configuration
    alert_channels: dict[str, Any] | None = Field(
        default=None, description="Alert channel routing configuration"
    )

    @field_validator("*", mode="before")
    @classmethod
    def skip_none_values(cls, value: Any) -> Any:
        """Skip None values to allow partial configs."""
        return value

    @field_validator(
        "repo_minimum_threshold",
        "repo_warning_threshold",
        "repo_target_threshold",
        "statement_coverage_minimum",
        "branch_coverage_minimum",
        "line_coverage_minimum",
        "regression_threshold_pct",
        "regression_7day_threshold_pct",
        "regression_30day_threshold_pct",
        "severity_critical_threshold",
        "severity_high_threshold",
        "severity_medium_threshold",
    )
    @classmethod
    def validate_percentage(cls, value: float | None) -> float | None:
        """Validate that percentage thresholds are in valid range (0-100)."""
        if value is not None and (value < 0 or value > 100):
            msg = f"Threshold must be between 0 and 100, got {value}"
            raise ValueError(msg)
        return value

    @field_validator("trend_degradation_days")
    @classmethod
    def validate_days(cls, value: int | None) -> int | None:
        """Validate that days is positive."""
        if value is not None and value <= 0:
            msg = f"Days must be positive, got {value}"
            raise ValueError(msg)
        return value


class CoverageConfigProvider(ABC):
    """Abstract base class for configuration providers."""

    @abstractmethod
    def load(self) -> dict[str, Any]:
        """Load configuration from source.

        Returns:
            Dictionary of configuration values
        """

    def validate(self, config: dict[str, Any]) -> CoverageConfigSchema:
        """Validate configuration against schema.

        Args:
            config: Configuration dictionary

        Returns:
            Validated CoverageConfigSchema

        Raises:
            ConfigValidationError: If validation fails
        """
        try:
            return CoverageConfigSchema(**config)
        except ValidationError as e:
            raise ConfigValidationError(f"Configuration validation failed: {e}") from e


class DefaultConfigProvider(CoverageConfigProvider):
    """Provider that returns default configuration values."""

    def load(self) -> dict[str, Any]:
        """Load default configuration.

        Returns:
            Default configuration dictionary
        """
        return {
            "repo_minimum_threshold": 80.0,
            "repo_warning_threshold": 85.0,
            "repo_target_threshold": 90.0,
            "statement_coverage_minimum": 75.0,
            "branch_coverage_minimum": 65.0,
            "line_coverage_minimum": 75.0,
            "regression_threshold_pct": 2.0,
            "regression_7day_threshold_pct": 3.0,
            "regression_30day_threshold_pct": 5.0,
            "trend_degradation_days": 5,
            "trend_degradation_velocity_pct": 1.0,
            "severity_critical_threshold": 50.0,
            "severity_high_threshold": 70.0,
            "severity_medium_threshold": 80.0,
            "module_thresholds": {},
            "alert_channels": {
                "routes": [
                    {
                        "channel_name": "operator",
                        "enabled": True,
                        "alert_types": [],
                        "severity_levels": [],
                        "enabled_modules": [],
                    }
                ],
                "default_channels": ["operator"],
            },
        }


class YamlConfigProvider(CoverageConfigProvider):
    """Provider that loads configuration from YAML files."""

    def __init__(self, path: str | Path):
        """Initialize provider with file path.

        Args:
            path: Path to YAML configuration file
        """
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        """Load configuration from YAML file.

        Returns:
            Configuration dictionary from YAML

        Raises:
            ConfigValidationError: If file not found or invalid YAML
        """
        if not self.path.exists():
            raise ConfigValidationError(f"Configuration file not found: {self.path}")

        try:
            with open(self.path) as f:
                data = yaml.safe_load(f) or {}
                # Filter out None values
                return {k: v for k, v in data.items() if v is not None}
        except yaml.YAMLError as e:
            raise ConfigValidationError(f"Invalid YAML in {self.path}: {e}") from e
        except OSError as e:
            raise ConfigValidationError(f"Cannot read {self.path}: {e}") from e


class EnvironmentConfigProvider(CoverageConfigProvider):
    """Provider that loads configuration from environment variables.

    Environment variables use the prefix COVERAGE_ and follow the pattern:
    - COVERAGE_REPO_MINIMUM_THRESHOLD -> repo_minimum_threshold
    - COVERAGE_MODULE_THRESHOLDS_<MODULE> -> module_thresholds.<module>
    """

    PREFIX = "COVERAGE_"

    def load(self) -> dict[str, Any]:
        """Load configuration from environment variables.

        Returns:
            Configuration dictionary from environment
        """
        config: dict[str, Any] = {}

        for key, value in os.environ.items():
            if not key.startswith(self.PREFIX):
                continue

            # Remove prefix and convert to lowercase
            config_key = key[len(self.PREFIX) :].lower()

            # Skip empty values
            if not value:
                continue

            # Try to parse as number/boolean
            parsed_value: Any = value
            if value.lower() in ("true", "false"):
                parsed_value = value.lower() == "true"
            elif value.isdigit():
                parsed_value = int(value)
            else:
                try:
                    parsed_value = float(value)
                except ValueError:
                    parsed_value = value

            config[config_key] = parsed_value

        return config


class CompositeConfigProvider(CoverageConfigProvider):
    """Provider that combines multiple providers with precedence ordering."""

    def __init__(self, providers: list[CoverageConfigProvider]):
        """Initialize with ordered list of providers.

        Providers are applied in order, with later providers overriding earlier ones.

        Args:
            providers: List of CoverageConfigProvider instances
        """
        self.providers = providers

    def load(self) -> dict[str, Any]:
        """Load configuration by combining all providers.

        Returns:
            Merged configuration dictionary
        """
        config: dict[str, Any] = {}

        for provider in self.providers:
            provider_config = provider.load()
            # Merge dicts, with special handling for nested dicts
            for key, value in provider_config.items():
                if key == "module_thresholds" and isinstance(value, dict):
                    if "module_thresholds" not in config:
                        config["module_thresholds"] = {}
                    config["module_thresholds"].update(value)
                else:
                    config[key] = value

        return config


class CoverageConfigManager:
    """Manager for loading, validating, and applying coverage configuration."""

    def __init__(self, providers: CoverageConfigProvider | list[CoverageConfigProvider]):
        """Initialize manager with configuration provider(s).

        Args:
            providers: Single provider or list of providers
        """
        if isinstance(providers, CoverageConfigProvider):
            self.provider = providers
        elif isinstance(providers, list):
            self.provider = CompositeConfigProvider(providers)
        else:
            msg = f"Invalid provider type: {type(providers)}"
            raise TypeError(msg)

        self._config: dict[str, Any] | None = None
        self._alert_config: CoverageAlertConfig | None = None
        self._alert_channel_config: AlertChannelConfig | None = None

    @classmethod
    def create_default(cls) -> CoverageConfigManager:
        """Create manager with default configuration only.

        Returns:
            CoverageConfigManager instance
        """
        return cls(DefaultConfigProvider())

    @classmethod
    def create_with_yaml(cls, config_path: str | Path) -> CoverageConfigManager:
        """Create manager with YAML file and defaults (YAML takes precedence).

        Args:
            config_path: Path to YAML configuration file

        Returns:
            CoverageConfigManager instance
        """
        return cls(
            [
                DefaultConfigProvider(),
                YamlConfigProvider(config_path),
                EnvironmentConfigProvider(),
            ]
        )

    @classmethod
    def create_auto_discovery(
        cls, search_paths: list[str | Path] | None = None
    ) -> CoverageConfigManager:
        """Create manager with auto-discovery of configuration files.

        Searches for .console/coverage-config.yaml in standard locations.

        Args:
            search_paths: List of paths to search for config file

        Returns:
            CoverageConfigManager instance
        """
        if search_paths is None:
            search_paths = [
                Path.cwd() / ".console" / "coverage-config.yaml",
                Path.home() / ".operations_center" / "coverage-config.yaml",
            ]

        providers: list[CoverageConfigProvider] = [DefaultConfigProvider()]

        for search_path in search_paths:
            if isinstance(search_path, str):
                search_path = Path(search_path)
            if search_path.exists():
                providers.append(YamlConfigProvider(search_path))
                break

        providers.append(EnvironmentConfigProvider())

        return cls(providers)

    def load_config(self) -> dict[str, Any]:
        """Load and validate configuration.

        Returns:
            Validated configuration dictionary

        Raises:
            ConfigValidationError: If validation fails
        """
        if self._config is None:
            raw_config = self.provider.load()
            # Validate before caching
            self.provider.validate(raw_config)
            self._config = raw_config

        return self._config

    def get_alert_config(self) -> CoverageAlertConfig:
        """Get CoverageAlertConfig instance from loaded configuration.

        Returns:
            CoverageAlertConfig instance

        Raises:
            ConfigValidationError: If configuration is invalid
        """
        if self._alert_config is None:
            config = self.load_config()
            # Create CoverageAlertConfig with loaded values
            # Only pass values that are in the config and not None
            alert_config_dict = {k: v for k, v in config.items() if v is not None and k != "config"}
            self._alert_config = CoverageAlertConfig(**alert_config_dict)

        return self._alert_config

    def get_alert_channel_config(self) -> AlertChannelConfig:
        """Get AlertChannelConfig instance from loaded configuration.

        Returns:
            AlertChannelConfig instance with routing configuration

        Raises:
            ConfigValidationError: If configuration is invalid
        """
        if self._alert_channel_config is None:
            config = self.load_config()
            alert_channels_config = config.get("alert_channels", {})

            if not alert_channels_config:
                # Use default empty config
                self._alert_channel_config = AlertChannelConfig()
            else:
                try:
                    # Build AlertChannelRoute objects from config
                    routes = []
                    for route_config in alert_channels_config.get("routes", []):
                        routes.append(AlertChannelRoute(**route_config))

                    self._alert_channel_config = AlertChannelConfig(
                        routes=routes,
                        default_channels=alert_channels_config.get(
                            "default_channels", ["operator"]
                        ),
                    )
                except ValidationError as e:
                    raise ConfigValidationError(f"Invalid alert channel configuration: {e}") from e

        return self._alert_channel_config

    def reload(self) -> None:
        """Clear cached configuration to force reload on next access."""
        self._config = None
        self._alert_config = None
        self._alert_channel_config = None


__all__ = [
    "ConfigValidationError",
    "CoverageConfigSchema",
    "CoverageConfigProvider",
    "DefaultConfigProvider",
    "YamlConfigProvider",
    "EnvironmentConfigProvider",
    "CompositeConfigProvider",
    "CoverageConfigManager",
    "AlertChannelRoute",
    "AlertChannelConfig",
]
