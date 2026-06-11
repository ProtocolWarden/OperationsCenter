# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Alert notification channels for validation failure alerting.

Defines:
- AlertChannel base class and protocol
- OperatorLogChannel — log alerts to operator logs
- PlaneTaskChannel — create Plane improve tasks
- SlackChannel — full Slack webhook integration
- EmailChannel — SMTP email notifications
- GitHubChannel — GitHub PR comments
- PagerDutyChannel stub — for future PagerDuty integration
- AlertChannelFactory — instantiate channels from configuration
"""

from __future__ import annotations

import json
import logging
import smtplib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Optional
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


@dataclass
class AlertChannelResult:
    """Result of alert notification attempt."""

    channel: str
    success: bool
    message: str = ""
    error: Optional[str] = None


class AlertChannel(ABC):
    """Base class for alert notification channels."""

    @abstractmethod
    def notify(self, context: dict[str, Any]) -> AlertChannelResult:
        """Send alert notification through this channel.

        Args:
            context: Alert context with condition, metrics, severity, etc.

        Returns:
            AlertChannelResult with success status and message
        """
        ...

    @abstractmethod
    def validate_configuration(self) -> bool:
        """Validate that the channel is properly configured.

        Returns:
            True if channel is ready to use, False otherwise
        """
        ...


class OperatorLogChannel(AlertChannel):
    """Send alerts to operator logs."""

    def __init__(self, logger_name: str = "operations_center.alerts") -> None:
        self.logger = logging.getLogger(logger_name)
        self.name = "operator_log"

    def notify(self, context: dict[str, Any]) -> AlertChannelResult:
        """Log alert to operator logger.

        Args:
            context: Alert context

        Returns:
            AlertChannelResult with success status
        """
        try:
            severity = context.get("severity", "MEDIUM")
            condition = context.get("condition_name", "unknown")
            error_count = context.get("error_count", 0)
            threshold = context.get("threshold", 0)
            collector = context.get("collector_name", "system")

            # Build alert message
            message = (
                f"ALERT [{condition}] — {error_count}/{threshold} errors "
                f"in {context.get('time_window_minutes', 5)}min "
                f"(collector={collector}, severity={severity})"
            )

            # Log at appropriate level
            if severity == "HIGH":
                self.logger.critical(message, extra={"alert_context": context})
            elif severity == "MEDIUM":
                self.logger.warning(message, extra={"alert_context": context})
            else:
                self.logger.info(message, extra={"alert_context": context})

            return AlertChannelResult(
                channel=self.name,
                success=True,
                message=f"Logged {condition} to {self.logger.name}",
            )
        except Exception as e:
            return AlertChannelResult(
                channel=self.name,
                success=False,
                error=f"Failed to log alert: {str(e)}",
            )

    def validate_configuration(self) -> bool:
        """Operator log channel is always available."""
        return self.logger is not None


class PlaneTaskChannel(AlertChannel):
    """Create Plane improve tasks for alerts.

    NOTE: This is a stub implementation for Stage 2.
    Full implementation requires PlaneClient integration in Stage 3+.
    """

    def __init__(self, plane_client: Any = None) -> None:
        self.plane_client = plane_client
        self.name = "plane"
        self.enabled = plane_client is not None

    def notify(self, context: dict[str, Any]) -> AlertChannelResult:
        """Create Plane improve task for alert.

        Args:
            context: Alert context

        Returns:
            AlertChannelResult with success status
        """
        if not self.enabled:
            return AlertChannelResult(
                channel=self.name,
                success=False,
                error="PlaneClient not configured",
            )

        try:
            condition = context.get("condition_name", "unknown")
            error_count = context.get("error_count", 0)
            threshold = context.get("threshold", 0)

            # Stub: full Plane integration planned for Stage 3+.
            # Will call plane_client.create_issue() with condition/description/labels.
            logger.info(
                "Plane task creation stub: %s (%s/%s errors)",
                condition,
                error_count,
                threshold,
                extra={"alert_context": context},
            )

            return AlertChannelResult(
                channel=self.name,
                success=True,
                message=f"Would create Plane task for {condition}",
            )
        except Exception as e:
            return AlertChannelResult(
                channel=self.name,
                success=False,
                error=f"Failed to create Plane task: {str(e)}",
            )

    def validate_configuration(self) -> bool:
        """Check if Plane client is configured."""
        return self.enabled

    @staticmethod
    def _build_task_description(context: dict[str, Any]) -> str:
        """Build task description from alert context."""
        lines = [
            f"**Alert Condition**: {context.get('condition_name')}",
            f"**Severity**: {context.get('severity')}",
            f"**Collector**: {context.get('collector_name', 'N/A')}",
            f"**Error Count**: {context.get('error_count')} / {context.get('threshold')}",
            f"**Time Window**: {context.get('time_window_minutes')}m",
        ]

        if context.get("artifact_type"):
            lines.append(f"**Artifact Type**: {context.get('artifact_type')}")

        if context.get("sample_errors"):
            lines.append("\n**Sample Errors**:\n")
            for error in context.get("sample_errors", [])[:3]:
                lines.append(f"- {error}")

        return "\n".join(lines)

    @staticmethod
    def _severity_to_priority(severity: str) -> str:
        """Map severity to Plane priority."""
        severity_map = {
            "HIGH": "urgent",
            "MEDIUM": "high",
            "LOW": "medium",
        }
        return severity_map.get(severity, "medium")

    @staticmethod
    def _build_labels(context: dict[str, Any]) -> list[str]:
        """Build labels for Plane task from alert context."""
        labels = ["validation", "alert"]
        if context.get("condition_name"):
            labels.append((context.get("condition_name") or "").replace("_", "-"))
        if context.get("severity"):
            labels.append(f"severity-{(context.get('severity') or '').lower()}")
        return labels


class SlackChannel(AlertChannel):
    """Send alerts to Slack via webhook integration."""

    def __init__(self, webhook_url: str | None = None) -> None:
        self.webhook_url = webhook_url
        self.name = "slack"
        self.enabled = webhook_url is not None

    def notify(self, context: dict[str, Any]) -> AlertChannelResult:
        """Send alert to Slack via webhook.

        Args:
            context: Alert context with alert type, severity, test details

        Returns:
            AlertChannelResult with success status
        """
        if not self.enabled:
            return AlertChannelResult(
                channel=self.name,
                success=False,
                error="Slack webhook not configured",
            )

        try:
            message = self._build_slack_message(context)
            request = Request(
                self.webhook_url,
                data=json.dumps(message).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=10) as response:
                if response.status != 200:
                    return AlertChannelResult(
                        channel=self.name,
                        success=False,
                        error=f"Slack webhook returned {response.status}",
                    )

            return AlertChannelResult(
                channel=self.name,
                success=True,
                message=f"Slack notification sent for {context.get('condition_name')}",
            )
        except Exception as e:
            logger.exception("Failed to send Slack notification")
            return AlertChannelResult(
                channel=self.name,
                success=False,
                error=f"Failed to send Slack notification: {str(e)}",
            )

    def validate_configuration(self) -> bool:
        """Check if Slack is properly configured."""
        return self.enabled

    @staticmethod
    def _build_slack_message(context: dict[str, Any]) -> dict[str, Any]:
        """Build Slack message from alert context.

        Args:
            context: Alert context

        Returns:
            Dictionary formatted for Slack webhook
        """
        alert_type = context.get("condition_name", "Unknown Alert")
        severity = context.get("severity", "MEDIUM")
        test_count = context.get("flaky_test_count", 0)
        failure_rate = context.get("failure_rate", 0.0)

        color_map = {
            "LOW": "#36a64f",
            "MEDIUM": "#ff9900",
            "HIGH": "#ff3333",
            "CRITICAL": "#8b0000",
        }
        color = color_map.get(severity, "#cccccc")

        fields = [
            {"title": "Severity", "value": severity, "short": True},
            {"title": "Alert Type", "value": alert_type, "short": True},
            {"title": "Flaky Tests Count", "value": str(test_count), "short": True},
            {"title": "Failure Rate", "value": f"{failure_rate:.1%}", "short": True},
        ]

        if context.get("most_problematic_tests"):
            test_list = ", ".join(
                test["name"] for test in context.get("most_problematic_tests", [])[:3]
            )
            fields.append(
                {"title": "Top Problematic Tests", "value": test_list, "short": False}
            )

        return {
            "attachments": [
                {
                    "fallback": f"{alert_type} - {severity}",
                    "color": color,
                    "title": f"Flaky Test Alert: {alert_type}",
                    "fields": fields,
                    "footer": "Flaky Test Reporter",
                    "ts": int(context.get("timestamp", 0)),
                }
            ]
        }


class EmailChannel(AlertChannel):
    """Send alerts via email using SMTP."""

    def __init__(
        self,
        smtp_host: str | None = None,
        smtp_port: int = 587,
        sender: str | None = None,
        recipients: list[str] | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.sender = sender
        self.recipients = recipients or []
        self.username = username
        self.password = password
        self.name = "email"
        self.enabled = bool(smtp_host and sender and recipients)

    def notify(self, context: dict[str, Any]) -> AlertChannelResult:
        """Send alert email.

        Args:
            context: Alert context

        Returns:
            AlertChannelResult with success status
        """
        if not self.enabled:
            return AlertChannelResult(
                channel=self.name,
                success=False,
                error="Email not properly configured (missing host, sender, or recipients)",
            )

        try:
            subject, text_body, html_body = self._build_email_message(context)

            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.sender
            msg["To"] = ", ".join(self.recipients)

            msg.attach(MIMEText(text_body, "plain"))
            msg.attach(MIMEText(html_body, "html"))

            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as server:
                server.starttls()
                if self.username and self.password:
                    server.login(self.username, self.password)
                server.sendmail(self.sender, self.recipients, msg.as_string())

            return AlertChannelResult(
                channel=self.name,
                success=True,
                message=f"Email sent to {len(self.recipients)} recipient(s)",
            )
        except Exception as e:
            logger.exception("Failed to send email alert")
            return AlertChannelResult(
                channel=self.name,
                success=False,
                error=f"Failed to send email: {str(e)}",
            )

    def validate_configuration(self) -> bool:
        """Check if email is properly configured."""
        return self.enabled

    @staticmethod
    def _build_email_message(context: dict[str, Any]) -> tuple[str, str, str]:
        """Build email subject and body from alert context.

        Args:
            context: Alert context

        Returns:
            Tuple of (subject, text_body, html_body)
        """
        alert_type = context.get("condition_name", "Flaky Test Alert")
        severity = context.get("severity", "MEDIUM")
        test_count = context.get("flaky_test_count", 0)
        failure_rate = context.get("failure_rate", 0.0)

        subject = f"[{severity}] {alert_type} - Flaky Test Alert"

        text_body = f"""
