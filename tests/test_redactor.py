"""Tests for redactor.py — secret/PII scrubbing."""

from redactor import redact, redact_event


class TestRedact:
    def test_redacts_jwt(self):
        token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        assert "<JWT>" in redact(f"auth failed for {token}")
        assert token not in redact(f"auth failed for {token}")

    def test_redacts_aws_access_key(self):
        assert "<AWS_ACCESS_KEY>" in redact("key=AKIAIOSFODNN7EXAMPLE error")

    def test_redacts_bearer_header(self):
        out = redact("GET / Authorization: Bearer abcdef1234567890abcdefXYZ")
        assert "Bearer <REDACTED>" in out

    def test_redacts_password_assignment(self):
        out = redact("connecting with password=hunter2 to db")
        assert "password=<REDACTED>" in out
        assert "hunter2" not in out

    def test_redacts_api_key_assignment(self):
        out = redact("api_key=sk_live_abc123xyz")
        assert "api_key=<REDACTED>" in out

    def test_redacts_email(self):
        out = redact("notify user@example.com about failure")
        assert "<EMAIL>" in out
        assert "user@example.com" not in out

    def test_redacts_db_connection_string(self):
        out = redact("connecting to postgres://admin:s3cret@db.internal:5432/app")
        assert "s3cret" not in out
        assert "<REDACTED>" in out

    def test_redacts_long_hex_secret(self):
        # 'token:' triggers the key-value rule; either way the secret must be gone.
        out = redact("token: 9a3b7c1d2e4f5061728394a5b6c7d8e9")
        assert "9a3b7c1d2e4f5061728394a5b6c7d8e9" not in out
        assert "<REDACTED>" in out or "<HEX_SECRET>" in out

    def test_redacts_bare_hex_secret(self):
        # No key=, so this exercises the dedicated hex-secret rule.
        out = redact("session id 9a3b7c1d2e4f5061728394a5b6c7d8e9 expired")
        assert "<HEX_SECRET>" in out

    def test_preserves_safe_text(self):
        out = redact("nginx worker process exited with code 9")
        assert "nginx worker process" in out


class TestRedactEvent:
    def test_shallow_copy(self):
        ev = {"host": "n1", "unit": "x", "message": "password=p secrets here"}
        out = redact_event(ev)
        assert out is not ev
        assert "password=<REDACTED>" in out["message"]
        assert out["host"] == "n1"

    def test_handles_empty_event(self):
        assert redact_event({})["message"] == ""
