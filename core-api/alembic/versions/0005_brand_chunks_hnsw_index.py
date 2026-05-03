"""brand_chunks HNSW index su embedding

Sessione 7 step 2 — index ANN per cosine similarity retrieval su `brand_chunks`.

HNSW (Hierarchical Navigable Small World) vs IVFFlat:
  - HNSW: approximate, scala meglio, "set it and forget it" (no tuning lists).
  - IVFFlat: exact-ish, richiede tuning `lists` parameter al crescere dei dati.
  - Per Marketing OS (≤10K vectors per client in S7, ~100K previsti S+),
    HNSW è la scelta moderna corretta a tutte le scale.

Operator class: `vector_cosine_ops`. Coerente con la nostra distanza scelta
in ADR-0008 (cosine: misura semantica per text embeddings, normalizzata
indipendente da magnitudine, scelta industry standard per RAG).

Params:
  - `m = 16`: numero di connessioni per nodo HNSW. Default pgvector,
    bilanciamento memory vs recall.
  - `ef_construction = 64`: candidate pool size durante build.
    Default pgvector, recall sufficient per i nostri use-case.

Tuning futuro (S8+ se servirà):
  - Aumentare `m` a 32+ per recall migliore (cost: +memory + slower build).
  - Aumentare `ef_construction` a 128+ per recall su query con bassa similarità.
  - Modificare anche `ef` runtime (`SET LOCAL hnsw.ef_search = 100`) per
    trade-off recall/latency sulle query individuali.

Vedi ADR-0008 §"Decisione 3 — Index type HNSW".

Revision ID: 0005_brand_chunks_hnsw_index
Revises: 0004_brand_brain
Create Date: 2026-05-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_brand_chunks_hnsw_index"
down_revision: str | Sequence[str] | None = "0004_brand_brain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # CREATE INDEX bloccante (no CONCURRENTLY in alembic transactional DDL).
    # Per S7 con 0 rows reali: istantaneo. Per produzione con > 100K vectors,
    # il build richiede secondi-minuti — accettabile in finestra di
    # manutenzione, oppure si può manualmente fare CREATE INDEX CONCURRENTLY
    # fuori da alembic (vedi ADR-0008 §"Index migration in production").
    op.execute(
        "CREATE INDEX ix_brand_chunks_embedding_hnsw "
        "ON brand_chunks "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)",
    )
    op.execute(
        "COMMENT ON INDEX ix_brand_chunks_embedding_hnsw IS '"
        "HNSW ANN index per cosine similarity retrieval (RAG top-K). "
        "Params m=16, ef_construction=64 (default pgvector). "
        "Vedi ADR-0008 §Decisione 3."
        "';",
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_brand_chunks_embedding_hnsw")
