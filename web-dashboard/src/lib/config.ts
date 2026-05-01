import "server-only";

/**
 * Typed environment reader. Server-only: importing from a Client Component
 * fails the build (server-only package).
 *
 * Pattern minimo: nessun framework di env-validation (zod-env, t3-env, ecc.)
 * finché non avremo > 5 env vars. Quando arriveremo, valutiamo zod.
 */

const FALLBACK_BACKEND_URL = "http://localhost:8001";

/**
 * URL base del core-api FastAPI. Letto solo server-side. Mai esposto al
 * browser (no `NEXT_PUBLIC_*`).
 *
 * Default sviluppo: `http://localhost:8001`. In production deve essere
 * settato esplicitamente via env (Vercel / Railway / Doppler).
 */
export const BACKEND_URL: string = process.env.BACKEND_URL ?? FALLBACK_BACKEND_URL;

if (process.env.NODE_ENV === "production" && !process.env.BACKEND_URL) {
  console.warn(
    "[config] BACKEND_URL non impostato in production: fallback a",
    FALLBACK_BACKEND_URL,
  );
}
