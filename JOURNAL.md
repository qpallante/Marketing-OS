# Journal — Marketing OS

Diario operativo del progetto. Tono asciutto, onesto. Riporta come si è arrivati alle decisioni, le frizioni reali, ciò che vale ricordare a 6 mesi di distanza. Non duplica codice, ADR o CLAUDE.md.

Entry in ordine cronologico inverso (più recenti in alto). Aggiornamento manuale, vedi sezione *Workflow* in fondo.

---

## 2026-05-03 — Sessione 6: Accept invitation flow + first client_admin onboarding

**Stato**: chiusa. 3 commit su `origin/main`: feat(auth-backend), feat(auth-frontend), docs(adr+journal) ADR-0007.

### Cosa è stato fatto

**Backend** (`core-api/`):
- **Migration 0003** (`alembic/versions/0003_invitation_accepted_by.py`): aggiunge `accepted_by_user_id UUID NULL FK users(id) ON DELETE SET NULL` alla tabella `invitations`. Rows pending esistenti restano NULL — popolato durante accept-invite insieme a `accepted_at` (linkati 1:1).
- **Helper centralizzato** (`app/core/invitations.py`): `validate_invitation(db, token_plaintext) -> Invitation` con 4 eccezioni dedicate (`InvitationNotFoundError`, `InvitationRevokedError`, `InvitationAcceptedError`, `InvitationExpiredError`). Ordine deterministico not_found > revoked > accepted > expired (admin action esplicita > scadenza implicita).
- **Schema extraction** (`app/schemas/auth.py`): trigger ADR-0006 §"Schema extraction" superato — 7 schemi distinti per il router auth. Spostati `LoginRequest`, `LoginResponse`, `RefreshRequest`, `MeResponse`, `ClientSummary` (auth-flavor, no `created_at`), aggiunti `InvitationPreviewResponse`, `AcceptInviteRequest`. Validators riusabili: `InvitationToken` (43 char esatti) + `NewPassword` (12-128).
- **GET preview** (`app/routers/auth.py::preview_invitation`): public, `openapi_extra={"security": []}`. Sempre **404 generico** per qualsiasi stato invalido. Logging `auth.invitation_preview` con `success`, `reason` (class name eccezione, mai detail), `token_prefix` (8 char di 43).
- **POST accept** (`app/routers/auth.py::accept_invite`): public, single transaction (validate + INSERT user + UPDATE invitation), READ COMMITTED + `IntegrityError` catch su `users_email_key` → 409. Pattern Public Transactional Handler (vedi sotto). 410 differenziato per UX (expired/already used/revoked) tramite `INVITATION_ERROR_HTTP_MAP`. Riusa `_build_token_pair(new_user)` esistente. Logging `auth.invitation_accepted` con `email_hash` SHA-256, `invitation_id`, `new_user_id`, `client_id`, `role` — mai password, mai token plaintext, mai email plaintext.
- **9 paranoid checks persistenti** in `scripts/smoke_test_session6.py` (~510 righe, pattern aderente a `scripts/smoke_test_session5.py`). 37 sub-asserzioni totali. Idempotente (pre/post cleanup CASCADE). Test #9 verifica **byte-equality** dei 4 body 404 su 4 stati invalidi distinti — no info disclosure provato empiricamente, non claimed.

**Frontend** (`web-dashboard/`):
- **Pagina `/accept-invite?token=...`** (`src/app/accept-invite/page.tsx`): Server Component standalone (NON `(dashboard)` group). Fetch GET preview con `auth: false`. 4 fail-paths convergono in `InvitationErrorState` (token assente/malformato, 404 backend, network, parse) → messaggio generico, link `/login`. Card shadcn centrato, no sidebar/topbar.
- **Form `AcceptInviteForm`** (Client Component): `useActionState` + `useState` per `password`/`confirmPassword`. Counter live "X/12 caratteri minimi" con classi `text-emerald-600` (≥12) / `text-muted-foreground` (<12). Confirm-password match check inline. Submit `disabled` finché `passwordValid && passwordsMatch && !isPending`. Confirm-password **NON** inviato al server (solo client-side guard contro typo).
- **Server Action `acceptInviteAction`** in `lib/actions/auth.ts`: server-side validation autorevole (token len=43, password 12-128). Mapping 404/410/422/409/5xx → messaggi italiani user-friendly. **410 differenziato** in 3 sub-cases parsando `detail`. Cookies httpOnly identici a `loginAction`. `redirect("/dashboard")` **fuori** dal try/catch (NEXT_REDIRECT signal non deve essere consumato dal generic catch).
- **Types** (`src/lib/types.ts`): nuovo `InvitationPreviewResponse`.
- **Proxy verificato**: `PROTECTED_PREFIXES` non include `/accept-invite` — anonimi raggiungono la pagina con HTTP 200, senza redirect a `/login`.

