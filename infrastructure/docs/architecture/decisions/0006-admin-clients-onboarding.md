# ADR-0006 — Admin clients endpoints + onboarding wizard

- **Status**: Accepted
- **Date**: 2026-05-02
- **Authors**: Quintino Pallante

## Context

Sessione 5: si entra nella fase di multi-tenant onboarding. Finora il bootstrap dati avviene via seed SQL (un super_admin globale + un client `Monoloco` con relativo client_admin). Per scalare oltre il primo cliente serve un meccanismo applicativo.

Requisiti emersi durante l'allineamento iniziale di S5:

1. **Solo super_admin** può creare nuovi clients. Client_admin/client_member NON devono poter creare cross-tenant.
2. **Una sola email globalmente unica** = un solo `users` row. Niente account per-client (no "stesso utente in più tenant"). Decisione esplicita: scope di Fase 1; rivedere se Fase 2 richiederà multi-tenant per utente.
3. **Invitation flow asincrono**: il super_admin crea il client + emette un'invitation per il primo `client_admin`. L'invitation è un token one-shot via URL. Niente email automatica in S5 (verrà in S6 con SES/Resend); per ora il token plaintext è ritornato nella response 201 e il super_admin lo copia/incolla manualmente al cliente.
4. **Sicurezza del token**: il plaintext esiste UNA SOLA VOLTA, in DB c'è solo SHA-256. Coerente con pattern industry (GitHub PAT, Stripe restricted keys).
5. **Logging GDPR-friendly**: niente PII plaintext nei log strutturati. Email → SHA-256 hash; token → mai loggato; invitation_id e client_id (UUID v4 random) sono OK.
6. **TTL invitation**: 7 giorni (allineato col refresh token TTL dev — catch breakage early). Hardcoded come `INVITATION_TTL_DAYS`, env-tunabile in S6+ se serve.

L'altro pezzo è il frontend: una zona admin dentro la dashboard, accessibile solo a super_admin, con lista clients + wizard "nuovo cliente" che invoca l'endpoint backend e mostra l'invitation_url al super_admin.

## Decision

### 1. Pattern famiglia `/api/v1/admin/*` (server-side admin operations)

Consolidiamo un nuovo pattern di routing nel core-api, parallelo a `/api/v1/auth/*`:

| Famiglia | Cosa contiene | Auth |
|---|---|---|
| `/api/v1/auth/*` | Token operations (login, refresh, me) | Misto (login/refresh public, me protected) |
| `/api/v1/admin/*` | Operazioni amministrative cross-tenant | Sempre `Depends(require_super_admin)` + RLS |

Tutto in `app/routers/admin.py`. Mountato in `main.py` con `prefix="/api/v1/admin"`. Endpoint S5:

- `POST /clients` — crea nuovo client + invitation
- `GET /clients` — lista tutti i client (no paginazione finché < 50)

S6+ aggiungerà `DELETE /clients/{id}`, `PATCH /clients/{id}/status`, `POST /invitations/{id}/resend`, `POST /invitations/{id}/revoke`. Same prefix, stesso guard.

### 2. Modello `invitations` + RLS super_admin only (S5 baseline)

Tabella `invitations` con campi essenziali:

```sql
id UUID PK (gen_random_uuid)
client_id UUID FK clients ON DELETE CASCADE NOT NULL
email TEXT NOT NULL CHECK (email = lower(email))
role TEXT NOT NULL CHECK (role IN ('client_admin', 'client_member'))
token_hash TEXT NOT NULL UNIQUE  -- SHA-256 hex (64 char)
invited_by_user_id UUID FK users ON DELETE SET NULL  -- accountability after admin offboarding
expires_at TIMESTAMPTZ NOT NULL
accepted_at TIMESTAMPTZ NULL
revoked_at TIMESTAMPTZ NULL
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
-- updated_at: NO. Append-only by design — state derived from {accepted_at, revoked_at, expires_at}
```

