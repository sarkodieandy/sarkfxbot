from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import HTTPException

from app.api.auth import AuthService, Role
from app.config.settings import Settings
from app.domain.errors import ConfigurationError

_SECRET = "unit-test-jwt-secret-that-is-longer-than-thirty-two-bytes"


def _settings(**overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        jwt_secret=_SECRET,
        jwt_issuer="goldflow-test",
        jwt_audience="goldflow-test-api",
        **overrides,
    )


def test_access_token_contains_and_validates_every_security_claim() -> None:
    settings = _settings()
    service = AuthService(settings)
    encoded = service.create_access_token("operator-1", Role.ADMIN)
    claims = jwt.decode(
        encoded,
        _SECRET,
        algorithms=["HS256"],
        audience="goldflow-test-api",
        issuer="goldflow-test",
    )

    assert {"sub", "role", "jti", "iss", "aud", "iat", "nbf", "exp"} <= claims.keys()
    principal = service.decode_access_token(encoded)
    assert principal.subject == "operator-1"
    assert principal.role is Role.ADMIN
    assert principal.token_id == claims["jti"]


@pytest.mark.parametrize("missing_claim", ["sub", "jti", "iat", "nbf", "exp", "iss", "aud"])
def test_missing_required_claim_is_rejected(missing_claim: str) -> None:
    settings = _settings()
    now = datetime.now(UTC)
    claims = {
        "sub": "operator-1",
        "role": "viewer",
        "jti": "token-1",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=5),
    }
    del claims[missing_claim]
    encoded = jwt.encode(claims, _SECRET, algorithm="HS256")

    with pytest.raises(HTTPException) as error:
        AuthService(settings).decode_access_token(encoded)
    assert error.value.status_code == 401


def test_unknown_role_expired_token_and_empty_subject_are_rejected() -> None:
    settings = _settings()
    now = datetime.now(UTC)
    common = {
        "jti": "token-1",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now - timedelta(minutes=10),
        "nbf": now - timedelta(minutes=10),
    }
    cases = (
        {**common, "sub": "user", "role": "owner", "exp": now + timedelta(minutes=5)},
        {**common, "sub": "user", "role": "viewer", "exp": now - timedelta(minutes=1)},
        {**common, "sub": " ", "role": "viewer", "exp": now + timedelta(minutes=5)},
    )
    for claims in cases:
        encoded = jwt.encode(claims, _SECRET, algorithm="HS256")
        with pytest.raises(HTTPException):
            AuthService(settings).decode_access_token(encoded)


def test_unapproved_jwt_algorithm_fails_closed() -> None:
    unsafe_settings = _settings().model_copy(update={"jwt_algorithm": "RS256"})
    with pytest.raises(ConfigurationError, match="approved HMAC"):
        AuthService(unsafe_settings)