**E2E**: 9/9 paranoid backend + 27/27 HTML render (`/tmp/test_step5.sh` durante S6) + 7/7 E2E backend flow (super_admin crea → accept-invite → /me → re-login). Tutti pass. Smoke S5 continua 9/9 (no regression).

### Frizioni reali

1. **`alembic_version` non visibile da role `authenticated`**. Subito dopo `alembic upgrade head`, query `SELECT version_num FROM alembic_version` con role-swap a `authenticated` ritorna `None` (la system table di alembic non ha GRANT esplicito per `authenticated`). Da role `postgres` (no swap) ritorna `0003_invitation_accepted_by` correttamente. Non è un bug — è behavior atteso (alembic_version è metadata, no RLS, accessibile solo al ruolo che ha eseguito la migration). Documentato per debug futuro: se uno smoke test deve verificare la versione, usare connection senza `SET LOCAL ROLE authenticated`.
2. **Pattern Public Transactional Handler nato qui**. `get_unauthenticated_db` apre solo `AsyncSession`, NON `session.begin()`. Mentre `get_authenticated_db` apre già il context manager (ADR-0002 richiede SET LOCAL dentro la transazione). Per accept-invite — pre-auth ma multi-write — abbiamo aperto `async with db.begin():` esplicito nel handler. Trigger condition per refactor in helper `get_unauthenticated_session_tx`: ≥2 public transactional handler. ADR-0007 §7 documenta.
3. **Coupling implicito frontend→backend `detail` strings**. `acceptInviteAction` parsa `detail.includes("expired")` / `"already used"` / `"revoked"` per UX 410. Cambiare wording lato backend rompe il match silenziosamente. Mitigazione attuale: smoke test #2/#3/#4 verifica `detail` esatto. Long-term: header dedicato `X-Invitation-State` con enum (TODO S7+).
4. **Step 5 prompt re-incollato per errore**. Ho riconosciuto il duplicato e ho confermato lo stato senza rifare il lavoro — risparmio di tempo. Lezione: prima di fare qualcosa, verificare lo stato corrente del filesystem (`ls`, `grep`) — è sempre più veloce.
5. **8 false-fail iniziali in `/tmp/test_step3.sh` e `/tmp/test_step4.sh`** dovuti a portabilità BSD vs GNU (`head -n -1` non supportato), ANSI escape codes nel structlog console renderer (`email_hash=` separato da `\x1b[0m`), import errati (`_engine` vs `async_session_factory`). Tutti bug del test runner, **zero** bug del codice. Stessa lezione di S5: il test environment merita lo stesso scrutinio del codice di produzione.

### Decisioni rilevanti

8 decisioni in [ADR-0007](./infrastructure/docs/architecture/decisions/0007-accept-invitation-flow.md):

1. `validate_invitation` centralization — single source of truth per i 4 state-check
2. Password policy NIST 800-63B aligned — 12 char min, no regex compositi, bcrypt 12
3. GET preview always-404-on-invalid — byte-identical bodies, no info disclosure
4. POST 410 sub-cases differenziati — UX > security marginal su submit
5. READ COMMITTED + UNIQUE constraint catch — no SERIALIZABLE
6. Auto-login Opzione A — cookies + redirect /dashboard, pattern Slack/Notion/GitHub
7. Public Transactional Handler — pattern emergente, helper deferred a 2° consumer
8. `_build_token_pair` shared — riuso, non estrazione preventiva (≥3 consumer trigger)

### Lezioni apprese

- **"Verify pattern più semplice già usato altrove prima di accettare l'over-engineering"**: il piano iniziale proponeva SERIALIZABLE per atomicity. Disagree gentilmente con UNIQUE catch + 409 (già in `admin.py::create_client`) → adottato. Lezione salvata in feedback memory persistente.
- **"Always-404-on-invalid" si verifica empiricamente, non si claima**: smoke check #9 confronta byte-by-byte i 4 response body. Diverso dal "controlliamo i 4 detail dal lato code review" — qui mostriamo che dal punto di vista network gli stati sono indistinguibili.
- **Pattern test persistente vs inline**: `/tmp/test_stepN.sh` ottimo per iterare durante una sessione, ma non sopravvive al riavvio del Mac. `scripts/smoke_test_sessionN.py` in version control è il pattern stabilito (S5 + S6 ora). Idempotenza + non-regression check del file precedente è ora la convenzione.
- **Non-regressione automatica**: lanciare `smoke_test_session5.py` dopo modifiche di S6 — entrambi 9/9 — dimostra che il sistema regge 6 sessioni di iterazione. Costo: ~30 secondi a smoke test.
- **Public Transactional Handler ≠ template**: nato dall'esercizio reale di accept-invite. Aspettiamo il 2° consumer concreto (password-reset più probabile candidato) prima di astrarre in helper. Premature abstraction è anti-pattern documentato in CLAUDE.md.

