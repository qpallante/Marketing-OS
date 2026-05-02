# Journal — Marketing OS

Diario operativo del progetto. Tono asciutto, onesto. Riporta come si è arrivati alle decisioni, le frizioni reali, ciò che vale ricordare a 6 mesi di distanza. Non duplica codice, ADR o CLAUDE.md.

Entry in ordine cronologico inverso (più recenti in alto). Aggiornamento manuale, vedi sezione *Workflow* in fondo.

---

## 2026-05-02 — Note: porte standardizzate 8001/3001 per progetto

Convenzione di sviluppo locale fissata: backend su `:8001`, frontend su `:3001`. Le porte di default 8000 e 3000 sono occupate da altri progetti dell'utente sulla stessa macchina (es. `:3000` → Vortex Trading System Python). Aggiornati `core-api/.env(.example)`, `web-dashboard/.env.local(.example)`, `web-dashboard/package.json` (script `dev` con `--port 3001`), CLAUDE.md §"Stack tecnologico". Niente `chore` di migrazione: i servizi giravano già su queste porte, abbiamo solo allineato la documentazione e i default.

---

## 2026-05-01 — Sessione 4-bis: Refresh token auto-rotation

**Stato**: chiusa. 3 commit su `origin/main`: feat(auth-frontend), docs(adr) ADR-0005, docs(journal) S4-bis.

