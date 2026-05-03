"use server";

import "server-only";

import { backendFetch } from "@/lib/api/server-fetch";
import type { BrandQueryResponse } from "@/lib/types";

/**
 * Server Action per il Brand Brain RAG query (S7 step 7b).
 *
 * Mappata su `POST /api/v1/clients/{client_id}/brand/query` (vedi
 * `core-api/app/routers/brand.py`). Pattern consolidato S5 (`admin.ts`):
 * Server Action + `backendFetch` con cookie auto-attached. NIENTE BFF route
 * handler perché non serve modificare cookies (i route handlers in
 * `/app/api/auth/*` esistono solo per cookie ops: clear/rotate).
 *
 * Validazione client-side immediata + autorevole server-side qui (i Client
 * Component possono essere bypassati).
 */

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export interface BrandQueryActionState {
  /** Errore generico user-friendly (mutuamente esclusivo con `success`). */
  error?: string;
  /** Body 200 OK: testo generato + chunks usati + tokens/latency. */
  success?: BrandQueryResponse;
}

function logEvent(payload: Record<string, unknown>): void {
  console.info(JSON.stringify({ ...payload, ts: new Date().toISOString() }));
}

export async function runBrandQueryAction(
  clientId: string,
  prompt: string,
): Promise<BrandQueryActionState> {
  const trimmed = prompt.trim();

  // ─── Validazione server-side autorevole ──────────────────────────────────
  if (trimmed.length < 1) {
    return { error: "Inserisci un prompt prima di generare" };
  }
  if (trimmed.length > 10_000) {
    return { error: "Prompt troppo lungo (max 10.000 caratteri)" };
  }
  if (!UUID_V4.test(clientId)) {
    return { error: "client_id non valido" };
  }

  // ─── Chiamata backend ────────────────────────────────────────────────────
  let response: Response;
  try {
    response = await backendFetch(`/api/v1/clients/${clientId}/brand/query`, {
      method: "POST",
      body: { user_prompt: trimmed },
    });
  } catch {
    logEvent({ action: "brand_query", success: false, reason: "network_error" });
    return { error: "Errore di connessione, riprova" };
  }

  // ─── Mapping status → messaggio ──────────────────────────────────────────
  if (response.status === 401) {
    logEvent({ action: "brand_query", success: false, reason: "unauthorized" });
    return { error: "Sessione scaduta, ricarica la pagina" };
  }
  if (response.status === 403) {
    logEvent({ action: "brand_query", success: false, reason: "forbidden" });
    return { error: "Non hai accesso a questo client" };
  }
  if (response.status === 404) {
    // Backend ritorna 404 sia per client inesistente sia per cross-tenant
    // (privacy-by-default — vedi pattern S5+ uniforme).
    logEvent({ action: "brand_query", success: false, reason: "not_found" });
    return { error: "Client non trovato" };
  }
  if (response.status === 422) {
    logEvent({ action: "brand_query", success: false, reason: "validation" });
    return { error: "Prompt non valido, controlla il testo" };
  }
  if (response.status === 502 || response.status === 503 || response.status === 504) {
    logEvent({
      action: "brand_query",
      success: false,
      reason: `provider_unavailable_${response.status}`,
    });
    return {
      error: "Servizio AI temporaneamente non disponibile, riprova tra qualche istante",
    };
  }
  if (!response.ok) {
    logEvent({
      action: "brand_query",
      success: false,
      reason: `http_${response.status}`,
    });
    return { error: "Errore del server, riprova tra qualche istante" };
  }

  // ─── Parse 200 OK ────────────────────────────────────────────────────────
  let data: BrandQueryResponse;
  try {
    data = (await response.json()) as BrandQueryResponse;
  } catch {
    logEvent({ action: "brand_query", success: false, reason: "invalid_response" });
    return { error: "Errore del server, riprova tra qualche istante" };
  }

  logEvent({
    action: "brand_query",
    success: true,
    client_id: clientId,
    generation_id: data.generation_id,
    latency_ms: data.latency_ms,
    chunks: data.retrieved_chunks.length,
    tokens_input: data.tokens_input,
    tokens_output: data.tokens_output,
  });

  return { success: data };
}
