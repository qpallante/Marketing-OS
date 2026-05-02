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

// ─────────────────────────────────────────────────────────────────────────────
// acceptInviteAction (S6)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * State pattern per `useActionState` del form accept-invite.
 *
 * Niente `fieldErrors` qui (a differenza di `LoginFormState`): la confirm
 * password è validata SOLO client-side (tipico pattern), e gli errori
 * server-side sono tutti "globali" (token invalido / scaduto / email
 * already used / network) — non attribuibili a un campo specifico.
 */
export interface AcceptInviteFormState {
  error?: string;
}

/**
 * Server Action per submit del form `/accept-invite`.
 *
 * **Flow**:
 *  1. Validazione server-side autorevole (token len=43, password ≥12 char)
 *  2. POST `/api/v1/auth/accept-invite` con `auth: false` (endpoint pubblico)
 *  3. Mapping errori 404/410/422/409/5xx → messaggi italiani user-friendly.
 *     410 differenziato in 3 sub-cases (expired/already used/revoked) per
 *     UX migliore — il backend popola `detail` distinguibile (vedi ADR-0007
 *     §3: 410 detail differenziato vs GET preview 404 generico).
 *  4. Successo → cookies.set httpOnly identici a `loginAction` (BFF pattern,
 *     ADR-0004) → `redirect("/dashboard")` (Auto-login Opzione A, ADR-0007 §2).
 *
 * **`redirect()` fuori dal try/catch**: throw NEXT_REDIRECT signal che Next.js
 * intercetta. Dentro try/catch verrebbe consumato dal generic catch.
 */
export async function acceptInviteAction(
  _prev: AcceptInviteFormState,
  formData: FormData,
): Promise<AcceptInviteFormState> {
  const token = String(formData.get("token") ?? "");
  const password = String(formData.get("password") ?? "");

  // Server-side validation (defense-in-depth: client può essere bypassato).
  if (token.length !== 43) {
    logEvent({ action: "accept_invite", success: false, reason: "invalid_token_format" });
    return { error: "Token non valido o malformato" };
  }
  if (password.length < 12) {
    logEvent({ action: "accept_invite", success: false, reason: "password_too_short" });
    return { error: "La password deve essere di almeno 12 caratteri" };
  }
  if (password.length > 128) {
    logEvent({ action: "accept_invite", success: false, reason: "password_too_long" });
    return { error: "La password non può superare i 128 caratteri" };
  }

  let response: Response;
  try {
    response = await backendFetch("/api/v1/auth/accept-invite", {
      method: "POST",
      body: { token, password },
      auth: false,
    });
  } catch {
    logEvent({ action: "accept_invite", success: false, reason: "network_error" });
    return { error: "Errore di connessione, riprova" };
  }

  // Mapping non-200 status → messaggi user-friendly.
  if (response.status === 404) {
    logEvent({ action: "accept_invite", success: false, reason: "not_found" });
    return {
      error: "Invito non trovato. Verifica il link o chiedi al super_admin di rigenerarlo.",
    };
  }
  if (response.status === 410) {
    // Backend differenzia 3 sub-cases nel `detail` (S6 ADR-0007 §3):
    //  - "invitation expired"
    //  - "invitation already used"
    //  - "invitation revoked"
    let detail = "";
    try {
      const body = (await response.json()) as { detail?: unknown };
      detail = typeof body.detail === "string" ? body.detail : "";
    } catch {
      // ignora parse error: cadiamo sul messaggio generico sotto
    }
    logEvent({ action: "accept_invite", success: false, reason: "gone", detail });
    if (detail.includes("expired")) {
      return { error: "L'invito è scaduto. Chiedi al super_admin di rigenerarlo." };
    }
    if (detail.includes("already used")) {
      return { error: "L'invito è già stato usato. Vai al login se hai già un account." };
    }
    if (detail.includes("revoked")) {
      return { error: "L'invito è stato revocato. Contatta il super_admin." };
    }
    return { error: "Invito non più valido" };
  }
  if (response.status === 409) {
    logEvent({ action: "accept_invite", success: false, reason: "email_already_exists" });
    return {
      error: "Esiste già un account con questa email. Vai al login.",
    };
  }
  if (response.status === 422) {
    logEvent({ action: "accept_invite", success: false, reason: "validation" });
    return { error: "La password non rispetta i requisiti minimi" };
  }
  if (!response.ok) {
    logEvent({
      action: "accept_invite",
      success: false,
      reason: `http_${response.status}`,
    });
    return { error: "Errore del server, riprova tra qualche istante" };
  }

  // Success path: parse LoginResponse + set cookies + redirect.
  let data: LoginResponse;
  try {
    data = (await response.json()) as LoginResponse;
  } catch {
    logEvent({ action: "accept_invite", success: false, reason: "invalid_response" });
    return { error: "Errore di connessione, riprova" };
  }

  // Stesse opzioni di loginAction (BFF cookie storage, ADR-0004).
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
    maxAge: data.expires_in,
  });
  cookieStore.set("refresh_token", data.refresh_token, {
    ...baseOpts,
    maxAge: data.refresh_expires_in,
  });

  logEvent({ action: "accept_invite", success: true });

  // Auto-login Opzione A: redirect a /dashboard. NB: throw NEXT_REDIRECT —
  // deve stare FUORI dal try/catch sopra, altrimenti il catch lo consuma.
  redirect("/dashboard");
}