### Da ricordare

- **Pattern famiglia `/api/v1/admin/*`** (S5) + **`/api/v1/auth/*`** ora include 5 endpoint: `/login`, `/refresh`, `/me`, `/invitation/{token}` (S6 GET preview), `/accept-invite` (S6 POST submit).
- **Schema extraction trigger consolidato**: ≥5 schemi distinti per router → estrarre in `app/schemas/<area>.py`. Auth router migrato in S6 con 7 schemi.
- **Pattern `validate_<entity>` con eccezioni dedicate**: helper centralizza state-check, gerarchia di eccezioni mappabili a HTTP codes. Riutilizzabile per future entity (es. `validate_password_reset_token`, `validate_magic_link`).
- **Public Transactional Handler**: `Depends(get_unauthenticated_db)` + `async with db.begin():` esplicito. Quando arriverà il 2° consumer, helper `get_unauthenticated_session_tx`.
- **Smoke test persistente per sessione**: `scripts/smoke_test_sessionN.py`, 9 paranoid checks, idempotente con CASCADE cleanup, includere check #9 di "no info disclosure" quando applicabile.

### TODO emersi durante S6

- [ ] **SMTP/SES/Resend** per email invitation automatica (S7+)
- [ ] **UI super_admin per revoke + re-invite** invitation (S7+)
- [ ] **`get_unauthenticated_session_tx` helper** quando 2° public transactional handler (probabile password-reset S7+)
- [ ] **Estrazione `_build_token_pair` in helper standalone** (`app/core/auth_tokens.py`?) quando ≥3 consumer
- [ ] **Header `X-Invitation-State` con enum** invece di parsing `detail` lato frontend (quando 4° sub-stato 410)
- [ ] **Regression script**: `scripts/run_all_smoke_tests.sh` che lancia smoke S2/S5/S6 in sequenza. Pre-commit hook futuro?

---

## 2026-05-02 — Sessione 5: Admin clients endpoints + onboarding wizard

**Stato**: chiusa. 3 commit su `origin/main`: feat(admin-backend), feat(admin-frontend), docs(adr) ADR-0006 + JOURNAL + TODO.

### Cosa è stato fatto

**Backend** (`core-api/`):
- **Schema `invitations`** (`app/models/invitation.py`, migration `0002_invitations.py`): UUIDPKMixin + CreatedAtMixin (no `updated_at` — append-only by design, state derivato da `{accepted_at, revoked_at, expires_at}`). FK CASCADE su `client_id`, FK SET NULL su `invited_by_user_id` (accountability dopo offboarding admin). UNIQUE PARTIAL INDEX su `(client_id, email) WHERE accepted_at IS NULL AND revoked_at IS NULL` per impedire più pending simultanee per la stessa coppia.
- **RLS `005_invitations.sql`**: super_admin only su tutte le operazioni (S5 baseline). Pattern `current_setting('app.is_super_admin')` consolidato dal contratto ADR-0002. Policy `client_admin SELECT own client invitations` rimandata a S6 (portale onboarding lato cliente).
- **Token strategy** (`app/core/security.py::generate_invitation_token`): `secrets.token_urlsafe(32)` plaintext (256 bit, 43 char URL-safe) + SHA-256 hex (64 char) in DB. Pattern industry (GitHub PAT, Stripe restricted keys). Nessun bcrypt: il token è già high-entropy, short-lived, no riuso cross-site.
- **Pattern famiglia `/api/v1/admin/*`** (`app/routers/admin.py`): consolidato simmetrico a `/api/v1/auth/*`. Endpoint S5: `POST /clients` (crea client + invitation) e `GET /clients` (lista). Tutti `Depends(require_super_admin)` + RLS authoritative.
- **Schemas extraction** (`app/schemas/admin.py`): 5 Pydantic models distinti → trigger di estrazione superato. Auth router resta inline (4 schemi); migra in S6+.
- **Logging GDPR-friendly**: `invitation_created` structured log con `email_hash` (SHA-256 hex), `invitation_id`, `client_id`, `expires_at`, `invited_by`, `role`. **Mai presente**: email plaintext, token plaintext, `invitation_url` completo. Plaintext esiste UNA SOLA VOLTA, nel response body 201.
- **9 test paranoici** in `tests_smoke/`: RLS bypass attempts (3 ruoli × INSERT/SELECT/DELETE), plaintext leakage, FK CASCADE behavior, UNIQUE PARTIAL INDEX (più pending stessa email/client), role enforcement at endpoint (403 client_admin), email lowercase normalization. **9/9 pass + 26 sub-asserzioni**.