**State derivation, non state column**: niente `status` enum. Lo stato dell'invitation è puramente derivabile dalle tre timestamp:

- `accepted_at IS NOT NULL` → accepted
- `revoked_at IS NOT NULL` → revoked
- `now() > expires_at` (e gli altri null) → expired
- altrimenti → pending

Vantaggi: niente desync tra status e timestamp, niente trigger di transizione, niente `CHECK (status = 'accepted' AND accepted_at IS NOT NULL)` ridondante.

**UNIQUE PARTIAL INDEX** su `(client_id, email) WHERE accepted_at IS NULL AND revoked_at IS NULL` per impedire più invitation pending simultanee per la stessa coppia. Index PARZIALE (non constraint UNIQUE puro) perché un'invitation accettata + una nuova pending devono coesistere.

**RLS S5 baseline**: super_admin only su tutte le operazioni (SELECT/INSERT/UPDATE/DELETE). In S6 aggiungeremo policy `client_admin can SELECT own client invitations` quando lavoreremo sul portale di onboarding lato cliente. Per ora il super_admin gestisce tutto da `/admin/*`.

### 3. Token strategy: plaintext one-shot + SHA-256 hash in DB

```python
def generate_invitation_token() -> tuple[str, str]:
    plaintext = secrets.token_urlsafe(32)  # 256 bit, 43 char URL-safe
    token_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    return plaintext, token_hash
```

- **Plaintext**: 256 bit di entropia (`secrets.token_urlsafe(32)`). Sufficiente: anche un attaccante con accesso completo al DB non può fare brute-force (servirebbero 2^255 hash SHA-256 in media).
- **Hash in DB**: solo SHA-256, niente salt/bcrypt. Motivazione: il token plaintext è già di alta entropia e short-lived (7 giorni); non c'è il problema "password riutilizzata fra siti" tipico del bcrypt password hashing. Consultare BCS o `secrets.compare_digest` quando confronteremo (S6+ accept-invite flow). Pattern: GitHub PAT, Stripe restricted keys — same approach.
- **Plaintext esiste UNA SOLA VOLTA**: nel response body di `POST /admin/clients` (campo `invitation.invitation_url`). Dopo non è più recuperabile. Se il super_admin perde il link, deve revocare l'invitation e crearne una nuova (S6+).

### 4. Pattern `flush()` invece di `commit()` dentro la session di Depends

`get_authenticated_session` apre `async with session.begin()` come context manager auto-commit. Dentro l'handler, scriviamo con `session.flush()` per ottenere `id` generati (es. `client.id` da `gen_random_uuid()`) e per scrivere le righe senza chiudere la transazione.

**NON** chiamare `session.commit()` esplicitamente: chiuderebbe la transazione prima dell'exit del context manager → SQLAlchemy lancia `InvalidRequestError: Can't operate on closed transaction`.

Lezione consolidata in S5 step 2 dopo errore in fase di smoke test. Documentato inline in `app/routers/admin.py::create_client`.

### 5. Logging GDPR-friendly per `invitation_created`

Structured log con `structlog` (formato `key=value` in dev, JSON in prod):

```
invitation_created
  invitation_id=<uuid>
  client_id=<uuid>
  email_hash=<sha256_hex>
  role=client_admin
  expires_at=<iso8601>
  invited_by=<uuid>
