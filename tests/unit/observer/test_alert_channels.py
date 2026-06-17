# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for alert notification channels."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from operations_center.observer.alert_channels import (
    AlertChannelFactory,
    AlertChannelResult,
    EmailChannel,
    GitHubChannel,
    OperatorLogChannel,
    PagerDutyChannel,
    PlaneTaskChannel,
    SlackChannel,
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

    def test_notify_success(self, caplog: pytest.LogCaptureFixture) -> None:
        channel = OperatorLogChannel()
        context = {
            "condition_name": "test_alert",
            "error_count": 15,
            "threshold": 10,
            "time_window_minutes": 5,
            "severity": "CRITICAL",
            "collector_name": "TestCollector",
        }

        with caplog.at_level(logging.CRITICAL):
            result = channel.notify(context)

        assert result.success is True
        assert result.channel == "operator_log"
        assert "test_alert" in result.message

    def test_notify_warning_severity(self, caplog: pytest.LogCaptureFixture) -> None:
        channel = OperatorLogChannel()
        context = {
            "condition_name": "test",
            "error_count": 5,
            "threshold": 5,
            "severity": "WARNING",
        }

        with caplog.at_level(logging.WARNING):
            result = channel.notify(context)

        assert result.success is True

    def test_notify_info_severity(self, caplog: pytest.LogCaptureFixture) -> None:
        channel = OperatorLogChannel()
        context = {
            "condition_name": "test",
            "severity": "INFO",
        }

        with caplog.at_level(logging.INFO):
            result = channel.notify(context)

        assert result.success is True

    def test_validate_configuration(self) -> None:
        channel = OperatorLogChannel()
        assert channel.validate_configuration() is True


class TestPlaneTaskChannel:
    """Test PlaneTaskChannel."""

    def test_notify_not_configured(self) -> None:
        channel = PlaneTaskChannel()
        context = {"condition_name": "test"}

        result = channel.notify(context)

        assert result.success is False
        assert "not configured" in result.error

    def test_notify_with_client(self) -> None:
        mock_client = object()  # Mock plane client
        channel = PlaneTaskChannel(plane_client=mock_client)
        context = {
            "condition_name": "test",
            "error_count": 10,
            "threshold": 10,
            "collector_name": "TestCollector",
            "severity": "CRITICAL",
        }

        result = channel.notify(context)

        assert result.success is True
        assert "Would create" in result.message

    def test_build_task_description(self) -> None:
        context = {
            "condition_name": "parse_error_spike",
            "severity": "CRITICAL",
            "collector_name": "ExecutionArtifactCollector",
            "error_count": 15,
            "threshold": 10,
            "artifact_type": "control_outcome.json",
            "sample_errors": ["Error 1", "Error 2"],
            "time_window_minutes": 5,
        }

        description = PlaneTaskChannel._build_task_description(context)

        assert "parse_error_spike" in description
        assert "CRITICAL" in description
        assert "ExecutionArtifactCollector" in description
        assert "15 / 10" in description

    def test_severity_to_priority(self) -> None:
        assert PlaneTaskChannel._severity_to_priority("EMERGENCY") == "urgent"
        assert PlaneTaskChannel._severity_to_priority("CRITICAL") == "high"
        assert PlaneTaskChannel._severity_to_priority("WARNING") == "medium"
        assert PlaneTaskChannel._severity_to_priority("INFO") == "low"
        assert PlaneTaskChannel._severity_to_priority("UNKNOWN") == "medium"

    def test_build_labels(self) -> None:
        context = {
            "condition_name": "parse_error_spike",
            "severity": "CRITICAL",
        }

        labels = PlaneTaskChannel._build_labels(context)

        assert "validation" in labels
        assert "alert" in labels
        assert "parse-error-spike" in labels
        assert "severity-critical" in labels

    def test_validate_configuration_not_enabled(self) -> None:
        channel = PlaneTaskChannel()
        assert channel.validate_configuration() is False

    def test_validate_configuration_enabled(self) -> None:
        mock_client = object()
        channel = PlaneTaskChannel(plane_client=mock_client)
        assert channel.validate_configuration() is True


