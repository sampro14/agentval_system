import pytest

from authkit.db import connect, migrate
from authkit.users import InvalidEmailError, UserError, get_user, register


@pytest.fixture
def conn():
    connection = connect()
    migrate(connection)
    return connection


@pytest.mark.parametrize("email", ["a@b", "@b.co", "a@@b.co", "a b@c.co", "a@b.", "a@.co", "plain", "a@b@c.co", ""])
def test_invalid_emails_rejected(conn, email):
    with pytest.raises(InvalidEmailError):
        register(conn, email, "correct-horse")


@pytest.mark.parametrize("email", ["a@b.co", "  First.Last@Example.COM  ", "x+tag@sub.domain.org"])
def test_valid_emails_registered_and_normalised(conn, email):
    user_id = register(conn, email, "correct-horse")
    assert get_user(conn, user_id)["email"] == email.strip().lower()


def test_invalid_email_error_is_a_user_error():
    assert issubclass(InvalidEmailError, UserError)
