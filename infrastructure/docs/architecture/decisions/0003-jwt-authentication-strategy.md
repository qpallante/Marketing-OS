# ADR-0003 — JWT authentication strategy

- **Status**: Accepted
- **Date**: 2026-05-01
- **Authors**: Quintino Pallante

## Context

Sessione 3 implementa l'autenticazione del Marketing OS. Vincoli e requisiti:

- **Multi-tenant**: il sistema deve sapere a quale `client_id` appartiene chi sta facendo una richiesta, perché il contratto RLS in ADR-0002 lo richiede esplicitamente. Il middleware setta `app.current_client_id` partendo da info ricavate dal token.
- **Single-service per ora**: il `core-api` è l'unico backend che firma e valida i token. Niente service mesh, niente API gateway esterno.
- **Tre ruoli** (vedi modelli Sessione 2): `super_admin`, `client_admin`, `client_member`. L'enforcement granulare avviene a livello applicativo (FastAPI deps), il filtro per tenant a livello DB (RLS).
- **Boring tech**: i bearer JWT sono lo standard più conosciuto e debuggabile (jwt.io). Nessun motivo per inventare un protocollo proprietario.

## Decision

JWT bearer tokens firmati HS256, con due tipi di token (access e refresh), claim discriminator obbligatorio, lifetimes configurabili per ambiente, strict checks server-side al decode.

### 1. Algorithm: HS256 (symmetric)

`HMAC-SHA256` con un singolo secret in `JWT_SECRET_KEY` (env var, generato con `openssl rand -hex 32` in dev). L'algoritmo asimmetrico (RS256/ES256) è sovradimensionato per un servizio singolo: niente bisogno che terze parti verifichino i token. Quando — se mai — esporremo i token a verificatori esterni (mobile SDK, webhook signed handlers), valuteremo RS256 con chiave pubblica esposta su `/.well-known/jwks.json`.

### 2. Token claims

**Access token** (60 minuti TTL):

```json
{
  "sub": "<user_uuid>",
  "type": "access",
  "iat": 1234567890,
  "exp": 1234571490,
  "client_id": "<client_uuid>" | null,
  "role": "super_admin" | "client_admin" | "client_member"
}
```

`client_id` è `null` per i super_admin (cross-tenant). `role` consente ai middleware/deps di evitare un round-trip DB per le authorization veloci.

**Refresh token** (7 giorni dev / 30 giorni prod):

```json
{
  "sub": "<user_uuid>",
  "type": "refresh",
  "iat": 1234567890,
  "exp": 1235172690
}
```

Volutamente minimale. Al refresh ricarichiamo l'utente da DB per ottenere `client_id`/`role` aggiornati (nel caso di promozioni/demote l'access token rotato sarà coerente).

### 3. `type` claim discriminator (obbligatorio)

Sia access che refresh hanno un campo `type`. `decode_token(..., expected_type=TokenType.ACCESS)` rifiuta esplicitamente un token con `type != "access"`. Previene:

- riuso di un refresh token come access token (escalation senza re-login)
- riuso simmetrico (un access non può essere scambiato come refresh)

I wrapper `decode_access_token` / `decode_refresh_token` rendono il check inevitabile.

### 4. Refresh: stateless con rotation

`/api/v1/auth/refresh` accetta un refresh token valido e ritorna **una nuova coppia** access + refresh. Stateless: nessuna tabella `refresh_tokens` lato DB.

**Implicazione esplicita**: il refresh token vecchio (quello appena usato) resta tecnicamente valido fino alla scadenza naturale. Non possiamo invalidare un singolo token compromesso prima che scada.

### Exit strategy

Quando l'esigenza di "logout-everywhere" / "invalidare tutti i token di un utente compromesso" diventerà reale (Sessione 5 onboarding o Sessione 6 admin actions), introdurremo un claim **`tv` (token_version)** sull'access E refresh token + colonna `users.token_version INTEGER NOT NULL DEFAULT 0`:

