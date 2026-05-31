# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for alert notification channels."""
import pytest
import logging

from operations_center.observer.alert_channels import (
    AlertChannel,
    AlertChannelFactory,
    AlertChannelResult,
    OperatorLogChannel,
    PlaneTaskChannel,
    SlackChannel,
    PagerDutyChannel,
)


class TestAlertChannelResult:
    """Test AlertChannelResult dataclass."""

    def test_success_result(self) -> None:
        result = AlertChannelResult(
            channel="test_channel",
            success=True,
            message="Test message",
        )
        assert result.channel == "test_channel"
        assert result.success is True
        assert result.message == "Test message"
        assert result.error is None

    def test_failure_result(self) -> None:
        result = AlertChannelResult(
            channel="test_channel",
            success=False,
            error="Test error",
        )
        assert result.success is False
        assert result.error == "Test error"


class TestOperatorLogChannel:
    """Test OperatorLogChannel."""

    @pytest.mark.asyncio
    async def test_notify_success(self, caplog: pytest.LogCaptureFixture) -> None:
        channel = OperatorLogChannel()
        context = {
            "condition_name": "test_alert",
            "error_count": 15,
            "threshold": 10,
            "time_window_minutes": 5,
            "severity": "HIGH",
            "collector_name": "TestCollector",
        }

        with caplog.at_level(logging.CRITICAL):
            result = await channel.notify(context)

        assert result.success is True
        assert result.channel == "operator_log"
        assert "test_alert" in result.message

    @pytest.mark.asyncio
    async def test_notify_medium_severity(self, caplog: pytest.LogCaptureFixture) -> None:
        channel = OperatorLogChannel()
        context = {
            "condition_name": "test",
            "error_count": 5,
            "threshold": 5,
            "severity": "MEDIUM",
        }

        with caplog.at_level(logging.WARNING):
            result = await channel.notify(context)

        assert result.success is True

    @pytest.mark.asyncio
    async def test_notify_info_severity(self, caplog: pytest.LogCaptureFixture) -> None:
        channel = OperatorLogChannel()
        context = {
            "condition_name": "test",
            "severity": "LOW",
        }

        with caplog.at_level(logging.INFO):
            result = await channel.notify(context)

        assert result.success is True

    def test_validate_configuration(self) -> None:
        channel = OperatorLogChannel()
        assert channel.validate_configuration() is True


class TestPlaneTaskChannel:
    """Test PlaneTaskChannel."""

    @pytest.mark.asyncio
    async def test_notify_not_configured(self) -> None:
        channel = PlaneTaskChannel()
        context = {"condition_name": "test"}

        result = await channel.notify(context)

        assert result.success is False
        assert "not configured" in result.error

    @pytest.mark.asyncio
    async def test_notify_with_client(self) -> None:
        mock_client = object()  # Mock plane client
        channel = PlaneTaskChannel(plane_client=mock_client)
        context = {
            "condition_name": "test",
            "error_count": 10,
            "threshold": 10,
            "collector_name": "TestCollector",
            "severity": "HIGH",
        }

        result = await channel.notify(context)

        assert result.success is True
        assert "Would create" in result.message

    def test_build_task_description(self) -> None:
        context = {
            "condition_name": "parse_error_spike",
            "severity": "HIGH",
            "collector_name": "ExecutionArtifactCollector",
            "error_count": 15,
            "threshold": 10,
            "artifact_type": "control_outcome.json",
            "sample_errors": ["Error 1", "Error 2"],
            "time_window_minutes": 5,
        }

        description = PlaneTaskChannel._build_task_description(context)

        assert "parse_error_spike" in description
        assert "HIGH" in description
        assert "ExecutionArtifactCollector" in description
        assert "15 / 10" in description

    def test_severity_to_priority(self) -> None:
        assert PlaneTaskChannel._severity_to_priority("HIGH") == "urgent"
        assert PlaneTaskChannel._severity_to_priority("MEDIUM") == "high"
        assert PlaneTaskChannel._severity_to_priority("LOW") == "medium"
        assert PlaneTaskChannel._severity_to_priority("UNKNOWN") == "medium"

    def test_build_labels(self) -> None:
        context = {
            "condition_name": "parse_error_spike",
            "severity": "HIGH",
        }

        labels = PlaneTaskChannel._build_labels(context)

        assert "validation" in labels
        assert "alert" in labels
        assert "parse-error-spike" in labels
        assert "severity-high" in labels

    def test_validate_configuration_not_enabled(self) -> None:
        channel = PlaneTaskChannel()
        assert channel.validate_configuration() is False

    def test_validate_configuration_enabled(self) -> None:
        mock_client = object()
        channel = PlaneTaskChannel(plane_client=mock_client)
        assert channel.validate_configuration() is True