```

**Mai presente**:
- `email` plaintext
- `token` plaintext (né `invitation_url` completo)

Pattern già usato in `auth.login_failed` (`attempted_email_hash`). Standardizzato in `_email_hash(email)` helper duplicato in `auth.py` e `admin.py` (per ora; consolideremo in `app/core/security.py` quando avremo 3 chiamanti — YAGNI).

### 6. Frontend: route group `(dashboard)/admin/*` + sidebar role-aware

**Route group + layout guard secondario**:

- `(dashboard)/admin/layout.tsx` Server Component. Chiama `getCurrentUser()` (cached da `React.cache`, zero overhead vs layout parent). Se `user.role !== "super_admin"` → `redirect("/dashboard")` silenzioso. **Non un 403 esplicito**: vogliamo che `/admin` sembri "non esistere" per non-super_admin (security through obscurity, light layer).
- Backend RLS è autorevole: anche con UI bypassed, un client_admin riceve 403 dal backend. Il guard frontend è UX, non security.

**Sidebar role-aware**:

- Prop `isSuperAdmin?: boolean` passato dal layout `(dashboard)`.
- Sezione "Admin" condizionale, nuova heading uppercase, separata visivamente dal blocco principale. Sub-link `/admin/clients`.
- client_admin / client_member vedono solo MAIN_NAV — nessun trace dell'area admin.

**Proxy pattern**:

- `/admin` aggiunto a `PROTECTED_PREFIXES` in `src/proxy.ts`. Anonimi (no cookie) → fast-path redirect a `/login` prima del rendering. Coerente con `/dashboard`, `/content`, `/analytics`, `/settings`.

### 7. New client form: NO redirect post-success, success panel inline

Pattern Client Component con `useActionState`:

- Submit → Server Action `createClientAction` → backend POST → 201 con body `{client, invitation: {invitation_url, ...}}`.
- **Niente `redirect()` post-success**: il super_admin DEVE vedere e copiare `invitation_url` (il plaintext token esiste solo in questa response, dopo è perso). La form mostra un success panel inline con:
  - Conferma "Client creato: {name}"
  - Campo readonly con `invitation_url` + bottone "Copia link" (`navigator.clipboard.writeText`)
  - Stato `copied` con feedback "Copiato!" 2s
  - Scadenza dell'invitation in chiaro
  - Bottone "Torna alla lista" (esplicito, l'utente decide quando navigare via)

`revalidatePath("/admin/clients")` dopo la mutation: la prossima visita alla lista include il nuovo client.

### 8. Error mapping in Server Action: differenziato per status

Il `createClientAction` mappa errori backend a messaggi italiani user-friendly senza leak di detail tecnici, ma differenziando 401/403/409/422/5xx perché ognuno suggerisce un'azione diversa all'utente:

| Backend | Action UX |
|---|---|
| 401 | "Sessione scaduta, ricarica la pagina per fare login" |
| 403 | "Non hai i permessi per creare un client" (rare: redirect dovrebbe averla già intercettata) |
| 409 con `detail` "email already in use" | field-error sotto Email: "Questa email è già registrata" |
| 409 con `detail` "slug already exists" | field-error sotto Slug: "Questo slug è già in uso" |
| 422 | "Dati non validi, controlla i campi e riprova" |
| 5xx / network | "Errore del server, riprova tra qualche istante" |

L'attribuzione del 409 al campo specifico richiede di leggere `detail` dal response body. I valori possibili sono noti e benigni (no leak), quindi è OK. Per 422 NON parsiamo il `detail` Pydantic verbose — sarebbe rumore per l'utente; preferiamo la doppia validazione client-side (HTML attrs + server-side) che attribuisce gli errori al campo giusto a monte.

## Scope limitations (decisioni esplicite per S5)

Le seguenti voci sono **deliberatamente fuori scope di S5** e tracciate in `TODO.md` per S6+:

1. **Email automatica all'invitato**: per ora il super_admin copia/incolla manualmente l'`invitation_url` al cliente. SES/Resend integration in S6.
2. **Endpoint `POST /accept-invite`**: il flow di accettazione invitation (validate token, create user, set password, mark accepted_at) verrà in S6. Il link `/accept-invite?token=...` per ora è un dead end frontend — placeholder per il portale onboarding lato cliente.
3. **Resend / revoke invitation**: `POST /admin/invitations/{id}/resend` e `POST /admin/invitations/{id}/revoke` in S6. Per ora si ricrea da zero (con cleanup manuale del row pending).
4. **RLS client_admin own-invitations**: in S5 il super_admin è l'unico ad operare sulle invitations. Quando il portale onboarding lato cliente arriverà (S6+), aggiungeremo policy `client_admin SELECT own client invitations`.
5. **Multi-tenant per utente**: 1 email = 1 user, scope Fase 1. Se Fase 2 richiederà "stesso utente in più tenant", servirà refactor (`user_clients` join table, JWT con array `client_ids`, RLS rivedute).
6. **Paginazione lista clients**: assumiamo < 50 client in Fase 1 (1-3 reali per i prossimi 6 mesi). Cursor-based pagination quando supereremo la soglia.
7. **Search/filter UI**: niente search bar in `/admin/clients`. Inutile con < 10 client. Future quando avremo ~20+.
8. **Audit log row dedicata** per `invitation_created`: per ora basta il structured log su stdout. La tabella `audit_log` esiste (S2) ma usarla per ogni admin op è S6+ work.

## Consequences

### Positive

- **Pattern famiglia `/api/v1/admin/*` consolidato**: ogni futuro admin endpoint sa dove vivere e come è guardato. Stessa famiglia di `/api/v1/auth/*`, simmetrica con `/api/auth/*` Route Handler frontend.
- **Token plaintext NON-recuperabile** = postura di sicurezza forte per default. Anche con DB dump, l'attaccante non può accettare invitation (servirebbe il plaintext).
- **GDPR logging clean**: pattern `email_hash` standardizzato attraverso auth + admin router; quando audit-log strutturato arriverà (S6+), il pattern è già lì.
- **State derivation > state column**: niente trigger di transizione, niente desync. Le funzioni `is_pending`, `is_expired` sono one-liner SQL/Python.
- **UI guard layered**: proxy fast-path per anonimi, layout role-check per non-super_admin, backend RLS+`Depends(require_super_admin)` come fonte di verità. Defense-in-depth con costi minimi (zero fetch extra grazie a `React.cache`).

### Negative / trade-offs

- **No email automatica in S5**: il super_admin copia manualmente l'URL. Friction se i clients arrivano in batch. Mitigazione: bottone "Copia link" + feedback visivo. Risolto in S6.
- **Plaintext token nel response body**: rispetto a "email-only delivery", c'è una finestra in cui il plaintext attraversa la rete frontend↔backend↔browser. Mitigazioni: HTTPS in prod, response inviata solo a super_admin autenticato, browser non lo persiste (è in DOM volatile finché l'utente non naviga via).
- **Hardcoded `INVITATION_TTL_DAYS=7`**: non env-tunabile per ora. Cambierà in S6+ se servirà differenziare dev/staging/prod.
- **Niente `accept-invite` page funzionante**: il link generato è un dead end frontend in S5. Confondente per QA. Mitigazione: messaggio esplicito nella success panel ("Salva subito il link, non sarà più visibile").

### Lessons learned

- **Validate runtime constraints PRIMA del piano** (terzo trap consolidato in S5 dopo middleware→proxy [ADR-0004] e Server Component cookies [ADR-0005]): il pattern `flush() vs commit()` dentro `Depends` con context manager auto-commit è una trappola SQLAlchemy che ha rotto lo smoke test. Fix dopo 5 min di lettura della docs SQLAlchemy 2.0 async session lifecycle. Documentato inline + ADR.
- **Schema extraction trigger = 5 schemi**: l'auth router con 4 schemi inline è ancora OK; il momento di estrarre è quando il numero supera ~5 distinti. `app/schemas/admin.py` creato in S5; auth router migra in S6+ quando lo justify-rà un altro endpoint.
- **Test E2E paranoici come parte normale del ciclo**: 9/9 paranoid backend (RLS bypasses, plaintext leakage, role enforcement) + 41 sub-asserzioni E2E live. Il pattern "test paranoici" si consolida (S2 RLS, S3 auth, S5 admin) — sempre includerli quando si tocca security/permissions.
