# ADR-0004 — Frontend authentication strategy (Next.js BFF)

- **Status**: Accepted
- **Date**: 2026-05-01
- **Authors**: Quintino Pallante

## Context

Sessione 4 implementa il flow di autenticazione del `web-dashboard` Next.js 16 contro il backend `core-api` (FastAPI) di Sessione 3. Vincoli e premesse:

- **Backend già definito**: emette JWT bearer (HS256), endpoint `POST /api/v1/auth/login`, `POST /api/v1/auth/refresh`, `GET /api/v1/auth/me`. ADR-0003 documenta il modello server-side. Il frontend è un *consumer*, non re-implementa nulla.
- **Multi-tenant**: il backend usa RLS via `current_setting('app.current_client_id')` (ADR-0002). Il frontend deve solo passare il bearer token; la separazione tenant è enforced backend-side.
- **Single-app per ora**: solo questo `web-dashboard` consuma `core-api`. Niente mobile / partner / public client. Quando arriveranno (S6+ per OAuth Meta consumer-side, mai per pubblici), valuteremo CORS + RS256.
- **Boring tech bias** (CLAUDE.md): nessuna libreria nuova se non strettamente necessaria. shadcn/ui + Tailwind v4 + Next 16 base + `server-only` package. Stop.

## Decision

**Pattern BFF (Backend-for-Frontend) con cookie httpOnly**. Il browser parla **solo** con Next.js; Next.js parla server-to-server con `core-api`. Il JWT non lascia mai il server-side stack.

### 1. JWT storage: cookie `httpOnly` via Server Action

```
loginAction (Server Action)
  → POST core-api /api/v1/auth/login
  → response { access_token, refresh_token, expires_in, refresh_expires_in }
  → cookies().set("access_token",  ..., { httpOnly: true, secure: PROD,
                                          sameSite: "lax", path: "/",
                                          maxAge: expires_in })
  → cookies().set("refresh_token", ..., { ..., maxAge: refresh_expires_in })
  → redirect(next)
```

- `httpOnly: true` → token invisibile a JS client (anti-XSS)
- `secure: NODE_ENV === "production"` → su HTTP localhost in dev, il browser scarta cookies con flag Secure → gated su env. In prod (HTTPS via Vercel), Secure obbligatorio.
- `sameSite: "lax"` → cookie inviato su navigation (link/form) ma non su POST cross-site → mitigazione CSRF
- `path: "/"` → cookie disponibile a tutti i path
- `maxAge` → allineato al TTL del token (no cookie zombie post-expiry)

**Alternative scartate**:
- **localStorage**: vulnerabile XSS (qualunque script third-party legge il token). No.
- **sessionStorage**: idem + perdi sessione al chiudere tab. No.

### 2. API client: `fetch` nativo + manual TS types

```ts
// src/lib/api/server-fetch.ts (server-only)
export async function backendFetch(path, options = {}) {
  // legge cookie httpOnly via cookies(), aggiunge Bearer header,
  // serializza body JSON, cache: "no-store"
  ...
}
```

- Niente axios / ky / openapi-fetch. `fetch` ha tutto quello che serve.
- Tipi manuali in `src/lib/types.ts` (User, LoginResponse, ClientSummary, ...) in **snake_case** identico al backend (no transformation layer)
- `server-only` package importato in tutti i moduli che leggono cookie / chiamano backend → build-time enforcement della separazione

**Exit strategy**: quando il numero di endpoint backend supera ~10 e i tipi diventano complessi, valutare `openapi-typescript` per generare i types da `/openapi.json`. Per ora 4 endpoint (login, refresh, me + futuri admin) → manuale è OK.

### 3. State management: nessuno globale

- L'**autenticazione** vive nel cookie httpOnly server-side. Nessuno state JS.
- L'**user info** è caricato dal `(dashboard)/layout.tsx` Server Component via `getCurrentUser()` (helper wrapped con `React.cache()`).
- Le **pages dentro il route group** chiamano `getCurrentUser()` di nuovo → cache hit (request-tree memoization) → 0 round-trip aggiuntivi al backend.
- **No Context, no Zustand, no Jotai**. Nessuna libreria di state.
- **No TanStack Query** in S4 (Server Components fanno data fetching).

**Exit strategy**: TanStack Query entrerà quando avremo mutation client-side complesse (es. ottimistic updates su lista campagne) o cache cross-component condivisa (S5+).

### 4. Auth guard: `proxy.ts` presence-only + layout double-check

```
HTTP request
  → src/proxy.ts (Next 16 ex-middleware)
    - matcher: /((?!_next/static|_next/image|favicon.ico|api).*)
    - PROTECTED_PREFIXES = [/dashboard, /content, /analytics, /settings]
    - se path in PROTECTED && cookie absent → redirect /login?next=<path>
    - else → next()
  → (dashboard)/layout.tsx (Server Component)
    - cookie absent (defensive, race condition) → redirect /login
    - getCurrentUser():
      - 200 → render Topbar + Sidebar + children con user prop
      - 401 → redirect /api/auth/clear?to=/login (Route Handler che cancella
              i cookie, dato che Server Components NON possono mutare cookies)
      - network error → render NetworkErrorPage (NO cleanup cookie: blip
                        temporaneo, l'utente può ricaricare)
```

