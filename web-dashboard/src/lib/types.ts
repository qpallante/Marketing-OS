/**
 * Tipi autorevoli che riflettono lo schema del core-api FastAPI.
 *
 * Mantenuti **manualmente** in sync col backend (vedi
 * `core-api/app/routers/auth.py` e `core-api/app/models/`). Quando il numero
 * di endpoint cresce, valuteremo `openapi-typescript` per generarli da
 * `/openapi.json` (vedi review Sessione 4).
 *
 * Snake_case nei campi rispecchia lo schema backend — non transformiamo a
 * camelCase per evitare boilerplate al confine. I componenti React
 * accettano i field nel loro formato nativo.
 */

// ─── Enum types (backend Postgres enums) ─────────────────────────────────────

export type UserRole = "super_admin" | "client_admin" | "client_member";
export type ClientStatus = "active" | "paused" | "archived";

// ─── Domain DTOs ─────────────────────────────────────────────────────────────

/**
 * Riassunto di un client. Usato sia da `/api/v1/auth/me` (campo `client`) sia
 * da `/api/v1/admin/clients` (lista). Il backend admin include `created_at`,
 * mentre `/me` non lo serializza — quindi qui è opzionale. Vedi
 * `core-api/app/routers/auth.py` (ClientSummary inline) e
 * `core-api/app/schemas/admin.py` (ClientSummary admin).
 */
export interface ClientSummary {
  id: string; // UUID v4
  name: string;
  slug: string;
  status: ClientStatus;
  created_at?: string; // ISO 8601, presente solo da /api/v1/admin/clients
}

export interface User {
  id: string; // UUID v4
  email: string;
  role: UserRole;
  is_active: boolean;
  client_id: string | null; // null per super_admin (cross-tenant)
  client: ClientSummary | null;
}

// ─── Auth responses ──────────────────────────────────────────────────────────

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  expires_in: number; // seconds (60 min default = 3600)
  refresh_expires_in: number; // seconds (7d dev = 604800, 30d prod = 2592000)
}

// ─── Admin DTOs (vedi core-api/app/schemas/admin.py) ────────────────────────

export type InvitationRole = "client_admin" | "client_member";

/**
 * Invitation pending ritornata da POST /api/v1/admin/clients. Il campo
 * `invitation_url` è il **plaintext una-tantum**: dopo la response 201 non è
 * più recuperabile (in DB c'è solo l'hash SHA-256). Il super_admin deve
 * copiarlo subito o l'invitation va revocata + ricreata.
 */
export interface InvitationSummary {
  id: string; // UUID v4
  email: string;
  role: InvitationRole;
  expires_at: string; // ISO 8601
  invitation_url: string; // plaintext token nell'URL — solo nel response 201
}

/** Body 201 Created di POST /api/v1/admin/clients. */
export interface CreateClientResponse {
  client: ClientSummary;
  invitation: InvitationSummary;
}

/** Body 200 OK di GET /api/v1/admin/clients. */
export interface ListClientsResponse {
  clients: ClientSummary[];
}

// ─── Auth — accept invitation flow (S6) ─────────────────────────────────────

/**
 * Body 200 OK di GET /api/v1/auth/invitation/{token} (preview pre-form).
 * Backend ritorna SEMPRE 404 generico per qualsiasi stato invalido (no
 * information disclosure). Vedi ADR-0007 §3.
 */
export interface InvitationPreviewResponse {
  email: string;
  role: InvitationRole;
  client_name: string;
  expires_at: string; // ISO 8601
}

// ─── Brand Brain (S7) ───────────────────────────────────────────────────────

/**
 * Metadata di un chunk usato nel retrieval RAG. Esposto al frontend per audit
 * ("perché Claude ha generato questo testo?") e debug.
 *
 * Backend NON include `chunk_text` per response size (vedi
 * `core-api/app/schemas/brand.py` `RetrievedChunkInfo`). Per il preview del
 * testo serve futuro endpoint `GET /assets/{id}/chunks/{idx}` (out-of-scope S7).
 *
 * `similarity`: cosine similarity 0..1 (più alto = più rilevante). NON
 * `distance` (1 - similarity).
 */
export interface RetrievedChunkInfo {
  asset_id: string;
  asset_filename: string;
  chunk_index: number;
  similarity: number;
}

/**
 * Response 200 OK di `POST /api/v1/clients/{id}/brand/query`.
 *
 * Field naming match esatto col backend (`core-api/app/schemas/brand.py`
 * `BrandQueryResponse`):
 *  - `output_text` (non `text`)
 *  - `model_used` (non `model`)
 *  - **niente** `embedding_tokens` (non separato dai `tokens_input` LLM in S7)
 *
 * `latency_ms`: end-to-end LLM call. NON include embed + retrieval + DB writes.
 */
export interface BrandQueryResponse {
  generation_id: string;
  output_text: string;
  retrieved_chunks: RetrievedChunkInfo[];
  form_data_used: boolean;
  tokens_input: number;
  tokens_output: number;
  latency_ms: number;
  model_used: string;
}

// ─── Error envelope ──────────────────────────────────────────────────────────

/**
 * Forma comune dei 4xx/5xx del core-api (FastAPI default).
 * Per validation 422 il `detail` è un array di field errors, ma per il
 * frontend trattiamo tutto come stringa generica (mappata a messaggi
 * user-friendly nel Server Action).
 */
export interface ApiErrorBody {
  detail: string | unknown;
}
