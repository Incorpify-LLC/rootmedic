"""Stable issue fingerprinting for RootMedic.

A fingerprint collapses variable data (timestamps, PIDs, IPs, hex addresses,
arbitrary numerics) so the same *kind* of incident always hashes the same way.
The fingerprint is the primary key used by the remediation engine, the alert
deduplication state, and the known-issue vector store.
"""

from __future__ import annotations

import hashlib
import re

_TIMESTAMP_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}:\d{2}")
_PID_RE = re.compile(r"pid[= ]?\d+")
_IPV4_RE = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
_HEX_RE = re.compile(r"0x[0-9a-f]+")
_NUM_RE = re.compile(r"\d+")


def fingerprint_issue(log_message: str, unit: str = "") -> str:
    """Return a 16-char hex fingerprint for an issue type.

    The unit is included so the same error text in two different services
    fingerprints differently.
    """
    cleaned = log_message.lower()
    cleaned = _TIMESTAMP_RE.sub("<TS>", cleaned)
    cleaned = _PID_RE.sub("pid=<PID>", cleaned)
    cleaned = _IPV4_RE.sub("<IP>", cleaned)
    cleaned = _HEX_RE.sub("<HEX>", cleaned)
    cleaned = _NUM_RE.sub("<N>", cleaned)
    raw = f"{unit}:{cleaned}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
