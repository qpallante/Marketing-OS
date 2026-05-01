# ADR-0005 — Refresh token auto-rotation strategy

- **Status**: Accepted
- **Date**: 2026-05-01
- **Authors**: Quintino Pallante

## Context

Dopo Sessione 3 (auth backend) e Sessione 4 (frontend BFF), l'access token JWT scade dopo 60 minuti (vedi ADR-0003). Senza un meccanismo di auto-rotation, l'utente viene sloggato e deve fare re-login ogni 60 min — UX-molestia inaccettabile.

Il backend espone già `POST /api/v1/auth/refresh` con rotation server-side:
- body: `{refresh_token: str}`
- response: `{access_token, refresh_token, token_type, expires_in, refresh_expires_in}` (stessa shape del login)
- emette **una nuova coppia** access + refresh ad ogni call (rotation built-in, vedi ADR-0003 §4)

La domanda era: come integrare questa rotation lato Next.js BFF in modo trasparente.

## Decision

**PATH B — Route Handler `/api/auth/rotate` centralizzato**, invece di in-place wrapping nel `backendFetch` helper.

### Rationale: perché NON PATH A (in-place in backendFetch)

`backendFetch` viene chiamato da 3 contesti:
- Server Actions (`loginAction`) — `cookies().set()` ✓
- Route Handlers (`/api/auth/clear`) — `cookies().set()` ✓
- **Server Components** (`getCurrentUser` da `(dashboard)/layout.tsx`, `settings/page.tsx`) — `cookies().set()` ✗

**Limit Next.js 16** confermato dai docs ufficiali (`next/dist/docs/01-app/03-api-reference/04-functions/cookies.md` §"Cookie Behavior in Server Components"):

> Setting cookies is not supported during Server Component rendering. To modify cookies, invoke a Server Function from the client or use a Route Handler.
>
> HTTP does not allow setting cookies after streaming starts.

Conseguenza concreta del PATH A:
1. Layout Server Component chiama `getCurrentUser` → backendFetch /me → 401
2. backendFetch tenta rotation → POST /refresh OK
3. backendFetch chiama `cookies().set(...)` → **THROWS in Server Component**
4. Catch silenzioso, replay con nuovo access in Authorization header → 200
5. Response inviata. **Cookie del browser NON aggiornati** (set è fallito).
6. Page load successivo: stesso vecchio access scaduto → rotation di nuovo
7. **3 round-trip permanenti su ogni page load** finché refresh token vale (7 giorni).

Non è "rotation invisibile una tantum", è "rotation forzata su ogni request".

PATH A funziona solo per Server Action / Route Handler context. Sui Server Components — il caso 90%+ del nostro frontend — fallisce silenziosamente.

### Implementation: PATH B

**Route Handler `app/api/auth/rotate/route.ts`** (GET):

```
1. Sanitize ?to= (anti open-redirect, riusa pattern di /api/auth/clear)
2. Read refresh_token cookie (assente → redirect /api/auth/clear?to=/login)
3. POST {BACKEND_URL}/api/v1/auth/refresh con {refresh_token}
4. Success → cookies.set(access, refresh) con stesse opzioni di loginAction
   (httpOnly, secure NODE_ENV, sameSite=lax, path=/, maxAge da expires_in
   / refresh_expires_in), redirect(to)
5. Failure (4xx/5xx/network/parse) → redirect /api/auth/clear?to=/login
```

**Layout `(dashboard)/layout.tsx`** modificato:
```
catch (AuthRequiredError) {
  const currentPath = await getCurrentPathname();  // x-pathname + x-search
  redirect(`/api/auth/rotate?to=${encodeURIComponent(currentPath)}`);
}
```

**Proxy `src/proxy.ts`** modificato per propagare il pathname (e search) ai Server Components via header:
```
nextWithPathname(req):
  requestHeaders.set("x-pathname", req.nextUrl.pathname);
  requestHeaders.set("x-search", req.nextUrl.search);
  return NextResponse.next({ request: { headers: requestHeaders } });
```

(Workaround: Server Components non hanno `usePathname()`, è un hook React Client.)

### Pattern emergente: famiglia `/api/auth/*` per cookie operations

Questo ADR consolida un pattern emerso a Sessione 3-bis e formalizzato qui:

| Route Handler | Sessione | Scopo | Cookie writes |
|---|---|---|---|
| `/api/auth/clear` | S3-bis | Cancella access + refresh, redirige a `?to=` | sì (delete) |
| `/api/auth/rotate` | S4-bis (questo ADR) | Tenta refresh, success → set cookies + redirect, failure → /api/auth/clear | sì (set) |
| _futuro:_ `/api/auth/password-reset/initiate` | S5+ | Genera token reset, invia email | no (just trigger) |
| _futuro:_ `/api/auth/password-reset/complete?token=...` | S5+ | Cambia password, invalida sessions, set new cookies | sì (set) |
| _futuro:_ `/api/auth/email-verification/confirm?token=...` | S5+ | Marca email verificata, set updated cookies se serve | sì (set) |

