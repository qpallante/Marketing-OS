"""SQLAlchemy models per le 4 tabelle Brand Brain (S7).

Tabelle create dalla migration `0004_brand_brain.py`. RLS policies in
`supabase/policies/006_brand_brain.sql`. Vedi ADR-0008.

**Embedding column su `BrandChunk` NON è dichiarata** in SQLAlchemy:
- Il tipo `vector(1536)` di pgvector non è nativo SQLAlchemy.
- Niente `pgvector-python` dep (decisione ADR-0008 §AI deps).
- INSERT/SELECT del campo `embedding` usano raw SQL via `text()` con
  cast esplicito `CAST(:emb AS vector)` (lezione di Step 2 — ambiguità
  `:` param marker vs `::` cast operator).
- I model qui esposti coprono tutti gli ALTRI campi via ORM standard;
  il consumer step 6 (indexing pipeline) farà raw SQL solo per la column
  embedding.

**Mixin scelte**:
- `BrandAsset`, `BrandFormData` → `UUIDPKMixin + TimestampMixin` (mutable, both timestamps)
- `BrandChunk`, `BrandGeneration` → `UUIDPKMixin + CreatedAtMixin` (immutable / append-only)
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPKMixin

#: Enum types creati dalla migration 0004 — `create_type=False` perché
#: già presenti in DB. Coerente con pattern S2 (`UserRole`, `ClientStatus`).
BrandIndexingStatus = ENUM(
    "pending",
    "completed",
    "failed",
    name="brand_indexing_status",
    create_type=False,
)
BrandGenerationStatus = ENUM(
    "success",
    "error",
    "cancelled",
    name="brand_generation_status",
    create_type=False,
)


class BrandAsset(UUIDPKMixin, TimestampMixin, Base):
    """Brand asset caricato dal client (PDF / plain text snippet)."""

    __tablename__ = "brand_assets"
    __table_args__ = (
        CheckConstraint(
            "source_kind IN ('pdf', 'text')",
            name="ck_brand_assets_source_kind",
        ),
        CheckConstraint(
            "char_length(file_sha256) = 64",
            name="ck_brand_assets_sha256_format",
        ),
    )

    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    indexing_status: Mapped[str] = mapped_column(
        BrandIndexingStatus,
        nullable=False,
        server_default="pending",
    )
    indexing_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunks_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )


class BrandChunk(UUIDPKMixin, CreatedAtMixin, Base):
    """Chunk immutable estratto da un `BrandAsset`. `embedding` NON dichiarato."""

    __tablename__ = "brand_chunks"

    asset_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("brand_assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: client_id denormalizzato per RLS efficiency (no JOIN con brand_assets
    #: nelle policy). Vedi ADR-0008.
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    # `embedding vector(1536)`: NON dichiarato. Usa raw SQL via text() con
    # `CAST(:emb AS vector)` per INSERT/SELECT. Vedi docstring modulo.
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)


class BrandFormData(UUIDPKMixin, TimestampMixin, Base):
    """Tone-of-voice + visual identity per un client. UNIQUE(client_id)."""

    __tablename__ = "brand_form_data"

    #: UNIQUE constraint imposta 1 form per client. Upsert via ON CONFLICT.
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    tone_keywords: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    )
    dos: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    )
    donts: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    )
    colors_hex: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    )


class BrandGeneration(UUIDPKMixin, CreatedAtMixin, Base):
    """Append-only log delle generazioni RAG (pattern audit_log)."""

    __tablename__ = "brand_generations"
    __table_args__ = (
        CheckConstraint(
            "tokens_input >= 0",
            name="ck_brand_generations_tokens_input_non_negative",
        ),
        CheckConstraint(
            "tokens_output >= 0",
            name="ck_brand_generations_tokens_output_non_negative",
        ),
        CheckConstraint(
            "latency_ms >= 0",
            name="ck_brand_generations_latency_ms_non_negative",
        ),
    )

    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    requested_by_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    user_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    output_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: JSONB snapshot dei chunks usati: [{asset_id, chunk_index, similarity, asset_filename}, ...]
    retrieved_chunks: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    model_used: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(BrandGenerationStatus, nullable=False)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_input: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    tokens_output: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    latency_ms: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )

    # NB: niente updated_at (CreatedAtMixin only). Append-only by design —
    # niente policy FOR UPDATE in 006_brand_brain.sql + REVOKE UPDATE/DELETE
    # dal role authenticated. Defense-in-depth.
