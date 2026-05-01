# Journal — Marketing OS

Diario operativo del progetto. Tono asciutto, onesto. Riporta come si è arrivati alle decisioni, le frizioni reali, ciò che vale ricordare a 6 mesi di distanza. Non duplica codice, ADR o CLAUDE.md.

Entry in ordine cronologico inverso (più recenti in alto). Aggiornamento manuale, vedi sezione *Workflow* in fondo.

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
