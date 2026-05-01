# TODO — Marketing OS

Lavori pianificati ma non ancora svolti, organizzati per area. Ogni voce indica la sessione in cui è previsto l'attacco. Questo file **fa parte del repo** (NON gitignored).

Per il diario operativo (cosa è stato fatto, frizioni, decisioni) vedi [`JOURNAL.md`](./JOURNAL.md).

---

## Frontend (post Sessione 4)

| Item | Quando | Note |
|---|---|---|
| **Refresh token auto-rotation** | Sessione 4-bis | Catch del 401 da `/me` nel layout (o un client interceptor) → `POST /api/v1/auth/refresh` con cookie refresh → nuovi cookie httpOnly + replay request. Senza, l'utente fa re-login ogni 60min. Vedi ADR-0004 §"Da rivalutare quando". |
| **Mobile responsive** sidebar | Sessione 5+ | Attuale: `hidden md:block`. Servirà sheet/drawer con menu icon nella topbar (shadcn `Sheet` component). |
| **Custom `not-found.tsx`** con branding | Sessione 5+ | Attuale 404 default Next.js è generico. Sostituire con pagina branded "Marketing OS — pagina non trovata". |
| **Pre-compilazione email da `localStorage`** | Sessione 5+ | UX nice-to-have: ricordare l'ultimo email usato per login. Già supportiamo `?email=` nel URL ma localStorage è più friendly. |
| **Logo immagine** Marketing OS | Quando definito brand | Sostituire i `<h2>Marketing OS</h2>` text-only nella sidebar/login con `<Image src="/logo.svg">`. Sessione 5+ se urge. |
| **`openapi-typescript`** per type generation da `/openapi.json` | Quando endpoint backend > 10 | Per ora 4 endpoint, manual TS types in `src/lib/types.ts` sostenibile. Vedi ADR-0004 §2. |
| **TanStack Query** (`@tanstack/react-query`) | Quando emergerà la prima mutation client-side complessa | Per ora Server Components fanno tutto il fetch. Aggiungeremo on-demand. Vedi ADR-0004 §3. |
| **Decisione su `/`** (home page) | Sessione 5+ | Attuale: placeholder Sessione 1. Opzioni: redirect a `/login`, redirect a `/dashboard` se loggato, oppure pagina pubblica brandizzata. Decidere quando avremo signup flow. |
| **CORS sul backend** | Quando arriveranno client esterni | Per ora BFF puro, no CORS. Quando S6+ porterà OAuth Meta consumer-side o partner integrations, configurare. |
| **Request ID middleware + structlog binding** | Sessione 5+ | Bind `request_id` su `structlog.contextvars` lato backend per log tracing → frontend potrebbe propagare via header. Pattern già anticipato in `app/db/session.py` (S2). |

## Auth (post Sessione 3)

| Item | Quando | Note |
|---|---|---|
| **Rate limiting** su `POST /api/v1/auth/login` (5 tentativi / 15 min / IP) | Sessione 5+ | Richiede Redis (arriva con Celery). Mitigazioni interim: response uniforme su errore (no enumeration) + bcrypt cost. Vedi ADR-0003 §9. |
| **`tv` (token_version) claim** per invalidazione token | Sessione 5/6 | Colonna `users.token_version INT NOT NULL DEFAULT 0`. Bump = logout-everywhere per quell'utente. Vedi ADR-0003 §"Exit strategy". |
| **Password reset flow** | Sessione 5+ | Token-based, email link, scadenza breve (15 min). Tabella `password_reset_tokens` o stateless con `tv` bump. |
| **Email verification** al signup | Sessione 5+ | Verification email con token. Bloccare login (o capacità) finché non verificato. |
| **Refresh token blacklist** (opzionale) | Solo se serve dopo S6 | Probabilmente NON serve se `tv` claim copre il caso. Tabella `revoked_refresh_tokens` con TTL = scadenza naturale del token. |
| **Strict password policy** al signup (min 8 chars + classi) | Sessione 5+ | Allineato a NIST SP 800-63B. Login resta lasso (vedi ADR-0003 §8). |

## Multi-tenant onboarding (Sessione 5)

| Item | Note |
|---|---|
| Endpoint `POST /api/v1/admin/clients` (super_admin only) | Crea nuovo client + invita primo client_admin |
| Wizard onboarding nuovo cliente nel dashboard | Step: dati base → platform stub → invite admin |
| Email invitation flow | Token-based, scadenza 7 giorni |
| Dashboard `/admin/clients` con tabella + ricerca | super_admin only |

## Platform integrations (Sessione 6+)

| Item | Note |
|---|---|
| OAuth Meta Business Account flow | Token storage via Doppler/vault key |
| `MetaAdsAdapter` (fetch campaigns, ad sets, creatives, insights) | Rate limit + retry + pagination |
| Schema dati Data Lake (`campaigns`, `ad_groups`, `ad_creatives`, `creative_performance`, `organic_posts`, `post_performance`, `conversions`) | Sessione 7 |
| Backfill 90gg per Monoloco | Script `scripts/backfill_meta.py` |

## DX / Tooling

| Item | Note |
|---|---|
| **CI/CD GitHub Actions** | Lint + typecheck + test su PR. Da fare quando i test pytest formali esistono (Sessione 3-bis). |
| **Test pytest formali** in `core-api/tests/` (auth flow + isolation cross-tenant) | Sessione 3-bis (deferred da S3) |
| **Pre-commit hooks** (ruff, mypy) | Quando avremo > 1 contributor |
| **`request_id` middleware** che bind contextvars structlog | Sessione 4+ (per logging tracing) |
| **CORS middleware** (origini frontend) | Sessione 4 (quando il dashboard farà fetch al backend) |
| **GDPR data export / deletion** endpoint | Sessione 11+ |

## Brand Brain / AI (Sessione 9-10)

| Item | Note |
|---|---|
| Setup `pgvector` extension su Supabase | `CREATE EXTENSION IF NOT EXISTS vector;` |
| Tabella `brand_documents` con embedding vector(1536) + HNSW index | Sessione 9 |
| `BrandBrain` service: `embed_text`, `index_document`, `search_similar` | Sessione 9 |
| Caption Agent con few-shot dal Brand Brain | Sessione 10 |
| Tabella `ai_invocations` per cost tracking | Sessione 10 |

---

## Convenzioni

- **Quando completi un item**: sposta in `JOURNAL.md` (entry della sessione corrispondente) e rimuovi da qui.
- **Quando aggiungi un item nuovo**: includi sessione target + 1 riga di motivazione/nota.
- **Item urgenti / blocking**: prefissa con 🚨 e aggiungi in cima alla sezione.
