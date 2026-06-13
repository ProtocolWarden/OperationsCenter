# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Coverage-specific alert channel formatters and routers.

Provides:
- CoverageSlackFormatter — Format coverage alerts for Slack
- CoverageEmailFormatter — Format coverage alerts for email
- CoverageGitHubFormatter — Format coverage alerts for GitHub PR comments
- CoverageOperatorFormatter — Format coverage alerts for operator logs
- CoverageAlertRouter — Route coverage alerts to appropriate channels
"""

from __future__ import annotations

import smtplib
from typing import Any, Literal
from urllib.request import Request, urlopen

from operations_center.observer.alert_channels import (
    AlertChannelResult,
    EmailChannel,
    GitHubChannel,
    OperatorLogChannel,
    SlackChannel,
)
from operations_center.observer.coverage_alerting import AlertSeverity, AlertType
from operations_center.observer.coverage_models import CoverageAlert


def get_severity_color(severity: str) -> str:
    """Get hex color code for alert severity level.

    Args:
        severity: Severity level string

    Returns:
        Hex color code
    """
    color_map: dict[str, str] = {
        "info": "#36a64f",
        "warning": "#ff9900",
        "critical": "#ff3333",
        "emergency": "#8b0000",
    }
    return color_map.get(severity, "#cccccc")


def format_metric_display(metric_type: str, granularity: str) -> str:
    """Format metric type and granularity for display.

    Args:
        metric_type: Type of metric
        granularity: Granularity level

    Returns:
        Formatted display string
    """
    display_str: str = f"{metric_type.capitalize()} ({granularity})"
    return display_str


def create_alert_summary(alert: CoverageAlert) -> dict[str, Any]:
    """Create a summary dictionary of an alert for quick access to key fields.

    Args:
        alert: Alert to summarize

    Returns:
        Dictionary with key alert fields
    """
    summary: dict[str, Any] = {
        "id": alert.alert_id,
        "type": alert.alert_type,
        "severity": alert.severity,
        "metric": alert.metric_type,
        "scope": alert.scope_id,
        "value": alert.current_value,
        "threshold": alert.threshold_or_baseline,
        "delta": alert.delta_pct,
    }
    return summary


def should_notify_immediately(alert: CoverageAlert) -> bool:
    """Determine if an alert warrants immediate notification.

    Args:
        alert: Alert to evaluate

    Returns:
        True if alert should be notified immediately
    """
    immediate_severity: bool = alert.severity in ("critical", "emergency")
    return immediate_severity


def get_alert_action_items(alert: CoverageAlert) -> list[str]:
    """Get recommended action items for an alert.

    Args:
        alert: Alert to get actions for

    Returns:
        List of recommended actions
    """
    actions: list[str] = []

    if alert.alert_type == "below_threshold":
        actions = [
            "Review untested code paths",
            "Add tests for critical functionality",
            "Validate coverage measurement tools",
        ]
    elif alert.alert_type == "regression_detected":
        actions = [
            "Review recent code changes",
            "Add tests for new code",
            "Investigate coverage decrease root cause",
        ]
    elif alert.alert_type == "trend_degrading":
        actions = [
            "Analyze why coverage is declining",
            "Prioritize testing of new features",
            "Set team coverage goals",
        ]
    elif alert.alert_type == "module_gap":
        actions = [
            "Focus on critical modules first",
            "Add tests for frequently changed files",
            "Track module-level metrics",
        ]

    return actions


def calculate_alert_notification_delay(severity: str) -> int:
    """Calculate appropriate notification delay based on severity.

    Args:
        severity: Alert severity level

    Returns:
        Notification delay in seconds
    """
    delays: dict[str, int] = {
        "emergency": 0,
        "critical": 60,
        "warning": 300,
        "info": 900,
    }
    return delays.get(severity, 600)


class CoverageSlackFormatter:
    """Format coverage alerts for Slack delivery."""

    @staticmethod
    def format_alert(alert: CoverageAlert) -> dict[str, Any]:
        """Format coverage alert as Slack message.

        Args:
            alert: CoverageAlert instance

        Returns:
            Dictionary formatted for Slack webhook
        """
        color_map: dict[str, str] = {
            AlertSeverity.INFO.value: "#36a64f",
            AlertSeverity.WARNING.value: "#ff9900",
            AlertSeverity.CRITICAL.value: "#ff3333",
            AlertSeverity.EMERGENCY.value: "#8b0000",
        }
        color: str = color_map.get(alert.severity, "#cccccc")

        fields: list[dict[str, Any]] = [
            {"title": "Alert Type", "value": alert.alert_type, "short": True},
            {"title": "Severity", "value": alert.severity.upper(), "short": True},
            {"title": "Metric", "value": alert.metric_type, "short": True},
            {"title": "Granularity", "value": alert.granularity, "short": True},
        ]

        if alert.current_value is not None and alert.threshold_or_baseline is not None:
            cov_val = alert.current_value
            thresh_val = alert.threshold_or_baseline
            fields.append(
                {
                    "title": "Coverage",
                    "value": f"{cov_val:.1f}% (threshold: {thresh_val:.1f}%)",
                    "short": False,
                }
            )

        if alert.delta_pct is not None and alert.alert_type == AlertType.REGRESSION_DETECTED.value:
            delta = alert.delta_pct
            baseline = alert.threshold_or_baseline or 0
            fields.append(
                {
                    "title": "Regression",
                    "value": f"{delta:+.1f}% from baseline {baseline:.1f}%",
                    "short": False,
                }
            )

        if alert.affected_modules:
            modules_str = ", ".join(sorted(alert.affected_modules)[:3])
            if len(alert.affected_modules) > 3:
                modules_str += f" (+{len(alert.affected_modules) - 3} more)"
            fields.append({"title": "Affected Modules", "value": modules_str, "short": False})

        if alert.recommendation:
            fields.append(
                {"title": "Recommendation", "value": alert.recommendation, "short": False}
            )

        return {
            "attachments": [
                {
                    "fallback": f"Coverage Alert: {alert.alert_type}",
                    "color": color,
                    "title": f"📊 Coverage Alert: {alert.alert_type.replace('_', ' ').title()}",
                    "fields": fields,
                    "footer": "Coverage Threshold Alerter",
                    "ts": int(alert.timestamp.timestamp()) if alert.timestamp else 0,
                }
            ]
        }


class CoverageEmailFormatter:
    """Format coverage alerts for email delivery."""

    @staticmethod
    def format_alert(alert: CoverageAlert) -> tuple[str, str, str]:
        """Format coverage alert as email message.

        Args:
            alert: CoverageAlert instance

        Returns:
            Tuple of (subject, text_body, html_body)
        """
        alert_type_readable: str = alert.alert_type.replace("_", " ").title()
        subject: str = f"[{alert.severity.upper()}] Coverage Alert: {alert_type_readable}"

        threshold_part = (
            f"(threshold: {alert.threshold_or_baseline:.1f}%)"
            if alert.threshold_or_baseline
            else ""
        )
        text_body: str = f"""
