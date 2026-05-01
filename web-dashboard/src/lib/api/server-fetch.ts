import "server-only";

import { cookies } from "next/headers";

import { BACKEND_URL } from "@/lib/config";

/**
 * Helper per chiamate HTTP server-side verso il core-api FastAPI.
 *
 * - Lettura cookie `access_token` httpOnly e attach automatico di
 *   `Authorization: Bearer <token>` (disabilitabile con `auth: false`).
 * - Serializza `body` a JSON e setta `Content-Type` se necessario.
 * - `cache: "no-store"` di default — l'API è dinamica, no caching Next.
 *
 * Importazione protetta da `server-only`: se un Client Component prova
 * a importarlo, build fail.
 *
 * Esempio:
 *   const res = await backendFetch("/api/v1/auth/me");
 *   if (res.ok) { const user: User = await res.json(); ... }
 */

export interface BackendFetchOptions extends Omit<RequestInit, "body"> {
  /** Body JSON-serializzabile. Se presente, Content-Type: application/json default. */
  body?: unknown;
  /** Default: true. Se false, non attacca Bearer header (per /login, /refresh). */
  auth?: boolean;
}

export async function backendFetch(
  path: string,
  options: BackendFetchOptions = {},
): Promise<Response> {
  const { body, auth = true, headers: initHeaders, ...rest } = options;
  const headers = new Headers(initHeaders);

  if (auth) {
    const cookieStore = await cookies();
    const token = cookieStore.get("access_token")?.value;
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }
  }

  let serializedBody: BodyInit | undefined;
  if (body !== undefined) {
    serializedBody = JSON.stringify(body);
    if (!headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
  }

  return fetch(`${BACKEND_URL}${path}`, {
    ...rest,
    headers,
    body: serializedBody,
    cache: "no-store",
  });
}