Flaky Test Alert
================

Condition: {alert_type}
Severity: {severity}
Flaky Tests: {test_count}
Failure Rate: {failure_rate:.1%}

Top Problematic Tests:
"""
        if context.get("most_problematic_tests"):
            for i, test in enumerate(context.get("most_problematic_tests", [])[:5], 1):
                text_body += (
                    f"\n{i}. {test.get('name', 'Unknown')} "
                    f"(failure rate: {test.get('failure_rate', 0):.1%})"
                )

        text_body += "\n\nRemediation:\n"
        text_body += "1. Review the test logs and reproduction steps\n"
        text_body += "2. Identify the root cause (timing, environment, resource)\n"
        text_body += "3. Implement a fix or add proper synchronization\n"
        text_body += "4. Verify the fix with multiple test runs\n"

        html_body = f"""
<html>
<body style="font-family: Arial, sans-serif;">
<h2>Flaky Test Alert</h2>
<p><strong>Condition:</strong> {alert_type}</p>
<p><strong>Severity:</strong> <span style="color: red; font-weight: bold;">{severity}</span></p>
<p><strong>Flaky Tests:</strong> {test_count}</p>
<p><strong>Failure Rate:</strong> {failure_rate:.1%}</p>

<h3>Top Problematic Tests:</h3>
<ul>
"""
        if context.get("most_problematic_tests"):
            for test in context.get("most_problematic_tests", [])[:5]:
                html_body += (
                    f"<li>{test.get('name', 'Unknown')} "
                    f"(failure rate: {test.get('failure_rate', 0):.1%})</li>"
                )

        html_body += """
