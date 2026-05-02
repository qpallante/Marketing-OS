from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin
from app.models.user import UserRole


class Invitation(UUIDPKMixin, CreatedAtMixin, Base):
    """Pending invitations per onboarding di nuovi client_admin/client_member.

    Token plaintext **mai storato in DB**: solo SHA-256 hash (`token_hash`).
    Il plaintext è generato server-side, ritornato nel response 201 della
    creazione, e mostrato al super_admin nell'UI per condividerlo manualmente
    col destinatario (S5: skip SMTP).

    Stato dell'invito è derivato dai timestamp:
      - pending  : accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now()
      - expired  : accepted_at IS NULL AND expires_at <= now()
      - accepted : accepted_at IS NOT NULL
      - revoked  : revoked_at IS NOT NULL

    Niente enum dedicato per stato → meno coordinamento DB↔app, single source
    of truth nei timestamp. Vedi ADR-0006.
    """

    __tablename__ = "invitations"
    __table_args__ = (
        CheckConstraint(
            "email = lower(email)",
            name="ck_invitations_email_lowercase",
        ),
        CheckConstraint(
            "role IN ('client_admin', 'client_member')",
            name="ck_invitations_role_not_super_admin",
        ),
    )

    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(UserRole, nullable=False)
    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
    )
    invited_by_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
