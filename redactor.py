"""Secret / PII redactor.

Sanitizes log lines before they leave the host for the LLM, the alert
channels, or the long-term archive. Per ``log-analyzer-plan-A.md`` this is the
last line of defence against leaking credentials into outbound calls.

The strategy is intentionally regex-only: it is fast, deterministic and easy
to audit. Patterns lean toward over-redaction — a false positive is much
cheaper than a false negative when secrets are involved.
"""

from __future__ import annotations

import re
from typing import Iterable, Pattern

# Each entry is (compiled pattern, replacement). The order matters: more
# specific patterns must run first so they bind before the generic catch-alls.
PATTERNS: list[tuple[Pattern[str], str]] = [
    # JWTs (header.payload.signature)
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "<JWT>"),

    # AWS access keys / secret access keys
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<AWS_ACCESS_KEY>"),
    (re.compile(r"\b(?i:aws_secret_access_key)\s*[:=]\s*[A-Za-z0-9/+=]{40}\b"), "aws_secret_access_key=<REDACTED>"),

    # Bearer / authorization headers
    (re.compile(r"(?i)Bearer\s+[A-Za-z0-9._\-+/=]{16,}"), "Bearer <REDACTED>"),

    # Database connection strings with embedded credentials
    (re.compile(r"\b(mysql|postgres(?:ql)?|mongodb(?:\+srv)?|redis|amqp)://[^\s/@]+:[^\s/@]+@\S+"),
     r"\1://<USER>:<REDACTED>@<HOST>"),

    # Generic key=value style secrets (password, token, api_key, secret, ...)
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)\s*[:=]\s*[^\s,;]+"),
     r"\1=<REDACTED>"),

    # Email addresses
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "<EMAIL>"),

    # Long hex strings (likely secret material — keys, hashes, session ids)
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), "<HEX_SECRET>"),

    # Base64-ish high-entropy blobs of >= 40 chars
    (re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"), "<B64_SECRET>"),
]


def redact(text: str, patterns: Iterable[tuple[Pattern[str], str]] = PATTERNS) -> str:
    """Return ``text`` with every secret-like substring replaced."""
    for pattern, replacement in patterns:
        text = pattern.sub(replacement, text)
    return text


def redact_event(event: dict) -> dict:
    """Return a shallow copy of ``event`` with the ``message`` field redacted."""
    return {**(event or {}), "message": redact((event or {}).get("message", ""))}