```sql
ALTER TABLE users ADD COLUMN token_version INT NOT NULL DEFAULT 0;
```

Pattern:
- All'emissione, `tv = user.token_version`
- Al decode/validate: `if payload['tv'] != user.token_version: reject`
- "Logout everywhere": `UPDATE users SET token_version = token_version + 1 WHERE id = ?` invalida istantaneamente tutti i token in giro per quell'utente

Vantaggi rispetto a una tabella `refresh_tokens` allow-list/deny-list:
- O(1) check (un singolo confronto integer)
- Niente storage che cresce
- Si applica anche ai access token (più granulare)

Costo: una query DB extra al decode (oggi facciamo già il lookup user, quindi aggiungere un confronto è zero cost).

### 5. Lifetimes (env-tunable)

| Ambiente | Access | Refresh |
|----------|--------|---------|
| dev / staging | 60 min | **7 giorni** |
| production | 60 min | 30 giorni |

Refresh più corto in dev/staging per **scoprire le rotture early**. Se un dev sta usando il sistema da > 7 giorni e il refresh smette di funzionare, dev/staging è dove vogliamo accorgercene — non in prod dopo 30 giorni che il token è stato emesso.

Configurato via `REFRESH_TOKEN_TTL_DAYS` env var (default 7 in `Settings`). Override per prod: `REFRESH_TOKEN_TTL_DAYS=30`.

### 6. Strict checks al decode (OPTION A)

`get_current_user` (deps.py) carica lo user da DB e applica i seguenti check **prima** di accettare la request:

1. User esiste
2. `user.is_active == True`
3. Token `role` claim == `user.role` nel DB
4. Token `client_id` claim == `user.client_id` nel DB

Mismatch su (3) o (4) → 401 `"role changed, please re-login"` / `"client mismatch, please re-login"`. Questo significa che se un super_admin demote un client_admin a client_member, **il vecchio access token smette di lavorare** alla prossima request — non aspetta i 60 min di scadenza.

Costo: 1 query DB extra per request. Acceptable. Cache Redis con TTL breve in S5+ se diventa bottleneck.

### 7. Login response: uniforme su errore

Tutti i casi di fallimento `/auth/login` ritornano lo stesso payload:

```json
{"detail": "invalid credentials"}
```

Anche se l'utente è disabilitato (`is_active=False`), anche se non esiste, anche se la password è sbagliata. Questo previene:
- **User enumeration** via timing/risposte differenti
- **Account harvesting** (sapere che `mario.rossi@x.com` esiste senza la sua password)

Loggiamo a WARNING `auth.login_failed` con l'hash SHA-256 dell'email tentata (per forensics) — niente plaintext (GDPR).

Stesso pattern per `/auth/refresh`: errore uniforme `"refresh failed, please re-login"`.

### 8. No password validation al login

Login accetta `email: EmailStr` e `password: str` (nessun min-length / classi). È un *lookup*, non una creazione. La validazione strict (min 8 chars + classi) va al **signup/invitation flow** (Sessione 5+) dove ha senso applicare politiche.

Rimanere lassi al login significa che password legacy (più corte degli standard nuovi) restano usabili anche dopo che alziamo i requisiti di registrazione — accettabile, l'enforcement va al cambio password forzato post-policy-change (TODO se ci serviranno).

### 9. No rate limiting (TODO Sessione 5+)

Il rate limiting su `POST /auth/login` (5 tentativi / 15 min / IP) **non è implementato** in Sessione 3. Richiede Redis che non è ancora bootstrappato (arriverà in Sessione 5+ con Celery). Tracciato in TODO.md.

Mitigazioni interim: (a) login response uniforme (§7) limita l'utilità del brute-force; (b) bcrypt rallenta l'attacco a livello costo computazionale (10 work factor di default).

## Implementazione

Layering:

