# TODO — Marketing OS

Lavori pianificati ma non ancora svolti, organizzati per area. Ogni voce indica la sessione in cui è previsto l'attacco. Questo file **fa parte del repo** (NON gitignored).

Per il diario operativo (cosa è stato fatto, frizioni, decisioni) vedi [`JOURNAL.md`](./JOURNAL.md).

---

## Security debt (priorità ALTA)

| Item | Priorità | Note |
|---|---|---|
| **AUDIT LOG REVOKE** (debt da S2) | **ALTA — S7-bis o sessione dedicata di security audit** | `audit_log` (creata in S2) ha probabilmente le stesse default ACL Supabase del Brand Brain: `pg_default_acl` su public schema concede ALL DML privileges (`arwdDxtm`) a `authenticated`/`anon`/`service_role`. ADR-0002 §"audit_log append-only" reclama "blocco UPDATE/DELETE via RLS + privilege denied", ma in realtà nel contesto Supabase è solo RLS-only. Pattern: stesso REVOKE applicato in `006_brand_brain.sql` per `brand_generations`. **Verifica**: `SELECT has_table_privilege('authenticated', 'audit_log', 'UPDATE');` — atteso `True` (debt confermato) o `False` (già protetto). **Fix**: nuovo file `supabase/policies/004_audit_log_revoke.sql` con `REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM authenticated, anon, service_role;`. **Test**: addendum smoke che verifica `permission_denied` su tentativo UPDATE su `audit_log` da role `authenticated`. **Bonus**: aggiornare nota in ADR-0002 §"audit_log append-only" con la gotcha. |

---

## S7-bis priorities (post Sessione 7 — Brand Brain Foundation)

| Item | Priorità | Note |
|---|---|---|
| **Frontend turbopack HMR fix + live UI test Tab Genera** | **ALTA** | Backend RAG validato via curl in S7 (3 caption Monoloco generated, qualità pubblicabile), ma il flow click-Genera nel browser non verificato per turbopack HMR issue su sessione dev di metà giornata. **Fix**: dev restart pulito, oppure investigare HMR config. **Verifica**: aprire `http://127.0.0.1:3001/brand-brain` con login Monoloco, scrivere prompt, click Genera, verificare output renderizzato + MetaCard popolati + reference chunks in `<details>`. |
| **`BackgroundTasks` root cause investigation** | **ALTA** | Bug noto: con `BackgroundTasks.add_task` + bg task che usa `Depends(get_authenticated_session)`, l'asset INSERT della **request** transaction era invisibile per >22s. Workaround S7: `asyncio.create_task` fire-and-forget post-commit. Capire se è (a) bug nel nostro pattern session management, (b) bug FastAPI/SQLAlchemy noto, (c) interazione con `get_authenticated_session` che apre nuova session prima del commit della request. Test riproducibile: bg task immediato che SELECT su tabella appena INSERT-ata in request. **Bonus**: documentare in ADR / commento nel codice. |
| **`GET /brand/form` endpoint** | **ALTA** (blocker S7-bis Tab Assets) | Attualmente `/brand/form` è solo PUT (upsert). Per il Tab Assets in S7-bis serve GET per pre-populare il form con i valori esistenti. Pattern: standard CRUD GET + 404 se mai impostato. RLS già copre il client_id scope. |
| **Tab Assets in `/brand-brain`** | **MEDIA** | Implementare il Tab Assets (placeholder S7): brand form (tone/dos/donts/colors) con `react-hook-form` + Zod validation + counter live keywords (ADR-0006 trigger condition: form >5 input, wizard multi-step). PDF/text upload list con DELETE button (modal conferma) + indexing status badge live (poll su `indexing_status`). |
| **Tab Storico in `/brand-brain`** | **MEDIA** | Lista paginata di generations con filtro per data + status. Detail view modal con full prompt + output + retrieved_chunks (asset_filename + chunk_index + similarity). Re-run button (pre-fill prompt nel Tab Genera). |
| **`libmagic` install + `python-magic` enable** | MEDIA (multi-format MIME) | Ora MIME validation è simplified a `data.startswith(b"%PDF-")` (single-MIME allowlist S7). Per S+ multi-format (DOCX, MD, TXT, RTF): `brew install libmagic` (Mac) / `apt install libmagic1` (CI) + uncommit del `import magic` in `brand_storage.py`. |
| **Cron reconciliation asset `pending`** | MEDIA | `asyncio.create_task` non ha retry / persistence-on-shutdown. Se il processo muore mid-indexing, l'asset resta `indexing_status='pending'` per sempre. Cron job (Celery beat in S8+, o systemd timer interim): asset `pending` da >5 min → re-trigger indexing oppure marca `failed` con reason "worker died". |
| **Generalizzazione `_wait_for_asset_visible`** | BASSA (se ricorre) | Pattern `asyncio.create_task` post-commit + polling per visibility usato 2 volte in S7 (PDF upload, text upload). Se emerge un 3° consumer (es. webhook handler S+): estrarre helper `wait_for_row_visible(table, id, max_wait_s)`. |
| **Migrazione storage da filesystem a S3/B2** | BASSA (volume) | Trigger: (a) > 100MB totali per client, (b) deploy multi-instance (Vercel + Railway scale-out), (c) backup automation. Pattern `BrandStorage` (`app/core/brand_storage.py`) già astratto su `Path` interface — interfaccia con `BrandS3Storage` se serve. |
| **Embedder self-hosted (BGE-large-en o sentence-transformers)** | BASSA (privacy / cost) | Trigger: client esterni con privacy concern (GDPR-sensitive content), oppure cost optimization (embedding OpenAI ~$0.02/1M token, self-hosted ~$0). Pattern Adapter già pronto: nuova classe `LocalEmbedder` che implementa `EmbedderProtocol`. |
| **Diversity rule riusabile in altri moduli AI** | BASSA | Pattern emergente in S7 system prompt: senza vincolo esplicito Claude produce variazioni quasi identiche. Documentare e riusare quando arriverà Caption Agent / Content Studio. |
| **Cost tracking row su `brand_generations` con embedding cost separato** | BASSA | Ora `tokens_input` è solo LLM input, `embedding_tokens` non tracciato separatamente. Quando avremo cost dashboard, separare embed cost vs LLM cost (già in note schema brand.py:212). |