### Cosa è stato fatto
- **`app/api/auth/rotate/route.ts`** (NEW): Route Handler GET che tenta rotation. Sanitize `?to=`, read refresh_token cookie (assente → cleanup), POST `/api/v1/auth/refresh`, success → `cookies.set(access, refresh)` con stesse opzioni di `loginAction` + redirect(to), failure (4xx/5xx/network/parse) → `/api/auth/clear?to=/login`. Logging strutturato `console.info`/`console.warn` con `{action, success, reason?, to_path}`.
- **`(dashboard)/layout.tsx`** (MOD): catch `AuthRequiredError` ora redirige a `/api/auth/rotate?to=<encoded>` invece di `/api/auth/clear`. Helper `getCurrentPathname()` legge `x-pathname` + `x-search` headers (workaround Server Components no `usePathname`). Log `auth_rotation_triggered` con `from_path` prima del redirect.
- **`src/proxy.ts`** (MOD): propaga `x-pathname` + `x-search` headers su tutte le request passthrough via `NextResponse.next({ request: { headers } })`. Permette ai Server Components di ricostruire l'URL corrente con query string.
- **ADR-0005**: documenta PATH B vs PATH A, pattern `/api/auth/*` family, lessons learned (Trap Next.js 16 #2: Server Components no cookies().set()).

### Frizioni reali
1. **Trap Next.js 16 #2 — Server Components NON possono mutare cookies**. Scoperto leggendo i docs ufficiali (`cookies.md` §"Cookie Behavior in Server Components") **PRIMA** di scrivere il codice. Citazione testuale: "Setting cookies is not supported during Server Component rendering. To modify cookies, invoke a Server Function from the client or use a Route Handler. HTTP does not allow setting cookies after streaming starts." Il piano iniziale PATH A (rotation in-place dentro `backendFetch`) sarebbe fallito silenziosamente sui Server Components — il `cookies().set()` avrebbe lanciato eccezione, il replay con nuovo token in Authorization header avrebbe avuto successo per la singola request, ma i cookie del browser sarebbero rimasti col vecchio access scaduto → 3 round-trip permanenti su ogni page load post-expiry. Pivot a PATH B (Route Handler `/api/auth/rotate` centralizzato) che funziona ovunque e semplifica il codice (no mutex / replay / retry budget).
2. **Query string preservation richiede header dedicato `x-search`**. Inizialmente proxy propagava solo `x-pathname` (path puro). Test 10 (utente su `/settings?tab=integrations`) avrebbe perso la query: redirect a `/settings` invece di `/settings?tab=integrations`. Aggiunto `x-search` header separato, layout combina i due. Pulizia semantica > unificare in un solo header chiamato comunque "pathname".
3. **Helper `nextWithPathname()` da usare ovunque il proxy fa next()**, anche per path non protetti. Inizialmente lo aggiungevo solo al passthrough con cookie. Ma /login e altri Server Components non protetti potrebbero un domani volerlo. Costo zero, applicato universalmente con doc inline.
4. **Logging structured con livelli (info success / warn failure)**. Il pattern usato finora era sempre `console.info` con campo `success` nel payload. Spec esplicitamente WARN per failures → upgrade a `console.warn` con due helper separati (`logSuccess`/`logFailure`). Permette filtro per livello quando aggiungeremo aggregator (S5+).

### Decisioni rilevanti
Tutte tracciate in [ADR-0005](./infrastructure/docs/architecture/decisions/0005-refresh-token-auto-rotation.md). Punti critici:
- **PATH B over PATH A** per il vincolo Server Component cookie writes.
- **Pattern famiglia `/api/auth/*`** per tutte le cookie operations: clear (S3-bis), rotate (S4-bis), futuri password-reset/email-verification (S5+).
- **No mutex/replay/retry budget**: il browser serializza naturalmente la redirect chain. Test 7 (4 rotation parallele in race) conferma: 4 redirect, no errori, no loop.
- **`x-pathname` + `x-search` proxy headers** come workaround per Server Components senza `usePathname/useSearchParams`.

### Lezioni apprese
- **Validate runtime constraints PRIMA del piano, non solo naming conventions**. ADR-0004 catturava il primo trap Next.js 16 (middleware → proxy rename). Questo è il secondo. Il pattern emerso si consolida: prima di proporre architecture decisions su frontend Next.js, leggere `node_modules/next/dist/docs/` per la versione installata. Non solo "che file si chiama come" ma anche "cosa posso fare in quale contesto". 5 min di lettura preemptiva > 1h di debug e refactoring.
- **PATH B è più semplice di PATH A**: nessun mutex, nessun replay, nessun retry budget. Quando un piano richiede meccanismi compensatori complessi (mutex, retry, fallback chain), spesso significa che il path è sbagliato — il browser/framework potrebbe già fornire la serializzazione naturale via redirect.
- **Server Component → header dal proxy** per propagare info request-level. Pattern Next.js standard: `NextResponse.next({ request: { headers } })`. Utile anche per altri use case futuri (es. propagare locale, feature flags, A/B test bucket).
- **Test 11 (no rotation con cookie validi)** è il "do no harm" check: la modifica non deve attivare rotation in scenario felice. Sempre includerlo nei test E2E.

### Da ricordare
- **Pattern famiglia `/api/auth/*`**: ogni operazione che richiede cookie writes (set/delete) e va triggerata da Server Component o browser navigation → Route Handler dedicato. Mai `cookies().set()` in Server Component.
- **Header `x-pathname` + `x-search`** dal proxy: lo standard del progetto per propagare URL corrente ai Server Components. Documentato in `src/proxy.ts`.
- **Test 10 critical**: ogni feature che fa redirect deve preservare query string originale. Il flusso `/settings?tab=X → rotation → /settings?tab=X` è verificato.
- **Backend `/api/v1/auth/refresh` rotation server-side è IDEMPOTENTE**: 4 call con stesso refresh token funzionano (ognuno emette nuovi token). Quando aggiungeremo `tv` claim (S5/6), i refresh emessi prima del bump diventeranno invalidi.
- **Rate limiting su `/api/auth/rotate`**: TODO post-Redis. Per ora un attaccante con refresh token può abusare l'endpoint, ma il backend `/refresh` stesso non ha rate limit — issue equivalente lato backend (vedi ADR-0003 §9).

### Prossimo
- **Sessione 5**: admin clients endpoints (`POST /api/v1/admin/clients` super_admin only) + onboarding wizard (creare nuovo client, invitare primo client_admin). Userà `require_super_admin` dep + form complessi (forse TanStack Query + react-hook-form qui).
- **Sessione 4-ter (eventuale)** se emergono altri debiti minori frontend (mobile responsive, custom 404, logo brand). Vedi TODO.md.

---

## 2026-05-01 — Sessione 4: Frontend dashboard — login flow + struttura base

**Stato**: chiusa. 3 commit su `origin/main`: `36cf243` feat(frontend), `959db08` docs(adr), + commit 3 docs(journal+todo) che contiene questa entry.

### Cosa è stato fatto
- **API helper + types**: `lib/config.ts` (typed env reader), `lib/types.ts` (User/LoginResponse/ClientSummary/ApiErrorBody, snake_case match backend, no `any`), `lib/api/server-fetch.ts` (`backendFetch` con cookie auth automatico, `cache: "no-store"`), `lib/api/auth-context.ts` (`getCurrentUser` cached con `React.cache()`, `AuthRequiredError`/`NetworkError`).
- **Login flow**: `lib/actions/auth.ts` con `loginAction` Server Action (Zod-less validation, error mapping 401/422/network → messaggi user-friendly italiani, set di 2 cookie httpOnly con `Secure` gated su NODE_ENV) + `logoutAction` (cookie cleanup + redirect, no backend call). `app/login/page.tsx` Server Component (stale token check via `/me`, redirect a `/api/auth/clear` se 401). `app/login/login-form.tsx` Client Component con `useActionState`.
- **Cookie cleanup centralizzato**: `app/api/auth/clear/route.ts` Route Handler GET che cancella access+refresh cookies (workaround alla limitazione dei Server Components che NON possono mutare cookies in Next 15+/16). Sanitize `?to=` anti open-redirect.
- **Auth guard**: `src/proxy.ts` (Next.js 16 ex-`middleware.ts`), 4 PROTECTED_PREFIXES (dashboard, content, analytics, settings — NB no `/campaigns`), presence-only check, sanitize `next=`. Matcher esclude `_next/static`, `_next/image`, `favicon.ico`, `api`.
- **Dashboard layout**: `app/(dashboard)/layout.tsx` Server Component con double-check pattern (cookie presence + `getCurrentUser`), 3 esiti distinti (200/401/network — il network blip NON cancella i cookie). `components/dashboard/sidebar.tsx` Client Component con `usePathname` per active state. `components/dashboard/topbar.tsx` Server Component con `<form action={logoutAction}>` (no Client). 4 placeholder pages.
- **shadcn aggiunti**: Card, Input, Label.
- **Nuova dep**: `server-only` (build-time enforcement separazione server/client per `config.ts`, `server-fetch.ts`, `auth-context.ts`, `actions/auth.ts`).
- **ADR-0004**: documenta BFF pattern, JWT in cookie httpOnly, fetch+manual types, no global state, exit strategies.
- **CLAUDE.md**: aggiunta nota Next.js 16 breaking change (middleware → proxy) nello stack tecnologico.

### Frizioni reali
1. **Next.js 16 ha rinominato `middleware.ts` → `proxy.ts`** (breaking change, scoperto solo dopo che il primo run con `middleware.ts` non redirigeva). La docs ufficiale è in `node_modules/next/dist/docs/01-app/02-guides/upgrading/version-16.md`. La function export `middleware()` → `proxy()`. Inoltre con `src/` directory layout il file va in `src/proxy.ts`, NON a root come dicono molti tutorial (datati Next ≤14). Lezione: leggere SEMPRE i docs della major version installata, non Stack Overflow.
2. **Server Components in Next 15+/16 NON possono mutare cookies**. `cookies().delete()` funziona solo in Server Action o Route Handler. Spec di step 2 chiedeva al `/login` Server Component di cancellare cookie su 401. Workaround: piccolo Route Handler `/api/auth/clear?to=/login` (idempotente, sanitize `to` anti open-redirect) che fa il cleanup e redirige. Riusato anche dal `(dashboard)/layout.tsx` su 401 — un solo posto centralizzato.
3. **`React.cache()` per evitare prop drilling layout→pages**. Il layout chiama `getCurrentUser` per la auth check; `/settings` page la chiama di nuovo per mostrare info utente. Senza memoization, 2 fetch al backend per request. `cache(...)` da React 19 memoizza per request-tree → 1 fetch totale verificato. Pattern utile, da ricordare.
4. **`web-dashboard/.gitignore` ignorava `.env.example`**: il pattern `.env*` di create-next-app non aveva l'eccezione `!.env.example`. Aggiunta. Senza questo fix, il template per chi clona la repo non sarebbe stato committato.
5. **email-validator (Sessione 3) era già rimasto come strascico**: nessuna nuova frizione qui, ma confermato che `EmailStr` rifiuta `.local`/`.test`. Seed con `.example` (Sessione 3 fix) ha funzionato a livello frontend al primo colpo.

### Decisioni rilevanti
Tutte tracciate in [ADR-0004](./infrastructure/docs/architecture/decisions/0004-frontend-auth-strategy.md). Punti critici:
- **BFF pattern** con cookie httpOnly: browser parla solo con Next.js, il JWT non lascia mai server-side. No CORS necessario (browser non chiama il backend direttamente).
- **`fetch` nativo + manual TS types**: niente axios/openapi-fetch. Exit: `openapi-typescript` quando endpoint > 10.
- **No global state JS**: source of truth = cookie + `React.cache(getCurrentUser)`. Niente Context/Zustand/Jotai/TanStack Query (S4). Exit: TanStack Query per mutation client-side complesse (S5+).
- **Proxy presence-only + layout double-check**: doppia rete di sicurezza, no `JWT_SECRET_KEY` duplicato fra core-api e web-dashboard.
- **Refresh auto-rotation rimandato a Sessione 4-bis**: per ora se access scade → 401 → /login. UX da molestia ogni 60min, accettabile per dev.

### Lezioni apprese
- **Verificare docs della major version installata** prima di copiare pattern dal web. Next.js, Tailwind v4, shadcn — tutti hanno breaking changes recenti che i tutorial non riflettono. `node_modules/<lib>/dist/docs/` o equivalente è authoritative.
- **`React.cache()`** + Server Components è il modo idiomatico Next 16 per "passare dati dal layout alle pages" senza props drilling né Context. Cache-scope = request, perfetto per per-request data come user info.
- **Server Components vs Client Components** → la regola: Server di default, Client SOLO quando serve interattività JS (hooks come `usePathname`, `useState`, `useActionState`). Topbar (no JS) → Server. Sidebar (`usePathname`) → Client. Layout (read cookies + fetch) → Server. Login form (`useActionState` per pending) → Client.
- **Cookie mutation rules**: scrivibili solo in Server Action e Route Handler. Workaround = Route Handler dedicato per cleanup, condiviso fra punti di chiamata.
- **`<form action={ServerAction}>`** è il pattern modern Next 16 per logout/azioni semplici da Server Components — niente Client Component necessario, niente JS bundle extra.

### Da ricordare
- **Route group `(dashboard)`**: parentesi tonde nel nome cartella → non appare nell'URL ma il layout interno applica a tutti i child. URL effettivi: `/dashboard`, `/content`, `/analytics`, `/settings`. Pattern utile per layout shared.
- **`/api/auth/clear`**: pattern centralizzato per cancellare cookie auth. Riusato dal /login (token stale check) E dal dashboard layout (401 da /me). Quando aggiungeremo /me con `tv` claim cambiato, stesso flow.
- **Backend porta**: `BACKEND_URL=http://localhost:8001` in dev. Fallback nel `config.ts`. In prod via env.
- **Seed dev credenziali** (DEV ONLY, salvate in password manager): `admin@marketing-os.example` (super_admin), `admin@monoloco.example` (client_admin).
- **`/` home page**: ancora il placeholder Sessione 1 ("Marketing OS / Sessione 1 — Bootstrap completato"). Decisione su cosa farne (redirect a /login o /dashboard, o landing page) rimandata. Vedi TODO.md.
- **Mobile**: sidebar `hidden md:block` — su schermi <md non c'è menu. Mobile responsive da sistemare in S5+.

### Prossimo
- **Sessione 4-bis** (probabile): refresh token auto-rotation. Quando access scade → catch del 401 dal layout o middleware → `POST /api/v1/auth/refresh` con cookie refresh → nuovi cookie + replay request. Eviterà l'UX-molestia di re-login ogni 60min.
- **Sessione 5** (prompt 07): admin endpoints (clients management) + onboarding wizard nuovo cliente. Backend `POST /api/v1/admin/clients` + frontend dashboard con tabella/wizard. Richiederà `require_super_admin` dep + form complessi (forse qui TanStack Query / react-hook-form).

---

## 2026-05-01 — Sessione 3: Authentication, JWT, multi-tenant middleware

**Stato**: chiusa. 3 commit su `origin/main`: `feat(auth): …`, `docs(adr): ADR-0003`, `docs(journal): …`. (Sessione 3-bis copre i test pytest formali in `core-api/tests/`, deferred.)

### Cosa è stato fatto
- `app/core/security.py`: bcrypt + JWT (HS256) con `TokenType` enum, `_encode_token`, `decode_access_token` / `decode_refresh_token`, `hash_password` / `verify_password`. Mapping pulito di `ExpiredSignatureError` / `JWTError` a messaggi non-leak.
- `app/db/session.py`: split in `get_unauthenticated_db` (RLS bypass per /login + /refresh) e `get_authenticated_db(user)` che applica contratto ADR-0002 (`SET LOCAL ROLE authenticated` + `set_config` GUC vars + DEBUG log strutturato).
- `app/core/middleware.py`: `JWTAuthMiddleware` ASGI puro (no `BaseHTTPMiddleware`). Estrae Bearer header, decode, popola `request.state.token_payload`. 401 immediata su token invalido. Path esclusi: `/health`, `/docs*`, `/redoc`, `/openapi.json`.
- `app/core/deps.py`: `get_token_payload`, `get_current_user` (con OPTION A strict checks), `get_authenticated_session`, `require_super_admin`, `require_client_admin`. Helper `_reject` con `NoReturn` + structlog warning + `from None`.
- `app/routers/auth.py`: `POST /api/v1/auth/login`, `POST /api/v1/auth/refresh`, `GET /api/v1/auth/me`. Schemi pydantic inline (`LoginRequest`, `LoginResponse`, `RefreshRequest`, `MeResponse`, `ClientSummary`). `EmailStr` su login; case-insensitive lookup. Login fail uniforme `"invalid credentials"`. Refresh stateless con rotation. `/me` usa `get_authenticated_session` per esercitare RLS end-to-end.
- `app/main.py`: middleware mounted, router included con `prefix="/api/v1/auth"`, `_custom_openapi()` registra `BearerAuth` scheme globale.
- ADR-0003 documenta tutte le scelte (HS256, claims, stateless refresh + exit strategy `tv`, lifetimes per ambiente, type discriminator, no rate limit per ora, no password validation al login, OPTION A strict checks, login response uniforme).
- `scripts/seed_dev.py` aggiornato a email `.example` (passa EmailStr). Vecchi user `.local` cancellati, ri-seedati.
- 16/16 inline test pass (login OK / wrong creds / disabled / case-insensitive / /me ruolo + null per super_admin / no token / refresh rotation / token type discriminator / E2E flow / RLS scope / OpenAPI security per-endpoint).

### Frizioni reali
1. **`scope["state"]` può essere dict in Starlette recente.** Il primo run del middleware è andato in `AttributeError: 'dict' object has no attribute 'token_payload'`. Causa: Starlette propaga lifespan state come dict in `scope["state"]`, anche se la lifespan del nostro app non ritorna nulla. Fix: detect type e wrap con `State(existing_dict)` se dict, lasciando il dict come storage backing per non rompere altri consumer.
2. **`EmailStr` ↔ seed con TLD reservati.** Trap a due livelli: (a) seed iniziale con `@*.local` rifiutato da `email-validator` con "special-use or reserved name". Switch a `@*.test` (RFC 2606 testing) — ma anche `.test` viene rifiutato da `email-validator` (lo include nella lista special-use). Switch finale a `@*.example` (RFC 2606 documentation) che invece passa. Ricicli del seed × 2.
3. **`jose.encode()` ritorna `Any` in mypy strict.** `disallow_any_generics + warn_return_any` flaggava `return jwt.encode(...)`. Fix con annotazione locale esplicita `encoded: str = jwt.encode(...); return encoded`. Pattern da ricordare per altre lib senza type stubs (jose, vecchie SDK).
4. **`pydantic.EmailStr` richiede `email-validator` separatamente.** Non è una dep transitiva di pydantic (è opzionale). Aggiunto via `poetry add email-validator`. Marginale ma scoperto solo a runtime durante il primo /login.

### Decisioni rilevanti
Tutte tracciate in [ADR-0003](./infrastructure/docs/architecture/decisions/0003-jwt-authentication-strategy.md). Punti critici:

- **OPTION A strict checks** (token role/client_id vs DB) — mismatch forza re-login, costa zero query extra (user già loaded).
- **Login response uniforme** `"invalid credentials"` su tutti i casi di fallimento — anti user-enumeration. Il log WARNING usa SHA-256 dell'email (no plaintext, GDPR).
- **Refresh stateless** con rotation. Vecchio refresh resta tecnicamente valido fino a expiry. Exit strategy: claim `tv` (token_version) sull'utente, bump invalida tutto. Sessione 5/6.
- **Lifetimes env-tunable**: 60min access (sempre), 7d refresh dev / 30d refresh prod. Refresh corto in dev = scoprire rotture early.
- **No rate limiting per ora** — Redis arriva in Sessione 5+. TODO tracciato.

### Lezioni apprese
- **ASGI middleware: `scope["state"]` può essere dict.** Mai `setdefault("state", State())` ciecamente — `setdefault` ritorna l'esistente, che potrebbe essere dict. Detect type prima di fare attribute access.
- **`email-validator` (via pydantic.EmailStr) rifiuta TLD special-use (RFC 6761).** `.local`, `.test`, `.localhost` sono fuori. `.example` è in. Per fake-domain in seed/test usare `.example` (RFC 2606 reserved per documentation).
- **mypy strict + lib senza stubs**: `var: Type = lib_call(...)` è il modo idiomatico. Più leggibile di `cast(Type, lib_call(...))` e meno ipotecante di `# type: ignore`.
- **Login uniformity richiede attenzione**. È facile sbagliare e leakare info via timing o detail-string differenti. Il check uniforme `not user or not user.is_active or not verify_password(...)` in un solo `if` è più robusto di tre `if` separati con stessi `raise`.

### Da ricordare
- Credenziali seed dev (DEV ONLY): `admin@marketing-os.example` / `admin@monoloco.example` — password salvate localmente nel password manager dell'utente.
- Email-validator: `.example` ✓, `.test` ✗, `.local` ✗.
- Per la Sessione 3-bis (test pytest formali) i test inline scritti durante S3 sono buoni reference da convertire in fixture pytest.
- Quando in Sessione 4 aggiungeremo CORS middleware: deve stare PRIMA (più esterno) di `JWTAuthMiddleware` per gestire preflight OPTIONS senza attraversare l'auth.
- Quando in S5+ aggiungeremo Redis: rate limiting su `/api/v1/auth/login` (5/15min/IP) — vedi TODO.md.

### Prossimo
- **Sessione 3-bis**: pytest formali in `core-api/tests/{conftest.py, test_auth.py}` per coprire il login flow, refresh rotation, isolation cross-tenant via API. Convertire i 16 inline test di Sessione 3 in fixture e test pytest.
- **Sessione 4** (originale): Frontend dashboard — login + struttura base. Login form con i nuovi endpoint `/api/v1/auth/*`, JWT in httpOnly cookie via Next.js API route, layout con sidebar.

---

## 2026-05-01 — Sessione 1: Bootstrap monorepo

**Stato**: chiusa. 3 commit su `origin/main` (`afab0ae`, `013e81a`, `13f275a`).

### Cosa è stato fatto
- Struttura repo (4 cartelle): `core-api/`, `web-dashboard/`, `pipelines/`, `infrastructure/`.
- `core-api` operativo: Python 3.12 + Poetry 2.3 + FastAPI 0.136 + SQLAlchemy 2.0 async + Alembic 1.18 + Anthropic SDK 0.97 + OpenAI SDK 2.33 + structlog 25.5. `/health` risponde, ruff e mypy strict puliti.
- `web-dashboard` operativo: Next.js 16.2 + React 19 + Tailwind v4 + shadcn/ui (preset `base-nova`, neutral) + ESLint 9 + Prettier. Build, lint, typecheck verdi.
- ADR-0001 (stack iniziale) in `infrastructure/docs/architecture/decisions/`.
- Connessione a Supabase verificata (Postgres 17.6, pooler `eu-west-1`, session mode).
- Repo pubblicato su [github.com/qpallante/Marketing-OS](https://github.com/qpallante/Marketing-OS).

### Problemi incontrati
1. **`create-next-app@latest` ha installato Next.js 16.2, non 15** come previsto da CLAUDE.md. Tailwind v4 e React 19 a cascata. CLAUDE.md e ADR-0001 aggiornati per riflettere lo stack reale.
2. **Plan mode attivato a metà esecuzione**. Dovuto riscrivere il piano per le parti rimanenti (file in `~/.claude/plans/`).
3. **`shadcn init --base-color=neutral` ha fallito**: il flag non esiste più nella CLI corrente (`shadcn@4.6.0`). Risolto con `init -d` (preset `base-nova`, default neutral). Effetto collaterale: il preset ha generato `src/components/ui/button.tsx`, lasciato in repo (verrà comunque usato dalla Sessione 4).
4. **`sqlalchemy[asyncio]` non ha pullato `greenlet`** come dipendenza transitiva. Errore a runtime sul primo `engine.connect()`. Aggiunto `greenlet` come dipendenza diretta in `pyproject.toml`.
5. **Endpoint diretto `db.<ref>.supabase.co` non risolve in DNS**. I progetti Supabase nuovi (post-2024) espongono solo il connection pooler.
6. **Primo tentativo pooler `eu-central-1` ha risposto "Tenant or user not found"**. Sondate 14 regioni in parallelo: progetto effettivamente su **`eu-west-1`** (Ireland).
7. **`mv 07_Prompt_Claude_Code.md ~/Documents/`** dell'utente è fallito: Mac in italiano usa `~/Documenti`. File aggiunto a `.gitignore` (resta locale ma fuori dai commit).
8. **Primo commit con committer auto-rilevato** (`quintinopallante@MacBook-Pro-di-Quintino.local`) perché `git config --global user.name/email` non era impostata. Utente ha settato config + amendato il commit. Hash cambiato `6098547 → afab0ae`; CLAUDE.md disallineato fixato in un commit successivo (`13f275a`).
9. **`npm audit` segnala 2 moderate** in `postcss <8.5.10` annidato in `node_modules/next/`. Falso positivo: il fix proposto da npm è il downgrade a Next 9.3.3 (di 7 major version, non utilizzabile). Ignorato; rivalutare con Next 16.3+.

### Lezioni apprese
- **Le CLI cambiano interfaccia velocemente.** shadcn ha cambiato API tra v3 e v4. Sempre `<cli> --help` prima di scrivere flag a memoria, soprattutto per tool installati al volo via `npx@latest`.
- **`create-next-app@latest` ≠ stabile.** Pulla sempre la major più recente, non quella citata nei docs scritti in passato. Da accettare e documentare in ADR, non combattere.
- **SQLAlchemy `[asyncio]` extras è inaffidabile** sull'inclusione di `greenlet`. Aggiungerlo sempre esplicitamente per evitare errori a runtime.
- **Supabase: prendere il connection string dal dashboard.** Nessuna assunzione su `db.<ref>.supabase.co` (è scomparso). Pooler URL contiene username modificato (`postgres.<ref>`) e regione che va verificata caso per caso.
- **`git config --global user.name/email` PRIMA del primo commit** su una macchina fresh. Altrimenti git auto-rileva da hostname e i commit non si linkano al profilo GitHub. Force-push richiesto se ci si accorge dopo il push.
- **Mac in italiano: home folders in italiano.** `~/Documenti`, `~/Scrivania`, `~/Scaricati`. Mai assumere percorsi inglesi.
- **AGENTS.md/CLAUDE.md generati dai template** (es. `web-dashboard/CLAUDE.md` da create-next-app) vanno letti subito: avvisano di breaking changes che il modello non conosce.

### Decisioni rilevanti
- **Stack frontend**: Next.js 16 + Tailwind v4 + React 19 (deviazione dichiarata da CLAUDE.md, accettata, motivata in ADR-0001).
- **Pooler Supabase**: session mode (port 5432) per dev/migrations. Transaction mode (6543) da rivalutare per produzione serverless quando ci arriviamo.
- **Componenti shadcn**: aggiunti on-demand a partire da Sessione 4. Solo `Button` presente per effetto collaterale del preset init.
- **`07_Prompt_Claude_Code.md`**: locale, gitignored. Resta consultabile ma fuori dai commit.
- **`supabase/config.toml`**: committato (config locale CLI, no secrets). `.env` con secrets è gitignored.

### Da ricordare
- Project ref Supabase: `txmkxllfhzrfetordkap` · Region: `eu-west-1` · Postgres: `17.6`.
- Email committer corrente: `241859981+qpallante@users.noreply.github.com` (GitHub noreply).
- `web-dashboard/AGENTS.md` impone lettura di `node_modules/next/dist/docs/` prima di scrivere codice Next nuovo. Da rispettare nelle Sessioni 4+.
- Per Sessione 9 (Brand Brain): abilitare extension `pgvector` lato Supabase con `CREATE EXTENSION IF NOT EXISTS vector;`.
- CI/CD non ancora configurato. Da affrontare quando il blocco di codice supera la review locale (probabilmente Sessione 5-6).

### Prossimo
**Sessione 2** (pianificata): Alembic init async + primo schema (`Client`, `User`, `PlatformAccount`, `AuditLog`) + RLS policies + seed dev (super-admin + Monoloco + user admin client).

---

## Workflow

Aggiornamento manuale, su richiesta esplicita a Claude Code a fine sessione.

- **Quando**: alla chiusura di una sessione di lavoro (dopo commit + push), Quintino chiede a Claude Code di aggiungere l'entry per quella sessione.
- **Cosa includere**: data + titolo sessione, commit principali, problemi incontrati, lezioni apprese, decisioni rilevanti, "Da ricordare", "Prossimo".
- **Cosa non includere**: dettagli derivabili dal codice o da git log; ripetizioni di ADR/CLAUDE.md.
- **Niente automazione**: nessun agent schedulato. Il valore di questo file dipende dal commento umano sui passaggi non ovvi — l'agent autonomo finirebbe a riassumere git log, che è già consultabile.
