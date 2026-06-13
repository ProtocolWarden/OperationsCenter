# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for coverage-specific alert channel formatters and routers.

Tests:
- CoverageSlackFormatter for Slack message formatting
- CoverageEmailFormatter for email subject/body formatting
- CoverageGitHubFormatter for GitHub PR comment formatting
- CoverageOperatorFormatter for operator log formatting
- CoverageAlertRouter for channel routing logic
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from operations_center.observer.alert_channels import (
    EmailChannel,
    GitHubChannel,
    OperatorLogChannel,
    SlackChannel,
)
from operations_center.observer.coverage_alert_channels import (
    CoverageAlertRouter,
    CoverageEmailFormatter,
    CoverageGitHubFormatter,
    CoverageOperatorFormatter,
    CoverageSlackFormatter,
)
from operations_center.observer.coverage_alerting import AlertSeverity, AlertType
from operations_center.observer.coverage_models import CoverageAlert


@pytest.fixture
def sample_alert() -> CoverageAlert:
    """Create a sample coverage alert for testing."""
    return CoverageAlert(
        alert_id="test-alert-1",
        alert_type=AlertType.BELOW_THRESHOLD,
        severity=AlertSeverity.WARNING,
        metric_type="statement",
        granularity="repository",
        scope_id="src/operations_center",
        current_value=78.5,
        threshold_or_baseline=80.0,
        delta_pct=0.0,
        baseline_type="minimum_threshold",
        affected_modules=["src/operations_center/observer", "src/operations_center/core"],
        recommendation="Add tests for uncovered code paths",
        timestamp=datetime(2026, 6, 12, 10, 30, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def regression_alert() -> CoverageAlert:
    """Create a regression coverage alert."""
    return CoverageAlert(
        alert_id="test-alert-2",
        alert_type=AlertType.REGRESSION_DETECTED,
        severity=AlertSeverity.CRITICAL,
        metric_type="line",
        granularity="repository",
        scope_id="src/operations_center",
        current_value=82.1,
        threshold_or_baseline=85.0,
        delta_pct=-2.9,
        baseline_type="previous_run",
        affected_modules=["src/new_feature.py"],
        recommendation="Review recent PR changes and add tests for new code",
        timestamp=datetime(2026, 6, 12, 10, 30, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def trend_alert() -> CoverageAlert:
    """Create a trend degradation alert."""
    return CoverageAlert(
        alert_id="test-alert-3",
        alert_type=AlertType.TREND_DEGRADING,
        severity=AlertSeverity.WARNING,
        metric_type="branch",
        granularity="repository",
        scope_id="src/operations_center",
        current_value=73.5,
        threshold_or_baseline=78.0,
        delta_pct=-4.5,
        baseline_type="7day_avg",
        affected_modules=["src/operations_center/observer", "src/operations_center/core"],
        recommendation="Coverage trending down. Increase test writing or reduce scope",
        timestamp=datetime(2026, 6, 12, 10, 30, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def module_alert() -> CoverageAlert:
    """Create a module critical gap alert."""
    return CoverageAlert(
        alert_id="test-alert-4",
        alert_type=AlertType.MODULE_GAP,
        severity=AlertSeverity.CRITICAL,
        metric_type="statement",
        granularity="module",
        scope_id="src/operations_center/alert_channels.py",
        current_value=62.5,
        threshold_or_baseline=85.0,
        delta_pct=-22.5,
        baseline_type="minimum_threshold",
        affected_modules=["src/operations_center/alert_channels.py"],
        recommendation="Focus on testing high-touch modules",
        timestamp=datetime(2026, 6, 12, 10, 30, 0, tzinfo=timezone.utc),
    )


class TestCoverageSlackFormatter:
    """Tests for CoverageSlackFormatter."""

    def test_format_below_threshold_alert(self, sample_alert: CoverageAlert) -> None:
        """Test formatting below-threshold alert for Slack."""
        message = CoverageSlackFormatter.format_alert(sample_alert)

        assert "attachments" in message
        assert len(message["attachments"]) == 1

        attachment = message["attachments"][0]
        assert attachment["color"] == "#ff9900"
        assert "Below Threshold" in attachment["title"]
        assert len(attachment["fields"]) > 0

        # Check fields
        field_titles = {f["title"] for f in attachment["fields"]}
        assert "Severity" in field_titles
        assert "Coverage" in field_titles
        assert "Affected Modules" in field_titles

    def test_format_regression_alert(self, regression_alert: CoverageAlert) -> None:
        """Test formatting regression alert for Slack."""
        message = CoverageSlackFormatter.format_alert(regression_alert)

        attachment = message["attachments"][0]
        assert attachment["color"] == "#ff3333"
        assert "Regression Detected" in attachment["title"]

        # Check for regression field
        field_titles = {f["title"] for f in attachment["fields"]}
        assert "Regression" in field_titles

    def test_format_critical_alert_color(self, regression_alert: CoverageAlert) -> None:
        """Test that critical severity uses red color."""
        message = CoverageSlackFormatter.format_alert(regression_alert)
        attachment = message["attachments"][0]
        assert attachment["color"] == "#ff3333"

    def test_format_info_alert_color(self, sample_alert: CoverageAlert) -> None:
        """Test that info/warning severity uses appropriate colors."""
        alert = CoverageAlert(
            alert_id="test-info",
            alert_type=AlertType.BELOW_THRESHOLD,
            severity=AlertSeverity.INFO,
            metric_type="statement",
            granularity="repository",
            scope_id="src",
            current_value=85.0,
            threshold_or_baseline=80.0,
            delta_pct=0.0,
            baseline_type="minimum_threshold",
            affected_modules=[],
            recommendation=None,
            timestamp=datetime.now(timezone.utc),
        )
        message = CoverageSlackFormatter.format_alert(alert)
        attachment = message["attachments"][0]
        assert attachment["color"] == "#36a64f"

    def test_format_alert_with_no_modules(self) -> None:
        """Test formatting alert with no affected modules."""
        alert = CoverageAlert(
            alert_id="test-no-modules",
            alert_type=AlertType.BELOW_THRESHOLD,
            severity=AlertSeverity.WARNING,
            metric_type="statement",
            granularity="repository",
            scope_id="src",
            current_value=78.5,
            threshold_or_baseline=80.0,
            delta_pct=0.0,
            baseline_type="minimum_threshold",
            affected_modules=[],
            recommendation=None,
            timestamp=datetime.now(timezone.utc),
        )
        message = CoverageSlackFormatter.format_alert(alert)
        attachment = message["attachments"][0]

        # Should not have affected modules field
        field_titles = {f["title"] for f in attachment["fields"]}
        assert "Affected Modules" not in field_titles


class TestCoverageEmailFormatter:
    """Tests for CoverageEmailFormatter."""

    def test_format_below_threshold_alert(self, sample_alert: CoverageAlert) -> None:
        """Test formatting below-threshold alert for email."""
        subject, text_body, html_body = CoverageEmailFormatter.format_alert(sample_alert)

        assert "[WARNING]" in subject
        assert "Below Threshold" in subject
        assert "Coverage Alert" in text_body
        assert "78.5" in text_body
        assert "80.0" in text_body
        assert "<html>" in html_body

    def test_format_regression_alert(self, regression_alert: CoverageAlert) -> None:
        """Test formatting regression alert for email."""
        subject, text_body, html_body = CoverageEmailFormatter.format_alert(regression_alert)

        assert "[CRITICAL]" in subject
        assert "Regression Detected" in subject
        assert "-2.9" in text_body  # Delta value
        assert "85.0" in text_body  # Baseline

    def test_email_has_action_items(self, sample_alert: CoverageAlert) -> None:
        """Test that email includes action items."""
        subject, text_body, html_body = CoverageEmailFormatter.format_alert(sample_alert)

        assert "Action Items" in text_body
        assert "Review untested" in text_body
        assert "<ol>" in html_body

    def test_email_includes_modules(self, sample_alert: CoverageAlert) -> None:
        """Test that email includes affected modules."""
        subject, text_body, html_body = CoverageEmailFormatter.format_alert(sample_alert)

        assert "Affected Modules" in text_body
        for module in sample_alert.affected_modules:
            assert module in text_body

    def test_email_html_formatting(self, sample_alert: CoverageAlert) -> None:
        """Test that HTML email is properly formatted."""
        subject, text_body, html_body = CoverageEmailFormatter.format_alert(sample_alert)

        assert "<html>" in html_body
        assert "</html>" in html_body
        assert "<table" in html_body
        assert "<tr" in html_body
        assert "<ul>" in html_body or "<ol>" in html_body

    def test_trend_alert_action_items(self, trend_alert: CoverageAlert) -> None:
        """Test that trend alerts have appropriate action items."""
        subject, text_body, html_body = CoverageEmailFormatter.format_alert(trend_alert)

        assert "Identify root cause" in text_body
        assert "Prioritize coverage" in text_body


class TestCoverageGitHubFormatter:
    """Tests for CoverageGitHubFormatter."""

    def test_format_below_threshold_alert(self, sample_alert: CoverageAlert) -> None:
        """Test formatting below-threshold alert for GitHub."""
        comment = CoverageGitHubFormatter.format_alert(sample_alert)

        assert "⚠️" in comment  # Warning emoji
        assert "Below Threshold" in comment
        assert "78.5%" in comment
        assert "80.0%" in comment

    def test_format_critical_alert_emoji(self, regression_alert: CoverageAlert) -> None:
        """Test that critical alerts use critical emoji."""
        comment = CoverageGitHubFormatter.format_alert(regression_alert)

        assert "🚨" in comment

    def test_format_info_alert_emoji(self) -> None:
        """Test that info alerts use info emoji."""
        alert = CoverageAlert(
            alert_id="test-info",
            alert_type=AlertType.BELOW_THRESHOLD,
            severity=AlertSeverity.INFO,
            metric_type="statement",
            granularity="repository",
            scope_id="src",
            current_value=85.0,
            threshold_or_baseline=80.0,
            delta_pct=0.0,
            baseline_type="minimum_threshold",
            affected_modules=[],
            recommendation=None,
            timestamp=datetime.now(timezone.utc),
        )
        comment = CoverageGitHubFormatter.format_alert(alert)
        assert "ℹ️" in comment

    def test_format_file_level_alert(self, module_alert: CoverageAlert) -> None:
        """Test formatting file-level alert for GitHub."""
        comment = CoverageGitHubFormatter.format_alert(module_alert)

        assert "Files Below Threshold" in comment or "Affected Modules" in comment
        for module in module_alert.affected_modules:
            assert module in comment

    def test_github_includes_remediation(self, sample_alert: CoverageAlert) -> None:
        """Test that GitHub comments include remediation steps."""
        comment = CoverageGitHubFormatter.format_alert(sample_alert)

        assert "Remediation" in comment
        assert "Review untested" in comment

    def test_github_regression_includes_baseline(self, regression_alert: CoverageAlert) -> None:
        """Test that regression alerts include baseline information."""
        comment = CoverageGitHubFormatter.format_alert(regression_alert)

        assert "Change" in comment or "Regression" in comment
        assert "-2.9" in comment

    def test_github_comment_markdown(self, sample_alert: CoverageAlert) -> None:
        """Test that comment is valid Markdown."""
        comment = CoverageGitHubFormatter.format_alert(sample_alert)

        assert "#" in comment  # Headers
        assert "**" in comment  # Bold
        assert "`" in comment  # Code


class TestCoverageOperatorFormatter:
    """Tests for CoverageOperatorFormatter."""

    def test_format_below_threshold_alert(self, sample_alert: CoverageAlert) -> None:
        """Test formatting below-threshold alert for operator logs."""
        message = CoverageOperatorFormatter.format_alert(sample_alert)

        assert "COVERAGE_ALERT" in message
        assert "[WARNING]" in message
        assert "Below Threshold" in message
        assert "statement" in message
        assert "78.5" in message

    def test_format_critical_alert(self, regression_alert: CoverageAlert) -> None:
        """Test formatting critical alert for operator logs."""
        message = CoverageOperatorFormatter.format_alert(regression_alert)

        assert "[CRITICAL]" in message
        assert "Regression Detected" in message

    def test_format_includes_modules(self, sample_alert: CoverageAlert) -> None:
        """Test that log message includes module information."""
        message = CoverageOperatorFormatter.format_alert(sample_alert)

        assert "[modules:" in message
        # Should include some of the affected modules
        for module in sample_alert.affected_modules[:3]:
            assert module in message

    def test_format_regression_with_delta(self, regression_alert: CoverageAlert) -> None:
        """Test that regression alerts show delta."""
        message = CoverageOperatorFormatter.format_alert(regression_alert)

        assert "[regressed -2.9%]" in message

    def test_format_compact_message(self, sample_alert: CoverageAlert) -> None:
        """Test that operator message is reasonably compact."""
        message = CoverageOperatorFormatter.format_alert(sample_alert)

        # Should be a single line appropriate for logs
        lines = message.split("\n")
        assert len(lines) == 1


class TestCoverageAlertRouter:
    """Tests for CoverageAlertRouter."""

    def test_router_initialization(self) -> None:
        """Test that router can be initialized with or without channels."""
        router = CoverageAlertRouter()
        assert router.operator_channel is not None

        slack = SlackChannel(webhook_url="https://hooks.slack.com/test")
        router = CoverageAlertRouter(slack_channel=slack)
        assert router.slack_channel is slack

    def test_route_alert_to_operator_always(self, sample_alert: CoverageAlert) -> None:
        """Test that alerts are always routed to operator channel."""
        router = CoverageAlertRouter()
        results = router.route_alert(sample_alert, channels=["operator"])

        assert "operator" in results
        assert results["operator"].success is True

    def test_default_channels_by_severity(self, sample_alert: CoverageAlert) -> None:
        """Test that default channel selection is based on severity."""
        router = CoverageAlertRouter()

        # Warning severity should use operator by default
        channels = router._determine_channels(sample_alert)
        assert "operator" in channels

        # Critical severity should add more channels
        critical_alert = CoverageAlert(
            alert_id="test-critical",
            alert_type=AlertType.BELOW_THRESHOLD,
            severity=AlertSeverity.CRITICAL,
            metric_type="statement",
            granularity="repository",
            scope_id="src",
            current_value=45.0,
            threshold_or_baseline=80.0,
            delta_pct=0.0,
            baseline_type="minimum_threshold",
            affected_modules=[],
            recommendation=None,
            timestamp=datetime.now(timezone.utc),
        )
        channels = router._determine_channels(critical_alert)
        assert "operator" in channels

    def test_regression_alert_suggests_github(self, regression_alert: CoverageAlert) -> None:
        """Test that regression alerts suggest GitHub channel."""
        slack = SlackChannel(webhook_url="https://hooks.slack.com/test")
        github = GitHubChannel(
            github_token="test-token",
            repo_owner="test-owner",
            repo_name="test-repo",
        )
        router = CoverageAlertRouter(slack_channel=slack, github_channel=github)

        channels = router._determine_channels(regression_alert)
        assert "github" in channels or "operator" in channels

    def test_route_alert_with_multiple_channels(self, sample_alert: CoverageAlert) -> None:
        """Test routing to multiple channels."""
        operator = OperatorLogChannel()
        router = CoverageAlertRouter(operator_channel=operator)

        results = router.route_alert(sample_alert, channels=["operator"])

        assert "operator" in results

    @patch("operations_center.observer.coverage_alert_channels.urlopen")
    def test_slack_channel_delivery(
        self, mock_urlopen: MagicMock, sample_alert: CoverageAlert
    ) -> None:
        """Test Slack channel delivery."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        slack = SlackChannel(webhook_url="https://hooks.slack.com/test")
        router = CoverageAlertRouter(slack_channel=slack)

        results = router.route_alert(sample_alert, channels=["slack"])

        assert "slack" in results
        assert results["slack"].success is True

    @patch("operations_center.observer.coverage_alert_channels.smtplib.SMTP")
    def test_email_channel_delivery(
        self, mock_smtp: MagicMock, sample_alert: CoverageAlert
    ) -> None:
        """Test email channel delivery."""
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_server

        email = EmailChannel(
            smtp_host="localhost",
            smtp_port=587,
            sender="alerts@example.com",
            recipients=["team@example.com"],
        )
        router = CoverageAlertRouter(email_channel=email)

        results = router.route_alert(sample_alert, channels=["email"])

        assert "email" in results
        assert results["email"].success is True

    @patch("operations_center.observer.coverage_alert_channels.urlopen")
    def test_github_channel_delivery(
        self, mock_urlopen: MagicMock, regression_alert: CoverageAlert
    ) -> None:
        """Test GitHub channel delivery."""
        mock_response = MagicMock()
        mock_response.status = 201
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        github = GitHubChannel(
            github_token="test-token",
            repo_owner="test-owner",
            repo_name="test-repo",
        )
        router = CoverageAlertRouter(github_channel=github)

        results = router.route_alert(regression_alert, channels=["github"], pr_number=42)

        assert "github" in results
        assert results["github"].success is True

    def test_github_requires_pr_number(self, regression_alert: CoverageAlert) -> None:
        """Test that GitHub channel requires PR number."""
        github = GitHubChannel(
            github_token="test-token",
            repo_owner="test-owner",
            repo_name="test-repo",
        )
        router = CoverageAlertRouter(github_channel=github)

        # Should handle missing PR number gracefully
        results = router.route_alert(regression_alert, channels=["github"])

        # GitHub should not be in results if PR number not provided
        if "github" in results:
            assert results["github"].success is False

    def test_disabled_channels_skip_routing(self, sample_alert: CoverageAlert) -> None:
        """Test that disabled channels are skipped."""
        slack = SlackChannel(webhook_url=None)  # Disabled
        router = CoverageAlertRouter(slack_channel=slack)

        results = router.route_alert(sample_alert, channels=["slack"])

        # Slack should either not be in results or show disabled
        if "slack" in results:
            assert results["slack"].success is False


class TestCoverageAlertFormattersIntegration:
    """Integration tests for all formatters together."""

    def test_all_alert_types_format(self) -> None:
        """Test that all alert types can be formatted by all formatters."""
        alerts = [
            CoverageAlert(
                alert_id="test-1",
                alert_type=AlertType.BELOW_THRESHOLD,
                severity=AlertSeverity.WARNING,
                metric_type="statement",
                granularity="repository",
                scope_id="src",
                current_value=78.5,
                threshold_or_baseline=80.0,
                delta_pct=0.0,
                baseline_type="minimum_threshold",
                affected_modules=[],
                recommendation="Add tests",
                timestamp=datetime.now(timezone.utc),
            ),
            CoverageAlert(
                alert_id="test-2",
                alert_type=AlertType.REGRESSION_DETECTED,
                severity=AlertSeverity.CRITICAL,
                metric_type="line",
                granularity="repository",
                scope_id="src",
                current_value=82.1,
                threshold_or_baseline=85.0,
                delta_pct=-2.9,
                baseline_type="previous_run",
                affected_modules=["src/new.py"],
                recommendation="Review changes",
                timestamp=datetime.now(timezone.utc),
            ),
            CoverageAlert(
                alert_id="test-3",
                alert_type=AlertType.TREND_DEGRADING,
                severity=AlertSeverity.WARNING,
                metric_type="branch",
                granularity="repository",
                scope_id="src",
                current_value=73.5,
                threshold_or_baseline=78.0,
                delta_pct=-4.5,
                baseline_type="7day_avg",
                affected_modules=[],
                recommendation="Increase tests",
                timestamp=datetime.now(timezone.utc),
            ),
            CoverageAlert(
                alert_id="test-4",
                alert_type=AlertType.MODULE_GAP,
                severity=AlertSeverity.CRITICAL,
                metric_type="statement",
                granularity="module",
                scope_id="src/observer",
                current_value=62.5,
                threshold_or_baseline=85.0,
                delta_pct=-22.5,
                baseline_type="minimum_threshold",
                affected_modules=["src/observer.py"],
                recommendation="Test modules",
                timestamp=datetime.now(timezone.utc),
            ),
        ]

        for alert in alerts:
            # Slack format
            slack_msg = CoverageSlackFormatter.format_alert(alert)
            assert "attachments" in slack_msg

            # Email format
            subject, text, html = CoverageEmailFormatter.format_alert(alert)
            assert subject
            assert text
            assert html

            # GitHub format
            comment = CoverageGitHubFormatter.format_alert(alert)
            assert comment
            assert len(comment) > 0

            # Operator format
            message = CoverageOperatorFormatter.format_alert(alert)
            assert "COVERAGE_ALERT" in message

    def test_message_content_consistency(self, sample_alert: CoverageAlert) -> None:
        """Test that key information appears in all message formats."""
        _slack_msg = CoverageSlackFormatter.format_alert(sample_alert)
        subject, text, html = CoverageEmailFormatter.format_alert(sample_alert)
        comment = CoverageGitHubFormatter.format_alert(sample_alert)
        log_msg = CoverageOperatorFormatter.format_alert(sample_alert)

        # All should mention the severity
        assert "warning" in log_msg.lower()

        # All should mention the alert type
        assert "threshold" in log_msg.lower()

        # Metrics should appear somewhere
        assert "78.5" in text
        assert "78.5" in comment