- **Proxy non valida il JWT**: solo presence check. Validation richiederebbe `JWT_SECRET_KEY` qui (duplicato fra core-api e web-dashboard, anti-pattern).
- **Layout fa il check serio** via call al backend. Doppia rete di sicurezza.

**Anti-loop**: il matcher esclude `/api`. Senza questa esclusione, `/api/auth/clear` (chiamato per cleanup post-401) potrebbe entrare in loop di redirect.

### 5. Anti open-redirect su `next=`

`sanitizeNext()` in 3 punti (login page, loginAction, /api/auth/clear):

```ts
function sanitizeNext(next: string): string {
  if (!next.startsWith("/"))    return "/dashboard"; // assoluto fuori dominio
  if (next.startsWith("//"))    return "/dashboard"; // protocol-relative
  if (next.includes("://"))     return "/dashboard"; // URL completo
  return next;
}
```

User non può forzare redirect a `https://evil.com` con `?next=https://evil.com`.

### 6. Logout

```
logoutAction (Server Action)
  → cookies().delete("access_token") + delete("refresh_token")
  → redirect("/login")
```

**NON chiama backend** — modello stateless (ADR-0003). Quando arriverà il claim `tv` (Sessione 5/6), faremo `POST /api/v1/auth/logout` per bumpare `users.token_version` e invalidare i refresh server-side.

## Trade-off

| Scelta | Pro | Contro / Mitigazione |
|---|---|---|
| Cookie httpOnly | Anti-XSS, Server Actions hanno CSRF protection built-in | Server Components non possono mutare cookie → workaround Route Handler `/api/auth/clear` |
| `fetch` + manual types | Zero deps, controllo totale | Manutenzione manuale dei tipi in sync col backend → switch a openapi-typescript se >10 endpoint |
| No global state | Niente complessità, source of truth = cookie + cache helper | TanStack Query forse necessaria per mutation client-side complesse → aggiungeremo on-demand |
| Proxy presence-only | Veloce (no fetch backend nel proxy), no `JWT_SECRET_KEY` duplicato | Cookie scaduto passa il proxy → layout double-check necessario |
| `React.cache()` per `getCurrentUser` | 1 fetch /me per request anche da multipli punti | Cache request-scoped, no cache cross-request → è feature, non bug |
| BFF pattern | No CORS, token mai in JS client | Aggiunge un hop (browser→Next→backend) → marginale, ~ms in dev |

## Da rivalutare quando

- **Refresh token auto-rotation**: per ora se access scade → 401 → redirect login. UX molestia ogni 60 min. Sessione 4-bis: middleware/handler che, su 401 da /me, prova `/refresh` con il `refresh_token` cookie, se OK riemette nuovi cookie e replay della request. Vedi TODO.md.
- **`tv` claim** (token_version su user): permette logout-everywhere. ADR-0003 §"Exit strategy". Sessione 5/6.
- **CORS**: non configurato sul backend perché non serve in BFF. Quando aggiungeremo client mobile o partner integrations, valuteremo.
- **openapi-typescript**: quando endpoint > 10.
- **TanStack Query**: quando mutation client-side complesse arriveranno (S5+).

## Alternatives considered

- **Stack Auth / NextAuth / BetterAuth**: scartate. Aggiungono superficie d'attacco non necessaria, e il modello backend è già definito (ADR-0003) — userebbero pattern duplicati.
- **JWT in localStorage + Authorization header da Client Component**: scartato per XSS. Inoltre richiederebbe CORS sul backend.
- **Cookie ma non httpOnly** (per leggere da JS): non serve, il pattern Server Action evita la lettura JS lato client.
- **Refresh token rotation automatica già in S4**: out-of-scope (vedi vincoli utente). S4-bis.

## References

- [Next.js 16 docs / Authentication](file:web-dashboard/node_modules/next/dist/docs/01-app/02-guides/authentication.md)
- [Next.js 16 docs / Proxy file convention](file:web-dashboard/node_modules/next/dist/docs/01-app/03-api-reference/03-file-conventions/proxy.md) (era `middleware.md` in ≤15)
- ADR-0002 (RLS strategy backend-side)
- ADR-0003 (JWT authentication strategy backend-side)
- `web-dashboard/src/lib/{config,types}.ts`, `src/lib/api/{server-fetch,auth-context}.ts`
- `web-dashboard/src/lib/actions/auth.ts`
- `web-dashboard/src/proxy.ts`
- `web-dashboard/src/app/{login,api/auth/clear,(dashboard)/layout.tsx}`