</ul>

<h3>Remediation Steps:</h3>
<ol>
<li>Review the test logs and reproduction steps</li>
<li>Identify the root cause (timing, environment, resource)</li>
<li>Implement a fix or add proper synchronization</li>
<li>Verify the fix with multiple test runs</li>
</ol>
</body>
</html>
"""

        return subject, text_body, html_body


class GitHubChannel(AlertChannel):
    """Post alert comments on GitHub pull requests."""

    def __init__(
        self,
        github_token: str | None = None,
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> None:
        self.github_token = github_token
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.name = "github"
        self.enabled = bool(github_token and repo_owner and repo_name)
        self.api_base = "https://api.github.com"

    def notify(self, context: dict[str, Any]) -> AlertChannelResult:
        """Post alert comment on GitHub PR.

        Args:
            context: Alert context with pr_number

        Returns:
            AlertChannelResult with success status
        """
        if not self.enabled:
            return AlertChannelResult(
                channel=self.name,
                success=False,
                error="GitHub not properly configured (missing token, owner, or repo)",
            )

        pr_number = context.get("pr_number")
        if not pr_number:
            return AlertChannelResult(
                channel=self.name,
                success=False,
                error="PR number not provided in alert context",
            )

        try:
            comment_body = self._build_github_comment(context)
            endpoint = f"/repos/{self.repo_owner}/{self.repo_name}/issues/{pr_number}/comments"
            url = f"{self.api_base}{endpoint}"

            headers = {
                "Authorization": f"token {self.github_token}",
                "Accept": "application/vnd.github.v3+json",
                "Content-Type": "application/json",
            }

            data = json.dumps({"body": comment_body}).encode()
            request = Request(url, data=data, headers=headers, method="POST")

            with urlopen(request, timeout=10) as response:
                if response.status not in (200, 201):
                    return AlertChannelResult(
                        channel=self.name,
                        success=False,
                        error=f"GitHub API returned {response.status}",
                    )

            return AlertChannelResult(
                channel=self.name,
                success=True,
                message=f"Posted comment on PR #{pr_number}",
            )
        except Exception as e:
            logger.exception("Failed to post GitHub comment")
            return AlertChannelResult(
                channel=self.name,
                success=False,
                error=f"Failed to post GitHub comment: {str(e)}",
            )

    def validate_configuration(self) -> bool:
        """Check if GitHub is properly configured."""
        return self.enabled

    @staticmethod
    def _build_github_comment(context: dict[str, Any]) -> str:
        """Build GitHub PR comment from alert context.

        Args:
            context: Alert context

        Returns:
            Markdown-formatted comment body
        """
        alert_type = context.get("condition_name", "Flaky Test Alert")
        severity = context.get("severity", "MEDIUM")
        test_count = context.get("flaky_test_count", 0)
        failure_rate = context.get("failure_rate", 0.0)

        severity_emoji = {
            "LOW": "⚠️",
            "MEDIUM": "⚠️⚠️",
            "HIGH": "🚨",
            "CRITICAL": "🚨🚨",
        }
        emoji = severity_emoji.get(severity, "⚠️")

        comment = f"""
{emoji} **Flaky Test Alert: {alert_type}**

