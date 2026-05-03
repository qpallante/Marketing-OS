"""Brand Brain router — `/api/v1/clients/{client_id}/brand/*` (S7).

Famiglia route nuova in S7: client-scoped resources. Pattern emergente
(diverso da `/api/v1/auth/*` e `/api/v1/admin/*`):
- Path include `client_id` esplicito → `require_client_access` dep enforces
  che `user.client_id == client_id` (o `user.role == 'super_admin'`).
- Defense-in-depth con RLS DB-layer (policy `client_id = current_app_client_id()`).

Endpoint S7 progressive:
  - `PUT /form` (questo step 5a)
  - `POST /assets/upload` + `POST /assets/text` (step 5b)
  - `GET /assets` + `DELETE /assets/{asset_id}` (step 5c)
  - `GET /history` (step 5c)
  - `POST /query` (step 6 — RAG)

Vedi ADR-0008 §"Pattern: route family /api/v1/clients/{client_id}/...".
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai.embedder import EmbedderError
from app.core.ai.factory import get_embedder, get_llm
from app.core.ai.llm import LLMError
from app.core.brand_indexing import index_pdf_asset_task, index_text_asset_task
from app.core.brand_query import build_system_prompt
from app.core.brand_storage import (
    FileTooBigError,
    UnsupportedFileTypeError,
    delete_asset_from_filesystem,
    save_asset_to_filesystem,
    validate_and_hash_pdf,
)
from app.core.deps import (
    get_authenticated_session,
    get_current_user,
    require_client_access,
)
from app.models import Client, User
from app.models.brand import BrandAsset, BrandFormData, BrandGeneration
from app.schemas.brand import (
    BrandAssetListResponse,
    BrandAssetSummary,
    BrandAssetTextCreateRequest,
    BrandFormResponse,
    BrandFormUpsertRequest,
    BrandGenerationSummary,
    BrandHistoryResponse,
    BrandQueryRequest,
    BrandQueryResponse,
    RetrievedChunkInfo,
)

log = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/clients/{client_id}/brand",
    tags=["brand"],
)


@router.put(
    "/form",
    response_model=BrandFormResponse,
    summary="Upsert brand tone-of-voice + visual identity for a client",
)
async def upsert_brand_form(
    payload: BrandFormUpsertRequest,
    client_id: Annotated[UUID, Depends(require_client_access)],
    db: Annotated[AsyncSession, Depends(get_authenticated_session)],
) -> BrandFormResponse:
    """Upsert dei dati di brand form per il client.

    Una sola row per client (UNIQUE(client_id) constraint da migration 0004).
    Insert se non esiste, Update se esiste — atomic via Postgres
    `INSERT ... ON CONFLICT (client_id) DO UPDATE`.

    Trigger `set_updated_at` (S2) aggiorna automaticamente `updated_at` su UPDATE.

    NB sul `client_id`: viene da `require_client_access` (path param + auth
    check), passato come parametro alla query. Niente FK existence check
    pre-emptive: se il `client_id` nel path esiste come UUID valido ma NON
    è in `clients` table, l'INSERT fallisce con `ForeignKeyViolation` →
    SQLAlchemy `IntegrityError` → 500 generico. Caso teorico (super_admin
    con UUID arbitrario), accettato per S7 — proper validation in S+ se
    serve UX migliore.
    """
    stmt = (
        pg_insert(BrandFormData)
        .values(
            client_id=client_id,
            tone_keywords=payload.tone_keywords,
            colors_hex=payload.colors_hex,
            dos=payload.dos,
            donts=payload.donts,
        )
        .on_conflict_do_update(
            index_elements=[BrandFormData.client_id],
            set_={
                "tone_keywords": payload.tone_keywords,
                "colors_hex": payload.colors_hex,
                "dos": payload.dos,
                "donts": payload.donts,
                # `updated_at` gestito dal trigger DB su UPDATE — non lo
                # tocchiamo qui per evitare race con il trigger.
            },
        )
        .returning(
            BrandFormData.client_id,
            BrandFormData.tone_keywords,
            BrandFormData.colors_hex,
            BrandFormData.dos,
            BrandFormData.donts,
            BrandFormData.created_at,
            BrandFormData.updated_at,
        )
    )

    row = (await db.execute(stmt)).one()
    await db.flush()  # forza commit anticipato per visibility nei test

    log.info(
        "brand.form_upserted",
        client_id=str(client_id),
        tone_keywords_count=len(payload.tone_keywords),
        colors_count=len(payload.colors_hex),
        dos_count=len(payload.dos),
        donts_count=len(payload.donts),
    )

    return BrandFormResponse(
        client_id=row.client_id,
        tone_keywords=list(row.tone_keywords),
        colors_hex=list(row.colors_hex),
        dos=list(row.dos),
        donts=list(row.donts),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ─── Asset upload (S7 5b.1) ──────────────────────────────────────────────


@router.post(
    "/assets/upload",
    response_model=BrandAssetSummary,
    status_code=status.HTTP_201_CREATED,
    summary="Upload PDF brand asset (5b.1: stage + persist; indexing scheduled in 5b.2)",
    responses={
        409: {"description": "Stesso PDF già caricato per questo client (sha256 match)"},
        413: {"description": "File supera 20 MB"},
        415: {"description": "File non è un PDF valido (magic bytes mismatch)"},
    },
)
async def upload_asset(
    file: Annotated[UploadFile, File(description="PDF file (≤20 MB)")],
    client_id: Annotated[UUID, Depends(require_client_access)],
    db: Annotated[AsyncSession, Depends(get_authenticated_session)],
) -> BrandAssetSummary:
    """Upload PDF asset. Valida + salva su filesystem + INSERT row con
    `indexing_status='pending'`. **Indexing async in 5b.2** (per ora la
    row resta `pending` — frontend dovrà mostrare "in elaborazione").

    Flow:
      1. Read bytes da UploadFile
      2. Validate magic bytes + size + sha256 hash
      3. Pre-check sha256 dedup (UX: 409 immediato, no orphan file)
      4. Generate `asset_id` client-side (no server_default per file path)
      5. Save file FIRST (atomic tmp + rename) at `<client_id>/<asset_id>.pdf`
      6. INSERT brand_assets row
      7. Su `IntegrityError` (race con altra request): cleanup file + 409

    NB: file è salvato PRIMA del DB INSERT per due motivi:
      - Se DB fail → cleanup file (orphan possibile per ~1ms, accettabile)
      - Se file fail → no DB write (atomico zero-side-effect)

    Vedi ADR-0008 §"Decisione 6 — File storage local + atomic write".
    """
    file_bytes = await file.read()

    try:
        sha256, mime = validate_and_hash_pdf(file_bytes)
    except FileTooBigError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(exc),
        ) from exc
    except UnsupportedFileTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(exc),
        ) from exc

    asset_id = uuid.uuid4()

    # Save file FIRST (atomic). Cleanup on DB fail sotto.
    try:
        saved_path = save_asset_to_filesystem(
            client_id=client_id,
            asset_id=asset_id,
            file_bytes=file_bytes,
            extension="pdf",
        )
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"storage write failed: {exc}",
        ) from exc

    new_asset = BrandAsset(
        id=asset_id,
        client_id=client_id,
        source_kind="pdf",
        filename=file.filename or "unknown.pdf",
        file_path=str(saved_path),
        file_sha256=sha256,
        byte_size=len(file_bytes),
        indexing_status="pending",
    )
    db.add(new_asset)

    try:
        await db.flush()
    except IntegrityError as exc:
        # Cleanup orphan file (best-effort)
        try:
            delete_asset_from_filesystem(client_id, asset_id, "pdf")
        except OSError:
            log.warning("brand.cleanup_orphan_failed", asset_id=str(asset_id))
        if "uq_brand_assets_client_id_file_sha256" in str(exc.orig):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="asset already uploaded for this client (sha256 match)",
            ) from exc
        raise

    # Refresh per ottenere created_at/updated_at popolati da server_default
    await db.refresh(new_asset)

    log.info(
        "brand.asset_uploaded",
        asset_id=str(asset_id),
        client_id=str(client_id),
        filename=new_asset.filename,
        byte_size=new_asset.byte_size,
        sha256_prefix=sha256[:16],
        mime=mime,
    )

    # 5b.2: schedule indexing async via `asyncio.create_task`.
    # NB: NON usiamo `BackgroundTasks.add_task`: in S7 testing abbiamo osservato
    # che FastAPI BackgroundTasks scheduling **blocca il commit della request
    # transaction** fino al completamento dei tasks (timing implementation-
    # dependent: bg task runs prima del dep teardown auto-commit). Con
    # `asyncio.create_task` il task gira concurrent in event loop SENZA
    # bloccare il dep teardown, e il `_wait_for_asset_visible` poll gestisce
    # il piccolo race naturale di ~ms. Vedi ADR-0008 §"Indexing race fix".
    asyncio.create_task(  # noqa: RUF006 — fire-and-forget by design
        index_pdf_asset_task(asset_id=new_asset.id, client_id=client_id),
    )

    return BrandAssetSummary(
        id=new_asset.id,
        client_id=new_asset.client_id,
        source_kind=new_asset.source_kind,
        filename=new_asset.filename,
        file_sha256=new_asset.file_sha256,
        byte_size=new_asset.byte_size,
        indexing_status=new_asset.indexing_status,
        indexing_detail=new_asset.indexing_detail,
        chunks_count=new_asset.chunks_count,
        created_at=new_asset.created_at,
        updated_at=new_asset.updated_at,
    )


# ─── Text asset upload (S7 5b.2) ─────────────────────────────────────────


@router.post(
    "/assets/text",
    response_model=BrandAssetSummary,
    status_code=status.HTTP_201_CREATED,
    summary="Create brand asset from plain text snippet (no PDF)",
    responses={
        409: {"description": "Stesso text content già caricato per questo client"},
    },
)
async def upload_text_asset(
    payload: BrandAssetTextCreateRequest,
    client_id: Annotated[UUID, Depends(require_client_access)],
    db: Annotated[AsyncSession, Depends(get_authenticated_session)],
) -> BrandAssetSummary:
    """Crea brand asset da plain text (no PDF parsing).

    Stesso pattern di `upload_asset` (PDF):
      1. Compute sha256 dal text bytes (UTF-8 encoded)
      2. Generate `asset_id` client-side
      3. Save text bytes a filesystem (.txt) — atomic
      4. INSERT brand_assets row con `source_kind='text'`, `indexing_status='pending'`
      5. Schedule `index_text_asset_task` async via `asyncio.create_task`
         (NOT `BackgroundTasks` — vedi nota in `upload_asset` per il rationale)
      6. Su `IntegrityError` (dup sha256+client_id): cleanup file + 409

    Storage `.txt` per consistency con PDF (path layout `<client_id>/<asset_id>.<ext>`).
    Il file `.txt` è ridondante (text già passato al task in memoria), ma garantisce
    re-indexing futuro senza dover passare di nuovo il payload.
    """
    text_bytes = payload.text_content.encode("utf-8")
    sha256 = hashlib.sha256(text_bytes).hexdigest()
    asset_id = uuid.uuid4()

    # Save file FIRST (atomic). Cleanup on DB fail sotto.
    try:
        saved_path = save_asset_to_filesystem(
            client_id=client_id,
            asset_id=asset_id,
            file_bytes=text_bytes,
            extension="txt",
        )
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"storage write failed: {exc}",
        ) from exc

    new_asset = BrandAsset(
        id=asset_id,
        client_id=client_id,
        source_kind="text",
        filename=f"{payload.title}.txt",
        file_path=str(saved_path),
        file_sha256=sha256,
        byte_size=len(text_bytes),
        indexing_status="pending",
    )
    db.add(new_asset)

    try:
        await db.flush()
    except IntegrityError as exc:
        try:
            delete_asset_from_filesystem(client_id, asset_id, "txt")
        except OSError:
            log.warning("brand.cleanup_orphan_failed", asset_id=str(asset_id))
        if "uq_brand_assets_client_id_file_sha256" in str(exc.orig):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="text content already uploaded for this client (sha256 match)",
            ) from exc
        raise

    await db.refresh(new_asset)

    log.info(
        "brand.text_asset_uploaded",
        asset_id=str(asset_id),
        client_id=str(client_id),
        title=payload.title,
        text_length=len(payload.text_content),
        sha256_prefix=sha256[:16],
    )

    # Schedule indexing async via `asyncio.create_task` (NON `BackgroundTasks` —
    # vedi commento in `upload_asset` per il rationale).
    asyncio.create_task(  # noqa: RUF006 — fire-and-forget by design
        index_text_asset_task(
            asset_id=new_asset.id,
            client_id=client_id,
            text_content=payload.text_content,
        ),
    )

    return BrandAssetSummary(
        id=new_asset.id,
        client_id=new_asset.client_id,
        source_kind=new_asset.source_kind,
        filename=new_asset.filename,
        file_sha256=new_asset.file_sha256,
        byte_size=new_asset.byte_size,
        indexing_status=new_asset.indexing_status,
        indexing_detail=new_asset.indexing_detail,
        chunks_count=new_asset.chunks_count,
        created_at=new_asset.created_at,
        updated_at=new_asset.updated_at,
    )


# ─── RAG query (S7 step 6) ───────────────────────────────────────────────


@router.post(
    "/query",
    response_model=BrandQueryResponse,
    status_code=status.HTTP_200_OK,
    summary="RAG query: retrieve top-K brand chunks + generate via Claude",
    responses={
        503: {"description": "Embedder o LLM provider unavailable / error"},
    },
)
async def query_brand_rag(
    payload: BrandQueryRequest,
    client_id: Annotated[UUID, Depends(require_client_access)],
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_authenticated_session)],
) -> BrandQueryResponse:
    """RAG generation pipeline.

    Pipeline:
      1. Lookup `client.name` (per system prompt)
      2. Embed `user_prompt` (OpenAI text-embedding-3-small)
      3. Top-K=5 cosine retrieval su `brand_chunks` (RLS-scoped)
      4. Load `brand_form_data` (optional, graceful degrade se assente)
      5. Build XML-tagged system prompt (`build_system_prompt`)
      6. LLM call (Anthropic Claude Sonnet 4.6)
      7. INSERT `brand_generations` row (audit + analytics)
      8. Return shape con `output_text` + `retrieved_chunks` metadata + tokens

    **Graceful degrade**: client senza assets indicizzati → `retrieved_chunks=[]`.
    Claude genera con solo brand identity (form). Se mancano entrambi (form +
    chunks), output sarà generic.

    **Errori AI**: LLM/Embedder failure → 503. Niente row di
    `brand_generations` persistita su error path (S7 simplicità — error
    tracking + retry budget arrivano in S+). Vedi ADR-0008 §"RAG pipeline".
    """
    # 1. Client name
    client_name = (
        await db.execute(select(Client.name).where(Client.id == client_id))
    ).scalar_one_or_none()
    if client_name is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="client not found",
        )

    # 2. Embed user_prompt
    embedder = get_embedder()
    try:
        embed_result = await embedder.embed(payload.user_prompt)
    except EmbedderError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"embedder error: {exc}",
        ) from exc
    query_vector = embed_result.vectors[0]

    # 3. Top-K=5 cosine retrieval (raw SQL per pgvector <=> operator)
    vec_literal = "[" + ",".join(f"{v:.8f}" for v in query_vector) + "]"
    chunks_rows = (
        await db.execute(
            sa_text(
                "SELECT "
                "  bc.asset_id AS asset_id, "
                "  bc.chunk_index AS chunk_index, "
                "  bc.chunk_text AS chunk_text, "
                "  ba.filename AS asset_filename, "
                "  1 - (bc.embedding <=> CAST(:q AS vector)) AS similarity "
                "FROM brand_chunks bc "
                "JOIN brand_assets ba ON ba.id = bc.asset_id "
                "WHERE bc.client_id = :cid "
                "ORDER BY bc.embedding <=> CAST(:q AS vector) "
                "LIMIT :k",
            ),
            {"q": vec_literal, "cid": str(client_id), "k": 5},
        )
    ).mappings().all()

    # 4. Load form data (optional)
    form_data = (
        await db.execute(
            select(BrandFormData).where(BrandFormData.client_id == client_id),
        )
    ).scalar_one_or_none()

    # 5. Build XML-tagged system prompt
    system_prompt = build_system_prompt(
        client_name=client_name,
        form_data=form_data,
        retrieved_chunks=[dict(r) for r in chunks_rows],
    )

    # 6. LLM call
    llm = get_llm()
    try:
        llm_resp = await llm.generate(
            system=system_prompt,
            user=payload.user_prompt,
            max_output_tokens=2048,
        )
    except LLMError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LLM error: {exc}",
        ) from exc

    # 7. Build retrieved_chunks JSONB snapshot (NO chunk_text — response size)
    retrieved_meta = [
        {
            "asset_id": str(r["asset_id"]),
            "asset_filename": r["asset_filename"],
            "chunk_index": r["chunk_index"],
            "similarity": float(r["similarity"]),
        }
        for r in chunks_rows
    ]

    # 8. INSERT brand_generations (audit + analytics)
    new_gen = BrandGeneration(
        client_id=client_id,
        requested_by_user_id=user.id,
        user_prompt=payload.user_prompt,
        output_text=llm_resp.text,
        retrieved_chunks=retrieved_meta,
        model_used=llm_resp.model,
        status="success",
        tokens_input=llm_resp.tokens_input,
        tokens_output=llm_resp.tokens_output,
        latency_ms=llm_resp.latency_ms,
    )
    db.add(new_gen)
    await db.flush()
    await db.refresh(new_gen)

    log.info(
        "brand.query_succeeded",
        generation_id=str(new_gen.id),
        client_id=str(client_id),
        chunks_retrieved=len(retrieved_meta),
        form_data_used=form_data is not None,
        tokens_input=llm_resp.tokens_input,
        tokens_output=llm_resp.tokens_output,
        latency_ms=llm_resp.latency_ms,
        model=llm_resp.model,
    )

    return BrandQueryResponse(
        generation_id=new_gen.id,
        output_text=llm_resp.text,
        retrieved_chunks=[
            RetrievedChunkInfo(
                asset_id=UUID(r["asset_id"]),
                asset_filename=r["asset_filename"],
                chunk_index=r["chunk_index"],
                similarity=r["similarity"],
            )
            for r in retrieved_meta
        ],
        form_data_used=form_data is not None,
        tokens_input=llm_resp.tokens_input,
        tokens_output=llm_resp.tokens_output,
        latency_ms=llm_resp.latency_ms,
        model_used=llm_resp.model,
    )


# ─── List + delete + history (S7 step 5c) ────────────────────────────────


@router.get(
    "/assets",
    response_model=BrandAssetListResponse,
    summary="List brand assets for a client (paginated)",
)
async def list_brand_assets(
    client_id: Annotated[UUID, Depends(require_client_access)],
    db: Annotated[AsyncSession, Depends(get_authenticated_session)],
    limit: int = 100,
    offset: int = 0,
) -> BrandAssetListResponse:
    """Lista paginata degli assets del client.

    Ordering: `created_at DESC` (più recenti prima — coerente con
    `GET /admin/clients` di S5 e con l'expectation UX dei list endpoint).
    Pagination: `limit` (default 100, capped 200) + `offset` (default 0).

    `total` è il count globale del client (no filter), permette al frontend
    di renderizzare "1-100 of N" pagination metadata.
    """
    capped_limit = min(max(limit, 0), 200)
    safe_offset = max(offset, 0)

    total = (
        await db.execute(
            select(func.count(BrandAsset.id)).where(BrandAsset.client_id == client_id),
        )
    ).scalar_one()

    rows = (
        (
            await db.execute(
                select(BrandAsset)
                .where(BrandAsset.client_id == client_id)
                .order_by(BrandAsset.created_at.desc())
                .limit(capped_limit)
                .offset(safe_offset),
            )
        )
        .scalars()
        .all()
    )

    return BrandAssetListResponse(
        items=[
            BrandAssetSummary(
                id=a.id,
                client_id=a.client_id,
                source_kind=a.source_kind,
                filename=a.filename,
                file_sha256=a.file_sha256,
                byte_size=a.byte_size,
                indexing_status=a.indexing_status,
                indexing_detail=a.indexing_detail,
                chunks_count=a.chunks_count,
                created_at=a.created_at,
                updated_at=a.updated_at,
            )
            for a in rows
        ],
        total=total,
    )


@router.delete(
    "/assets/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete brand asset (DB CASCADE chunks + filesystem cleanup)",
    responses={
        404: {
            "description": (
                "Asset non trovato per questo client "
                "(privacy: stesso 404 anche per cross-tenant)"
            ),
        },
    },
)
async def delete_brand_asset(
    asset_id: UUID,
    client_id: Annotated[UUID, Depends(require_client_access)],
    db: Annotated[AsyncSession, Depends(get_authenticated_session)],
) -> None:
    """Elimina asset (DB row + filesystem file).

    **Cascade**:
      - DB: `ON DELETE CASCADE` da migration 0004 → `brand_chunks` rimossi automaticamente
      - Filesystem: best-effort cleanup del file `.pdf` o `.txt`. Se il file è
        già mancante (cleanup parziale precedente, race), nessun errore.

    **Privacy 404**: il SELECT è scoped a `WHERE id = asset_id AND client_id =
    path_client_id`. Se l'asset esiste ma per un altro client (cross-tenant),
    ritorniamo 404 (non 403): il caller non distingue "non esiste" da
    "esiste ma non è tuo". Pattern coerente con ADR-0007 §3
    "always-404-on-invalid".

    **Filesystem fail policy**: se cleanup file fallisce con OSError (permission,
    disk error), NON facciamo rollback del DB DELETE — la fonte di verità è il
    DB. Il file orfano sarà gestito da cron cleanup (S+). Logghiamo a livello
    error per visibility.
    """
    asset = (
        await db.execute(
            select(BrandAsset)
            .where(BrandAsset.id == asset_id)
            .where(BrandAsset.client_id == client_id),
        )
    ).scalar_one_or_none()

    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="asset not found",
        )

    extension = "pdf" if asset.source_kind == "pdf" else "txt"

    # 1. DELETE DB row (CASCADE su brand_chunks)
    await db.execute(sa_delete(BrandAsset).where(BrandAsset.id == asset_id))
    await db.flush()

    # 2. Best-effort filesystem cleanup
    try:
        deleted = delete_asset_from_filesystem(client_id, asset_id, extension)
        if not deleted:
            log.warning(
                "brand.asset_delete_orphan",
                asset_id=str(asset_id),
                client_id=str(client_id),
                extension=extension,
                detail="file not found on filesystem (already gone or never created)",
            )
    except OSError as exc:
        log.error(
            "brand.asset_delete_filesystem_error",
            asset_id=str(asset_id),
            error=str(exc),
        )

    log.info(
        "brand.asset_deleted",
        asset_id=str(asset_id),
        client_id=str(client_id),
        source_kind=asset.source_kind,
    )
    # 204 No Content (no body)


@router.get(
    "/history",
    response_model=BrandHistoryResponse,
    summary="History of brand RAG generations (paginated, append-only)",
)
async def list_brand_generations(
    client_id: Annotated[UUID, Depends(require_client_access)],
    db: Annotated[AsyncSession, Depends(get_authenticated_session)],
    limit: int = 50,
    offset: int = 0,
) -> BrandHistoryResponse:
    """History delle generation `brand_generations` per il client (append-only).

    Ordering: `created_at DESC` (più recenti prima).
    Pagination: `limit` (default 50, capped 200) + `offset` (default 0).

    `user_prompt` truncated a 500 char, `output_text` a 1000 char per
    response size. Per il full text, `GET /history/{id}` (out-of-scope S7).

    `retrieved_chunks_count`: derivato da `len(retrieved_chunks)` JSONB list.

    **NB**: dopo `DELETE /assets/{id}`, gli `asset_id` referenziati nei
    `retrieved_chunks` di generation passate diventano **orfani**
    (ON DELETE CASCADE su brand_chunks rimuove i chunks ma il JSONB
    snapshot resta in brand_generations). Frontend deve gestire UI
    graceful per asset rimossi. Vedi ADR-0008 §"History append-only".
    """
    capped_limit = min(max(limit, 0), 200)
    safe_offset = max(offset, 0)

    total = (
        await db.execute(
            select(func.count(BrandGeneration.id)).where(
                BrandGeneration.client_id == client_id,
            ),
        )
    ).scalar_one()

    rows = (
        (
            await db.execute(
                select(BrandGeneration)
                .where(BrandGeneration.client_id == client_id)
                .order_by(BrandGeneration.created_at.desc())
                .limit(capped_limit)
                .offset(safe_offset),
            )
        )
        .scalars()
        .all()
    )

    return BrandHistoryResponse(
        items=[
            BrandGenerationSummary(
                id=g.id,
                client_id=g.client_id,
                user_prompt=(g.user_prompt or "")[:500],
                output_text=(g.output_text or "")[:1000],
                status=g.status,
                error_detail=g.error_detail,
                model_used=g.model_used,
                tokens_input=g.tokens_input,
                tokens_output=g.tokens_output,
                latency_ms=g.latency_ms,
                retrieved_chunks_count=len(g.retrieved_chunks or []),
                created_at=g.created_at,
            )
            for g in rows
        ],
        total=total,
    )
