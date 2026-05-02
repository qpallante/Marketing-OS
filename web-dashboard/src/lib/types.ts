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
