import { NextResponse, type NextRequest } from "next/server";

/**
 * Auth guard via Next.js **Proxy** (l'ex-middleware): redirect a /login se
 * l'utente non ha un cookie `access_token` quando visita un path protetto.
 *
 * Next.js 16 ha rinominato `middleware.ts` → `proxy.ts` (breaking change in
 * v16). La funzione esportata si chiama ora `proxy`. Il runtime non è più
 * `edge` di default, ma `nodejs`. Per noi cambia poco: stessa API request/
 * response, stessi `cookies` helpers.
 *
 * Posizione del file: con `src/` directory layout DEVE stare in `src/proxy.ts`,
 * NON a root del progetto. La convenzione è "stesso livello di `app/` o
 * `pages/`" — se `src/app/` esiste, `src/proxy.ts`.
 *
 * Solo presence check (NON valida il JWT):
 *  - una validazione completa richiederebbe il `JWT_SECRET_KEY` qui
 *    (duplicato fra core-api e web-dashboard, anti-pattern)
 *  - la validazione vera vive nel `(dashboard)/layout.tsx` (Server Component)
 *    che chiama il backend `/api/v1/auth/me` e gestisce il 401 delegando
 *    a `/api/auth/rotate` (rotation tentativo) o `/api/auth/clear` (cleanup)
 *  - "doppia rete di sicurezza": proxy veloce blocca anonimi prima ancora
 *    del rendering; layout fa il check serio
 *
 * **`x-pathname` header propagation**: il proxy aggiunge un header
 * `x-pathname` con il pathname corrente alla request inoltrata. Workaround
 * Next.js: i Server Components NON hanno `usePathname()` (è un hook React,
 * solo Client Components). Layout / page Server Components leggono il path
 * corrente via `headers().get("x-pathname")`. Pattern noto Next.js per
 * propagare info request-level dal proxy ai Server Components.
 */

/**
 * Path che richiedono autenticazione. NB: `/campaigns` è esclusa
 * intenzionalmente. Sarà parte del modulo "Performance" più strutturato
 * (Sessioni 9-10), e definire ora un naming `/campaigns` sarebbe lock-in
 * prematuro. Vedi `TODO.md`.
 *
 * `/admin` aggiunto in S5: il route guard role-based vive in
 * `(dashboard)/admin/layout.tsx` (redirect a /dashboard per non-super_admin),
 * ma per anonimi senza cookie il proxy intercetta prima del rendering.
 */
const PROTECTED_PREFIXES = [
  "/dashboard",
  "/brand-brain",
  "/content",
  "/analytics",
  "/settings",
  "/admin",
];

function isProtected(pathname: string): boolean {
  return PROTECTED_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

/**
 * Crea una response next() con header `x-pathname` e `x-search` aggiunti
 * alla request inoltrata. Da usare ogni volta che il proxy passa al next
 * handler senza redirigere (sia per path protetti con cookie, sia per path
 * pubblici).
 *
 * Due header separati per pulizia semantica:
 *  - `x-pathname`: solo path (es. `/settings`)
 *  - `x-search`: query string completa con `?` iniziale (es. `?tab=integrations`),
 *    o vuota
 *
 * Server Components combinano i due quando serve l'URL completo
 * (es. il layout per redirigere `to=` preservando query string).
 */
function nextWithPathname(req: NextRequest): NextResponse {
  const requestHeaders = new Headers(req.headers);
  requestHeaders.set("x-pathname", req.nextUrl.pathname);
  requestHeaders.set("x-search", req.nextUrl.search);
  return NextResponse.next({ request: { headers: requestHeaders } });
}

export function proxy(req: NextRequest): NextResponse {
  const pathname = req.nextUrl.pathname;

  if (!isProtected(pathname)) {
    // Non protetto: passa avanti, propaga x-pathname per consistency
    // (Server Components su /login potrebbero usarlo per pre-fill, ecc.)
    return nextWithPathname(req);
  }

  if (req.cookies.has("access_token")) {
    return nextWithPathname(req);
  }

  console.info(
    JSON.stringify({
      action: "auth_redirect",
      from: pathname,
      to: "/login",
      ts: new Date().toISOString(),
    }),
  );

  // Sul redirect non aggiungiamo x-pathname: l'URL sta cambiando, il path
  // di destinazione è nuovo. /login leggerà il proprio pathname.
  const url = req.nextUrl.clone();
  url.pathname = "/login";
  url.search = "";
  url.searchParams.set("next", pathname);
  return NextResponse.redirect(url);
}

/**
 * Matcher: applica il proxy a tutti i path **tranne**:
 *  - `_next/static`, `_next/image`: assets statici Next.js (mai protetti)
 *  - `favicon.ico`: file statico
 *  - `api`: TUTTE le Route Handlers (incluso `/api/auth/clear` e
 *    `/api/auth/rotate`)
 *
 * **L'esclusione di `/api` è critica**: `/api/auth/clear` e `/api/auth/rotate`
 * vengono chiamati per gestire cookie auth (cleanup / rotation) e devono
 * funzionare anche con cookie scaduti/assenti. Se il proxy li intercettasse
 * potremmo finire in loop di redirect. I Route Handlers `auth/*` gestiscono
 * la propria logica di authz internamente (presenza refresh_token, ecc.).
 */
export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|api).*)"],
};