class TestSlackChannel:
    """Test SlackChannel."""

    @pytest.mark.asyncio
    async def test_notify_not_configured(self) -> None:
        channel = SlackChannel()
        context = {"condition_name": "test"}

        result = await channel.notify(context)

        assert result.success is False
        assert "not configured" in result.error

    @pytest.mark.asyncio
    async def test_notify_with_webhook(self) -> None:
        channel = SlackChannel(webhook_url="https://hooks.slack.com/test")
        context = {"condition_name": "test"}

        result = await channel.notify(context)

        assert result.success is True
        assert "would be sent" in result.message

    def test_validate_configuration_not_enabled(self) -> None:
        channel = SlackChannel()
        assert channel.validate_configuration() is False

    def test_validate_configuration_enabled(self) -> None:
        channel = SlackChannel(webhook_url="https://hooks.slack.com/test")
        assert channel.validate_configuration() is True


class TestPagerDutyChannel:
    """Test PagerDutyChannel."""

    @pytest.mark.asyncio
    async def test_notify_not_configured(self) -> None:
        channel = PagerDutyChannel()
        context = {"condition_name": "test"}

        result = await channel.notify(context)

        assert result.success is False
        assert "not configured" in result.error

    @pytest.mark.asyncio
    async def test_notify_with_api_key(self) -> None:
        channel = PagerDutyChannel(api_key="test_key_123")
        context = {"condition_name": "test"}

        result = await channel.notify(context)

        assert result.success is True
        assert "would be created" in result.message

    def test_validate_configuration_not_enabled(self) -> None:
        channel = PagerDutyChannel()
        assert channel.validate_configuration() is False

    def test_validate_configuration_enabled(self) -> None:
        channel = PagerDutyChannel(api_key="test_key")
        assert channel.validate_configuration() is True


class TestAlertChannelFactory:
    """Test AlertChannelFactory."""

    def test_create_operator_log_channel(self) -> None:
        channel = AlertChannelFactory.create_channel("operator_log")
        assert isinstance(channel, OperatorLogChannel)

    def test_create_plane_channel(self) -> None:
        channel = AlertChannelFactory.create_channel("plane")
        assert isinstance(channel, PlaneTaskChannel)

    def test_create_slack_channel(self) -> None:
        channel = AlertChannelFactory.create_channel("slack")
        assert isinstance(channel, SlackChannel)

    def test_create_pagerduty_channel(self) -> None:
        channel = AlertChannelFactory.create_channel("pagerduty")
        assert isinstance(channel, PagerDutyChannel)

    def test_create_unknown_channel(self) -> None:
        channel = AlertChannelFactory.create_channel("unknown_channel")
        assert channel is None

    def test_create_channel_with_config(self) -> None:
        config = {"webhook_url": "https://hooks.slack.com/test"}
        channel = AlertChannelFactory.create_channel("slack", config)
        assert isinstance(channel, SlackChannel)
        assert channel.validate_configuration() is True

    def test_create_channels_from_config(self) -> None:
        channel_names = ["operator_log", "plane", "slack"]
        config = {
            "slack": {"webhook_url": "https://hooks.slack.com/test"},
        }

        channels = AlertChannelFactory.create_channels_from_config(
            channel_names,
            config,
        )

        assert len(channels) == 3
        assert isinstance(channels["operator_log"], OperatorLogChannel)
        assert isinstance(channels["plane"], PlaneTaskChannel)
        assert isinstance(channels["slack"], SlackChannel)

    def test_create_channels_skip_missing(self) -> None:
        channel_names = ["operator_log", "unknown_channel"]
        channels = AlertChannelFactory.create_channels_from_config(channel_names)

        assert len(channels) == 1
        assert "operator_log" in channels
        assert "unknown_channel" not in channels

    def test_create_all_channels(self) -> None:
        channel_names = ["operator_log", "plane", "slack", "pagerduty"]
        channels = AlertChannelFactory.create_channels_from_config(channel_names)

        assert len(channels) == 4
        for name in channel_names:
            assert name in channels