class TestSlackChannel:
    """Test SlackChannel."""

    def test_notify_not_configured(self) -> None:
        channel = SlackChannel()
        context = {"condition_name": "test"}

        result = channel.notify(context)

        assert result.success is False
        assert "not configured" in result.error

    def test_notify_with_webhook(self) -> None:
        with patch("operations_center.observer.alert_channels.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=None)
            mock_urlopen.return_value = mock_response

            channel = SlackChannel(webhook_url="https://hooks.slack.com/test")
            context = {"condition_name": "test"}

            result = channel.notify(context)

            assert result.success is True
            assert "sent" in result.message

    def test_validate_configuration_not_enabled(self) -> None:
        channel = SlackChannel()
        assert channel.validate_configuration() is False

    def test_validate_configuration_enabled(self) -> None:
        channel = SlackChannel(webhook_url="https://hooks.slack.com/test")
        assert channel.validate_configuration() is True


class TestPagerDutyChannel:
    """Test PagerDutyChannel."""

    def test_notify_not_configured(self) -> None:
        channel = PagerDutyChannel()
        context = {"condition_name": "test"}

        result = channel.notify(context)

        assert result.success is False
        assert "not configured" in result.error

    def test_notify_with_api_key(self) -> None:
        channel = PagerDutyChannel(api_key="test_key_123")
        context = {"condition_name": "test"}

        result = channel.notify(context)

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

    def test_create_email_channel(self) -> None:
        channel = AlertChannelFactory.create_channel("email")
        assert isinstance(channel, EmailChannel)

    def test_create_email_channel_with_config(self) -> None:
        config = {
            "smtp_host": "smtp.example.com",
            "sender": "alerts@example.com",
            "recipients": ["ops@example.com"],
        }
        channel = AlertChannelFactory.create_channel("email", config)
        assert isinstance(channel, EmailChannel)
        assert channel.validate_configuration() is True

    def test_create_github_channel(self) -> None:
        channel = AlertChannelFactory.create_channel("github")
        assert isinstance(channel, GitHubChannel)

    def test_create_github_channel_with_config(self) -> None:
        config = {
            "github_token": "ghp_test123",
            "repo_owner": "TestOwner",
            "repo_name": "TestRepo",
        }
        channel = AlertChannelFactory.create_channel("github", config)
        assert isinstance(channel, GitHubChannel)
        assert channel.validate_configuration() is True


class TestOperatorLogChannelEdgeCases:
    """Test OperatorLogChannel exception handling."""

    def test_notify_exception_path(self) -> None:
        channel = OperatorLogChannel()
        channel.logger = MagicMock()
        channel.logger.critical.side_effect = RuntimeError("logging failed")

        context = {"condition_name": "test", "severity": "CRITICAL"}
        result = channel.notify(context)

        assert result.success is False
        assert "Failed to log alert" in result.error


class TestPlaneTaskChannelEdgeCases:
    """Test PlaneTaskChannel exception handling."""

    def test_notify_exception_path(self) -> None:
        mock_client = object()
        channel = PlaneTaskChannel(plane_client=mock_client)

        with patch(
            "operations_center.observer.alert_channels.logger",
        ) as mock_logger:
            mock_logger.info.side_effect = RuntimeError("logging failed")
            context = {"condition_name": "test", "severity": "CRITICAL"}
            result = channel.notify(context)

        assert result.success is False
        assert "Failed to create Plane task" in result.error


class TestSlackChannelEdgeCases:
    """Test SlackChannel error and exception paths."""

    def test_notify_non_200_response(self) -> None:
        with patch("operations_center.observer.alert_channels.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 400
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=None)
            mock_urlopen.return_value = mock_response

            channel = SlackChannel(webhook_url="https://hooks.slack.com/test")
            result = channel.notify({"condition_name": "test"})

            assert result.success is False
            assert "400" in result.error

    def test_notify_exception(self) -> None:
        with patch(
            "operations_center.observer.alert_channels.urlopen",
            side_effect=OSError("connection refused"),
        ):
            channel = SlackChannel(webhook_url="https://hooks.slack.com/test")
            result = channel.notify({"condition_name": "test"})

            assert result.success is False
            assert "Failed to send Slack notification" in result.error

    def test_build_slack_message_with_problematic_tests(self) -> None:
        context = {
            "condition_name": "high_flakiness",
            "severity": "CRITICAL",
            "flaky_test_count": 3,
            "failure_rate": 0.25,
            "most_problematic_tests": [
                {"name": "test_foo", "failure_rate": 0.5},
                {"name": "test_bar", "failure_rate": 0.3},
            ],
        }
        msg = SlackChannel._build_slack_message(context)
        assert "attachments" in msg
        fields = msg["attachments"][0]["fields"]
        field_names = [f["title"] for f in fields]
        assert "Top Problematic Tests" in field_names


class TestEmailChannel:
    """Test EmailChannel notification channel."""

    def test_notify_not_configured(self) -> None:
        channel = EmailChannel()
        result = channel.notify({"condition_name": "test"})

        assert result.success is False
        assert "not properly configured" in result.error

    def test_notify_not_configured_missing_recipients(self) -> None:
        channel = EmailChannel(smtp_host="smtp.example.com", sender="from@example.com")
        result = channel.notify({"condition_name": "test"})

        assert result.success is False

    def test_validate_configuration_not_enabled(self) -> None:
        channel = EmailChannel()
        assert channel.validate_configuration() is False

    def test_validate_configuration_enabled(self) -> None:
        channel = EmailChannel(
            smtp_host="smtp.example.com",
            sender="from@example.com",
            recipients=["to@example.com"],
        )
        assert channel.validate_configuration() is True

    def test_notify_smtp_success(self) -> None:
        channel = EmailChannel(
            smtp_host="smtp.example.com",
            smtp_port=587,
            sender="from@example.com",
            recipients=["to@example.com"],
        )
        context = {"condition_name": "test_alert", "severity": "CRITICAL"}

        with patch("operations_center.observer.alert_channels.smtplib.SMTP") as mock_smtp_cls:
            mock_server = MagicMock()
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=None)

            result = channel.notify(context)

        assert result.success is True
        assert "1 recipient" in result.message

    def test_notify_smtp_with_auth(self) -> None:
        channel = EmailChannel(
            smtp_host="smtp.example.com",
            sender="from@example.com",
            recipients=["to@example.com"],
            username="user",
            password="pass",
        )
        context = {"condition_name": "test"}

        with patch("operations_center.observer.alert_channels.smtplib.SMTP") as mock_smtp_cls:
            mock_server = MagicMock()
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=None)

            result = channel.notify(context)

        assert result.success is True
        mock_server.login.assert_called_once_with("user", "pass")

    def test_notify_smtp_exception(self) -> None:
        channel = EmailChannel(
            smtp_host="smtp.example.com",
            sender="from@example.com",
            recipients=["to@example.com"],
        )
        context = {"condition_name": "test"}

        with patch(
            "operations_center.observer.alert_channels.smtplib.SMTP",
            side_effect=OSError("connection failed"),
        ):
            result = channel.notify(context)

        assert result.success is False
        assert "Failed to send email" in result.error

    def test_build_email_message_basic(self) -> None:
        context = {
            "condition_name": "flaky_tests",
            "severity": "CRITICAL",
            "flaky_test_count": 5,
            "failure_rate": 0.3,
        }
        subject, text_body, html_body = EmailChannel._build_email_message(context)

        assert "[CRITICAL]" in subject
        assert "flaky_tests" in subject
        assert "flaky_tests" in text_body
        assert "CRITICAL" in text_body
        assert "<html>" in html_body
        assert "CRITICAL" in html_body

    def test_build_email_message_with_problematic_tests(self) -> None:
        context = {
            "condition_name": "test_alert",
            "severity": "WARNING",
            "flaky_test_count": 2,
            "failure_rate": 0.15,
            "most_problematic_tests": [
                {"name": "test_foo", "failure_rate": 0.4},
                {"name": "test_bar", "failure_rate": 0.3},
            ],
        }
        subject, text_body, html_body = EmailChannel._build_email_message(context)

        assert "test_foo" in text_body
        assert "test_foo" in html_body
        assert "test_bar" in text_body


