"""Pydantic schemas per Brand Brain endpoints (S7).

S7 step 5a (questo file): `BrandFormUpsertRequest` + `BrandFormResponse`.
Schemi per upload/list/query/history aggiunti progressivamente in step 5b/5c/6.

NB sui field names: `colors_hex` (non `colors`) — match con DB column
`brand_form_data.colors_hex` (migration 0004). Frontend usa stesso nome
per coerenza.

Vedi ADR-0008 per le decisioni di design (chunk size, retrieval, prompt).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

# ─── Reusable validators ────────────────────────────────────────────────────

#: Lista di tone keywords. Almeno 1 (form vuoto = no brand identity), max 20
#: (limit pragmatico — oltre diventa rumore per il system prompt LLM).
ToneKeywordsList = Annotated[
    list[str],
    Field(
        min_length=1,
        max_length=20,
        description="Es. ['playful', 'confidente', 'premium']. Min 1, max 20.",
    ),
]

#: Hex colors (es. '#FF5733'). Default empty. Validation pattern hex
#: `^#[0-9A-Fa-f]{6}$` aggiunta in S+ (ora accettiamo qualsiasi string).
ColorsHexList = Annotated[
    list[str],
    Field(
        default_factory=list,
        max_length=10,
        description="Hex colors brand. Max 10. Pattern validation in S+.",
    ),
]

#: Bullet list 'do' (cosa fare). Default empty. Max 20 bullets per evitare
#: prompt injection-style overflow del system prompt.
DosList = Annotated[
    list[str],
    Field(
        default_factory=list,
        max_length=20,
        description="Cosa fare nel content. Bullet points. Max 20.",
    ),
]

#: Bullet list 'don't' (cosa evitare). Stesso pattern di `DosList`.
DontsList = Annotated[
    list[str],
    Field(
        default_factory=list,
        max_length=20,
        description="Cosa NON fare nel content. Bullet points. Max 20.",
    ),
]


# ─── Request schemas ─────────────────────────────────────────────────────────


class BrandFormUpsertRequest(BaseModel):
    """Request body per `PUT /api/v1/clients/{client_id}/brand/form`.

    Upsert pattern: crea il form se non esiste per il client, aggiorna se
    esiste (UNIQUE(client_id) constraint a livello DB).
    """

    tone_keywords: ToneKeywordsList
    colors_hex: ColorsHexList
    dos: DosList
    donts: DontsList


# ─── Response schemas ────────────────────────────────────────────────────────


class BrandFormResponse(BaseModel):
    """Body 200 OK di `PUT /brand/form` e `GET /brand/form`.

    `client_id` è incluso per UX (frontend conferma su quale client ha
    salvato — utile per super_admin che switch contesto). `created_at` e
    `updated_at` sempre populati (server_default + trigger `set_updated_at`
    da S2).
    """

    client_id: UUID
    tone_keywords: list[str]
    colors_hex: list[str]
    dos: list[str]
    donts: list[str]
    created_at: datetime
    updated_at: datetime


# ─── Brand assets (S7 5b) ────────────────────────────────────────────────────


class BrandAssetSummary(BaseModel):
    """Body 201/200 di POST /assets/upload, /assets/text, GET /assets list.

    `byte_size`: matches DB column (NON `file_size_bytes` — coerenza DB).
    `indexing_status`: mirror della enum `brand_indexing_status` (migration 0004).
    `indexing_detail`: error message se `failed`, altrimenti NULL.
    """

    id: UUID
    client_id: UUID
    source_kind: Literal["pdf", "text"]
    filename: str
    file_sha256: str
    byte_size: int
    indexing_status: Literal["pending", "completed", "failed"]
    indexing_detail: str | None = None
    chunks_count: int
    created_at: datetime
    updated_at: datetime


class BrandAssetTextCreateRequest(BaseModel):
    """Body di POST /assets/text — crea asset da plain text snippet.

    Per S7 5b.2: indexing inline (chunking + embedding del text content,
    senza file PDF). Filename derivato dal `title` con suffix `.txt`.

    Limit di 500K char è pragmatico — sopra serve segmentazione semantica
    a monte (es. headers Markdown), out-of-scope S7.
    """

    title: Annotated[str, Field(min_length=1, max_length=200)]
    text_content: Annotated[str, Field(min_length=10, max_length=500_000)]


# ─── RAG query (S7 step 6) ───────────────────────────────────────────────────


class BrandQueryRequest(BaseModel):
    """Body di POST /query — RAG generation request."""

    user_prompt: Annotated[str, Field(min_length=1, max_length=10_000)]


class RetrievedChunkInfo(BaseModel):
    """Metadata di un chunk usato nel retrieval. Esposto al frontend per
    audit ("perché Claude ha generato questa caption?") e per analytics.

    NB: NON include `chunk_text` per response size — frontend può
    eventualmente fetchare via futuro `GET /assets/{id}/chunks/{idx}`.
    """

    asset_id: UUID
    asset_filename: str
    chunk_index: int
    similarity: float


class BrandQueryResponse(BaseModel):
    """Body 200 OK di POST /query. Tutti i campi sono salvati in
    `brand_generations` (audit + analytics).

    `form_data_used`: True se il client ha un brand_form_data row
    (iniettato nel system prompt). False se il prompt è solo
    `<brand name="..."><!-- no form data --></brand>` + retrieved chunks.

    `latency_ms`: end-to-end della LLM call (NON include embed + retrieval +
    DB writes). Per analytics di provider degradation.
    """

    generation_id: UUID
    output_text: str
    retrieved_chunks: list[RetrievedChunkInfo]
    form_data_used: bool
    tokens_input: int
    tokens_output: int
    latency_ms: int
    model_used: str


# ─── List/history responses (S7 step 5c) ────────────────────────────────────


class BrandAssetListResponse(BaseModel):
    """Body 200 OK di GET /brand/assets — lista paginata di assets del client.

    `total` è il count GLOBALE (no filter), permette al frontend di mostrare
    "1-50 of 213" pagination. `items` è la slice corrente (limit + offset).
    """

    items: list[BrandAssetSummary]
    total: int


class BrandGenerationSummary(BaseModel):
    """Singolo elemento history di generazioni (response di GET /brand/history).

    `user_prompt` e `output_text` **troncati** per tenere la response leggera
    (history list può avere centinaia di righe). Per il full text di una
    singola generation, future endpoint `GET /history/{generation_id}`
    (out-of-scope S7).

    `model_used`: matches DB column name (NON `model` come da spec utente —
    consistency con `BrandQueryResponse`).

    `retrieved_chunks_count`: derivato da `len(retrieved_chunks)` JSONB list.
    Niente `embedding_tokens` separato in S7 (la `tokens_input` qui è solo
    LLM input). Cost attribution embedding vs LLM separata in S+ se serve
    analytics granulare.

    NB: `retrieved_chunks` JSONB conserva snapshot dei chunks usati alla
    generation; **gli `asset_id` referenziati possono diventare orfani**
    dopo `DELETE /assets/{id}` (history è append-only). Consumer frontend
    deve gestire UI graceful (es. "asset rimosso") quando linka back.
    """

    id: UUID
    client_id: UUID
    user_prompt: str  # truncated 500 char
    output_text: str  # truncated 1000 char
    status: str  # "success" | "error" | "cancelled"
    error_detail: str | None
    model_used: str
    tokens_input: int
    tokens_output: int
    latency_ms: int
    retrieved_chunks_count: int
    created_at: datetime


class BrandHistoryResponse(BaseModel):
    """Body 200 OK di GET /brand/history — lista paginata di generation."""

    items: list[BrandGenerationSummary]
    total: int
