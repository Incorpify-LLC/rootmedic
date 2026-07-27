"""Plugin-based alert channels for RootMedic.

Plan-A calls for a plugin registry so deployments can wire Slack, email, IRC,
webhooks or anything else into the same incident flow. This module defines
the :class:`AlertPlugin` contract and ships four implementations:

* :class:`SlackPlugin`    – posts Slack Block Kit messages to an incoming webhook.
* :class:`WebhookPlugin`  – posts the full incident payload as JSON to a generic
  HTTP endpoint. Useful for PagerDuty, Opsgenie, custom relays, or local
  testing with ``requestbin``.
* :class:`TelegramPlugin` – posts a formatted text message via the Telegram
  Bot API's ``sendMessage``.
* :class:`EmailPlugin`    – posts ``{to, subject, text}`` to a configurable
  HTTP relay (not tied to any specific provider — point it at whatever mail
  relay a deployment has).

Adding a new channel is a self-contained change: subclass :class:`AlertPlugin`,
implement :meth:`is_configured` and :meth:`send`, then register the class in
:func:`build_default_plugins`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import requests


# ---------------------------------------------------------------------------
# Payload (shared by every plugin)
# ---------------------------------------------------------------------------


@dataclass
class AlertPayload:
    """Data needed to construct an alert across any channel."""

    fingerprint: str
    error_summary: str
    timestamp: float
    grafana_dashboard_uid: str = "system-logs"
    llm_root_cause: str = ""
    proposed_remediation: str = ""
    autonomy_level: str = "RECOMMEND"
    occurrence_count: int = 1
    host: str = ""
    unit: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "error_summary": self.error_summary,
            "timestamp": self.timestamp,
            "grafana_dashboard_uid": self.grafana_dashboard_uid,
            "llm_root_cause": self.llm_root_cause,
            "proposed_remediation": self.proposed_remediation,
            "autonomy_level": self.autonomy_level,
            "occurrence_count": self.occurrence_count,
            "host": self.host,
            "unit": self.unit,
        }


def _redact(text: str, *secrets: Optional[str]) -> str:
    """Strip secret substrings out of an error message before it gets logged.

    `requests` embeds the full request URL in exception messages (connection
    errors, timeouts, HTTP errors) — for Slack/Telegram, the secret IS part
    of that URL (webhook path / bot token), so printing `str(exc)` as-is on
    failure would leak it straight into journald. Bearer-header-based
    secrets (EmailPlugin) aren't affected by this specific mechanism, but
    every plugin redacts defensively rather than relying on staying that way.
    """
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***REDACTED***")
    return text


# ---------------------------------------------------------------------------
# Base plugin
# ---------------------------------------------------------------------------


class AlertPlugin(ABC):
    """Contract every alert channel must satisfy."""

    name: str = "base"

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if this plugin has enough config to actually send."""

    @abstractmethod
    def send(self, payload: AlertPayload, *, is_escalation: bool = False) -> bool:
        """Send the alert. Return True on success, False otherwise."""


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------


def build_slack_blocks(
    payload: AlertPayload,
    grafana_base_url: str,
    dedup_window_minutes: int,
    is_escalation: bool = False,
) -> list[dict[str, Any]]:
    """Render an :class:`AlertPayload` as Slack Block Kit JSON."""
    now = datetime.fromtimestamp(payload.timestamp)
    time_str = now.strftime("%Y-%m-%d %H:%M:%S")

    header_text = (
        "[ESCALATION] Human Intervention Required" if is_escalation
        else "Human Intervention Required"
    )

    blocks: list[dict[str, Any]] = [
        {"type": "header",
         "text": {"type": "plain_text", "text": header_text, "emoji": True}},
        {"type": "section",
         "text": {"type": "mrkdwn", "text": f"*Error:* {payload.error_summary}"}},
        {"type": "section",
         "fields": [
             {"type": "mrkdwn", "text": f"*Time:* {time_str}"},
             {"type": "mrkdwn", "text": f"*Occurrences:* {payload.occurrence_count}"},
             {"type": "mrkdwn", "text": f"*Autonomy Level:* {payload.autonomy_level}"},
             {"type": "mrkdwn", "text": f"*Fingerprint:* `{payload.fingerprint}`"},
         ]},
    ]

    if payload.llm_root_cause:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"*Root Cause Analysis:*\n{payload.llm_root_cause}"},
        })

    if payload.proposed_remediation:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"*Proposed Remediation:*\n```\n{payload.proposed_remediation}\n```"},
        })

    grafana_url = f"{grafana_base_url}/d/{payload.grafana_dashboard_uid}"
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn",
                 "text": f"<{grafana_url}|:bar_chart: View Grafana Dashboard>"},
    })

    dedup_until = payload.timestamp + (dedup_window_minutes * 60)
    dedup_str = datetime.fromtimestamp(dedup_until).strftime("%H:%M:%S")
    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn",
             "text": (f"Silenced until {dedup_str} if same issue recurs "
                      f"(dedup window: {dedup_window_minutes} min)")},
        ],
    })

    return blocks


