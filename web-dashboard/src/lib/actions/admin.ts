"use server";

import "server-only";

import { revalidatePath } from "next/cache";

import { backendFetch } from "@/lib/api/server-fetch";
import type { CreateClientResponse } from "@/lib/types";

/**
 * Server Action per super_admin: creazione nuovo client + invitation per il
 * primo `client_admin`. Mappata su POST `/api/v1/admin/clients` (vedi
 * `core-api/app/routers/admin.py`).
 *
 * **Pattern consolidato S5**:
 *  - Validazione client-side per UX immediata (HTML attrs, Tailwind feedback)
 *  - Validazione **server-side autorevole** qui: i Client Component possono
 *    essere bypassati con curl
 *  - Mapping errori backend → messaggi italiani user-friendly (no leak di
 *    detail tecnici, ma differenziazione 401/403/409/422/5xx perché ognuno
 *    suggerisce all'utente un'azione diversa)
 *
 * **Successo**: NON facciamo `redirect()` — il super_admin DEVE vedere e
 * copiare `invitation.invitation_url` (plaintext token presente solo in
 * questa response, non recuperabile dopo). La form mostra uno stato success
 * inline con bottone "Copia link". Vedi ADR-0006.
 */

export interface CreateClientFormState {
  /** Errore generico (sopra il form). Mutuamente esclusivo con `success`. */
  error?: string;
  /** Errori per-campo (mostrati sotto il rispettivo input). */
  fieldErrors?: { name?: string; slug?: string; admin_email?: string };
  /** Body 201 Created: contiene `invitation.invitation_url` da copiare. */
  success?: CreateClientResponse;
}

const SLUG_PATTERN = /^[a-z0-9]+(-[a-z0-9]+)*$/;

/**
 * Logging server-side strutturato JSON. Niente PII: email loggata solo come
 * SHA-256 hex (lo facciamo lato backend); qui ci limitiamo a action + esito.
 */
function logEvent(payload: Record<string, unknown>): void {
  console.info(JSON.stringify({ ...payload, ts: new Date().toISOString() }));
}

export async function createClientAction(
  _prev: CreateClientFormState,
  formData: FormData,
): Promise<CreateClientFormState> {
  const name = String(formData.get("name") ?? "").trim();
  const slug = String(formData.get("slug") ?? "").trim();
  const adminEmail = String(formData.get("admin_email") ?? "")
    .trim()
    .toLowerCase();

  // ─── Validazione server-side ─────────────────────────────────────────────
  const fieldErrors: NonNullable<CreateClientFormState["fieldErrors"]> = {};
  if (name.length < 2 || name.length > 120) {
    fieldErrors.name = "Il nome deve avere tra 2 e 120 caratteri";
  }
  if (!SLUG_PATTERN.test(slug)) {
    fieldErrors.slug = "Solo lettere minuscole, numeri e trattini (es. mio-cliente)";
  } else if (slug.length < 2 || slug.length > 100) {
    fieldErrors.slug = "Lo slug deve avere tra 2 e 100 caratteri";
  }
  if (!adminEmail || !adminEmail.includes("@")) {
    fieldErrors.admin_email = "Inserisci un'email valida";
  }
  if (Object.keys(fieldErrors).length > 0) {
    return { fieldErrors };
  }

  // ─── Chiamata backend ────────────────────────────────────────────────────
  let response: Response;
  try {
    response = await backendFetch("/api/v1/admin/clients", {
      method: "POST",
      body: { name, slug, admin_email: adminEmail },
    });
  } catch {
    logEvent({ action: "create_client", success: false, reason: "network_error" });
    return { error: "Errore di connessione, riprova" };
  }

  // ─── Mapping status → messaggio ──────────────────────────────────────────
  // Differenziamo: ogni 4xx suggerisce un'azione diversa per l'utente.
  if (response.status === 401) {
    logEvent({ action: "create_client", success: false, reason: "unauthorized" });
    return { error: "Sessione scaduta, ricarica la pagina per fare login" };
  }
  if (response.status === 403) {
    logEvent({ action: "create_client", success: false, reason: "forbidden" });
    return { error: "Non hai i permessi per creare un client" };
  }
  if (response.status === 409) {
    // Backend ritorna `detail: "email already in use"` o `detail: "slug already exists"`.
    // Usiamo il detail per attribuire il messaggio al campo giusto, senza
    // leak: i due valori possibili sono noti e benigni.
    let detail = "";
    try {
      const body = (await response.json()) as { detail?: unknown };
      detail = typeof body.detail === "string" ? body.detail : "";
    } catch {
      // ignora parse error: cadiamo sul messaggio generico sotto
    }
    logEvent({ action: "create_client", success: false, reason: "conflict", detail });
    if (detail.includes("email")) {
      return { fieldErrors: { admin_email: "Questa email è già registrata" } };
    }
    if (detail.includes("slug")) {
      return { fieldErrors: { slug: "Questo slug è già in uso" } };
    }
    return { error: "Conflitto: nome o email già in uso" };
  }
  if (response.status === 422) {
    logEvent({ action: "create_client", success: false, reason: "validation" });
    return { error: "Dati non validi, controlla i campi e riprova" };
  }
  if (!response.ok) {
    logEvent({
      action: "create_client",
      success: false,
      reason: `http_${response.status}`,
    });
    return { error: "Errore del server, riprova tra qualche istante" };
  }

  // ─── Parse 201 Created ───────────────────────────────────────────────────
  let data: CreateClientResponse;
  try {
    data = (await response.json()) as CreateClientResponse;
  } catch {
    logEvent({ action: "create_client", success: false, reason: "invalid_response" });
    return { error: "Errore del server, riprova tra qualche istante" };
  }

  logEvent({
    action: "create_client",
    success: true,
    client_id: data.client.id,
    slug: data.client.slug,
    invitation_id: data.invitation.id,
  });

  // Invalida la cache della lista clients: la prossima visita a
  // /admin/clients rifetcherà dal backend includendo il nuovo record.
  revalidatePath("/admin/clients");

  // NB: niente redirect. Il super_admin deve vedere `invitation_url` qui:
  // dopo la response, il plaintext non è più recuperabile (in DB c'è solo
  // SHA-256). Vedi ADR-0006 §"Invitation token strategy".
  return { success: data };
}