**Frontend** (`web-dashboard/`):
- **Route group `(dashboard)/admin/*`** con layout guard secondario: `getCurrentUser()` cached (zero overhead vs parent), redirect silenzioso a `/dashboard` se non super_admin (security through obscurity, no 403 esplicito).
- **Sidebar role-aware**: prop `isSuperAdmin?: boolean`, sezione "Admin" condizionale separata visivamente. client_admin/member non vedono trace dell'area admin.
- **Proxy update**: `/admin` aggiunto a `PROTECTED_PREFIXES` per fast-path redirect anonimi a `/login`.
- **Lista clients** (`(dashboard)/admin/clients/page.tsx`): Server Component fetch via `backendFetch`, Card per cliente con StatusBadge inline (no shadcn Badge, span Tailwind con preset emerald/amber/muted), empty state.
- **New client form** (`(dashboard)/admin/clients/new/`): Server Component thin host + Client Component con `useActionState`. **NO redirect post-success**: success panel inline con campo readonly + bottone "Copia link" (`navigator.clipboard.writeText`, feedback "Copiato!" 2s, fallback select-on-focus). Il super_admin DEVE vedere e copiare l'URL — il plaintext esiste solo in questa response.
- **Server Action `createClientAction`**: error mapping differenziato 401/403/409/422/5xx. 409 con detail "email already in use" / "slug already exists" → field-error attribuito al campo giusto. 422 generico (no parsing Pydantic verbose). `revalidatePath("/admin/clients")` post-success.
- **Types extension**: `ClientSummary.created_at?: string` (optional perché /me non lo serializza, /admin/clients sì), nuovi `InvitationRole`, `InvitationSummary`, `CreateClientResponse`, `ListClientsResponse`.

**E2E**: 41 sub-asserzioni live contro frontend :3001 + backend :8001. Coprono auth flow super_admin/monoloco_admin, UI gates (sidebar role-aware, layout guard, proxy redirect), creation flow end-to-end (status, body parse, log strutturato, FRONTEND_URL=:3001), revalidatePath, validation 422 (3 cases) + 409 con detail attribuibile (dup slug `monoloco`, dup email `admin@monoloco.example`), cleanup CASCADE. (e) clipboard via static analysis del bundle (sostituto del manual click — pattern accettato per assenza di Playwright). 41/41 pass.

### Frizioni reali

1. **`flush()` vs `commit()` nel context manager auto-commit**. Smoke test step 2 lanciava `InvalidRequestError: Can't operate on closed transaction` perché il router faceva `await session.commit()` esplicito, ma `get_authenticated_session` apre già `async with session.begin()` come context manager auto-commit. Il commit chiudeva la transazione prima dell'exit del context manager. Fix: `await session.flush()` per ottenere `id` generati senza chiudere — il commit avviene automaticamente quando l'handler termina e FastAPI consuma il generator dependency. Lezione documentata inline nel router admin + ADR-0006 §4.
2. **Pattern famiglia `/api/v1/admin/*`** è la prima volta che decidiamo a freddo come organizzare i prossimi N admin endpoint (DELETE client, PATCH status, resend/revoke invitation, audit log queries). Decisione: un solo `app/routers/admin.py` con tutto lì dentro finché non supera ~10 endpoint, poi split per area (clients, invitations, audit). Stessa filosofia "split solo quando giustificato dal volume" del frontend.
3. **Schema extraction trigger** = quando i schemi distinti superano ~5. Auth router con 4 inline è ancora OK; admin con 5 → estratto in `app/schemas/admin.py`. Niente refactor preventivo dell'auth router; migrerà in S6+ se introdurrà nuovi schemi.
4. **NO redirect post-creation client** è una decisione di security UX poco intuitiva. Pattern naturale per un form "wizard" è redirect alla lista; ma qui il plaintext token esiste solo nella response 201 — se redirigiamo, il super_admin perde l'unica chance di copiarlo. Quindi success panel inline + bottone "Torna alla lista" esplicito. Documentato in ADR-0006 §7 e nel commento `NewClientForm`.
5. **Proxy `/admin` aggiunto a PROTECTED_PREFIXES** non è strettamente necessario (layout `(dashboard)` già fa redirect via cookie check), ma serve a fare il redirect più early — prima del rendering. Coerente con gli altri 4 prefix protected. Costo zero, sicurezza marginalmente migliore.
6. **Status badge inline** invece di shadcn Badge: niente nuova dependency. Per 3 stati discreti con icone solo testuali, uno `<span>` con `Record<status, classNames>` è più semplice e zero kb. Quando arriveranno status più articolati o avremo già Badge per altri usi, si potrà standardizzare.
7. **41 E2E sub-asserzioni live** scoprivano 8 bug di scripting iniziali (BSD `head -n -1` non supportato, ANSI escape codes nello structlog console renderer che spezzavano il regex `email_hash=`, import sbagliato `_engine` vs `async_session_factory` in cleanup, regex `super_admin` vs response "super admin" con spazio). Tutti bug del test runner, zero bug del codice. Lezione: il test environment merita lo stesso scrutinio del codice di produzione — i false-fail sono cari da debuggare.

