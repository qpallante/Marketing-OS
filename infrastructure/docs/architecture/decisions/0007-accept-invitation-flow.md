# ADR-0007 — Accept invitation flow + first client_admin onboarding

- **Status**: Accepted
- **Date**: 2026-05-02 / 2026-05-03
- **Authors**: Quintino Pallante

## Context

Sessione 6 chiude il cerchio onboarding iniziato in Sessione 5: un `client_admin` ricevente l'URL invitation deve poter settare la propria password e atterrare automaticamente sulla dashboard del proprio client. Il super_admin (S5) crea client + invitation; in S6 l'invitato accetta.

Prerequisiti già consolidati (decisioni precedenti):
- **JWT bearer + stateless refresh** (ADR-0003): il flow di "auto-login" post-accept emette tokens identici a `/login`.
- **RLS con `current_setting('app.current_client_id')` + `app.is_super_admin`** (ADR-0002): l'utente nuovo nasce nello scope del client dell'invitation, isolato da altri tenant.
- **Schema `invitations`** (ADR-0006): tabella append-only con `accepted_at` / `revoked_at` / `expires_at`. Token plaintext mai persistito (solo SHA-256).

Cosa serviva nuovo in S6:
- Endpoint pubblico (no Bearer) per validare un token in arrivo.
- Endpoint pubblico per submit della password e creazione atomica del User + transizione `accepted_at`.
- Frontend: pagina `/accept-invite?token=...` standalone (pre-auth), form con confirm-password + auto-login post-submit.
- Test paranoici persistenti che verificano gli invarianti security (no information disclosure, idempotency, no-regression S5).

L'accept-invite è il **primo handler pubblico transactional** del progetto. Gli endpoint pre-auth esistenti (`/login`, `/refresh`) leggono soltanto. Il pattern è quindi nuovo e merita formalizzazione.

## Decisions

### 1. `validate_invitation` centralization

**Helper `app/core/invitations.py::validate_invitation(db, token_plaintext) -> Invitation`** unica fonte di verità per i 4 state-check di un invitation token.

Eccezioni dedicate (gerarchia con base `InvitationError`):
- `InvitationNotFoundError` → 404 (no info disclosure sul GET, distinguibile su POST)
- `InvitationRevokedError` → 410 `"invitation revoked"`
- `InvitationAcceptedError` → 410 `"invitation already used"`
- `InvitationExpiredError` → 410 `"invitation expired"`

**Ordine deterministico** del check: not_found > revoked > accepted > expired. Una invitation "expired AND revoked" surfaces come **revoked** (admin action esplicita > scadenza implicita). Test #1 di S5 + #9 di S6 verificano il behavior.

