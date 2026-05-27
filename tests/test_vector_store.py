"""Tests for vector_store.py — fingerprint-keyed known-issue cache."""

from pathlib import Path

from vector_store import KnownIssue, VectorStore


class TestVectorStoreLookup:
    def test_empty_store_returns_none(self, tmp_path):
        store = VectorStore(path=tmp_path / "known.json")
        assert store.lookup("connection refused", "nginx.service") is None

    def test_stored_message_is_found(self, tmp_path):
        store = VectorStore(path=tmp_path / "known.json")
        store.store(
            "connection refused", "nginx.service",
            description="restart nginx",
            commands=["systemctl restart nginx"],
            rollback_commands=[],
        )
        hit = store.lookup("connection refused", "nginx.service")
        assert hit is not None
        assert hit.description == "restart nginx"

    def test_lookup_ignores_variable_data(self, tmp_path):
        store = VectorStore(path=tmp_path / "known.json")
        store.store(
            "connect to 10.0.0.1:8080 refused", "nginx.service",
            description="restart upstream",
            commands=["systemctl restart nginx"],
            rollback_commands=[],
        )
        # Different IP but same pattern — should still match via fingerprint normalization
        hit = store.lookup("connect to 192.168.1.1:8080 refused", "nginx.service")
        assert hit is not None

    def test_different_units_dont_collide(self, tmp_path):
        store = VectorStore(path=tmp_path / "known.json")
        store.store(
            "error 500", "nginx.service",
            description="nginx fix", commands=[], rollback_commands=[],
        )
        assert store.lookup("error 500", "postgresql.service") is None

    def test_hits_counter_increments(self, tmp_path):
        path = tmp_path / "known.json"
        store = VectorStore(path=path)
        store.store("x", "u", "d", ["c"], [])
        store.lookup("x", "u")
        store.lookup("x", "u")
        # Re-open to verify persistence
        store2 = VectorStore(path=path)
        hit = store2.lookup("x", "u")
        assert hit.hits >= 3


class TestVectorStorePersistence:
    def test_survives_restart(self, tmp_path):
        path = tmp_path / "known.json"
        VectorStore(path=path).store(
            "disk full", "journald.service",
            description="clean logs",
            commands=["journalctl --vacuum-size=200M"],
            rollback_commands=[],
        )
        store2 = VectorStore(path=path)
        assert store2.lookup("disk full", "journald.service") is not None
        assert len(store2) == 1

    def test_forget_removes_entry(self, tmp_path):
        store = VectorStore(path=tmp_path / "known.json")
        issue = store.store("x", "u", "d", ["c"], [])
        assert store.forget(issue.fingerprint) is True
        assert store.lookup("x", "u") is None

    def test_forget_unknown_returns_false(self, tmp_path):
        store = VectorStore(path=tmp_path / "known.json")
        assert store.forget("nonexistent") is False