**Regola del pattern**: ogni operazione che richiede cookie writes (set/delete) e che è triggerata da Server Component / browser navigation va in `/api/auth/*` Route Handler dedicato. **Mai** tentare cookie writes da Server Component direttamente.

I Route Handlers sono inoltre esclusi dal proxy matcher (`/api/...`), evitando loop di redirect e permettendo di funzionare con cookie scaduti/assenti.

## Consequences

### Positive

- **Cookie persistono dopo rotation**: nuovi access + refresh salvati nel browser, page load successivi sono normali.
- **1 hop di redirect una tantum** a expiry, non 3 round-trip permanenti.
- **No mutex / replay / retry budget necessari** rispetto a PATH A: il browser serializza naturalmente la redirect chain (un solo GET ad `/api/auth/rotate` per page load), niente race conditions reali. Confermato da test E2E (4 rotation parallele, no errori, no loop).
- **Pattern coerente** con `/api/auth/clear` esistente.
- **Debuggable**: rotation ha URL proprio, log proprio, testabile in isolamento via curl.
- **Single source of truth** per la logica di rotation: layout / future pages / Server Actions delegano allo stesso Route Handler.

### Negative / Trade-off

- **1 hop redirect visibile** in Network tab (~100-200ms) vs il "magic inline" di PATH A. Accettato — l'overhead è una tantum a expiry, non per request.
- **Tre redirect step a expiry** (page → rotate → page) invece di uno solo. Browser-cached, percepibilmente quasi istantaneo.
- **Dipendenza dal proxy `x-pathname`/`x-search` headers** per preservare il path utente. Se il proxy fosse bypassato (raro), fallback a `/dashboard`.

### Da rivalutare quando

- **Client mobile o external integrations**: questi non beneficiano del BFF pattern. Servirebbe un endpoint backend `POST /api/v1/auth/rotate` che ritorna i token in body (non in cookie) per quei client. Vedi anche TODO.md "CORS sul backend".
- **Refresh token blacklist / `tv` claim** (S5/6): quando aggiungeremo `users.token_version` (ADR-0003 §"Exit strategy"), il rotate handler dovrà controllarlo. Se `tv` non corrisponde, redirect a `/api/auth/clear` (logout-everywhere effettivo). Modifica chirurgica, non architetturale.
- **Rate limiting su `/api/auth/rotate`** (S5+ con Redis): un attaccante con un refresh token potrebbe abusare l'endpoint. Mitigazione attuale: il backend `/refresh` dovrebbe essere rate-limited come `/login` (oggi NON lo è — vedi ADR-0003 §9 e TODO.md).

## Lessons learned

Questo è il **secondo** trap Next.js 16 incontrato durante lo sprint:

1. **Trap #1** (S4): `middleware.ts` rinominato a `proxy.ts` (function name `middleware` → `proxy`, file in `src/` con src/-layout). Documentato in ADR-0004 e CLAUDE.md.
2. **Trap #2** (questo ADR): Server Components NON possono `cookies().set()` durante rendering. Documentato qui.

**Pattern stabilito**: prima di proporre un piano che coinvolge cookies / streaming / Edge runtime / file conventions Next.js, **leggere i docs della major version installata** in `web-dashboard/node_modules/next/dist/docs/`. Non solo nomi di API e convention naming, **anche runtime constraints** (cosa è permesso fare, dove, quando, dal quale contesto).

Il valore concreto stamattina: leggendo `cookies.md` PRIMA di scrivere il codice del PATH A, abbiamo evitato di:
- Implementare un mutex / replay / retry budget che sarebbero stati codice morto
- Spendere tempo a debuggare un PATH A che fallisce silenziosamente
- Pivoting tardivo con codice da rimuovere

Costo della lettura preemptiva docs: ~5 minuti. Costo evitato: probabilmente un'ora di debug + scrittura ADR retrospettivo.

## References

- [Next.js 16 docs / `cookies()` function](file:web-dashboard/node_modules/next/dist/docs/01-app/03-api-reference/04-functions/cookies.md)
- ADR-0003 (JWT auth backend, §4 stateless rotation, §"Exit strategy")
- ADR-0004 (frontend BFF, §1 cookie storage, §"Da rivalutare quando")
- `web-dashboard/src/app/api/auth/{clear,rotate}/route.ts`
- `web-dashboard/src/app/(dashboard)/layout.tsx`
- `web-dashboard/src/proxy.ts`