```
HTTP request
    │
    ▼
JWTAuthMiddleware (ASGI puro, app/core/middleware.py)
    │  estrae Bearer header, decode_access_token,
    │  popola request.state.token_payload
    │  401 immediata su token invalido
    ▼
get_token_payload (deps.py)
    │  legge request.state.token_payload, 401 se assente
    ▼
get_current_user (deps.py)
    │  lookup user da DB (unauthenticated_db, RLS bypassed: chicken/egg),
    │  applica strict checks (§6)
    ▼
get_authenticated_session (deps.py)
    │  apre AsyncSession con contratto ADR-0002 (SET LOCAL ROLE + GUC vars)
    ▼
require_super_admin / require_client_admin (deps.py)
    │  check di ruolo, 403 altrimenti
    ▼
endpoint handler riceve (user, db) — RLS attiva
```

OpenAPI: BearerAuth scheme registrato globalmente in `_custom_openapi()`. Endpoint pubblici (`/login`, `/refresh`) override con `openapi_extra={"security": []}`.

## Consequences

### Positive

- **Stateless lato auth**: niente DB lookup per validare la firma JWT. Solo un lookup user (per i check strict OPTION A) per request autenticata.
- **Portabile**: la logica auth non ha dipendenze su Supabase Auth o IdP esterni. Un domani possiamo plug-in OAuth/SAML/Auth0 cambiando solo il login flow, lasciando intatto middleware/deps/router.
- **Fail-closed**: token invalido → 401 immediata; user demote → 401 alla prossima request senza aspettare expiry; middleware bypassed → request.state.token_payload assente → deps falliscono.
- **Debuggable**: jwt.io decode visivo; structlog INFO/WARNING strutturato su ogni success/failure; log con `attempted_email_hash` invece di plaintext (GDPR).

### Negative / Trade-off

- **Refresh token compromesso non revocabile** prima dell'expiry (mitigato dall'exit strategy `tv` claim).
- **1 query DB per request autenticata** (acceptable, cacheable in S5+ se diventa bottleneck).
- **Rate limit dimenticato è una vulnerabilità** finché non aggiungiamo Redis. Mitigato da bcrypt + uniform response, ma documentato come gap in TODO.md.
- **HS256 secret rotation è disruptive**: cambiare `JWT_SECRET_KEY` invalida TUTTI i token (tutti gli utenti rifanno login). Acceptable in un contesto piccolo; per rotation graceful servirebbe key versioning (`kid` claim) — non ora.

### Da rivalutare quando

- Esponiamo i token a client esterni (mobile SDK, partner integrations) → RS256 + JWKS.
- Servirà logout-everywhere o credential compromise response → introduciamo `tv` claim (Sessione 5/6).
- Redis disponibile → rate limiting su `/login` (5 tentativi / 15 min / IP).
- Multi-region o distribuiamo il `core-api` su più istanze → rivalutare se il secret rotation richiede coordination.

## Alternatives considered

- **Server-side sessions con cookie**: scartato — vincola alla stessa origine, complica mobile, perde i benefici stateless.
- **Refresh stateful con tabella `refresh_tokens`**: scartato per ora (vedi §4 e exit strategy `tv`).
- **`auth.jwt()` Supabase nativo**: scartato in ADR-0002 — vincola a Supabase Auth come unica identity, costa la portabilità.
- **OAuth 2.0 device flow / PKCE per il dashboard**: sovradimensionato per un dashboard interno. Quando avremo client esterni terze-parti, sì.

## References

- [RFC 7519 — JSON Web Token (JWT)](https://datatracker.ietf.org/doc/html/rfc7519)
- [OWASP — JWT Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/JSON_Web_Token_for_Java_Cheat_Sheet.html)
- ADR-0002 (RLS strategy — il middleware contract a cui questo ADR si aggancia)
- `app/core/security.py`, `app/core/middleware.py`, `app/core/deps.py`, `app/routers/auth.py`
