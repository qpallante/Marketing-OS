# ADR-0008 — Brand Brain Foundation (RAG + indexing)

- **Status**: Accepted
- **Date**: 2026-05-03
- **Authors**: Quintino Pallante

## Context

Sessione 7 introduce il **primo modulo prodotto AI** di Marketing OS dopo 6 sessioni di fondamenta (auth + multi-tenancy + onboarding). Il Brand Brain è il punto di partenza: un sistema di Retrieval-Augmented Generation (RAG) che permette ad ogni client di:

1. Configurare la propria **brand identity** (form: tone, do/don't, colori).
2. Caricare **materiale di riferimento** (PDF + plain text) come fonte autorevole per le generazioni.
3. Indicizzare in modo asincrono il materiale via **embeddings** (vector store su pgvector).
4. Generare contenuti coerenti col brand via **RAG retrieval + LLM** (Claude Sonnet 4.6) con citazione dei chunks usati.
5. Conservare lo **storico append-only** delle generazioni per audit / cost analysis / future fine-tuning data.

Prerequisiti già consolidati nelle sessioni precedenti:

- **Multi-tenant RLS** (ADR-0002): ogni dato è scoped a `client_id`. Nuove 4 tabelle ereditano lo stesso pattern.
- **JWT + role-aware** (ADR-0003): `super_admin` vs `client_admin/member`. La dipendenza `require_client_access` (introdotta in S7) replica la semantica `/admin/*` per il path-param `client_id`.
- **Frontend BFF + Server Actions** (ADR-0004): nessun BFF route handler nuovo per la query — Server Action `runBrandQueryAction` aderisce al pattern S5 (`admin.ts`).

Cosa serviva nuovo in S7 (oggetto delle decisioni qui sotto):

- Schema vettoriale (pgvector + HNSW) e pipeline di indexing async.
- Adapter pattern AI sostituibile (OpenAI embed + Anthropic LLM, con sub-fornitori interscambiabili in futuro).
- Validazione magic-bytes e storage filesystem dedup-safe per gli asset PDF.
- System prompt RAG strutturato per output coerenti.
- Defense-in-depth multi-tenant a 3 layer per il vector store (HTTP + RLS + FK CASCADE).

## Decisions

### 1. Adapter pattern AI via PEP 544 Protocol

**`EmbedderProtocol` + `LLMProtocol`** (`app/core/ai/embedder.py`, `llm.py`) sono `typing.Protocol` con `runtime_checkable=False` (duck typing strutturale, senza ABC). Implementazioni concrete: `OpenAIEmbedder` (`text-embedding-3-small`) e `AnthropicLLM` (Claude Sonnet 4.6). Factory module `app/core/ai/factory.py` espone `get_embedder()` e `get_llm()` con singleton lazy.

**Razionale**: separare il contratto dall'implementazione facilita lo switch di provider (es. self-hosted BGE-large-en in caso di privacy esterni, OpenAI GPT come fallback se Anthropic ha outage). I consumer (`brand_indexing`, `brand_query`) dipendono dal Protocol, mai dalla classe concreta.

**Trade-off**: layer in più rispetto a istanziare direttamente i client SDK. Mitigato dal fatto che la factory è ~30 righe; il valore emerge alla seconda implementazione concreta (S+ quando arriverà multi-provider routing per cost optimization).

### 2. pgvector con HNSW index, separate migration per tunability

**Estensione `vector` su Supabase Postgres 17.6**, colonna `vector(1536)` su `brand_chunks.embedding`, indice **HNSW** con `vector_cosine_ops`, parametri conservativi `m=16, ef_construction=64` (default pgvector). L'indice è in **migration separata 0005** (`0005_brand_chunks_hnsw_index`) per permettere tuning futuro (drop + recreate con `m=32, ef_construction=128` per recall più alto) senza toccare lo schema principale 0004.

**Razionale**: vector retrieval performante self-hosted dentro Postgres, niente Pinecone/Weaviate (cost + complexity). HNSW > IVFFlat per recall a parità di latenza. Cosine similarity > L2 perché OpenAI ridocumenta che gli embeddings sono già normalizzati.

**Trade-off**: ANN approximation può perdere il top-1 esatto (~95% recall su K=5). Accettabile per RAG — la scelta del chunking domina la quality più del recall esatto. Se quality issue emergono in S+, alzare `ef_search` runtime parameter senza re-indexing.

### 3. Token-aware chunking con sliding window

**`chunk_tokens=512`, `overlap_tokens=50`** via tokenizer **`cl100k_base`** (matching OpenAI v3 embeddings). Implementazione in `app/core/ai/chunking.py`: encode → slide window 512 token con 50 di overlap → decode → emit chunk. NON sentence-aware.

**Razionale**: 512 token è il chunk size industry-standard per RAG che bilancia (a) tokens enough per contesto semantico, (b) embedding budget (text-embedding-3-small accetta max 8191 token), (c) Claude context window quando passiamo top-K=5 chunks (~2.5K token totali, lontani dal limite). Overlap evita di spezzare a metà unit semantiche al confine.

**Trade-off**: chunking semantico (Markdown headers, sentence boundary detection) produrrebbe chunk più self-contained ma aumenta la complessità. Deferred a S+ se quality issue emergono empiricamente.

### 4. Indexing pipeline `asyncio.create_task` (NOT `BackgroundTasks`)

**`asyncio.create_task(_index_asset_async(...))`** fire-and-forget dopo l'INSERT della asset row. Lato client una poll utility (`_wait_for_asset_visible` con budget 10s) attende che la background task abbia commit-ato per evitare race read-your-write.

**Razionale**: la prima implementazione usava `FastAPI BackgroundTasks.add_task`. Bug scoperto empiricamente: con bg task che usa `Depends(get_authenticated_session)`, l'asset INSERT della **request** transaction restava invisibile per **>22s** (anche dopo che la request aveva ritornato 201 al client). Root cause sospetto: `BackgroundTasks` runs after response ma prima che la session della request sia rilasciata al pool, e la nuova session aperta dalla bg task reads una snapshot pre-INSERT. **`asyncio.create_task` schedulato post-`await commit()`** evita la race perché parte solo dopo che la transazione è davvero visibile.

**Trade-off**: nessun retry, nessuna persistence-on-shutdown, nessun back-pressure. Se il processo muore mid-indexing l'asset resta `indexing_status='pending'` per sempre (debt: cron di reconciliation per tagging `failed` dopo timeout). Per S7 dev con 1 worker uvicorn è accettabile; **production-grade S8+**: Celery o RQ con retry logic.

**Debt registrato**: investigare root cause `BackgroundTasks` (TODO S7-bis). Se è un bug del nostro pattern di session management, vale la pena di documentare per non rifare l'errore.

### 5. Storage filesystem con dedup SHA-256 + atomic write

**Filesystem dir env-driven** (`BRAND_ASSETS_DIR`, default `storage/brand_assets/`), path `{client_id}/{asset_id}.{ext}`. Dedup via UNIQUE `(client_id, file_sha256)` constraint a livello DB → upload duplicato → 409 `"asset already uploaded for this client (sha256 match)"`. Atomic write: tmp file + `os.rename()` per evitare partial-write visible al lettore.

**Razionale**: niente S3 / Backblaze B2 in S7 — il volume è basso (1-10 PDF per client, ~10-100MB totali per i 3 client interni). Filesystem locale è il "boring tech" che vince. Migrazione a S3 quando volume / multi-instance richiedono persistenza condivisa: il pattern `BrandStorage` (`app/core/brand_storage.py`) è già astratto sulla `Path` interface.

**Trade-off**: file leak su DB constraint failure è gestito con cleanup esplicito. File orfani da operatore manuale (`DROP TABLE`) richiedono garbage collector custom — out of scope.

### 6. RAG system prompt XML-tagged (Anthropic best practice)

**Sistema prompt strutturato in 3 blocchi XML** (`app/core/brand_query.py::build_system_prompt`):

```xml
<brand name="...">
  <tone_of_voice>...</tone_of_voice>
  <dos>...</dos>
  <donts>...</donts>
  <brand_colors>...</brand_colors>
</brand>
<reference_material>
  <chunk source="filename.pdf">...</chunk>
  ...
</reference_material>

REGOLE:
- Non inventare fatti specifici (prezzi, date, eventi, persone, promozioni) ...
- Rispetta i "do" e "don't" del brand.
- Non scrivere i brand colors nel content.
- Se generi più output (es. 3 caption), assicurati che siano DIVERSI tra loro ...
```

**Razionale**: Anthropic docs (§"Use XML tags") documentano che Claude segue gli XML tag più affidabilmente del Markdown. Le 4 regole (anti-hallucination + diversity rule + color non-output + brand consistency) sono frutto di test empirici con i 3 brand interni (Monoloco, Nightify, Interfibra).

**Diversity rule** è la più sottile: senza il vincolo esplicito "DIVERSI tra loro (varia lunghezza, tono leggermente, angle)", Claude produce 3 variazioni quasi identiche di una stessa idea. Verificato empiricamente nel test reale Step 7b (3 caption Monoloco con 3 angle distinti: evocativo / esclusività / musica).

**Trade-off**: prompt verboso (`tokens_input ~500-1000` per query). Cost negligible su Sonnet 4.6 ($3/M input token), ma se mai useremo Haiku per il discriminator/scoring il prompt va dimagrito.

### 7. Defense-in-depth multi-tenant a 3 layer

Per le 4 tabelle Brand Brain (`brand_form_data`, `brand_assets`, `brand_chunks`, `brand_generations`):

| Layer | Implementazione | Cosa protegge |
|---|---|---|
| **HTTP** | `Depends(require_client_access)` su tutti gli endpoint `/api/v1/clients/{client_id}/brand/*` | Cross-tenant access da client_admin con JWT diverso |
| **DB RLS** | `current_setting('app.current_client_id')` + `app.is_super_admin` (ADR-0002 contract) — policies in `supabase/policies/006_brand_brain.sql` | Bypass del HTTP layer (es. SQL injection), bug logico nel handler |
| **FK CASCADE** | `clients` → `brand_*` ON DELETE CASCADE; `brand_assets` → `brand_chunks` ON DELETE CASCADE | Orfani dopo delete client/asset |

**Privacy 404-on-miss**: cross-tenant DELETE asset_id ritorna 404 (non 403), per non rivelare l'esistenza dell'asset di altro tenant. Stesso pattern S5/S6.

**Verifica empirica**: smoke_test_session7.py check **[h]** verifica i 3 layer (Monoloco JWT su `random_uuid` → 403, cross-tenant DELETE → 404 privacy, asset NOT cancellato dopo cross-tenant attempt).

### 8. Append-only `brand_generations` (history immutable)

**Ogni POST /query crea una row** (anche su failure). `retrieved_chunks` è uno **snapshot JSONB** del top-K usato, non FK — i chunks possono essere cancellati post-DELETE asset, ma la history conserva il riferimento orfano (asset_id presente, asset effettivo gone).

**Defense-in-depth privilege**: `REVOKE UPDATE, DELETE, TRUNCATE ON brand_generations FROM authenticated, anon, service_role` (vedi §9). Anche con bug nelle policies RLS, il privilege layer fa short-circuit.

**Razionale**: audit trail + cost analysis (sum tokens × model) + future training data per fine-tuning custom. Mai modificabile retroattivamente — se serve invalidare una generation, aggiungere una flag `superseded_at` (out of scope S7).

**Verifica empirica**: smoke check **[g]** — dopo DELETE asset, `brand_generations.total` resta invariato.

### 9. Supabase default ACL gotcha + REVOKE espliciti

**Scoperta in S7 step 1**: `pg_default_acl` su schema `public` concede `arwdDxtm` (ALL DML) a `authenticated`, `anon`, `service_role` di default per ogni nuova table creata da `postgres`. Conseguenze:

- I `GRANT SELECT, INSERT` espliciti delle policy file di S2-S6 erano **ridondanti** (i privilegi erano già concessi).
- I `REVOKE` mancanti su tabelle "append-only" come `audit_log` (S2) erano **lacune di defense-in-depth** non rilevate.
- ADR-0002 §"audit_log append-only" reclamava "blocco UPDATE/DELETE via RLS + privilege denied", ma in realtà era solo RLS-only.

**Fix S7** (`supabase/policies/006_brand_brain.sql`): `REVOKE UPDATE ON brand_chunks FROM authenticated, anon, service_role` + `REVOKE UPDATE, DELETE, TRUNCATE ON brand_generations`. Verifica post-REVOKE: tentativo UPDATE da role `authenticated` ritorna `permission denied for table brand_generations` (privilege layer fa short-circuit del check RLS).

**Pattern stabilito**: per ogni tabella append-only o immutable, `REVOKE UPDATE/DELETE/TRUNCATE` espliciti dopo i `CREATE POLICY`. Documentare nel commento del file RLS.

**Side effect operativo**: cleanup di test/admin script su tabelle append-only deve usare connection `postgres` (no role swap), non `SET LOCAL ROLE authenticated`. **`alembic_version` table**: stesso pattern — `authenticated` non ha SELECT, query metadata richiede default postgres role (smoke check [k] usa session senza `_set_super_admin_context`).

**Debt retroattivo** (TODO ALTA): replicare il REVOKE su `audit_log` (S2) — vedi `TODO.md §Security debt`.

## Consequences

### Positive

- **MVP Brand Brain end-to-end funzionante**: 7 endpoint REST, 4 tabelle, 41 source files mypy clean, 54 paranoid sub-asserzioni in `smoke_test_session7.py`.
- **Quality output validata**: query reale "3 caption Instagram per Monoloco summer drop" → 3 angle distinti (evocativo / esclusività / musica), latency 4-5s, tokens 460/156, qualità pubblicabile (verificato empiricamente nel Step 7b).
- **Multi-tenant security a 3 layer**: HTTP + RLS + privilege defense, verificato empiricamente nel smoke test.
- **Pattern Adapter AI** pronto per S+ multi-provider (cost optimization Sonnet vs Haiku, fallback OpenAI).
- **Costo dev cumulativo S7**: ~$0.05 (test reali con Anthropic + OpenAI). Una run completa di smoke test ~$0.001.

### Negative

- **Frontend live UI test deferred a S7-bis**: turbopack HMR issue su sessione dev di metà giornata. Backend RAG validato via curl, ma il flow click-Genera nel browser non verificato in S7. Mitigazione: dev restart pulito o porta default 3000.
- **`GET /brand/form` mancante** (solo PUT): serve per S7-bis tab Assets / brand-form pre-populated UI.
- **`asyncio.create_task` no retry**: bg task che muore lascia asset `pending` per sempre. Workaround interim acceptable (nessun retry-storm), production-grade Celery in S8+.
- **`python-magic` non attivo** (libmagic non installato sul Mac dev): MIME validation simplified a `data.startswith(b"%PDF-")`. Sufficiente per PDF allowlist S7, restrittivo per S+ multi-format.
- **Embedding via OpenAI**: i 3 client S7 sono tutti owned da Quintino Pallante (no GDPR concern), ma per client esterni in S+ va documentata clausola in ToS o switch a embedder self-hosted.

### Debt registered (vedi `TODO.md` §S7-bis)

- BackgroundTasks investigation (root cause race con request transaction).
- `libmagic` install + `python-magic` enable (S+ multi-format MIME).
- `audit_log` REVOKE retroattivo (debt da S2, scoperto in S7).
- `GET /brand/form` endpoint.
- Frontend turbopack HMR fix + live UI test S7-bis.
- Pre-flight `_wait_for_asset_visible` polling: candidato per generalizzazione in helper se il pattern ricorre.
- Cron di reconciliation per asset `pending` da >N minuti (dopo crash worker).

## References

- **Migrations**: `core-api/alembic/versions/0004_brand_brain.py`, `0005_brand_chunks_hnsw_index.py`
- **RLS policies**: `supabase/policies/006_brand_brain.sql`
- **AI adapter**: `core-api/app/core/ai/` (embedder.py, llm.py, factory.py, embedder_openai.py, llm_anthropic.py, chunking.py, pdf.py)
- **Pipeline core**: `core-api/app/core/brand_storage.py`, `brand_indexing.py`, `brand_query.py`
- **Schemi + router**: `core-api/app/schemas/brand.py`, `app/routers/brand.py`, `app/models/brand.py`
- **Frontend**: `web-dashboard/src/app/(dashboard)/brand-brain/`, `web-dashboard/src/lib/actions/brand.ts`, `web-dashboard/src/lib/types.ts` (BrandQueryResponse + RetrievedChunkInfo)
- **Smoke test**: `core-api/scripts/smoke_test_session7.py` (54 paranoid checks, costo ~$0.001/run)
- **Related ADRs**: 0001 (stack), 0002 (RLS contract), 0003 (JWT), 0004 (frontend BFF), 0005 (refresh rotation), 0006 (admin clients), 0007 (accept invitation)