Coverage Alert Notification
============================

Alert Type: {alert_type_readable}
Severity: {alert.severity.upper()}
Metric Type: {alert.metric_type}
Granularity: {alert.granularity}
Scope: {alert.scope_id}

Current Measurement: {alert.current_value:.1f}% {threshold_part}
"""

        if alert.alert_type == AlertType.REGRESSION_DETECTED.value and alert.delta_pct is not None:
            delta = alert.delta_pct
            baseline = alert.threshold_or_baseline or 0
            text_body += f"\nRegression: {delta:+.1f}% from baseline {baseline:.1f}%\n"

        if alert.affected_modules:
            text_body += "\nAffected Modules:\n"
            for module in sorted(alert.affected_modules)[:10]:
                text_body += f"  - {module}\n"
            if len(alert.affected_modules) > 10:
                text_body += f"  ... and {len(alert.affected_modules) - 10} more\n"

        text_body += "\nRecommendation:\n"
        if alert.recommendation:
            text_body += f"{alert.recommendation}\n"
        else:
            text_body += "Review coverage metrics and adjust testing strategy accordingly.\n"

        text_body += "\nAction Items:\n"
        if alert.alert_type == AlertType.BELOW_THRESHOLD.value:
            text_body += "1. Review untested code paths\n"
            text_body += "2. Add tests for critical paths\n"
            text_body += "3. Validate test coverage tools\n"
        elif alert.alert_type == AlertType.REGRESSION_DETECTED.value:
            text_body += "1. Review recent code changes\n"
            text_body += "2. Add tests for new code\n"
            text_body += "3. Block PR merge if below threshold\n"
        elif alert.alert_type == AlertType.TREND_DEGRADING.value:
            text_body += "1. Identify root cause of degradation\n"
            text_body += "2. Prioritize coverage improvements\n"
            text_body += "3. Establish coverage goals\n"
        elif alert.alert_type == AlertType.CRITICAL_MODULE_COVERAGE.value:
            text_body += "1. Focus on high-touch modules\n"
            text_body += "2. Add tests for frequently changed files\n"
            text_body += "3. Track module-level coverage metrics\n"

        td_style = 'style="padding: 8px; border: 1px solid #ddd;"'
        severity_span = (
            f'<span style="color: red; font-weight: bold;">{alert.severity.upper()}</span>'
        )
        html_body: str = f"""
