import pytest
from datetime import timedelta
from jose import jwt
from app.core.security import (
    create_access_token,
    verify_password,
    get_password_hash,
)
from app.config import settings


class TestPasswordHashing:
    def test_hash_produces_non_plain_string(self):
        hashed = get_password_hash("plaintext")
        assert hashed != "plaintext"
        assert isinstance(hashed, str)
        assert len(hashed) > 0

    def test_hash_is_deterministic_in_verification(self):
        password = "my_secret_password"
        hashed = get_password_hash(password)
        assert verify_password(password, hashed) is True

    def test_different_passwords_produce_different_hashes(self):
        hash1 = get_password_hash("password1")
        hash2 = get_password_hash("password2")
        assert hash1 != hash2

    def test_same_password_produces_different_hashes(self):
        hash1 = get_password_hash("same_password")
        hash2 = get_password_hash("same_password")
        assert hash1 != hash2

    def test_verify_password_returns_false_for_wrong_password(self):
        hashed = get_password_hash("correct")
        assert verify_password("wrong", hashed) is False

    def test_verify_password_returns_false_for_empty_string(self):
        hashed = get_password_hash("not_empty")
        assert verify_password("", hashed) is False


class TestAccessToken:
    def test_token_is_non_empty_string(self):
        token = create_access_token(subject="testuser")
        assert isinstance(token, str)
        assert len(token) > 0

    def test_token_contains_sub_claim(self):
        token = create_access_token(subject="testuser")
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        assert payload["sub"] == "testuser"

    def test_token_contains_exp_claim(self):
        token = create_access_token(subject="testuser")
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        assert "exp" in payload

    def test_token_expiry_is_in_future(self):
        from datetime import datetime, timezone

        token = create_access_token(subject="testuser")
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        assert payload["exp"] > datetime.now(timezone.utc).timestamp()

    def test_custom_expiry(self):
        from datetime import datetime, timezone

        token = create_access_token(
            subject="testuser", expires_delta=timedelta(minutes=5)
        )
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        now = datetime.now(timezone.utc).timestamp()
        assert payload["exp"] - now < 310

    def test_expired_token_raises_error(self):
        token = create_access_token(
            subject="testuser", expires_delta=timedelta(seconds=-1)
        )
        with pytest.raises(Exception):
            jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])

    def test_tampered_token_raises_error(self):
        token = create_access_token(subject="testuser")
        tampered = token[:-5] + "XXXXX"
        with pytest.raises(Exception):
            jwt.decode(tampered, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])

    def test_wrong_secret_raises_error(self):
        token = create_access_token(subject="testuser")
        with pytest.raises(Exception):
            jwt.decode(token, "wrong-secret-key", algorithms=[settings.ALGORITHM])

    def test_alg_none_attack_rejected(self):
        from datetime import datetime, timezone

        header = jwt.encode({"alg": "none"}, "unused")
        payload = jwt.encode(
            {"sub": "admin", "exp": datetime.now(timezone.utc).timestamp() + 3600},
            "unused",
        )
        forged = f"{header}.{payload}."
        with pytest.raises(Exception):
            jwt.decode(forged, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