**Severity:** {severity}
**Affected Tests:** {test_count}
**Failure Rate:** {failure_rate:.1%}

### Top Problematic Tests
"""

        if context.get("most_problematic_tests"):
            for i, test in enumerate(context.get("most_problematic_tests", [])[:5], 1):
                comment += (
                    f"\n{i}. `{test.get('name', 'Unknown')}` "
                    f"({test.get('failure_rate', 0):.1%} failure rate)"
                )

        comment += """

### Remediation Steps
1. **Review logs** — Check failure logs to understand the failure mode
2. **Identify root cause** — Determine if timing, environment, or resource-related
3. **Implement fix** — Add synchronization, timeout adjustments, or resource cleanup
4. **Verify** — Run test multiple times to confirm stability

---
*Posted by Flaky Test Reporter*
"""

        return comment


class PagerDutyChannel(AlertChannel):
    """Page on-call engineer via PagerDuty.

    NOTE: This is a stub implementation for Stage 2.
    Full implementation planned for Stage 3+.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key
        self.name = "pagerduty"
        self.enabled = api_key is not None

    def notify(self, context: dict[str, Any]) -> AlertChannelResult:
        """Page on-call engineer (stub).

        Args:
            context: Alert context

        Returns:
            AlertChannelResult indicating stub status
        """
        if not self.enabled:
            return AlertChannelResult(
                channel=self.name,
                success=False,
                error="PagerDuty API key not configured",
            )

        # Stub implementation — would call PagerDuty API
        logger.debug("PagerDuty notification stub: %s", context.get("condition_name"))
        return AlertChannelResult(
            channel=self.name,
            success=True,
            message="PagerDuty incident would be created (stub)",
        )

    def validate_configuration(self) -> bool:
        """Check if PagerDuty is properly configured."""
        return self.enabled


