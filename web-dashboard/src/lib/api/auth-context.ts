import "server-only";

import { cache } from "react";

import { backendFetch } from "@/lib/api/server-fetch";
import type { User } from "@/lib/types";

/**
 * Helper per caricare l'utente corrente dal backend `/api/v1/auth/me`.
 *
 * Wrapped con `React.cache()` → request-scoped memoization: layout + page
 * possono entrambi chiamare `getCurrentUser()` nella stessa request, ma il
 * fetch verso il backend avviene **una sola volta**.
 *
 * Le tre condizioni di fallimento producono eccezioni dedicate per permettere
 * al layout di gestirle distintamente:
 *   - 401 → AuthRequiredError → cleanup cookies + redirect /login
 *   - network / 5xx / parse error → NetworkError → render NetworkErrorPage
 *     (NON cancelliamo i cookie: sarebbe distruttivo per un blip temporaneo)
 */

export class AuthRequiredError extends Error {
  constructor() {
    super("auth_required");
    this.name = "AuthRequiredError";
  }
}

export class NetworkError extends Error {
  constructor(reason: string) {
    super(reason);
    this.name = "NetworkError";
  }
}

export const getCurrentUser = cache(async (): Promise<User> => {
  let res: Response;
  try {
    res = await backendFetch("/api/v1/auth/me");
  } catch {
    throw new NetworkError("fetch_failed");
  }

  if (res.status === 401) {
    throw new AuthRequiredError();
  }
  if (!res.ok) {
    throw new NetworkError(`http_${res.status}`);
  }

  try {
    return (await res.json()) as User;
  } catch {
    throw new NetworkError("parse_failed");
  }
});