### Decisioni rilevanti

Tutte tracciate in [ADR-0006](./infrastructure/docs/architecture/decisions/0006-admin-clients-onboarding.md). Punti critici:

- **Pattern famiglia `/api/v1/admin/*`** simmetrica a `/api/v1/auth/*`.
- **Token plaintext one-shot**: SHA-256 in DB, mai recuperabile. Pattern industry.
- **State derivation > state column** in `invitations`: nessun trigger, nessun desync.
- **Logging GDPR-friendly**: `email_hash` standardizzato attraverso auth + admin.
- **NO redirect post-success**: il super_admin DEVE vedere `invitation_url` inline.
- **Error mapping 401/403/409/422/5xx differenziato**: ogni status suggerisce un'azione diversa all'utente.
- **UI guard layered**: proxy fast-path → layout role-check → backend RLS.
- **Scope limitations esplicite** (S6+): email automatica, accept-invite endpoint, resend/revoke, RLS client_admin own-invitations, multi-tenant per utente, paginazione, audit_log row.

### Lezioni apprese

- **Trap n.3 di S5 dopo middleware→proxy (S4) e Server Component cookies (S4-bis)**: `flush()` vs `commit()` dentro `Depends` con context manager auto-commit. Pattern emergente: ogni session/router/middleware piuttosto recente in async Python ha trappole specifiche; la docs ufficiale è la prima fonte, non il codice di esempio web.
- **Test paranoici come fixture stabile del workflow**: S2 RLS, S3 auth, S5 admin. Diventano la "canary line" di sicurezza prima di toccare endpoint che mescolano permission + cross-tenant + plaintext sensibile. ~30 minuti di scrittura, salvano ore di debug post-deploy.
- **Bug del test runner ≠ bug del codice**: 8 false-fail iniziali tutti dovuti a portabilità BSD/GNU + ANSI escapes + import errati. Diagnosticabili in 5 minuti se si parte dall'ipotesi "il codice funziona, lo script è rotto" — il contrario costa molto.
- **Decidere di NON redirigere è anch'essa una decisione architetturale**: il pattern naturale era redirect-to-list post-create. Override esplicito perché il plaintext token vincolerebbe l'utente a perderlo. Documentato in ADR-0006 perché è il tipo di decisione che 6 mesi dopo qualcuno potrebbe "ottimizzare via" senza capire la motivazione.

### Da ricordare

- **Pattern famiglia `/api/v1/admin/*`**: ogni futuro endpoint admin server-side va lì. Stessa estensione del backend `/api/v1/auth/*`. Speculare al frontend `/api/auth/*`.
- **Plaintext token policy**: `secrets.token_urlsafe(32)` + SHA-256 hex in DB; plaintext esiste UNA SOLA VOLTA nel response 201; mai loggato; UI mostra inline + bottone copia. Questo template si ripete per password reset (S5+), email verification (S5+), API keys (Phase 2).
- **State derivation > status column** per entità append-only: `invitations.{accepted_at, revoked_at, expires_at}` derivano lo stato. Riutilizzabile per `audit_log`, future `webhooks`, `api_keys`.
- **`flush()` not `commit()`** dentro un handler che usa `Depends(get_authenticated_session)`. Documentato inline nei router; quando aggiungeremo nuovi handler con scrittura DB, ricordare il pattern.

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
