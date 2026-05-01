# ADR-0002 — RLS Strategy: per-session settings con `current_setting`

- **Status**: Accepted
- **Date**: 2026-05-01
- **Authors**: Quintino Pallante

## Context

Il Marketing OS è multi-tenant. Il vincolo non negoziabile dichiarato in CLAUDE.md è che ogni accesso ai dati di un cliente debba essere isolato: un utente di Monoloco non può mai (per bug, per query mal scritta, per JOIN dimenticato) leggere o modificare dati di Nightify o Interfibra.

Postgres Row-Level Security (RLS) è lo strumento giusto: applica il filtro multi-tenant **a livello di motore DB**, indipendentemente dal codice applicativo. Anche una `SELECT * FROM users` dimenticata in un endpoint resta scoped al client corretto.

Per attivare RLS serve esporre al motore DB **chi è l'utente corrente e a che client appartiene**. Le opzioni considerate:

### Opzione A — `auth.jwt()` di Supabase Auth
Le policy leggono direttamente dal JWT firmato da Supabase Auth: `auth.jwt() ->> 'client_id'`. Ogni request HTTP verso Postgres porta il JWT nell'header `Authorization`, GoTrue lo verifica, e le funzioni `auth.uid()` / `auth.jwt()` sono disponibili nelle policy.

- **Pro**: zero codice applicativo; auth e RLS coordinati out-of-the-box.
- **Contro**: vincola tutto a Supabase Auth come unico identity provider, e a un formato JWT che diventa contratto pubblico tra layer; un domani uscire da Supabase richiederebbe riscrivere tutte le policy. Inoltre i JWT custom claims su Supabase richiedono setup di hook (`custom_access_token` / DB functions) non banali, e il debug è opaco.

### Opzione B — `current_setting('app.current_client_id')`
Le policy leggono variabili di sessione Postgres che l'applicazione setta esplicitamente prima di ogni query autenticata, via `SET LOCAL "app.current_client_id" = '<uuid>'` all'interno della transazione corrente.

