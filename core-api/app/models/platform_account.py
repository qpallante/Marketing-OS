from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin

Platform = ENUM(
    "meta",
    "tiktok",
    "google",
    "instagram",
    name="platform",
    create_type=False,
)

PlatformAccountStatus = ENUM(
    "connected",
    "disconnected",
    "error",
    name="platform_account_status",
    create_type=False,
)


class PlatformAccount(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "platform_accounts"
    __table_args__ = (
        UniqueConstraint(
            "platform",
            "account_external_id",
            name="uq_platform_accounts_platform_external_id",
        ),
        Index(
            "ix_platform_accounts_client_id_platform",
            "client_id",
            "platform",
        ),
    )

    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    platform: Mapped[str] = mapped_column(Platform, nullable=False)
    account_external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    account_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    credentials_vault_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        PlatformAccountStatus,
        nullable=False,
        server_default="disconnected",
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
