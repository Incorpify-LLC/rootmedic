"""Plugin-based alert channels for RootMedic.

Plan-A calls for a plugin registry so deployments can wire Slack, email, IRC,
webhooks or anything else into the same incident flow. This module defines
the :class:`AlertPlugin` contract and ships two implementations:

* :class:`SlackPlugin`   – posts Slack Block Kit messages to an incoming webhook.
* :class:`WebhookPlugin` – posts the full incident payload as JSON to a generic
  HTTP endpoint. Useful for PagerDuty, Opsgenie, custom relays, or local
  testing with ``requestbin``.

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
            print(f"[ALERT][slack] webhook failed: {exc}")
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
            print(f"[ALERT][webhook] post failed: {exc}")
            return False


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def build_default_plugins(config) -> list[AlertPlugin]:
    """Construct the list of configured plugins from an ``AlertConfig``."""
    plugins: list[AlertPlugin] = [
        SlackPlugin(
            webhook_url=getattr(config, "slack_webhook_url", None),
            grafana_base_url=getattr(config, "grafana_base_url", "http://localhost:3000"),
            dedup_window_minutes=getattr(config, "dedup_window_minutes", 15),
        ),
        WebhookPlugin(
            url=getattr(config, "webhook_url", None),
            headers=getattr(config, "webhook_headers", None),
        ),
    ]
    return [p for p in plugins if p.is_configured()]
