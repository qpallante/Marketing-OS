from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin


class AuditLog(UUIDPKMixin, CreatedAtMixin, Base):
    """Append-only log of relevant actions.

    No `updated_at` (immutable). RLS blocks UPDATE/DELETE — only INSERT allowed.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index(
            "ix_audit_log_client_id_created_at",
            "client_id",
            text("created_at DESC"),
        ),
        Index(
            "ix_audit_log_user_id_created_at",
            "user_id",
            text("created_at DESC"),
        ),
        Index(
            "ix_audit_log_action_created_at",
            "action",
            text("created_at DESC"),
        ),
    )

    user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    client_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resource_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
