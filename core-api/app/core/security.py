"""Password hashing (bcrypt) and JWT (jose) utilities.

Centralizza il codice security per evitare duplicazione e per facilitare audit
e rotazione del segreto. Il secret JWT viene letto da Settings (env-driven).

Vedi ADR-0003 per le scelte di design (HS256, stateless refresh, claim shape,
exit strategy via token_version).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

import bcrypt
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError

from app.core.config import get_settings

settings = get_settings()


# ─── Errors ──────────────────────────────────────────────────────────────────


class InvalidTokenError(Exception):
    """Raised when a JWT fails to decode (signature, expiry, malformed) or has
    an unexpected `type` claim. The HTTP layer converts this to 401.
    """


# ─── Token types ─────────────────────────────────────────────────────────────


class TokenType(StrEnum):
    """Discriminator for the `type` claim. Prevents using a refresh token as an
    access token (and vice versa) — see ADR-0003 §"Type-tagging".
    """

    ACCESS = "access"
    REFRESH = "refresh"


# ─── Password hashing ────────────────────────────────────────────────────────


def hash_password(plain: str) -> str:
    """bcrypt hash with library-managed salt."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time bcrypt verify. Returns False on malformed hashes (never raises)."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


# ─── JWT ─────────────────────────────────────────────────────────────────────


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _encode_token(
    *,
    subject: UUID,
    token_type: TokenType,
    expires_delta: timedelta,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = _utc_now()
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type.value,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    if extra_claims:
        payload.update(extra_claims)
    encoded: str = jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return encoded


def create_access_token(
    *,
    user_id: UUID,
    client_id: UUID | None,
    role: str,
) -> str:
    """Short-lived (default 60 min) access token.

    Claims: sub, type=access, iat, exp, client_id (str | None), role.
    `client_id` is None for super_admin (cross-tenant).
    """
    return _encode_token(
        subject=user_id,
        token_type=TokenType.ACCESS,
        expires_delta=timedelta(minutes=settings.jwt_access_token_expire_minutes),
        extra_claims={
            "client_id": str(client_id) if client_id is not None else None,
            "role": role,
        },
    )


def create_refresh_token(*, user_id: UUID) -> str:
    """Long-lived refresh token. Minimal claims: sub, type=refresh, iat, exp.

    On refresh we re-load the user from DB to get fresh client_id/role/is_active,
    so we don't replicate them in the refresh payload.
    """
    return _encode_token(
        subject=user_id,
        token_type=TokenType.REFRESH,
        expires_delta=timedelta(days=settings.refresh_token_ttl_days),
    )


def decode_token(token: str, *, expected_type: TokenType) -> dict[str, Any]:
    """Decode + validate a JWT.

    Raises InvalidTokenError on:
      - expired (exp claim in the past)            → "token expired"
      - signature mismatch / malformed / unknown   → "invalid token signature"
      - `type` claim missing or wrong              → "token type mismatch: …"

    I messaggi sono volutamente sintetici: niente leak di dettagli interni
    (es. "Invalid crypto padding"), ma abbastanza specifici da essere utili
    al frontend per distinguere expired da malformed.
    """
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except ExpiredSignatureError as e:
        raise InvalidTokenError("token expired") from e
    except JWTError as e:
        raise InvalidTokenError("invalid token signature") from e

    actual_type = payload.get("type")
    if actual_type != expected_type.value:
        raise InvalidTokenError(
            f"token type mismatch: expected {expected_type.value!r}, got {actual_type!r}",
        )
    return payload


def decode_access_token(token: str) -> dict[str, Any]:
    """Convenience wrapper: decode_token(token, expected_type=ACCESS)."""
    return decode_token(token, expected_type=TokenType.ACCESS)


def decode_refresh_token(token: str) -> dict[str, Any]:
    """Convenience wrapper: decode_token(token, expected_type=REFRESH)."""
    return decode_token(token, expected_type=TokenType.REFRESH)


# ─── Invitation tokens ────────────────────────────────────────────────────────


def generate_invitation_token() -> tuple[str, str]:
    """Genera coppia (plaintext, sha256_hash) per invitation token.

    - **Plaintext**: `secrets.token_urlsafe(32)` → 43 char base64url
      (~256 bit entropia). Va nel response 201 di POST /admin/clients e
      poi nel link condiviso col destinatario (mai re-recuperabile dopo).
    - **Hash**: SHA-256 hex (64 char). L'unica forma persistita in DB
      (defense-in-depth contro DB leak — vedi ADR-0006).

    Per validare un token in arrivo (accept-invite, Sessione 6): SHA-256
    del token ricevuto, lookup `invitations.token_hash` per match.

    Bcrypt non è necessario qui: il token è già random + lungo, no
    rainbow-table risk. SHA-256 è abbastanza per il nostro modello di
    minaccia (DB leak), e ~zero CPU cost.
    """
    plaintext = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    return plaintext, token_hash
