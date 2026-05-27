"""LiteLLM client for RootMedic.

Talks to an OpenAI-compatible LiteLLM proxy (default: https://litellm.saneax.in)
to turn a normalized log event into a RemediationPlan. The LLM is consulted only
when the rule-based stub in fetch_normalize_logs.build_remediation_plan returns
None, so it acts as a fallback brain for issues the static rules don't know about.

Config resolution order:
  1. /etc/rootmedic/config.yaml  (preferred; written by install.sh)
  2. ~/.rootmedic/config.yaml
  3. Environment: LITELLM_BASE_URL, LITELLM_API_KEY, LITELLM_MODEL

If no API key is found, get_client() returns None and the caller skips the LLM
path entirely — existing rule-based behavior is preserved.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

DEFAULT_BASE_URL = "https://litellm.saneax.in"
DEFAULT_MODEL = "smart"
REQUEST_TIMEOUT = 30

CONFIG_PATHS = [
    Path("/etc/rootmedic/config.yaml"),
    Path.home() / ".rootmedic" / "config.yaml",
]


@dataclass
class LLMConfig:
    base_url: str
    api_key: str
    model: str = DEFAULT_MODEL


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError:
        return {}
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}


def load_config() -> Optional[LLMConfig]:
    """Resolve LiteLLM config from file or env. Returns None if no API key."""
    cfg: dict[str, Any] = {}
    for p in CONFIG_PATHS:
        loaded = _load_yaml(p)
        if loaded:
            cfg = loaded
            break

    base_url = (
        os.environ.get("LITELLM_BASE_URL")
        or cfg.get("litellm_base_url")
        or DEFAULT_BASE_URL
    )
    api_key = os.environ.get("LITELLM_API_KEY") or cfg.get("litellm_api_key")
    model = (
        os.environ.get("LITELLM_MODEL")
        or cfg.get("litellm_model")
        or DEFAULT_MODEL
    )

    if not api_key:
        return None
    return LLMConfig(base_url=base_url.rstrip("/"), api_key=api_key, model=model)


SYSTEM_PROMPT = """You are RootMedic, an autonomous Linux remediation agent.
Given a single log event from a Linux host, you must decide whether the event
indicates a real, actionable problem and, if so, propose a safe remediation.

Reply with strict JSON only (no prose, no markdown fences) matching:
{
  "actionable": <bool>,
  "description": <short human-readable description>,
  "commands": [<shell command>, ...],
  "rollback_commands": [<shell command>, ...]
}

Rules:
- Prefer the least invasive fix (restart service > drop caches > config edit).
- Never propose: rm -rf, dd, mkfs, format, shutdown, reboot, userdel, passwd.
- Every command must be idempotent and safe to run twice.
- If unsure, set actionable=false and return empty command lists.
- Use systemctl for service control. Reference the exact unit from the event.
"""


def propose_plan(event: dict[str, Any], config: Optional[LLMConfig] = None):
    """Ask LiteLLM for a remediation plan. Returns a RemediationPlan or None.

    Import is deferred so this module is safe to import in environments where
    remediation_engine isn't importable (e.g., the installer's smoke test).
    """
    cfg = config or load_config()
    if cfg is None:
        return None

    import requests

    from remediation_engine import RemediationPlan, fingerprint_issue

    user_prompt = json.dumps(
        {
            "timestamp": event.get("timestamp"),
            "host": event.get("host"),
            "unit": event.get("unit"),
            "message": event.get("message"),
        }
    )

    payload = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(
            f"{cfg.base_url}/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        content = body["choices"][0]["message"]["content"]
        decision = json.loads(content)
    except Exception as exc:
        print(f"[llm_client] LiteLLM call failed: {exc}")
        return None

    if not decision.get("actionable") or not decision.get("commands"):
        return None

    return RemediationPlan(
        issue_fingerprint=fingerprint_issue(event["message"], event.get("unit", "")),
        description=str(decision.get("description", "LLM-proposed remediation")),
        commands=[str(c) for c in decision["commands"]],
        rollback_commands=[str(c) for c in decision.get("rollback_commands", [])],
    )
