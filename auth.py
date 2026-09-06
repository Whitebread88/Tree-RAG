"""Verify short-lived user JWTs issued exclusively by chatbot-ui's server."""

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

import jwt
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthenticatedUser:
    id: str
    name: str | None = None

    def require_matching_id(self, supplied_id: str) -> None:
        if supplied_id != self.id:
            raise HTTPException(status_code=403, detail="User ID does not match authenticated user")


@lru_cache(maxsize=1)
def auth_settings() -> tuple[RSAPublicKey, str, str]:
    values = {}
    for name in ("RAG_JWT_PUBLIC_KEY", "RAG_JWT_ISSUER", "RAG_JWT_AUDIENCE"):
        value = os.environ.get(name, "")
        if not value.strip():
            raise RuntimeError(f"{name} is required")
        values[name] = value
    key = load_pem_public_key(values["RAG_JWT_PUBLIC_KEY"].replace("\\n", "\n").encode())
    if not isinstance(key, RSAPublicKey) or key.key_size < 2048:
        raise RuntimeError("RAG_JWT_PUBLIC_KEY must be an RSA public key of at least 2048 bits")
    return key, values["RAG_JWT_ISSUER"], values["RAG_JWT_AUDIENCE"]


def require_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AuthenticatedUser:
    unauthorized = HTTPException(
        status_code=401, detail="Invalid or missing access token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized
    key, issuer, audience = auth_settings()
    try:
        claims = jwt.decode(
            credentials.credentials, key, algorithms=["RS256"],
            issuer=issuer, audience=audience, leeway=5,
            options={"require": ["sub", "iss", "aud", "iat", "exp"], "strict_aud": True},
        )
        subject = claims["sub"]
        if not isinstance(subject, str) or not subject or subject.strip() != subject:
            raise jwt.InvalidTokenError("Invalid subject")
        if any(type(claims[name]) is not int for name in ("iat", "exp")):
            raise jwt.InvalidTokenError("Invalid token timestamps")
        if not 0 < claims["exp"] - claims["iat"] <= 300:
            raise jwt.InvalidTokenError("Invalid token lifetime")
        name = claims.get("name")
        if name is not None and (not isinstance(name, str) or not name.strip()):
            raise jwt.InvalidTokenError("Invalid name")
        return AuthenticatedUser(id=subject, name=name)
    except (jwt.InvalidTokenError, TypeError, ValueError) as exc:
        raise unauthorized from exc


CurrentUser = Annotated[AuthenticatedUser, Depends(require_user)]