---

## Frontend (post Sessione 4)

| Item | Quando | Note |
|---|---|---|
| ✅ ~~**Refresh token auto-rotation**~~ | ~~Sessione 4-bis~~ **DONE** (commit feat S4-bis) | Implementato via Route Handler `/api/auth/rotate` (PATH B). Layout catch 401 → redirect a /api/auth/rotate → POST /refresh → cookies aggiornati → redirect a `to=`. Failure → /api/auth/clear → /login. Vedi [ADR-0005](./infrastructure/docs/architecture/decisions/0005-refresh-token-auto-rotation.md). |
| **Pattern famiglia `/api/auth/*`** per cookie operations | Riferimento per S5+ | Tutte le operazioni che richiedono cookie writes triggate da Server Component o browser navigation vanno in Route Handler dedicato. Esistenti: `/api/auth/clear`, `/api/auth/rotate`. Pianificati S5+: `/api/auth/password-reset/*`, `/api/auth/email-verification/*`. Mai `cookies().set()` in Server Component. Vedi ADR-0005 §"Pattern emergente". |
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

## Onboarding flows (post Sessione 6)

### Done

| Item | Quando | Note |
|---|---|---|
| ✅ ~~Endpoint `POST /api/v1/admin/clients` (super_admin only)~~ | ~~Sessione 5~~ **DONE** | Crea client + invitation. Vedi [ADR-0006](./infrastructure/docs/architecture/decisions/0006-admin-clients-onboarding.md). |
| ✅ ~~Endpoint `GET /api/v1/admin/clients`~~ | ~~Sessione 5~~ **DONE** | Lista tutti i client (no paginazione finché < 50). |
| ✅ ~~Wizard onboarding (nuovo client + invitation)~~ | ~~Sessione 5~~ **DONE** | Form `(dashboard)/admin/clients/new` con success panel + copy URL. |
| ✅ ~~Dashboard `/admin/clients` con lista~~ | ~~Sessione 5~~ **DONE** | Card layout + StatusBadge inline. Search/filter rimandati a quando avremo ~20+ client. |
| ✅ ~~Endpoint `GET /api/v1/auth/invitation/{token}` (preview)~~ | ~~Sessione 6~~ **DONE** | Public, always-404-on-invalid. Vedi [ADR-0007 §3](./infrastructure/docs/architecture/decisions/0007-accept-invitation-flow.md). |
| ✅ ~~Endpoint `POST /api/v1/auth/accept-invite`~~ | ~~Sessione 6~~ **DONE** | Public, single transaction (validate + INSERT user + UPDATE invitation), READ COMMITTED + UNIQUE catch + 409. Auto-login Opzione A. |
| ✅ ~~Frontend `/accept-invite` + form~~ | ~~Sessione 6~~ **DONE** | Server Component standalone (pre-auth) + Client Component con `useActionState` + counter live + match check. |
| ✅ ~~Helper `validate_invitation`~~ | ~~Sessione 6~~ **DONE** | `app/core/invitations.py`, 4 eccezioni dedicate, ordine deterministico (revoked > accepted > expired). |
| ✅ ~~Migration 0003: `invitations.accepted_by_user_id`~~ | ~~Sessione 6~~ **DONE** | FK NULL → users(id) ON DELETE SET NULL. Popolato durante accept-invite. |