<html>
<body style="font-family: Arial, sans-serif; color: #333;">
<h2>📊 Coverage Alert Notification</h2>

<table style="border-collapse: collapse; margin: 20px 0;">
<tr style="background-color: #f5f5f5;">
    <td {td_style}><strong>Alert Type</strong></td>
    <td {td_style}>{alert_type_readable}</td>
</tr>
<tr>
    <td {td_style}><strong>Severity</strong></td>
    <td {td_style}>{severity_span}</td>
</tr>
<tr style="background-color: #f5f5f5;">
    <td {td_style}><strong>Metric Type</strong></td>
    <td {td_style}>{alert.metric_type}</td>
</tr>
<tr>
    <td {td_style}><strong>Current Measurement</strong></td>
    <td {td_style}>{alert.current_value:.1f}%</td>
</tr>
"""

        if alert.threshold_or_baseline is not None:
            html_body += f"""<tr style="background-color: #f5f5f5;">
    <td {td_style}><strong>Threshold</strong></td>
    <td {td_style}>{alert.threshold_or_baseline:.1f}%</td>
</tr>
"""

        if alert.affected_modules:
            td_style_vt = (
                'style="padding: 8px; border: 1px solid #ddd; vertical-align: top;"'
            )
            html_body += f"""<tr>
    <td {td_style_vt}><strong>Affected Modules</strong></td>
    <td {td_style}>
        <ul style="margin: 0; padding-left: 20px;">
"""
            for module in sorted(alert.affected_modules)[:10]:
                html_body += f"            <li>{module}</li>\n"
            if len(alert.affected_modules) > 10:
                html_body += (
                    f"            <li>... and {len(alert.affected_modules) - 10} more</li>\n"
                )
            html_body += """        </ul>
    </td>
</tr>
"""

        html_body += f"""</table>

<h3>Recommendation</h3>
<p>{alert.recommendation or "Review coverage metrics and adjust testing strategy accordingly."}</p>

<h3>Action Items</h3>
<ol>
"""

        if alert.alert_type == AlertType.BELOW_THRESHOLD.value:
            html_body += """
    <li>Review untested code paths</li>
    <li>Add tests for critical paths</li>
    <li>Validate test coverage tools</li>
"""
        elif alert.alert_type == AlertType.REGRESSION_DETECTED.value:
            html_body += """
    <li>Review recent code changes</li>
    <li>Add tests for new code</li>
    <li>Block PR merge if below threshold</li>
"""
        elif alert.alert_type == AlertType.TREND_DEGRADING.value:
            html_body += """
    <li>Identify root cause of degradation</li>
    <li>Prioritize coverage improvements</li>
    <li>Establish coverage goals</li>
"""
        elif alert.alert_type == AlertType.CRITICAL_MODULE_COVERAGE.value:
            html_body += """
    <li>Focus on high-touch modules</li>
    <li>Add tests for frequently changed files</li>
    <li>Track module-level coverage metrics</li>
"""

        html_body += """
</ol>