- **Pro**: portabile (nessuna dipendenza dall'identity provider); una migrazione futura da Supabase Auth a un IdP esterno (Auth0, Clerk, Authentik) non tocca le policy; debug semplice (`SHOW "app.current_client_id"` in psql); permette anche bypass cross-tenant pulito per super-admin con un secondo flag.
- **Contro**: l'app ha una **responsabilità contrattuale forte** — ogni request autenticata deve settare la variabile di sessione prima di emettere query. Se il middleware FastAPI che imposta i settings dovesse essere bypassato per qualunque motivo, le query restituiscono 0 righe (fail-closed) — comportamento safe ma da capire al primo bug. Inoltre `SET LOCAL` è transaction-scoped, quindi richiede pooler in **session mode** o uso esplicito di transazioni.

## Decision

Adottiamo **Opzione B**. Le policy RLS leggono da:

- `current_setting('app.current_client_id', true)::uuid` — ID del client corrente, settato dal middleware FastAPI all'inizio di ogni request autenticata.
- `current_setting('app.is_super_admin', true)::boolean` — flag opt-in per bypass cross-tenant; settato a `'true'` solo per richieste autenticate come super-admin.

Per nascondere il boilerplate `nullif/coalesce` e dare un punto unico di modifica, le policy chiamano due funzioni helper SQL:

```sql
CREATE OR REPLACE FUNCTION current_app_client_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('app.current_client_id', true), '')::uuid;
$$;

CREATE OR REPLACE FUNCTION current_app_is_super_admin() RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT coalesce(nullif(current_setting('app.is_super_admin', true), ''), 'false')::boolean;
$$;
```

Definite in `supabase/policies/000_helpers.sql`, applicate prima delle policy per-tabella (`001_clients.sql`, `002_users.sql`, `003_platform_accounts.sql`, `004_audit_log.sql`).

### Contratto del middleware FastAPI (Sessione 3)

**Scoperta in Sessione 2 (smoke test)**: l'utente Postgres `postgres` con cui ci connettiamo via pooler Supabase è `SUPERUSER` e bypassa **automaticamente** RLS. Anche con `FORCE ROW LEVEL SECURITY` attivo, le policy non si applicano per i superuser.

Per attivare effettivamente RLS sulle query del backend, il middleware deve eseguire `SET LOCAL ROLE authenticated` all'inizio di ogni transazione autenticata. Il role `authenticated` è creato automaticamente da Supabase, non è superuser, quindi RLS si applica.

Il contratto completo del middleware diventa:

```python
async with engine.begin() as conn:           # opens a transaction
    await conn.execute("SET LOCAL ROLE authenticated")
    await conn.execute(
        f"SET LOCAL \"app.current_client_id\" = '{client_id}'"
    )
    if user.is_super_admin:
        await conn.execute("SET LOCAL \"app.is_super_admin\" = 'true'")
    # ... business queries here ...
```

Per migrazioni (`alembic upgrade`), seed script, e operazioni di system-admin si **resta sul role `postgres` superuser**, bypassando RLS volutamente. Solo le query in risposta a request HTTP autenticate passano per `authenticated`.

I privilegi minimi del role `authenticated` sono concessi in `000_helpers.sql`:
`USAGE` sullo schema `public`, `SELECT/INSERT/UPDATE/DELETE` sulle tabelle di dominio (eccetto `audit_log` che ha solo `SELECT/INSERT`), `EXECUTE` sulle funzioni helper.

## Consequences

### Positive

- **Fail-closed di default**: senza `SET LOCAL`, `current_app_client_id()` ritorna NULL e `current_app_is_super_admin()` ritorna `false`; tutte le policy `USING` valutano falso e ogni `SELECT/UPDATE/DELETE` su tabelle multi-tenant restituisce 0 righe. Il sistema è sicuro per *omissione*, non per *commissione*.
- **Portabile**: cambiare identity provider (oggi Supabase Auth, domani Auth0 / Clerk / proprio) richiede solo modificare il middleware FastAPI che setta le variabili. Le policy DB non cambiano.
- **Debuggable**: in psql / Supabase Studio si può fare `BEGIN; SET LOCAL "app.current_client_id" = '...'; SELECT * FROM users; ROLLBACK;` e verificare manualmente.
- **`audit_log` append-only enforced by RLS**: per la tabella `audit_log` definiamo policy `FOR INSERT` ma nessuna `FOR UPDATE` o `FOR DELETE`. Il default deny di Postgres (con `FORCE ROW LEVEL SECURITY` attivo) impedisce ogni modifica anche al table owner.

### Negative / Trade-off

- **Contratto applicativo da rispettare in due punti**: il middleware FastAPI deve fare TRE cose all'inizio di ogni transazione autenticata: (1) `SET LOCAL ROLE authenticated`, (2) `SET LOCAL "app.current_client_id"`, (3) opzionalmente `SET LOCAL "app.is_super_admin"`. Se anche solo uno di questi viene saltato (refactor, eccezione mal gestita, test che bypassano middleware), le query falliscono silenziosamente. Mitigazione: test E2E che verificano esplicitamente l'invariante; logging del setting al middleware level; helper `with authenticated_context(client_id)` che incapsula i 3 settings.
- **Pooler mode constraint**: `SET LOCAL` funziona dentro una transazione. Richiede pooler Supabase in **session mode (5432)** oppure transazioni esplicite (`engine.begin()`). Transaction mode (6543) richiederebbe re-set ad ogni nuova transazione. La connessione corrente è già in session mode (ADR-0001), coerente.
- **Super-admin bypass è un coltello a doppio taglio**: il flag `app.is_super_admin` dà accesso totale. La responsabilità di non flaggarlo accidentalmente è tutta del middleware. Richiede code review specifica sul codice di auth; logging preciso quando il flag viene settato.
- **Connessioni amministrative bypassano RLS**: superuser (Alembic, seed, manutenzione) non passa per `authenticated`. Comportamento voluto, ma significa che un bug in uno script di amministrazione può ignorare RLS. Mitigazione: i tool admin sono in `core-api/scripts/` e non esposti su Internet, scope ristretto.

### Da rivalutare quando

- Adotteremo Supabase Auth in modo profondo (es. Realtime con auth client-side): valutare se vale la pena migrare a `auth.jwt()` per coerenza.
- Migrazione fuori da Supabase / pgbouncer-style pooler: rivalutare se transaction mode è sufficiente con un pattern diverso (es. session settings per ogni transazione esplicita).
- Avremo > 30 client e profilazione mostra che le funzioni helper aggiungono overhead misurabile: caching o inline.

## Alternatives considered

- **Vista materializzata per-tenant**: scartato — esplosione di oggetti DB con N client, manutenzione costosa.
- **Filtro a livello applicativo (sempre `WHERE client_id = ?` nelle query)**: scartato — fragile, basta una query dimenticata e i dati leak. RLS è una rete di sicurezza in più.
- **Schema separato per ogni client**: scartato — non scala oltre poche decine di client (ogni nuovo client richiede migrations N volte; statistiche planner peggiorano; backup più complessi).
- **`auth.jwt()` puro Supabase**: vedi sopra (Opzione A) — vincola troppo presto; la Sessione 3 può ancora valutare di usare Supabase Auth come identity provider mantenendo l'astrazione `current_setting` invariata.

## References

- [Postgres docs — Row Security Policies](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)
- [Postgres docs — `current_setting()` and custom GUC variables](https://www.postgresql.org/docs/current/functions-admin.html#FUNCTIONS-ADMIN-SET)
- [Supabase RLS guide](https://supabase.com/docs/guides/auth/row-level-security)
- ADR-0001 (stack iniziale, pooler session mode)
- `supabase/policies/000_helpers.sql` (implementazione helpers)