### Future enhancements

| Item | Quando | Note |
|---|---|---|
| **Email invitation flow** (SMTP/SES/Resend) | Sessione 7+ | Trigger: super_admin si stanca di copia/incolla manuale, o > 5 client onboardati al mese. Provider candidato: Resend (developer-friendly) o AWS SES (low-cost a volume). |
| **UI super_admin per revoke + re-invite** | Sessione 7+ | Trigger: ≥1 invitation reale che deve essere ri-inviata. Endpoint `POST /admin/invitations/{id}/revoke` + `POST /admin/invitations/{id}/resend`. Per ora UPDATE diretto via psql. |
| **`get_unauthenticated_session_tx` helper** | Quando arriva 2° public transactional handler | Probabile candidato: password-reset complete. Apre `session.begin()` per il consumer, evita duplicazione di `async with db.begin():` boilerplate. Vedi [ADR-0007 §7](./infrastructure/docs/architecture/decisions/0007-accept-invitation-flow.md). |
| **Estrazione `_build_token_pair` in helper standalone** (`app/core/auth_tokens.py`) | Quando ≥3 consumer | Ora 2 (login, accept-invite). Candidati S7+: password-reset, magic-link, signup self-serve, OAuth callback. Vedi [ADR-0007 §8](./infrastructure/docs/architecture/decisions/0007-accept-invitation-flow.md). |
| **Header `X-Invitation-State` enum invece di parsing `detail`** | Quando 4° sub-stato 410 | Ora il frontend parsa `String.includes("expired"|"already used"|"revoked")` da `detail`. Aggiungere un 4° sub-stato (es. `requires_email_verification`) renderebbe il parsing ambiguo. Header dedicato con enum strict. |
| **Regression script `scripts/run_all_smoke_tests.sh`** | Sessione 7+ | Lancia smoke_test_session{2,5,6}.py in sequenza. Pre-commit hook futuro? |
| **RLS policy `client_admin SELECT own client invitations`** | Sessione 7+ | Per il portale di onboarding lato cliente. S5 + S6 è super_admin-only. |
| **Endpoint `POST /admin/invitations/{id}/resend`** | Sessione 7+ | Crea nuovo token + estende `expires_at` (vecchio invalidato). |
| **Endpoint `POST /admin/invitations/{id}/revoke`** | Sessione 7+ | Set `revoked_at = now()`. Tentativo di accept dopo revoca → 410 Gone. |
| **Endpoint `DELETE /admin/clients/{id}`** + soft-delete | Sessione 7+ | Decisione: hard delete con CASCADE (semplice) o soft via `status='archived'` (audit-friendly). Probabilmente soft. |
| **Endpoint `PATCH /admin/clients/{id}/status`** | Sessione 7+ | Pause/resume/archive flow. |
| **Password reset flow** (con token + email link, scadenza 15min) | Sessione 7+ | Già tracciato in sezione Auth. Sarà il primo consumer di `get_unauthenticated_session_tx` → trigger refactor. |
| **Search/filter in `/admin/clients`** | Quando ~20+ client | Niente search bar finché < 10. |
| **Paginazione cursor-based `GET /admin/clients`** | Quando > 50 client | Per ora `ORDER BY created_at DESC LIMIT all`. |
| **Audit log row** per `invitation_created`, `invitation_revoked`, `client_created`, `accept_invite` | Sessione 7+ | Tabella `audit_log` esiste (S2) ma non è ancora popolata. S7+ quando avremo admin actions distruttive (revoke, delete). |
| **Audit log queries dashboard** | Sessione 7+ | `/admin/audit` page con filtri per actor/action/target. |
| **Multi-tenant per utente** (1 email → N clients) | Phase 2 | Refactor: `user_clients` join table, JWT con array `client_ids`, RLS rivedute. Decisione esplicita di S5 + S6 di NON farlo finché non emerge una richiesta concreta dal business (Founder/agenzia). |
| **Rate limiting su `POST /accept-invite`** (attaccante con sniffato URL) | Sessione 7+ con Redis | Mitigazione attuale: HTTPS + breve TTL (7 giorni dev). Provider: Redis (arriva con Celery). |

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