</body>
</html>
"""

        return subject, text_body, html_body


class CoverageGitHubFormatter:
    """Format coverage alerts for GitHub PR comments."""

    @staticmethod
    def format_alert(alert: CoverageAlert, pr_number: int | None = None) -> str:
        """Format coverage alert as GitHub PR comment.

        Args:
            alert: CoverageAlert instance
            pr_number: Optional PR number for context

        Returns:
            Markdown-formatted comment body
        """
        alert_type_readable: str = alert.alert_type.replace("_", " ").title()

        severity_emoji: dict[str, str] = {
            AlertSeverity.INFO.value: "ℹ️",
            AlertSeverity.WARNING.value: "⚠️",
            AlertSeverity.CRITICAL.value: "🚨",
            AlertSeverity.EMERGENCY.value: "🚨🚨",
        }
        emoji: str = severity_emoji.get(alert.severity, "⚠️")

        comment: str = f"""
{emoji} **Coverage Alert: {alert_type_readable}**

**Severity:** `{alert.severity.upper()}`
**Metric:** `{alert.metric_type}` ({alert.granularity})
**Measurement:** {alert.current_value:.1f}%
"""

        if alert.threshold_or_baseline:
            comment += f"**Threshold:** {alert.threshold_or_baseline:.1f}%\n"

        if alert.alert_type == AlertType.REGRESSION_DETECTED.value and alert.delta_pct is not None:
            delta = alert.delta_pct
            baseline = alert.threshold_or_baseline or 0
            comment += f"**Change:** {delta:+.1f}% from baseline {baseline:.1f}%\n"

        # Module-specific section for file-level alerts
        if alert.granularity == "file" and alert.affected_modules:
            comment += "\n### Files Below Threshold\n\n"
            for i, module in enumerate(sorted(alert.affected_modules)[:10], 1):
                comment += f"{i}. `{module}`\n"
            if len(alert.affected_modules) > 10:
                comment += f"\n... and {len(alert.affected_modules) - 10} more files\n"

        elif alert.affected_modules:
            comment += "\n### Affected Modules\n\n"
            for i, module in enumerate(sorted(alert.affected_modules)[:10], 1):
                comment += f"{i}. `{module}`\n"
            if len(alert.affected_modules) > 10:
                comment += f"\n... and {len(alert.affected_modules) - 10} more modules\n"

        comment += "\n### Remediation\n\n"

        if alert.alert_type == AlertType.BELOW_THRESHOLD.value:
            comment += """- **Review untested code** — Check what's not covered by tests
- **Add test cases** — Focus on critical paths first
- **Validate tools** — Ensure coverage measurement is accurate
"""
        elif alert.alert_type == AlertType.REGRESSION_DETECTED.value:
            comment += """- **Review PR changes** — Check what new code was added
- **Add tests** — Test all new code paths
- **Check baseline** — Ensure comparison baseline is correct
"""
        elif alert.alert_type == AlertType.TREND_DEGRADING.value:
            comment += """- **Analyze trend** — Determine why coverage is declining
- **Add tests** — Increase test coverage for new code
- **Set goals** — Establish team coverage targets
"""
        elif alert.alert_type == AlertType.CRITICAL_MODULE_COVERAGE.value:
            comment += """- **Focus on modules** — Prioritize listed files for testing