Centralizzare evita drift fra GET preview e POST accept (un endpoint controlla anche `expires_at`, l'altro no, e nessuno se ne accorge). Aggiungere un nuovo state-check (es. `requires_email_verification` in S7+) richiede modifica in **un solo posto**.

**Riferimenti**: `app/core/invitations.py`, smoke_test_session6.py check #1-#5, #9.

### 2. Password policy NIST SP 800-63B aligned

**Min length 12, max 128, no regex compositi, bcrypt cost default 12.**

| Aspetto | Scelta | Rationale |
|---|---|---|
| Min length | 12 | NIST 800-63B § 5.1.1.2 ≥ 8 minimo, 12 è il moderno consensus (1Password, Bitwarden defaults) |
| Max length | 128 | bcrypt accetta max 72 byte; Pydantic accetta 128, bcrypt tronca silenziosamente |
| Regex compositi | NO | NIST 800-63B § 5.1.1.2 sconsiglia: peggiora UX senza più security reale |
| bcrypt cost | 12 (default `bcrypt.gensalt()`) | bumpare a 14 = 4× più CPU. Trigger upgrade: load reali in produzione |

Validazione **server-side autorevole** (Pydantic `StringConstraints(min_length=12, max_length=128)` su `NewPassword` annotated type in `app/schemas/auth.py`). Client-side solo feedback UX (HTML5 + JS counter live "X/12 caratteri minimi" + match check).

**Riferimenti**: `app/schemas/auth.py::NewPassword`, `web-dashboard/src/app/accept-invite/accept-invite-form.tsx`.

### 3. GET preview always-404-on-invalid (no information disclosure)

`GET /api/v1/auth/invitation/{token}` ritorna **sempre 404 generico** per qualsiasi stato invalido (not_found / revoked / accepted / expired). Body: `{"detail":"invitation not found"}` byte-identical.

Rationale: un attaccante con un token plaintext non deve poter distinguere "invito che esiste ma è scaduto" da "invito che non esiste". Anche con un token plaintext rubato, l'enumeration via timing/error-shape è bloccata a livello protocollo.

**Verifica empirica**: smoke_test_session6.py check #9 esegue 4 GET su 4 stati invalidi distinti, asserisce `all(b == bodies[0] for b in bodies)` su raw response text. Pass = no info leak provato, non solo claimed.

UI frontend ribadisce a livello di rendering: 4 fail-paths convergono in `InvitationErrorState` con messaggio generico "Link non valido o scaduto".

**Riferimenti**: `app/routers/auth.py::preview_invitation`, smoke_test_session6.py check #9, `web-dashboard/src/app/accept-invite/page.tsx::InvitationErrorState`.

### 4. POST 410 sub-cases differenziati (UX > security marginal)

Su submit del form, l'utente sta **agendo** — merita feedback specifico per orientarsi:
- 410 `"invitation expired"` → "L'invito è scaduto. Chiedi al super_admin di rigenerarlo."
- 410 `"invitation already used"` → "L'invito è già stato usato. Vai al login se hai già un account."
- 410 `"invitation revoked"` → "L'invito è stato revocato. Contatta il super_admin."

Trade-off vs §3: il GET preview è callable senza azione utente (può essere automatizzato), POST richiede un Submit cosciente + payload (password). La superficie di enumeration è quindi limitata; il valore UX della differenziazione lo giustifica.

`acceptInviteAction` (Server Action) parsa il `detail` con `String.includes` e mappa su messaggi italiani. NB: il backend usa `detail` come API contract — i confronti `includes` sono robusti a piccoli aggiornamenti (es. "invitation already used" → "already accepted") solo se preserviamo le keyword. Tracciato in TODO un coupling implicito.

**Riferimenti**: `app/routers/auth.py::INVITATION_ERROR_HTTP_MAP` + `accept_invite`, `web-dashboard/src/lib/actions/auth.ts::acceptInviteAction`.

### 5. READ COMMITTED + UNIQUE constraint catch (NON SERIALIZABLE)

Per atomicity di **validate + INSERT user + UPDATE invitation** dentro una singola transazione, **READ COMMITTED + IntegrityError catch su `users_email_key`** è sufficiente.

Rationale:
- Postgres ha già un UNIQUE constraint su `users.email`. Se due transazioni concorrenti tentano INSERT con la stessa email, una committa, l'altra riceve `IntegrityError` → catch → 409 `"user already exists with this email"`.
- SERIALIZABLE introduce `serialization_failure` con retry logic obbligatoria (Postgres può abortare transazioni che non possono essere serializzate) + perf cost — overkill per il nostro caso d'uso.
- Pattern già coerente con `admin.py::create_client` (S5): pre-check email + flush + IntegrityError fallback per la race.

Nel handler accept-invite, `try/except IntegrityError` solo dopo `db.flush()` (non dopo `commit()`), così l'eccezione viene catturata prima che il context manager esca, e il rollback dell'intera transazione è garantito.

**Riferimenti**: `app/routers/auth.py::accept_invite`, smoke_test_session6.py check #7.

### 6. Auto-login Opzione A (cookies + redirect /dashboard)

Dopo accept-invite riuscito:
1. Backend genera `access_token` + `refresh_token` (helper `_build_token_pair`, vedi §8).
2. Server Action `acceptInviteAction` setta cookies httpOnly identici a `loginAction` (BFF, ADR-0004).
3. `redirect("/dashboard")` (fuori dal try/catch — NEXT_REDIRECT signal non deve essere consumato dal generic catch).

Pro:
- **Friction minima al primo onboarding**: 1 step (no "vai al login dopo aver settato password").
- **Sicuro**: equivalente a un login successful (token valido + password appena settata = same trust di `/login`).
- **Pattern industry**: Slack, Notion, GitHub fanno lo stesso.

Opzione B scartata (success message + redirect /login con email pre-compilata) perché aggiunge friction senza più sicurezza reale, e non c'è email confirmation step separato che la giustifichi.

**Riferimenti**: `web-dashboard/src/lib/actions/auth.ts::acceptInviteAction`, `app/routers/auth.py::accept_invite` return.

### 7. Public Transactional Handler — pattern emergente

`accept-invite` è il **primo** handler pubblico (no JWT richiesto) + transactional (multi-write atomic) del progetto.

Pattern attuale:
- `Depends(get_unauthenticated_db)` — apre `AsyncSession` ma **non** apre `session.begin()` automaticamente.
- Nel handler: `async with db.begin():` esplicito che inizia la transazione.
- Dentro il context manager: `validate_invitation`, `db.add(new_user)`, `db.flush()`, `IntegrityError` catch, mutazioni su `invitation.accepted_at` / `accepted_by_user_id`, `db.flush()`.
- Exit del context manager → commit automatico.
- **Mai `await session.commit()` esplicito** dentro: chiuderebbe la transazione prima dell'exit → `InvalidRequestError` (lezione consolidata da S5 step 2).

Confronto con `get_authenticated_db` (S2): quel helper apre già `session.begin()` automaticamente perché il contratto ADR-0002 richiede `SET LOCAL ROLE authenticated` + GUC vars dentro la transazione. Per i public handler, il contratto RLS non si applica (postgres role bypass), quindi non c'è `session.begin()` automatico.

**Trigger condition per refactor in helper**:

| Quando | Cosa | Costo evitato |
|---|---|---|
| ≥2 public transactional handler | Creare `get_unauthenticated_session_tx` che apre già `session.begin()` per il consumer | Duplicazione di `async with db.begin():` boilerplate |

Candidati S7+: password-reset (`POST /api/v1/auth/password-reset/complete?token=...`), magic-link login, eventuale signup self-serve. Per ora **1 consumer = no helper**.

**Riferimenti**: `app/routers/auth.py::accept_invite`, ADR-0006 §"Pattern flush() not commit()".

### 8. `_build_token_pair` shared helper — riuso, non estrazione preventiva

Helper esistente in `app/routers/auth.py::_build_token_pair(user: User) -> LoginResponse`. Già usato da `/login`. In S6 viene riusato da `/accept-invite`. **2 consumer ora**.

**Trigger condition per estrazione standalone** (es. `app/core/auth_tokens.py`):
- ≥3 consumer concreti.
- Candidati S7+: password-reset complete (emette nuovi tokens dopo cambio password), magic-link login, signup self-serve, OAuth callback.

Per ora 2 consumer = `_build_token_pair` rimane file-locale dell'auth router. Estraendo prematuramente, paghiamo il cost dell'import + 1 file in più senza beneficio.

**Riferimenti**: `app/routers/auth.py::_build_token_pair` (chiamato da `login` e `accept_invite`), CLAUDE.md "Astrai solo quando hai 3 use case concreti".

## Scope limitations

Decisioni esplicite per S6, deferred a sessioni future:

1. **Email send skip per S6**: il super_admin copia l'invitation_url manualmente (S5) e lo invia al cliente fuori-sistema (Slack, mail manuale). Trigger upgrade a SMTP/SES/Resend: 5+ client onboardati manualmente o feedback esplicito del super_admin. Tracciato in TODO.
2. **Token plaintext SOLO in response 201 di POST /admin/clients (S5)**: mai persistito in DB, mai loggato. Confermato da log review S5 + S6 (`token_prefix` 8 char only).
3. **1 email = 1 user** (no multi-tenancy lato user): un utente con ruoli in più client richiederebbe `user_clients` join table. Decisione esplicita di S5 e S6 di NON farlo finché non emerge un caso d'uso documentato (es. agency che gestisce 5 client). Tracciato in TODO.
4. **Re-invite logic UI**: l'invitation creata via S5 deve essere revocata manualmente via DB direct prima di re-invitare. UI per super_admin "revoke + re-invite" pendente S7+. Tracciato in TODO.
5. **Email verification al accept**: skip. L'invitation arriva via canale fidato dal super_admin, l'accept dimostra possesso del link (proof-of-possession sufficiente). Da rivisitare se aggiungeremo signup self-serve pubblico in S7+.

## Consequences

### Positive

- **Onboarding flow completo end-to-end**: super_admin (S5) → email/link manuale → client_admin accept (S6) → /dashboard. Zero dipendenze esterne (SMTP, OAuth IdP, magic-link service).
- **No information disclosure** verificato empiricamente (byte-identical bodies, smoke check #9). Pattern riutilizzabile per future operazioni public lookup (password-reset preview, account recovery).
- **Password policy moderna** allineata a NIST 800-63B: niente regex compositi che peggiorano UX, focus su lunghezza + bcrypt cost ragionevole.
- **Public Transactional Handler** identificato come pattern: la prossima operazione del genere (password-reset complete) potrà riusare la struttura senza re-discovering trade-off.
- **18 paranoid checks persistenti totali** (9 da S5 + 9 da S6, ~80 sub-asserzioni). Pattern `smoke_test_sessionN.py` in `scripts/` ora è la convenzione del progetto.

### Negative / trade-off

- **Coupling implicito frontend→backend `detail` strings**: `acceptInviteAction` parsa "expired" / "already used" / "revoked" da `detail` per UX. Cambiare il wording lato backend rompe la differenziazione 410 lato frontend silenziosamente. Mitigazione: smoke_test_session6.py check #2/#3/#4 verifica `detail` esatto. Future: header dedicato `X-Invitation-State` con enum, parsing strict frontend.
- **`get_unauthenticated_db` + `session.begin()` esplicito è duplicazione**: un solo handler oggi, due ridondanze evitabili (apertura context manager + cleanup). Scelta: aspettare il 2° consumer prima di astrarre (vedi §7).
- **`_build_token_pair` non standalone**: import da `routers/auth.py` se in S7 servirà fuori dal router → refactor "free" perché è già pure function. Ma se forgetting why è pure function (closure su `settings` modulo-level), un consumer fuori dal modulo potrebbe sorprendersi. Da rivisitare al 3° consumer.
- **Auto-login Opzione A su token recente**: se un attaccante riesce a sniffare un invitation_url **e** a essere il primo a accettarlo, ottiene direttamente un account loggato. Mitigazione: HTTPS + breve TTL invitation (7 giorni dev). Future: rate limiting su POST `/accept-invite` per IP (Redis-based, S7+).

## Lessons learned

- **"Verify pattern più semplice già usato altrove prima di accettare l'over-engineering"**. Il piano iniziale di S6 proponeva `SERIALIZABLE` per atomicity. La proposta alternativa (READ COMMITTED + UNIQUE catch + 409, già usata in `admin.py::create_client`) è stata adottata. La lezione è ora un feedback memory persistente: quando vedo SERIALIZABLE/mutex/retry-budget, prima cerco nel codebase un constraint DB o pattern esistente che già copre l'invariante.
- **"Always-404-on-invalid" è una proprietà che si verifica empiricamente, non si claima**. Smoke check #9 confronta byte-by-byte i 4 response body su 4 stati invalidi distinti. È diverso da "controlliamo i 4 detail e sono tutti uguali al claim del codice" — qui mostriamo che dal punto di vista network, gli stati sono indistinguibili.
- **"Public Transactional Handler" è pattern, non template**. È nato dall'esercizio reale di accept-invite. Sarà formalizzato in helper (`get_unauthenticated_session_tx`) solo quando arriverà il 2° consumer concreto. Pattern ≠ template: aspettare la duplicazione prima di astrarre, evitare premature abstraction.
- **Idempotenza dei test paranoici**: smoke_test_session6.py al 2° run di seguito è ancora 9/9 grazie a `_pre_cleanup` + `_post_cleanup` con CASCADE. Costo trascurabile, valore enorme: scriptable in CI senza state management.
- **Non-regressione di S5 dopo S6**: smoke_test_session5.py continua a passare 9/9 dopo modifiche al router auth + nuova migration + nuovi schemi. Pattern test persistente per sessione + check di non-regressione = sistema robusto a 6 sessioni di iterazione.

## Da rivalutare quando

- **SERIALIZABLE necessario?** — Solo se vediamo race condition reali in produzione che UNIQUE catch non gestisce. Trigger: ≥1 caso documentato di data corruption attribuibile a race.
- **SMTP per invitation send?** — Quando il super_admin si stanca di copia/incolla manuale o quando onboardiamo > 5 client al mese. Provider candidato: Resend (developer-friendly) o AWS SES (low-cost a volume).
- **`get_unauthenticated_session_tx` helper?** — Quando arriva il 2° public transactional handler. Candidato più probabile: password-reset complete in S7+.
- **`_build_token_pair` standalone helper?** — ≥3 consumer concreti.
- **UI revoke + re-invite invitation?** — Trigger: ≥1 invitation reale che deve essere ri-inviata. Per ora UPDATE diretto via `psql` o smoke test scripting.
- **Multi-tenancy lato user (1 email = N client memberships)?** — Trigger: ≥1 caso d'uso reale documentato (es. agency operativa che gestisce 5 client).
- **Header `X-Invitation-State` con enum invece di parsing `detail`?** — Quando aggiungeremo il 4° sub-stato di 410 (es. `requires_email_verification`) e il parsing `String.includes` diventerà ambiguo.

## References

- ADR-0002 (RLS contract — il public handler bypassa, contratto rispettato altrove)
- ADR-0003 (JWT auth backend, `_build_token_pair` shape, lifetimes)
- ADR-0004 (frontend BFF, cookie storage, redirect pattern)
- ADR-0005 (`/api/auth/*` family, Server Components cookie limitation)
- ADR-0006 (invitations schema, token strategy, `flush()` vs `commit()`)
- `app/core/invitations.py`, `app/schemas/auth.py`, `app/routers/auth.py`
- `web-dashboard/src/app/accept-invite/page.tsx`, `accept-invite-form.tsx`
- `web-dashboard/src/lib/actions/auth.ts::acceptInviteAction`
- `core-api/scripts/smoke_test_session6.py`
- `core-api/alembic/versions/0003_invitation_accepted_by.py`
