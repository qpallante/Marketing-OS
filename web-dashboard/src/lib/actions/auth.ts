"use server";

import "server-only";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { backendFetch } from "@/lib/api/server-fetch";
import type { LoginResponse } from "@/lib/types";

/**
 * State pattern per `useActionState` (React 19 / Next.js 16).
 *
 * - `error`: errore generico mostrato sopra il form (non legato a un campo)
 * - `fieldErrors`: errori di validazione per-campo (email/password vuoti)
 *
 * Niente leak di dettagli backend. I messaggi sono in italiano user-facing.
 */
export interface LoginFormState {
  error?: string;
  fieldErrors?: { email?: string; password?: string };
}

const FALLBACK_NEXT = "/dashboard";

/**
 * Anti open-redirect: accetta solo path interni (iniziano con `/`, NON `//`,
 * NON contengono `://`). Tutto il resto cade sul fallback `/dashboard`.
 */
function sanitizeNext(next: string): string {
  if (!next.startsWith("/")) return FALLBACK_NEXT;
  if (next.startsWith("//")) return FALLBACK_NEXT;
  if (next.includes("://")) return FALLBACK_NEXT;
  return next;
}

/**
 * Logging server-side strutturato JSON. Nessun framework (structlog Python
 * non esiste in Node). Per ora `console.info`, upgrade a pino/winston in
 * Sessione 5+ se servirà aggregazione.
 *
 * MAI loggare la password. Email loggata in plaintext server-side è OK in
 * Node (i log restano sul server, non come PII trasmessa).
 */
function logEvent(payload: Record<string, unknown>): void {
  console.info(JSON.stringify({ ...payload, ts: new Date().toISOString() }));
}

// ─────────────────────────────────────────────────────────────────────────────
// loginAction
// ─────────────────────────────────────────────────────────────────────────────

export async function loginAction(
  _prev: LoginFormState,
  formData: FormData,
): Promise<LoginFormState> {
  const email = String(formData.get("email") ?? "").trim();
  const password = String(formData.get("password") ?? "");
  const next = sanitizeNext(String(formData.get("next") ?? FALLBACK_NEXT));

  // Field-level validation (server-side: i Client Component possono essere
  // bypassati, validate sempre qui)
  const fieldErrors: NonNullable<LoginFormState["fieldErrors"]> = {};
  if (!email) fieldErrors.email = "Inserisci la tua email";
  if (!password) fieldErrors.password = "Inserisci la password";
  if (fieldErrors.email !== undefined || fieldErrors.password !== undefined) {
    return { fieldErrors };
  }

  // Chiamata backend
  let response: Response;
  try {
    response = await backendFetch("/api/v1/auth/login", {
      method: "POST",
      body: { email, password },
      auth: false, // login: nessun cookie da attaccare
    });
  } catch {
    logEvent({ action: "login_attempt", success: false, reason: "network_error" });
    return { error: "Errore di connessione, riprova" };
  }

  // Mapping response → user-friendly message (no backend detail leak)
  if (response.status === 401) {
    logEvent({ action: "login_attempt", success: false, reason: "invalid_credentials" });
    return { error: "Email o password non corretti" };
  }
  if (response.status === 422) {
    logEvent({ action: "login_attempt", success: false, reason: "validation" });
    return { error: "Inserisci un'email valida" };
  }
  if (!response.ok) {
    logEvent({ action: "login_attempt", success: false, reason: `http_${response.status}` });
    return { error: "Errore di connessione, riprova" };
  }

  let data: LoginResponse;
  try {
    data = (await response.json()) as LoginResponse;
  } catch {
    logEvent({ action: "login_attempt", success: false, reason: "invalid_response" });
    return { error: "Errore di connessione, riprova" };
  }

  // Set cookies httpOnly. Secure è gated su NODE_ENV: in dev su HTTP localhost
  // il browser scarta cookies con flag Secure, quindi disattiviamo. In prod
  // (HTTPS via Vercel/Railway) Secure è obbligatorio.
  const cookieStore = await cookies();
  const isProd = process.env.NODE_ENV === "production";
  const baseOpts = {
    httpOnly: true,
    secure: isProd,
    sameSite: "lax" as const,
    path: "/",
  };
  cookieStore.set("access_token", data.access_token, {
    ...baseOpts,
    maxAge: data.expires_in, // 3600s default
  });
  cookieStore.set("refresh_token", data.refresh_token, {
    ...baseOpts,
    maxAge: data.refresh_expires_in, // 604800s dev / 2592000s prod
  });

  logEvent({ action: "login_attempt", success: true });

  // redirect() throws internally — l'action non torna mai normalmente da qui
  redirect(next);
}

// ─────────────────────────────────────────────────────────────────────────────
// logoutAction
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Logout: cancella cookies access + refresh, redirect a /login.
 *
 * NON chiama il backend. Il modello attuale è stateless (vedi ADR-0003): non
 * c'è nulla da invalidare server-side. Il refresh token nel cookie cancellato
 * scade naturalmente; non revocabile prima dell'expiry. Quando aggiungeremo
 * il claim `tv` (Sessione 5/6) qui faremo POST /api/v1/auth/logout per
 * bumpare `users.token_version` e invalidare immediatamente tutti i refresh
 * attivi di quell'utente.
 *
 * Cancelliamo anche `refresh_token` benché ora non sia ancora usato per la
 * rotation automatica — è già pronto per Sessione 4-bis.
 */
export async function logoutAction(): Promise<void> {
  const cookieStore = await cookies();
  cookieStore.delete("access_token");
  cookieStore.delete("refresh_token");

  logEvent({ action: "logout" });

  redirect("/login");
}