class TestGitHubChannel:
    """Test GitHubChannel notification channel."""

    def test_notify_not_configured(self) -> None:
        channel = GitHubChannel()
        result = channel.notify({"condition_name": "test"})

        assert result.success is False
        assert "not properly configured" in result.error

    def test_notify_no_pr_number(self) -> None:
        channel = GitHubChannel(
            github_token="token123",
            repo_owner="owner",
            repo_name="repo",
        )
        result = channel.notify({"condition_name": "test"})

        assert result.success is False
        assert "PR number not provided" in result.error

    def test_validate_configuration_not_enabled(self) -> None:
        channel = GitHubChannel()
        assert channel.validate_configuration() is False

    def test_validate_configuration_enabled(self) -> None:
        channel = GitHubChannel(
            github_token="token",
            repo_owner="owner",
            repo_name="repo",
        )
        assert channel.validate_configuration() is True

    def test_notify_success_201(self) -> None:
        channel = GitHubChannel(
            github_token="token123",
            repo_owner="TestOwner",
            repo_name="TestRepo",
        )
        context = {"condition_name": "flaky_alert", "pr_number": 42}

        with patch("operations_center.observer.alert_channels.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 201
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=None)
            mock_urlopen.return_value = mock_response

            result = channel.notify(context)

        assert result.success is True
        assert "PR #42" in result.message

    def test_notify_success_200(self) -> None:
        channel = GitHubChannel(
            github_token="token123",
            repo_owner="TestOwner",
            repo_name="TestRepo",
        )
        context = {"condition_name": "flaky_alert", "pr_number": 99}

        with patch("operations_center.observer.alert_channels.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=None)
            mock_urlopen.return_value = mock_response

            result = channel.notify(context)

        assert result.success is True

    def test_notify_non_200_response(self) -> None:
        channel = GitHubChannel(
            github_token="token123",
            repo_owner="owner",
            repo_name="repo",
        )
        context = {"condition_name": "test", "pr_number": 1}

        with patch("operations_center.observer.alert_channels.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 403
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=None)
            mock_urlopen.return_value = mock_response

            result = channel.notify(context)

        assert result.success is False
        assert "403" in result.error

    def test_notify_exception(self) -> None:
        channel = GitHubChannel(
            github_token="token123",
            repo_owner="owner",
            repo_name="repo",
        )
        context = {"condition_name": "test", "pr_number": 1}

        with patch(
            "operations_center.observer.alert_channels.urlopen",
            side_effect=OSError("network error"),
        ):
            result = channel.notify(context)

        assert result.success is False
        assert "Failed to post GitHub comment" in result.error

    def test_build_github_comment_basic(self) -> None:
        context = {
            "condition_name": "flaky_tests",
            "severity": "CRITICAL",
            "flaky_test_count": 3,
            "failure_rate": 0.25,
        }
        comment = GitHubChannel._build_github_comment(context)

        assert "flaky_tests" in comment
        assert "CRITICAL" in comment
        assert "Remediation Steps" in comment

    def test_build_github_comment_with_tests(self) -> None:
        context = {
            "condition_name": "test_alert",
            "severity": "CRITICAL",
            "flaky_test_count": 5,
            "failure_rate": 0.5,
            "most_problematic_tests": [
                {"name": "test_critical", "failure_rate": 0.8},
            ],
        }
        comment = GitHubChannel._build_github_comment(context)

        assert "test_critical" in comment
        assert "CRITICAL" in comment

    def test_build_github_comment_severity_emoji(self) -> None:
        for severity in ["INFO", "WARNING", "CRITICAL", "EMERGENCY"]:
            context = {
                "condition_name": "test",
                "severity": severity,
                "flaky_test_count": 1,
                "failure_rate": 0.1,
            }
            comment = GitHubChannel._build_github_comment(context)
            assert severity in comment