class AlertChannelFactory:
    """Factory for instantiating alert notification channels."""

    @staticmethod
    def create_channel(
        channel_name: str,
        config: dict[str, Any] | None = None,
    ) -> AlertChannel | None:
        """Create a notification channel by name.

        Args:
            channel_name: Channel name. Valid: "operator_log", "plane", "slack",
                "email", "github", "pagerduty"
            config: Channel configuration dictionary

        Returns:
            AlertChannel instance or None if channel not found
        """
        config = config or {}

        if channel_name == "operator_log":
            return OperatorLogChannel(
                logger_name=config.get("logger_name") or "operations_center.alerts"
            )
        elif channel_name == "plane":
            return PlaneTaskChannel(plane_client=config.get("plane_client"))
        elif channel_name == "slack":
            return SlackChannel(webhook_url=config.get("webhook_url"))
        elif channel_name == "email":
            return EmailChannel(
                smtp_host=config.get("smtp_host"),
                smtp_port=config.get("smtp_port", 587),
                sender=config.get("sender"),
                recipients=config.get("recipients"),
                username=config.get("username"),
                password=config.get("password"),
            )
        elif channel_name == "github":
            return GitHubChannel(
                github_token=config.get("github_token"),
                repo_owner=config.get("repo_owner"),
                repo_name=config.get("repo_name"),
            )
        elif channel_name == "pagerduty":
            return PagerDutyChannel(api_key=config.get("api_key"))
        else:
            logger.warning("Unknown alert channel: %s", channel_name)
            return None

    @staticmethod
    def create_channels_from_config(
        channel_names: list[str],
        config: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, AlertChannel]:
        """Create multiple channels from configuration.

        Args:
            channel_names: List of channel names to create
            config: Configuration dict mapping channel name to channel config

        Returns:
            Dictionary mapping channel names to AlertChannel instances
        """
        config = config or {}
        channels = {}

        for name in channel_names:
            channel = AlertChannelFactory.create_channel(
                name,
                config.get(name, {}),
            )
            if channel:
                channels[name] = channel

        return channels
