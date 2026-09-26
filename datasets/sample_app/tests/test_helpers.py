from datetime import UTC, datetime

from authkit.audit import AuditLog
from authkit.clock import add_minutes, utcnow
from authkit.mailer import build_message, welcome_email
from authkit.ratelimit import TokenBucket
from authkit.roles import has_permission
from authkit.validators import is_valid_username, normalize_whitespace


def test_clock() -> None:
    assert utcnow().tzinfo is UTC
    assert add_minutes(datetime(2026, 1, 1, tzinfo=UTC), 90) == datetime(2026, 1, 1, 1, 30, tzinfo=UTC)


def test_validators() -> None:
    assert is_valid_username("alice_1") and not is_valid_username("1alice") and not is_valid_username("al")
    assert normalize_whitespace("  a   b \n c ") == "a b c"


def test_audit_log() -> None:
    log = AuditLog()
    log.record("login", user_id=1)
    log.record("logout", user_id=1)
    assert [e["event"] for e in log.entries()] == ["login", "logout"]
    assert len(log.entries("login")) == 1
    assert len(log.since(datetime(2000, 1, 1, tzinfo=UTC))) == 2


def test_mailer() -> None:
    message = build_message("a@example.com", "Hi", "Body")
    assert message["To"] == "a@example.com" and message["Subject"] == "Hi"
    assert "a@example.com" in welcome_email("a@example.com").get_content()


def test_roles() -> None:
    assert has_permission("admin", "delete_user") and not has_permission("user", "delete_user")
    assert not has_permission("nobody", "read_profile")


def test_token_bucket() -> None:
    bucket = TokenBucket(capacity=2, refill_per_second=1)
    assert bucket.allow(0) and bucket.allow(0) and not bucket.allow(0)
    assert bucket.allow(1.5)
