"""Password and token hashing helpers (standard library only)."""

import hashlib
import hmac
import secrets

SCHEME = "pbkdf2_sha256"
ITERATIONS = 100_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"{SCHEME}${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != SCHEME:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
    return hmac.compare_digest(digest.hex(), digest_hex)


def hash_token(token: str) -> str:
    """Deterministic SHA-256 of a random token, so tokens can be stored and looked up without keeping them."""
    return hashlib.sha256(token.encode()).hexdigest()
