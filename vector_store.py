"""Known-issue retrieval (the RAG layer described in plan-A).

Plan-A specifies a Qdrant + sentence-transformers vector store as the first
stop for incoming events: if a known issue matches, its remediation is reused
and the LLM is skipped. This module exposes that *interface* backed by a
simple fingerprint-keyed JSON store. Swapping in a real vector backend is a
contained change: re-implement :meth:`lookup` and :meth:`store` to embed and
query Qdrant, and keep the rest of the pipeline untouched.

The MVP gives us three things the pipeline depends on today:

* deterministic cache of validated remediations across runs,
* an explicit "learned from past incident" code path so the LLM is a true
  fallback rather than the default,
* a place to grow real embeddings without re-plumbing call sites.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from fingerprint import fingerprint_issue

KNOWN_ISSUES_FILE = Path("known_issues.json")


@dataclass
class KnownIssue:
    fingerprint: str
    description: str
    commands: list[str]
    rollback_commands: list[str]
    source: str = "learned"   # "seed", "learned", "manual"
    hits: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "description": self.description,
            "commands": self.commands,
            "rollback_commands": self.rollback_commands,
            "source": self.source,
            "hits": self.hits,
        }


class VectorStore:
    """Fingerprint-keyed known-issue store.

    Same surface as a Qdrant-backed store would expose. Calls are O(1) on
    fingerprint equality; semantic similarity is *not* yet implemented — when
    we wire in real embeddings, ``lookup`` will additionally consult Qdrant
    for fuzzy matches and fall through to the fingerprint table only when no
    embedding match clears the similarity threshold.
    """

    def __init__(self, path: Path = KNOWN_ISSUES_FILE) -> None:
        self.path = Path(path)
        self._issues: dict[str, KnownIssue] = self._load()

    # -- persistence ------------------------------------------------------

    def _load(self) -> dict[str, KnownIssue]:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text())
        return {k: KnownIssue(**v) for k, v in raw.items()}

    def _save(self) -> None:
        self.path.write_text(
            json.dumps({k: v.to_dict() for k, v in self._issues.items()}, indent=2)
        )

    # -- queries ----------------------------------------------------------

    def lookup(self, message: str, unit: str = "") -> Optional[KnownIssue]:
        """Return a :class:`KnownIssue` if the fingerprint is known."""
        fp = fingerprint_issue(message, unit)
        issue = self._issues.get(fp)
        if issue is not None:
            issue.hits += 1
            self._save()
        return issue

    def store(
        self,
        message: str,
        unit: str,
        description: str,
        commands: list[str],
        rollback_commands: list[str],
        source: str = "learned",
    ) -> KnownIssue:
        """Insert or overwrite the entry for this message/unit pair."""
        fp = fingerprint_issue(message, unit)
        issue = KnownIssue(
            fingerprint=fp,
            description=description,
            commands=list(commands),
            rollback_commands=list(rollback_commands),
            source=source,
            hits=self._issues.get(fp, KnownIssue(fp, "", [], [])).hits,
        )
        self._issues[fp] = issue
        self._save()
        return issue

    def forget(self, fingerprint: str) -> bool:
        """Drop a known issue. Returns True if anything was removed."""
        if fingerprint in self._issues:
            del self._issues[fingerprint]
            self._save()
            return True
        return False

    # -- introspection ----------------------------------------------------

    def __len__(self) -> int:
        return len(self._issues)

    def all(self) -> list[KnownIssue]:
        return list(self._issues.values())
