"""Invitation token validation — single source of truth.

Centralizza la logica di validazione di un invitation token (lookup +
state-check) usata da DUE endpoint:
  - `GET /api/v1/auth/invitation/{token}` (preview pre-form, S6)
  - `POST /api/v1/auth/accept-invite` (submit, S6)

Pattern: la duplicazione del check di stato in 2+ posti è un magnet per
divergenze (un endpoint controlla anche `expires_at`, l'altro no, e nessuno
se ne accorge per mesi). `validate_invitation` garantisce coerenza.

Aggiungere un nuovo state-check in futuro (es. "invitation requires email
verification" in S7+, o `requires_mfa_setup` in Phase 2) richiede modifica
in UN solo posto.

Vedi ADR-0007 per il flow accept-invite completo.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Invitation

# ─── Exception hierarchy ─────────────────────────────────────────────────────


class InvitationError(Exception):
    """Base per tutti gli errori di invitation validation.

    L'HTTP layer mapperà ciascuna sottoclasse a uno status code specifico:
    - `InvitationNotFoundError` → 404
    - `InvitationExpiredError` / `InvitationAcceptedError` /
      `InvitationRevokedError` → 410 Gone

    Il GET preview (S6 step 3) tratta TUTTE le sottoclassi come 404 generico
    (no information disclosure), il POST accept-invite (S6 step 4)
    differenzia per UX.
    """


class InvitationNotFoundError(InvitationError):
    """Token non corrisponde a nessuna invitation in DB (sha256 mismatch)."""


class InvitationExpiredError(InvitationError):
    """`expires_at < now()`."""


class InvitationAcceptedError(InvitationError):
    """`accepted_at IS NOT NULL` — invito già usato."""


class InvitationRevokedError(InvitationError):
    """`revoked_at IS NOT NULL` — super_admin ha annullato l'invito."""


# ─── Helper ──────────────────────────────────────────────────────────────────


async def validate_invitation(
    db: AsyncSession,
    token_plaintext: str,
) -> Invitation:
    """Valida un invitation token plaintext e ritorna l'`Invitation` se valida.

    Esegue SHA-256 hash del token plaintext, fa lookup in DB, e verifica i 4
    stati di validità in **ordine deterministico**:

      1. Esiste (hash match) → altrimenti `InvitationNotFoundError`
      2. Non revocata (`revoked_at IS NULL`) → altrimenti `InvitationRevokedError`
      3. Non accettata (`accepted_at IS NULL`) → altrimenti `InvitationAcceptedError`
      4. Non scaduta (`expires_at > now(UTC)`) → altrimenti `InvitationExpiredError`

    L'ordine di check è deterministico per facilitare i test e per garantire
    messaggi di errore coerenti. Una invitation può essere "expired AND
    revoked" — in tal caso ritorniamo `InvitationRevokedError` per priorità
    (admin action esplicita > scadenza implicita).

    Args:
        db: AsyncSession (generalmente da `get_unauthenticated_db` —
            l'endpoint accept-invite è pre-auth, no JWT, no RLS).
        token_plaintext: il token in chiaro ricevuto dal client (dall'URL
            `?token=...` per il preview, dal body JSON per l'accept).

    Returns:
        Invitation: l'invitation valida e pendente.

    Raises:
        InvitationNotFoundError: token non esiste (no information disclosure).
        InvitationRevokedError: invitation revocata da super_admin.
        InvitationAcceptedError: invitation già usata.
        InvitationExpiredError: invitation scaduta naturalmente.
    """
    token_hash = hashlib.sha256(token_plaintext.encode("utf-8")).hexdigest()

    invitation = (
        await db.execute(select(Invitation).where(Invitation.token_hash == token_hash))
    ).scalar_one_or_none()

    if invitation is None:
        raise InvitationNotFoundError

    # Order matters: revoked > accepted > expired
    # (priority of explicit admin/user actions over implicit expiry)
    if invitation.revoked_at is not None:
        raise InvitationRevokedError

    if invitation.accepted_at is not None:
        raise InvitationAcceptedError

    if invitation.expires_at < datetime.now(UTC):
        raise InvitationExpiredError

    return invitation
