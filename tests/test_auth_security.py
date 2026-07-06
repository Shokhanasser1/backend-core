"""Unit tests for core/auth security primitives (no DB/Redis)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from app.config import Settings
from core.auth.security import passwords, tokens, totp
from core.auth.security.encryption import SecretCipher, generate_key
from shared.errors import AuthenticationError


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"_env_file": None, "jwt_secret": "unit-test-secret"}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class TestPasswords:
    def test_hash_and_verify(self) -> None:
        h = passwords.hash_password("correct horse battery staple")
        assert h.startswith("$argon2id$")
        assert passwords.verify_password(h, "correct horse battery staple")
        assert not passwords.verify_password(h, "wrong")

    def test_verify_none_hash_is_false_but_spends_time(self) -> None:
        # Unknown user: no hash → always False (dummy verification for timing).
        assert passwords.verify_password(None, "anything") is False


class TestAccessToken:
    def test_roundtrip_with_tenant(self) -> None:
        s = settings()
        user_id, tenant_id = uuid4(), uuid4()
        token, jti = tokens.encode_access_token(user_id=user_id, tenant_id=tenant_id, settings=s)
        claims = tokens.decode_access_token(token, s)
        assert claims["sub"] == str(user_id)
        assert claims["tenant"] == str(tenant_id)
        assert claims["jti"] == str(jti)
        assert claims["type"] == "access"

    def test_user_scope_token_has_null_tenant(self) -> None:
        s = settings()
        token, _ = tokens.encode_access_token(user_id=uuid4(), tenant_id=None, settings=s)
        assert tokens.decode_access_token(token, s)["tenant"] is None

    def test_expired_token_rejected(self) -> None:
        s = settings(access_token_ttl_seconds=1)
        past = datetime.now(UTC) - timedelta(hours=1)
        token, _ = tokens.encode_access_token(user_id=uuid4(), tenant_id=None, settings=s, now=past)
        with pytest.raises(AuthenticationError):
            tokens.decode_access_token(token, s)

    def test_wrong_secret_rejected(self) -> None:
        token, _ = tokens.encode_access_token(
            user_id=uuid4(), tenant_id=None, settings=settings(jwt_secret="secret-a")
        )
        with pytest.raises(AuthenticationError):
            tokens.decode_access_token(token, settings(jwt_secret="secret-b"))

    def test_alg_none_rejected(self) -> None:
        # alg:none attack — an unsigned token must never be accepted (V3).
        s = settings()
        forged = jwt.encode(
            {"sub": str(uuid4()), "type": "access", "iat": 0, "exp": 9_999_999_999},
            key="",
            algorithm="none",
        )
        with pytest.raises(AuthenticationError):
            tokens.decode_access_token(forged, s)

    def test_alg_confusion_rejected(self) -> None:
        # Token signed with a different algorithm than the pinned one is rejected.
        s = settings(jwt_algorithm="HS256")
        forged = jwt.encode(
            {"sub": "x", "type": "access", "iat": 0, "exp": 9_999_999_999}, "k", algorithm="HS384"
        )
        with pytest.raises(AuthenticationError):
            tokens.decode_access_token(forged, s)

    def test_wrong_type_rejected(self) -> None:
        s = settings()
        other = jwt.encode(
            {"sub": "x", "type": "refresh", "iat": 0, "exp": 9_999_999_999},
            s.jwt_secret,
            algorithm="HS256",
        )
        with pytest.raises(AuthenticationError, match="type"):
            tokens.decode_access_token(other, s)


class TestOpaqueTokens:
    def test_unique_and_hashed(self) -> None:
        a, b = tokens.new_opaque_token(), tokens.new_opaque_token()
        assert a != b
        assert tokens.hash_token(a) == tokens.hash_token(a)
        assert tokens.hash_token(a) != tokens.hash_token(b)
        assert len(tokens.hash_token(a)) == 64  # sha256 hex


class TestTotp:
    def test_verify_current_code(self) -> None:
        import pyotp

        secret = totp.generate_totp_secret()
        assert totp.verify_totp(secret, pyotp.TOTP(secret).now())
        assert not totp.verify_totp(secret, "000000")

    def test_provisioning_uri(self) -> None:
        uri = totp.provisioning_uri(totp.generate_totp_secret(), "user@example.uz")
        assert uri.startswith("otpauth://totp/")
        assert "backend-core" in uri

    def test_recovery_codes_unique_and_hashable(self) -> None:
        codes = totp.generate_recovery_codes()
        assert len(codes) == totp.RECOVERY_CODE_COUNT
        assert len(set(codes)) == len(codes)
        # Case/space-insensitive hashing.
        assert totp.hash_recovery_code(codes[0]) == totp.hash_recovery_code(
            f"  {codes[0].lower()} "
        )


class TestEncryption:
    def test_roundtrip_dev_key(self) -> None:
        cipher = SecretCipher(keys=())
        token = cipher.encrypt("s3cr3t")
        assert token != b"s3cr3t"
        assert cipher.decrypt(token) == "s3cr3t"

    def test_rotation_decrypts_old_and_new(self) -> None:
        old_key, new_key = generate_key(), generate_key()
        old_cipher = SecretCipher(keys=(old_key,))
        token = old_cipher.encrypt("value")
        # New primary key first, old key retained for decryption.
        rotated = SecretCipher(keys=(new_key, old_key))
        assert rotated.decrypt(token) == "value"
        re_encrypted = rotated.rotate(token)
        assert SecretCipher(keys=(new_key,)).decrypt(re_encrypted) == "value"
