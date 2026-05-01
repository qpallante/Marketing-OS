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
 *    che chiama il backend `/api/v1/auth/me` e gestisce il 401
 *  - "doppia rete di sicurezza": proxy veloce blocca anonimi prima ancora
 *    del rendering; layout fa il check serio + cleanup cookie su 401
 */

/**
 * Path che richiedono autenticazione. NB: solo 4, **non 5** — `/campaigns`
 * è esclusa intenzionalmente. Sarà parte del modulo "Performance" più
 * strutturato (Sessioni 9-10), e definire ora un naming `/campaigns`
 * sarebbe lock-in prematuro. Vedi `TODO.md`.
 */
const PROTECTED_PREFIXES = ["/dashboard", "/content", "/analytics", "/settings"];

function isProtected(pathname: string): boolean {
  return PROTECTED_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

export function proxy(req: NextRequest): NextResponse {
  const pathname = req.nextUrl.pathname;

  if (!isProtected(pathname)) {
    return NextResponse.next();
  }

  if (req.cookies.has("access_token")) {
    return NextResponse.next();
  }

  console.info(
    JSON.stringify({
      action: "auth_redirect",
      from: pathname,
      to: "/login",
      ts: new Date().toISOString(),
    }),
  );

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
 *  - `api`: TUTTE le Route Handlers (incluso `/api/auth/clear` di step 2)
 *
 * **L'esclusione di `/api` è critica**: `/api/auth/clear` viene chiamato per
 * cancellare cookie auth (es. dopo un 401 dal backend). Se il proxy lo
 * intercettasse e il cookie fosse assente o scaduto, potremmo finire in un
 * loop di redirect (clear → login → ... → clear). I Route Handlers sono
 * endpoint "infrastrutturali" e gestiscono la propria authz se serve
 * (per-handler), non via proxy globale.
 */
export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|api).*)"],
};
