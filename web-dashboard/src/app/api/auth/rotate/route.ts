import { cookies } from "next/headers";
import { NextResponse, type NextRequest } from "next/server";

import { backendFetch } from "@/lib/api/server-fetch";
import type { LoginResponse } from "@/lib/types";

/**
 * Route Handler GET /api/auth/rotate?to=<path>
 *
 * Tenta una refresh-token rotation. Esiste come Route Handler dedicato perché
 * **i Server Components in Next.js 15+/16 non possono mutare cookies** (vedi
 * `cookies.md` §"Cookie Behavior in Server Components"). Layout / pagine che
 * ricevono 401 da `/me` redirigono qui invece di tentare la rotation inline.
 *
 * Flow:
 *   1. Sanitize `?to=` (anti open-redirect).
 *   2. Read `refresh_token` cookie. Se assente → redirect /api/auth/clear?to=/login.
 *   3. POST `/api/v1/auth/refresh` con il refresh token.
 *   4. Success: cookies.set() per nuovi access + refresh (stesse options di
 *      loginAction: httpOnly, secure gated NODE_ENV, sameSite=lax, path=/,
 *      maxAge allineato a expires_in / refresh_expires_in), redirect(to).
 *   5. Failure (4xx/5xx/network/parse): redirect /api/auth/clear?to=/login →
 *      cleanup totale + login fresco.
 *
 * Niente retry budget né mutex: il browser serializza naturalmente (un solo
 * GET ad /api/auth/rotate per redirect chain). Il proxy esclude `/api` dal
 * matcher quindi questo handler non si auto-protegge (correttamente — è
 * autenticato dal solo refresh_token cookie).
 *
 * Vedi ADR-0005 per il pattern completo.
 */

const FALLBACK_TO = "/dashboard";
const CLEAR_TO_LOGIN = "/api/auth/clear?to=/login";

function sanitizeTo(to: string | null): string {
  if (!to) return FALLBACK_TO;
  if (!to.startsWith("/")) return FALLBACK_TO;
  if (to.startsWith("//")) return FALLBACK_TO;
  if (to.includes("://")) return FALLBACK_TO;
  return to;
}

/**
 * Logging strutturato JSON. `console.info` per success, `console.warn` per
 * failure → permette filtro per livello quando avremo aggregator centralizzato
 * (S5+: probabilmente pino sul Node.js side).
 */
function logSuccess(payload: Record<string, unknown>): void {
  console.info(JSON.stringify({ ...payload, ts: new Date().toISOString() }));
}

function logFailure(payload: Record<string, unknown>): void {
  console.warn(JSON.stringify({ ...payload, ts: new Date().toISOString() }));
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  const to = sanitizeTo(request.nextUrl.searchParams.get("to"));

  const cookieStore = await cookies();
  const refreshToken = cookieStore.get("refresh_token")?.value;

  if (!refreshToken) {
    logFailure({
      action: "token_rotation",
      success: false,
      reason: "no_refresh_cookie",
      to_path: to,
    });
    return NextResponse.redirect(new URL(CLEAR_TO_LOGIN, request.url));
  }

  // Chiamata backend con auth: false — nessun Authorization header, solo
  // il refresh_token nel body. Coerente con loginAction.
  let response: Response;
  try {
    response = await backendFetch("/api/v1/auth/refresh", {
      method: "POST",
      body: { refresh_token: refreshToken },
      auth: false,
    });
  } catch {
    logFailure({
      action: "token_rotation",
      success: false,
      reason: "network_error",
      to_path: to,
    });
    return NextResponse.redirect(new URL(CLEAR_TO_LOGIN, request.url));
  }

  if (!response.ok) {
    logFailure({
      action: "token_rotation",
      success: false,
      reason: `http_${response.status}`,
      to_path: to,
    });
    return NextResponse.redirect(new URL(CLEAR_TO_LOGIN, request.url));
  }

  let data: LoginResponse;
  try {
    data = (await response.json()) as LoginResponse;
  } catch {
    logFailure({
      action: "token_rotation",
      success: false,
      reason: "parse_failed",
      to_path: to,
    });
    return NextResponse.redirect(new URL(CLEAR_TO_LOGIN, request.url));
  }

  // Set nuovi cookie httpOnly. Stesse options di loginAction (lib/actions/auth.ts).
  // Secure è gated su NODE_ENV: in dev su HTTP localhost il browser scarta
  // i cookies con flag Secure.
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

  logSuccess({
    action: "token_rotation",
    success: true,
    new_access_expires_in: data.expires_in,
    new_refresh_expires_in: data.refresh_expires_in,
    to_path: to,
  });

  return NextResponse.redirect(new URL(to, request.url));
}
