from sqlalchemy import String
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin

ClientStatus = ENUM(
    "active",
    "paused",
    "archived",
    name="client_status",
    create_type=False,
)


class Client(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "clients"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(
        ClientStatus,
        nullable=False,
        server_default="active",
    )
