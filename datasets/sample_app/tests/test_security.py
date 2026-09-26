from authkit.security import hash_password, hash_token, verify_password


def test_hash_and_verify() -> None:
    stored = hash_password("s3cret-pass")
    assert stored.startswith("pbkdf2_sha256$")
    assert verify_password("s3cret-pass", stored)
    assert not verify_password("other", stored)


def test_hashes_are_salted() -> None:
    assert hash_password("same-password") != hash_password("same-password")


def test_verify_rejects_malformed_hash() -> None:
    assert not verify_password("x", "not-a-hash")
    assert not verify_password("x", "md5$1$00$00")


def test_hash_token_is_deterministic() -> None:
    assert hash_token("abc") == hash_token("abc")
    assert hash_token("abc") != hash_token("abd")