- **Add tests** — Test high-touch modules thoroughly
- **Track progress** — Monitor module-level metrics
"""

        if alert.recommendation:
            comment += f"\n### Notes\n\n{alert.recommendation}\n"

        comment += "\n---\n*Posted by Coverage Threshold Alerter*\n"

        return comment


class CoverageOperatorFormatter:
    """Format coverage alerts for operator logs."""

    @staticmethod
    def format_alert(alert: CoverageAlert) -> str:
        """Format coverage alert as operator log message.

        Args:
            alert: CoverageAlert instance

        Returns:
            Formatted log message
        """
        alert_type_readable: str = alert.alert_type.replace("_", " ").title()
        severity: str = alert.severity.upper()

        message: str = (
            f"COVERAGE_ALERT [{severity}] {alert_type_readable} — "
            f"{alert.metric_type} ({alert.granularity}): {alert.current_value:.1f}%"
        )

        if alert.threshold_or_baseline is not None:
            message += f" (threshold: {alert.threshold_or_baseline:.1f}%)"

        if alert.alert_type == AlertType.REGRESSION_DETECTED.value and alert.delta_pct is not None:
            message += f" [regressed {alert.delta_pct:+.1f}%]"

        if alert.affected_modules:
            modules_preview: str = ", ".join(sorted(alert.affected_modules)[:3])
            if len(alert.affected_modules) > 3:
                modules_preview += f" (+{len(alert.affected_modules) - 3} more)"
            message += f" [modules: {modules_preview}]"

        return message


class CoverageAlertRouter:
    """Route coverage alerts to appropriate notification channels."""

    def __init__(
        self,
        slack_channel: SlackChannel | None = None,
        email_channel: EmailChannel | None = None,
        github_channel: GitHubChannel | None = None,
        operator_channel: OperatorLogChannel | None = None,
    ) -> None:
        """Initialize alert router with configured channels.

        Args:
            slack_channel: Optional SlackChannel instance
            email_channel: Optional EmailChannel instance
            github_channel: Optional GitHubChannel instance
            operator_channel: Optional OperatorLogChannel instance (always used)
        """
        self.slack_channel = slack_channel
        self.email_channel = email_channel
        self.github_channel = github_channel
        self.operator_channel = operator_channel or OperatorLogChannel()

    def route_alert(
        self,
        alert: CoverageAlert,
        channels: list[str] | None = None,
        pr_number: int | None = None,
    ) -> dict[str, AlertChannelResult]:
        """Route a coverage alert to specified channels.

        Args:
            alert: CoverageAlert instance
            channels: List of channel names ("slack", "email", "github", "operator")
                     If None, uses intelligent defaults based on severity
            pr_number: Optional PR number for GitHub channel

        Returns:
            Dictionary mapping channel names to AlertChannelResult instances
        """
        if channels is None:
            channels = self._determine_channels(alert)

        results: dict[str, AlertChannelResult] = {}

        for channel_name in channels:
            if channel_name == "slack" and self.slack_channel:
                context: dict[str, Any] = {
                    "alert_type": alert.alert_type,
                    "severity": alert.severity,
                    "metric_type": alert.metric_type,
                    "current_measurement": alert.current_value,
                    "threshold": alert.threshold_or_baseline,
                    "delta": alert.delta_pct,
                    "affected_modules": alert.affected_modules,
                    "recommendation": alert.recommendation,
                }
                message = CoverageSlackFormatter.format_alert(alert)
                try:
                    if not self.slack_channel.webhook_url:
                        raise ValueError("Slack webhook_url is not configured")
                    # Direct webhook call
                    import json

                    request = Request(
                        self.slack_channel.webhook_url,
                        data=json.dumps(message, ensure_ascii=False).encode(),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=10) as response:
                        if response.status == 200:
                            results[channel_name] = AlertChannelResult(
                                channel=channel_name,
                                success=True,
                                message="Coverage alert sent to Slack",
                            )
                        else:
                            results[channel_name] = AlertChannelResult(
                                channel=channel_name,
                                success=False,
                                error=f"Slack webhook returned {response.status}",
                            )
                except Exception as e:
                    results[channel_name] = AlertChannelResult(
                        channel=channel_name,
                        success=False,
                        error=f"Failed to send Slack alert: {str(e)}",
                    )

            elif channel_name == "email" and self.email_channel:
                context = {
                    "alert_type": alert.alert_type,
                    "severity": alert.severity,
                    "metric_type": alert.metric_type,
                    "current_measurement": alert.current_value,
                }
                subject, text_body, html_body = CoverageEmailFormatter.format_alert(alert)
                try:
                    from email.mime.multipart import MIMEMultipart
                    from email.mime.text import MIMEText

                    if not self.email_channel.smtp_host or not self.email_channel.sender:
                        raise ValueError("Email channel smtp_host or sender not configured")
                    smtp_host: str = self.email_channel.smtp_host
                    sender: str = self.email_channel.sender
                    msg = MIMEMultipart("alternative")
                    msg["Subject"] = subject
                    msg["From"] = sender
                    msg["To"] = ", ".join(self.email_channel.recipients)

                    msg.attach(MIMEText(text_body, "plain"))
                    msg.attach(MIMEText(html_body, "html"))

                    with smtplib.SMTP(
                        smtp_host, self.email_channel.smtp_port, timeout=10
                    ) as server:
                        server.starttls()
                        if self.email_channel.username and self.email_channel.password:
                            server.login(self.email_channel.username, self.email_channel.password)
                        server.sendmail(
                            sender,
                            self.email_channel.recipients,
                            msg.as_string(),
                        )

                    recipient_count = len(self.email_channel.recipients)
                    results[channel_name] = AlertChannelResult(
                        channel=channel_name,
                        success=True,
                        message=f"Coverage alert sent to {recipient_count} recipient(s)",
                    )
                except Exception as e:
                    results[channel_name] = AlertChannelResult(
                        channel=channel_name,
                        success=False,
                        error=f"Failed to send email alert: {str(e)}",
                    )

            elif channel_name == "github" and self.github_channel and pr_number:
                context = {
                    "alert_type": alert.alert_type,
                    "severity": alert.severity,
                    "pr_number": pr_number,
                }
                comment_body = CoverageGitHubFormatter.format_alert(alert, pr_number)
                try:
                    import json

                    endpoint = (
                        f"/repos/{self.github_channel.repo_owner}/"
                        f"{self.github_channel.repo_name}/issues/{pr_number}/comments"
                    )
                    url = f"{self.github_channel.api_base}{endpoint}"

                    headers = {
                        "Authorization": f"token {self.github_channel.github_token}",
                        "Accept": "application/vnd.github.v3+json",
                        "Content-Type": "application/json",
                    }

                    data = json.dumps({"body": comment_body}, ensure_ascii=False).encode()
                    request = Request(url, data=data, headers=headers, method="POST")

                    with urlopen(request, timeout=10) as response:
                        if response.status in (200, 201):
                            results[channel_name] = AlertChannelResult(
                                channel=channel_name,
                                success=True,
                                message=f"Posted coverage comment on PR #{pr_number}",
                            )
                        else:
                            results[channel_name] = AlertChannelResult(
                                channel=channel_name,
                                success=False,
                                error=f"GitHub API returned {response.status}",
                            )
                except Exception as e:
                    results[channel_name] = AlertChannelResult(
                        channel=channel_name,
                        success=False,
                        error=f"Failed to post GitHub comment: {str(e)}",
                    )

            elif channel_name == "operator":
                message = CoverageOperatorFormatter.format_alert(alert)
                context = {
                    "alert_type": alert.alert_type,
                    "severity": alert.severity,
                    "message": message,
                }
                result = self.operator_channel.notify(context)
                results[channel_name] = result

        return results

    def _determine_channels(self, alert: CoverageAlert) -> list[str]:
        """Determine optimal channels for an alert based on severity and type.

        Args:
            alert: CoverageAlert instance

        Returns:
            List of channel names to use
        """
        channels: list[str] = ["operator"]

        if alert.severity in (AlertSeverity.CRITICAL.value, AlertSeverity.EMERGENCY.value):
            if self.slack_channel:
                channels.append("slack")
            if self.email_channel:
                channels.append("email")
        elif alert.severity == AlertSeverity.WARNING.value:
            if self.slack_channel:
                channels.append("slack")

        if alert.alert_type == AlertType.REGRESSION_DETECTED.value and self.github_channel:
            channels.append("github")

        return channels

    def _is_channel_configured(
        self, channel_name: Literal["slack", "email", "github", "operator"]
    ) -> bool:
        """Check if a channel is configured.

        Args:
            channel_name: Channel name to check

        Returns:
            True if channel is available
        """
        if channel_name == "slack":
            return self.slack_channel is not None
        elif channel_name == "email":
            return self.email_channel is not None
        elif channel_name == "github":
            return self.github_channel is not None
        elif channel_name == "operator":
            return self.operator_channel is not None
        return False

    def _should_route_to_channel(
        self, alert: CoverageAlert, channel_name: Literal["slack", "email", "github", "operator"]
    ) -> bool:
        """Determine if alert should be routed to channel based on severity and type.

        Args:
            alert: Alert to evaluate
            channel_name: Channel to check

        Returns:
            True if alert should be routed to this channel
        """
        is_critical: bool = alert.severity in (
            AlertSeverity.CRITICAL.value,
            AlertSeverity.EMERGENCY.value,
        )
        is_warning: bool = alert.severity == AlertSeverity.WARNING.value
        is_regression: bool = alert.alert_type == AlertType.REGRESSION_DETECTED.value

        if channel_name == "operator":
            return True
        elif channel_name == "slack":
            return is_critical or is_warning
        elif channel_name == "email":
            return is_critical
        elif channel_name == "github":
            return is_regression
        return False
