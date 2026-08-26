"""JWT authentication and role-based authorization dependencies."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any
from uuid import uuid4

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import ContainerDependency
from app.config.settings import Settings
from app.domain.errors import ConfigurationError


class Role(StrEnum):
    ADMIN = "admin"
    TRADER = "trader"
    VIEWER = "viewer"


class Principal(BaseModel):
    model_config = ConfigDict(frozen=True)

    subject: str
    role: Role
    token_id: str


class AuthService:
    """Issue and verify conventional HS256 JWTs without storing bearer tokens."""

    def __init__(self, settings: Settings) -> None:
        self._secret = settings.jwt_secret.get_secret_value()
        self._algorithm = settings.jwt_algorithm
        if self._algorithm not in {"HS256", "HS384", "HS512"}:
            raise ConfigurationError("JWT algorithm must be an approved HMAC algorithm")
        if not self._secret:
            raise ConfigurationError("JWT secret cannot be empty")
        self._issuer = settings.jwt_issuer
        self._audience = settings.jwt_audience
        self._minutes = settings.access_token_minutes

    def create_access_token(self, subject: str, role: Role) -> str:
        if not subject.strip():
            raise ValueError("token subject is required")
        now = datetime.now(UTC)
        claims: dict[str, Any] = {
            "sub": subject,
            "role": role.value,
            "jti": str(uuid4()),
            "iss": self._issuer,
            "aud": self._audience,
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(minutes=self._minutes),
        }
        return jwt.encode(claims, self._secret, algorithm=self._algorithm)

    def decode_access_token(self, token: str) -> Principal:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=[self._algorithm],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["sub", "role", "jti", "iss", "aud", "iat", "nbf", "exp"]},
            )
            subject = str(claims["sub"]).strip()
            token_id = str(claims["jti"]).strip()
            if not subject or not token_id:
                raise ValueError("JWT subject and identifier must be non-empty")
            return Principal(subject=subject, role=Role(str(claims["role"])), token_id=token_id)
        except (jwt.PyJWTError, ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or expired access token",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc


_bearer = HTTPBearer(auto_error=False)


def get_auth_service(container: ContainerDependency) -> AuthService:
    return AuthService(container.settings)


def get_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return auth.decode_access_token(credentials.credentials)


def require_roles(*roles: Role) -> Callable[[Principal], Principal]:
    allowed = frozenset(roles)

    def dependency(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if principal.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient role",
            )
        return principal

    return dependency


ViewerPrincipal = Annotated[Principal, Depends(require_roles(Role.VIEWER, Role.TRADER, Role.ADMIN))]
TraderPrincipal = Annotated[Principal, Depends(require_roles(Role.TRADER, Role.ADMIN))]
AdminPrincipal = Annotated[Principal, Depends(require_roles(Role.ADMIN))]
