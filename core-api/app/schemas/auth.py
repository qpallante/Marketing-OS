"""Pydantic schemas for the auth router (`app/routers/auth.py`).

Estratti da inline a file dedicato in S6: ora abbiamo 7 schemi auth distinti
(LoginRequest, LoginResponse, RefreshRequest, ClientSummary, MeResponse,
InvitationPreviewResponse, AcceptInviteRequest), che soddisfano il trigger
condition di ADR-0006 §"Schema extraction" (≥5 schemi distinti per router).

Vedi ADR-0003 per le decisioni JWT (claims, lifetimes, response shape) e
ADR-0007 per il flow accept-invite.

**Nota sul `ClientSummary` "duplicato"**: esiste anche
`app.schemas.admin.ClientSummary` con un campo `created_at` aggiuntivo.
La duplicazione è intenzionale:
  - L'endpoint `GET /me` (auth) ritorna info minimal del client per la
    topbar/sidebar — `created_at` non serve.
  - L'endpoint `GET /admin/clients` (admin) ritorna anche `created_at`
    perché la lista admin lo mostra.

Consolidare i due in `app/schemas/common.py` con `created_at` opzionale
sarebbe DRY-correct ma:
  - rende il tipo "weakly typed" (un campo opzionale che è sempre presente
    dal lato admin e mai dal lato auth è una bugia tipologica),
  - risparmia 5 righe a costo di un import indiretto.

Trade-off accettato: due `ClientSummary` separati con scope chiaro per file.
Se in S7+ ci ritroviamo con un terzo `ClientSummary` differente, sarà il
momento di consolidare con un base class + estensioni.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, StringConstraints

# ─── Reusable validators ────────────────────────────────────────────────────

#: Token plaintext invitation — `secrets.token_urlsafe(32)` produce sempre
#: esattamente 43 caratteri base64url-safe (256 bit di entropia, no padding).
#: Validare la lunghezza esatta lato Pydantic permette di rifiutare a monte
#: token deformati prima di arrivare al lookup DB.
InvitationToken = Annotated[
    str,
    StringConstraints(min_length=43, max_length=43),
]

#: Password al accept-invite: NIST SP 800-63B § 5.1.1.2 aligned.
#: Min 12 caratteri (consensus 1Password/Bitwarden), max 128 (bcrypt
#: tronca silenziosamente sopra 72 byte, 128 è un upper bound generoso).
#: NESSUN regex composito (1 maj + 1 num + 1 special) — sconsigliato da
#: NIST: peggiora UX senza più security reale. Vedi ADR-0007.
NewPassword = Annotated[
    str,
    StringConstraints(min_length=12, max_length=128),
]


# ─── Request schemas ─────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class AcceptInviteRequest(BaseModel):
    """Body di POST /api/v1/auth/accept-invite (S6).

    Validation server-side autorevole: il client può essere bypassato.
    """

    token: InvitationToken
    password: NewPassword


# ─── Response schemas ────────────────────────────────────────────────────────


class LoginResponse(BaseModel):
    """Token pair emesso da /login, /refresh, /accept-invite (S6).

    `expires_in` e `refresh_expires_in` sono in **secondi** per allineamento
    con l'OAuth 2.0 RFC 6749 § 4.2.2 (`expires_in: integer seconds`).
    """

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    refresh_expires_in: int


class ClientSummary(BaseModel):
    """Riassunto minimal del client per `/me` topbar/sidebar.

    NB: `app.schemas.admin.ClientSummary` ha anche `created_at` per l'uso
    nella lista admin. Vedi note in cima al file sul perché due copie.
    """

    id: UUID
    name: str
    slug: str
    status: str


class MeResponse(BaseModel):
    id: UUID
    email: str
    role: str
    is_active: bool
    client_id: UUID | None
    client: ClientSummary | None


class InvitationPreviewResponse(BaseModel):
    """Body di GET /api/v1/auth/invitation/{token} (S6).

    Ritornato SOLO se l'invitation è in stato pending valido. Per qualsiasi
    altro stato (scaduto/accettato/revocato/non esistente) il backend
    risponde 404 generico — no information disclosure (ADR-0007 §3).

    `role` è limitato a `client_admin | client_member` dal CHECK constraint
    su `invitations.role` (defense-in-depth: un super_admin via invitation
    sarebbe un escalation path indesiderato).
    """

    email: str
    role: Literal["client_admin", "client_member"]
    client_name: str
    expires_at: datetime
