"""brand_brain schema

Sessione 7 — Brand Brain Foundation.

Crea:
  - extension `vector` (pgvector, idempotent — required per `vector(1536)` column)
  - 2 enum: brand_indexing_status, brand_generation_status
  - 4 tabelle: brand_assets, brand_chunks, brand_form_data, brand_generations
  - vector(1536) column su brand_chunks.embedding (HNSW index in S7 step 2)
  - UNIQUE(client_id, file_sha256) su brand_assets per dedup per-tenant
    (stesso PDF caricato per Monoloco e Nightify resta dual-indexed perché
    sono brand context separati — UNIQUE è (client_id, hash) non solo hash)
  - UNIQUE(client_id) su brand_form_data (1 form per client)
  - FK CASCADE: chunks→assets, all→clients
  - Trigger set_updated_at su tabelle mutabili (brand_assets, brand_form_data)

NB: le RLS policies vivono in `supabase/policies/006_brand_brain.sql`,
applicate separatamente via `scripts/apply_rls.py` dopo questa migration
(vedi pattern S2/S5).

Vedi ADR-0008 per le decisioni di design (chunk size, retrieval, adapter).

Revision ID: 0004_brand_brain
Revises: 0003_invitation_accepted_by
Create Date: 2026-05-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_brand_brain"
down_revision: str | Sequence[str] | None = "0003_invitation_accepted_by"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ─── pgvector extension (idempotent) ─────────────────────────────────
    # Su Supabase l'estensione `vector` è in allow-list; CREATE EXTENSION
    # IF NOT EXISTS funziona via pooler in session mode (postgres role,
    # SUPERUSER per migrations — vedi ADR-0002).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ─── Enums ───────────────────────────────────────────────────────────
    indexing_status = postgresql.ENUM(
        "pending",
        "completed",
        "failed",
        name="brand_indexing_status",
    )
    generation_status = postgresql.ENUM(
        "success",
        "error",
        "cancelled",
        name="brand_generation_status",
    )
    indexing_status.create(op.get_bind())
    generation_status.create(op.get_bind())

    # ─── brand_assets ────────────────────────────────────────────────────
    op.create_table(
        "brand_assets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "client_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        # NULL per source_kind='text' (testo inline, no file storage)
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column(
            "indexing_status",
            postgresql.ENUM(name="brand_indexing_status", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("indexing_detail", sa.Text(), nullable=True),
        sa.Column(
            "chunks_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_brand_assets"),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_brand_assets_client_id_clients",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "client_id",
            "file_sha256",
            name="uq_brand_assets_client_id_file_sha256",
        ),
        sa.CheckConstraint(
            "source_kind IN ('pdf', 'text')",
            name="ck_brand_assets_source_kind",
        ),
        sa.CheckConstraint(
            "char_length(file_sha256) = 64",
            name="ck_brand_assets_sha256_format",
        ),
        sa.CheckConstraint(
            "byte_size > 0",
            name="ck_brand_assets_byte_size_positive",
        ),
        sa.CheckConstraint(
            "chunks_count >= 0",
            name="ck_brand_assets_chunks_count_non_negative",
        ),
    )
    op.create_index("ix_brand_assets_client_id", "brand_assets", ["client_id"])
    op.execute(
        "CREATE TRIGGER trg_brand_assets_updated_at "
        "BEFORE UPDATE ON brand_assets "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()",
    )

    # ─── brand_chunks ────────────────────────────────────────────────────
    # `client_id` denormalizzato (FK CASCADE + RLS efficiency: la policy
    # client_id-scoped non richiede JOIN con brand_assets per ogni row).
    op.create_table(
        "brand_chunks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        # `embedding vector(1536)` aggiunto via op.execute sotto.
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_brand_chunks"),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["brand_assets.id"],
            name="fk_brand_chunks_asset_id_brand_assets",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_brand_chunks_client_id_clients",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "asset_id",
            "chunk_index",
            name="uq_brand_chunks_asset_id_chunk_index",
        ),
        sa.CheckConstraint(
            "chunk_index >= 0",
            name="ck_brand_chunks_chunk_index_non_negative",
        ),
        sa.CheckConstraint(
            "token_count > 0",
            name="ck_brand_chunks_token_count_positive",
        ),
    )
    # `vector(1536)` non è un tipo SQLAlchemy nativo (no `pgvector-python`
    # in deps): aggiungiamo la column via raw SQL. INSERT/SELECT useranno
    # cast esplicito :param::vector. Vedi ADR-0008.
    op.execute(
        "ALTER TABLE brand_chunks ADD COLUMN embedding vector(1536) NOT NULL",
    )
    op.create_index("ix_brand_chunks_asset_id", "brand_chunks", ["asset_id"])
    op.create_index("ix_brand_chunks_client_id", "brand_chunks", ["client_id"])

    # ─── brand_form_data ─────────────────────────────────────────────────
    # 1 form per client (UNIQUE su client_id). Upsert pattern:
    # INSERT ... ON CONFLICT (client_id) DO UPDATE.
    op.create_table(
        "brand_form_data",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "tone_keywords",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "dos",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "donts",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "colors_hex",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_brand_form_data"),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_brand_form_data_client_id_clients",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "client_id",
            name="uq_brand_form_data_client_id",
        ),
    )
    op.execute(
        "CREATE TRIGGER trg_brand_form_data_updated_at "
        "BEFORE UPDATE ON brand_form_data "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()",
    )

    # ─── brand_generations (append-only, pattern audit_log) ──────────────
    # Niente updated_at: il record è immutabile dopo creation. Status set
    # alla creation: success / error / cancelled.
    op.create_table(
        "brand_generations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "requested_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("user_prompt", sa.Text(), nullable=False),
        sa.Column("output_text", sa.Text(), nullable=True),
        # JSONB per i chunks usati: [{asset_id, chunk_index, similarity, asset_filename}]
        sa.Column(
            "retrieved_chunks",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("model_used", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="brand_generation_status", create_type=False),
            nullable=False,
        ),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column(
            "tokens_input",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "tokens_output",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "latency_ms",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_brand_generations"),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_brand_generations_client_id_clients",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name="fk_brand_generations_requested_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "tokens_input >= 0",
            name="ck_brand_generations_tokens_input_non_negative",
        ),
        sa.CheckConstraint(
            "tokens_output >= 0",
            name="ck_brand_generations_tokens_output_non_negative",
        ),
        sa.CheckConstraint(
            "latency_ms >= 0",
            name="ck_brand_generations_latency_ms_non_negative",
        ),
    )
    # Index composito client_id + created_at DESC per `GET /history` efficient.
    op.create_index(
        "ix_brand_generations_client_id_created_at",
        "brand_generations",
        ["client_id", sa.text("created_at DESC")],
    )

    # ─── COMMENT ON TABLE/COLUMN (debug aid in psql/Studio) ──────────────
    op.execute(
        "COMMENT ON TABLE brand_assets IS '"
        "Brand assets caricati dal client (PDF + plain text snippet) per "
        "alimentare RAG. Stato indicizzazione tracciato via indexing_status. "
        "UNIQUE(client_id, file_sha256) per dedup per-tenant. Vedi ADR-0008."
        "';",
    )
    op.execute(
        "COMMENT ON COLUMN brand_assets.file_sha256 IS '"
        "SHA-256 hex dei bytes del file. Permette dedup idempotente all''upload "
        "(stesso file caricato 2 volte → 409). Per source_kind=''text'' è hash "
        "del testo plaintext UTF-8."
        "';",
    )
    op.execute(
        "COMMENT ON COLUMN brand_assets.indexing_status IS '"
        "pending: appena creato, BackgroundTask schedulato. completed: "
        "chunking + embedding OK. failed: errore (vedi indexing_detail)."
        "';",
    )
    op.execute(
        "COMMENT ON TABLE brand_chunks IS '"
        "Chunks immutabili estratti da brand_assets, con embedding vector(1536) "
        "via OpenAI text-embedding-3-small. client_id denormalizzato per RLS "
        "efficiency (no JOIN nelle policy). HNSW index su embedding aggiunto "
        "in migration 0005 (S7 step 2)."
        "';",
    )
    op.execute(
        "COMMENT ON TABLE brand_form_data IS '"
        "Tone-of-voice + do/don''t + brand colors per client. Iniettati nel "
        "system prompt di Claude al momento della query. NON embedded — il "
        "form non genera vettori. UNIQUE(client_id) garantisce 1 form per "
        "client (upsert pattern)."
        "';",
    )
    op.execute(
        "COMMENT ON TABLE brand_generations IS '"
        "Append-only log delle generazioni RAG (pattern audit_log). Conserva "
        "user_prompt + output + retrieved_chunks (JSONB snapshot) + tokens + "
        "latency_ms. Mai UPDATE: niente policy FOR UPDATE → default deny. "
        "DELETE solo via CASCADE su clients."
        "';",
    )
    op.execute(
        "COMMENT ON COLUMN brand_generations.retrieved_chunks IS '"
        "JSONB: [{asset_id, chunk_index, similarity, asset_filename}, ...]. "
        "Snapshot dei chunks usati alla generation, NON una FK live: se "
        "l''asset viene cancellato, i chunks scompaiono ma questo log "
        "mantiene asset_id come UUID orfano (audit core resta intatto). "
        "Snapshot completo del chunk_text deferred a S8+. Vedi ADR-0008 "
        "§Decisione 9 ON-DELETE-CASCADE strategy."
        "';",
    )
    op.execute(
        "COMMENT ON COLUMN brand_generations.latency_ms IS '"
        "End-to-end latency della query (embed + retrieval + LLM call). "
        "Anche early-warning system per provider degradation: spike anomali "
        "→ alerting. Vedi ADR-0008 §Decisione 5."
        "';",
    )


def downgrade() -> None:
    op.drop_table("brand_generations")
    op.drop_table("brand_form_data")
    op.drop_table("brand_chunks")
    op.drop_table("brand_assets")
    op.execute("DROP TYPE IF EXISTS brand_generation_status")
    op.execute("DROP TYPE IF EXISTS brand_indexing_status")
    # Niente DROP EXTENSION — può essere usata da altre tabelle in futuro.