## Brand Brain / AI

### Done

| Item | Quando | Note |
|---|---|---|
| ✅ ~~Setup `pgvector` extension su Supabase~~ | ~~Sessione 7~~ **DONE** | `CREATE EXTENSION IF NOT EXISTS vector;` in migration 0004. |
| ✅ ~~Tabella `brand_chunks` con embedding vector(1536) + HNSW index~~ | ~~Sessione 7~~ **DONE** | Migrations 0004 + 0005, `vector_cosine_ops`, `m=16, ef_construction=64`. Vedi [ADR-0008 §2](./infrastructure/docs/architecture/decisions/0008-brand-brain-foundation.md). |
| ✅ ~~`EmbedderProtocol` + `LLMProtocol` adapter pattern~~ | ~~Sessione 7~~ **DONE** | `app/core/ai/`. PEP 544 Protocol + factory lazy. Concrete: OpenAI text-embedding-3-small + Anthropic Claude Sonnet 4.6. |
| ✅ ~~7 endpoint `/api/v1/clients/{id}/brand/*`~~ | ~~Sessione 7~~ **DONE** | PUT form, POST upload (PDF), POST text, GET assets, DELETE asset, GET history, POST query. |
| ✅ ~~Frontend page `/brand-brain` (Tab Genera live)~~ | ~~Sessione 7~~ **DONE** (Tab Assets/Storico → S7-bis) | Server Component + Client Tabs (shadcn base-nova). GenerateTab con Server Action, output + MetaCard + retrieved_chunks `<details>`. |

### Future (Sessione 8+)

| Item | Note |
|---|---|
| **Caption Agent** con few-shot dal Brand Brain | Sessione 8-9 |
| **Tabella `ai_invocations`** per cost tracking unificato (embed + LLM separati) | Sessione 9-10. Per ora `brand_generations` traccia solo LLM. |
| **Multi-LLM routing cost-aware** (Sonnet vs Haiku) | Sessione 9+. Adapter già pronto, manca il selector. |
| **A/B test embeddings**: OpenAI vs BGE-large-en self-hosted | Sessione 10+ se quality issue emergono o privacy esterni richiesti. |
| **Fine-tuning custom su `brand_generations` history** | Sessione 12+. Append-only è già design-ready. |

---

## Convenzioni

- **Quando completi un item**: sposta in `JOURNAL.md` (entry della sessione corrispondente) e rimuovi da qui.
- **Quando aggiungi un item nuovo**: includi sessione target + 1 riga di motivazione/nota.
- **Item urgenti / blocking**: prefissa con 🚨 e aggiungi in cima alla sezione.
