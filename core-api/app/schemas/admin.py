"""Pydantic schemas for admin router (super_admin only).

Estratti da `app/routers/admin.py` per pulizia (>5 schemi distinti).
Auth router (`app/routers/auth.py`) tiene ancora i suoi schemi inline; la
migrazione sarà fatta in S6+ (refactor "schemas centralizzati") quando il
numero di endpoint giustifica la consolidazione.

Vedi ADR-0006 per il pattern famiglia /api/v1/admin/*.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, StringConstraints

# ─── Reusable validators ────────────────────────────────────────────────────

#: Slug client: lowercase alphanumerici + hyphens, no leading/trailing hyphen,
#: no underscores, no spazi. Esempi validi: "monoloco", "nightify-events",
#: "interfibra-2026". Non validi: "Monoloco" (uppercase), "test_slug" (underscore),
#: "-test" / "test-" (hyphen agli estremi).
SlugStr = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$",
        min_length=2,
        max_length=100,
    ),
]

#: Nome client: trimmed, 2-120 char. Permesso whitespace interno e maiuscole.
NameStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=2, max_length=120),
]


# ─── Request schemas ─────────────────────────────────────────────────────────


class CreateClientRequest(BaseModel):
    name: NameStr
    slug: SlugStr
    admin_email: EmailStr


# ─── Response schemas ────────────────────────────────────────────────────────


class ClientSummary(BaseModel):
    id: UUID
    name: str
    slug: str
    status: str
    created_at: datetime


class InvitationSummary(BaseModel):
    id: UUID
    email: str
    role: Literal["client_admin", "client_member"]
    expires_at: datetime
    invitation_url: str


class CreateClientResponse(BaseModel):
    client: ClientSummary
    invitation: InvitationSummary


class ListClientsResponse(BaseModel):
    clients: list[ClientSummary]