class SlackPlugin(AlertPlugin):
    name = "slack"

    def __init__(
        self,
        webhook_url: Optional[str],
        grafana_base_url: str = "http://localhost:3000",
        dedup_window_minutes: int = 15,
    ) -> None:
        self.webhook_url = webhook_url
        self.grafana_base_url = grafana_base_url
        self.dedup_window_minutes = dedup_window_minutes

    def is_configured(self) -> bool:
        return bool(self.webhook_url)

    def send(self, payload: AlertPayload, *, is_escalation: bool = False) -> bool:
        if not self.is_configured():
            return False
        blocks = build_slack_blocks(
            payload, self.grafana_base_url, self.dedup_window_minutes, is_escalation,
        )
        try:
            response = requests.post(
                self.webhook_url,
                json={"blocks": blocks},
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            response.raise_for_status()
            return True
        except requests.RequestException as exc:
            print(f"[ALERT][slack] webhook failed: {_redact(str(exc), self.webhook_url)}")
            return False


# ---------------------------------------------------------------------------
# Generic webhook
# ---------------------------------------------------------------------------


class WebhookPlugin(AlertPlugin):
    """Posts the alert payload as JSON to an arbitrary HTTP endpoint."""

    name = "webhook"

    def __init__(
        self,
        url: Optional[str],
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        self.url = url
        self.headers = {"Content-Type": "application/json", **(headers or {})}

    def is_configured(self) -> bool:
        return bool(self.url)

    def send(self, payload: AlertPayload, *, is_escalation: bool = False) -> bool:
        if not self.is_configured():
            return False
        body = payload.to_dict()
        body["is_escalation"] = is_escalation
        try:
            response = requests.post(
                self.url,
                json=body,
                headers=self.headers,
                timeout=10,
            )
            response.raise_for_status()
            return True
        except requests.RequestException as exc:
            print(f"[ALERT][webhook] post failed: {_redact(str(exc), self.url)}")
            return False


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------


def build_alert_text(
    payload: AlertPayload,
    grafana_base_url: str,
    dedup_window_minutes: int,
    is_escalation: bool = False,
) -> str:
    """Render an :class:`AlertPayload` as a plain-text/Markdown message.

    Same content as :func:`build_slack_blocks`, without Slack's Block Kit —
    for channels (Telegram, plain email) that just want formatted text.
    """
    now = datetime.fromtimestamp(payload.timestamp)
    time_str = now.strftime("%Y-%m-%d %H:%M:%S")

    header = (
        "*[ESCALATION] Human Intervention Required*" if is_escalation
        else "*Human Intervention Required*"
    )

    lines = [
        header,
        "",
        f"*Error:* {payload.error_summary}",
        f"*Time:* {time_str}",
        f"*Occurrences:* {payload.occurrence_count}",
        f"*Autonomy Level:* {payload.autonomy_level}",
        f"*Fingerprint:* `{payload.fingerprint}`",
    ]

    if payload.llm_root_cause:
        lines += ["", f"*Root Cause Analysis:*\n{payload.llm_root_cause}"]

    if payload.proposed_remediation:
        lines += ["", f"*Proposed Remediation:*\n```\n{payload.proposed_remediation}\n```"]

    grafana_url = f"{grafana_base_url}/d/{payload.grafana_dashboard_uid}"
    lines += ["", f"[View Grafana Dashboard]({grafana_url})"]

    dedup_until = payload.timestamp + (dedup_window_minutes * 60)
    dedup_str = datetime.fromtimestamp(dedup_until).strftime("%H:%M:%S")
    lines += ["", f"_Silenced until {dedup_str} if same issue recurs "
                  f"(dedup window: {dedup_window_minutes} min)_"]

    return "\n".join(lines)


class TelegramPlugin(AlertPlugin):
    name = "telegram"

    def __init__(
        self,
        bot_token: Optional[str],
        chat_id: Optional[str],
        grafana_base_url: str = "http://localhost:3000",
        dedup_window_minutes: int = 15,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.grafana_base_url = grafana_base_url
        self.dedup_window_minutes = dedup_window_minutes

    def is_configured(self) -> bool:
        return bool(self.bot_token) and bool(self.chat_id)

    def send(self, payload: AlertPayload, *, is_escalation: bool = False) -> bool:
        if not self.is_configured():
            return False
        text = build_alert_text(
            payload, self.grafana_base_url, self.dedup_window_minutes, is_escalation,
        )
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=10,
            )
            response.raise_for_status()
            return True
        except requests.RequestException as exc:
            print(f"[ALERT][telegram] sendMessage failed: {_redact(str(exc), self.bot_token)}")
            return False


# ---------------------------------------------------------------------------
# Email (via a configurable HTTP relay)
# ---------------------------------------------------------------------------


class EmailPlugin(AlertPlugin):
    """Posts ``{to, subject, text}`` to an HTTP mail relay.

    Deliberately relay-agnostic: this deployment points ``relay_url`` at a
    Cloudflare Worker fronting ``send_email``, but any relay accepting the
    same shape (bearer auth, JSON body) works.
    """

    name = "email"

    def __init__(
        self,
        relay_url: Optional[str],
        relay_api_key: Optional[str],
        to: Optional[str],
        grafana_base_url: str = "http://localhost:3000",
        dedup_window_minutes: int = 15,
    ) -> None:
        self.relay_url = relay_url.rstrip("/") if relay_url else relay_url
        self.relay_api_key = relay_api_key
        self.to = to
        self.grafana_base_url = grafana_base_url
        self.dedup_window_minutes = dedup_window_minutes

    def is_configured(self) -> bool:
        return bool(self.relay_url) and bool(self.relay_api_key) and bool(self.to)

    def send(self, payload: AlertPayload, *, is_escalation: bool = False) -> bool:
        if not self.is_configured():
            return False
        text = build_alert_text(
            payload, self.grafana_base_url, self.dedup_window_minutes, is_escalation,
        )
        subject_prefix = "[ESCALATION] " if is_escalation else ""
        subject = f"{subject_prefix}RootMedic alert: {payload.error_summary}"[:200]
        try:
            response = requests.post(
                f"{self.relay_url}/alerts/send",
                json={"to": self.to, "subject": subject, "text": text},
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.relay_api_key}",
                },
                timeout=10,
            )
            response.raise_for_status()
            return True
        except requests.RequestException as exc:
            print(f"[ALERT][email] relay post failed: {_redact(str(exc), self.relay_api_key)}")
            return False


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def build_default_plugins(config) -> list[AlertPlugin]:
    """Construct the list of configured plugins from an ``AlertConfig``."""
    grafana_base_url = getattr(config, "grafana_base_url", "http://localhost:3000")
    dedup_window_minutes = getattr(config, "dedup_window_minutes", 15)
    plugins: list[AlertPlugin] = [
        SlackPlugin(
            webhook_url=getattr(config, "slack_webhook_url", None),
            grafana_base_url=grafana_base_url,
            dedup_window_minutes=dedup_window_minutes,
        ),
        WebhookPlugin(
            url=getattr(config, "webhook_url", None),
            headers=getattr(config, "webhook_headers", None),
        ),
        TelegramPlugin(
            bot_token=getattr(config, "telegram_bot_token", None),
            chat_id=getattr(config, "telegram_chat_id", None),
            grafana_base_url=grafana_base_url,
            dedup_window_minutes=dedup_window_minutes,
        ),
        EmailPlugin(
            relay_url=getattr(config, "email_relay_url", None),
            relay_api_key=getattr(config, "email_relay_api_key", None),
            to=getattr(config, "email_to", None),
            grafana_base_url=grafana_base_url,
            dedup_window_minutes=dedup_window_minutes,
        ),
    ]
    return [p for p in plugins if p.is_configured()]
