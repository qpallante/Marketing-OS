-- ============================================================================
-- 000_helpers.sql — RLS helper functions
-- ============================================================================
-- Tutte le policy nei file 001-004 leggono il "contesto utente" da due
-- variabili di sessione Postgres che il middleware FastAPI deve impostare
-- all'inizio di ogni request autenticata via `SET LOCAL`:
--
--   SET LOCAL "app.current_client_id" = '<uuid_del_client>';
--   SET LOCAL "app.is_super_admin"   = 'true' | 'false';
--
-- Le funzioni qui sotto sono solo wrapper read-only:
--   - normalizzano stringhe vuote a NULL/false
--   - centralizzano il cast (uuid, boolean)
--   - rendono le policy leggibili (`current_app_client_id() = client_id`
--     vs. `nullif(current_setting('...', true), '')::uuid = client_id`)
--
-- Vedi ADR-0002 per la rationale della scelta.
-- ============================================================================

CREATE OR REPLACE FUNCTION current_app_client_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('app.current_client_id', true), '')::uuid;
$$;

COMMENT ON FUNCTION current_app_client_id() IS
  'Returns the current_client_id from session settings, or NULL if unset. Used by RLS policies.';

CREATE OR REPLACE FUNCTION current_app_is_super_admin() RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT coalesce(nullif(current_setting('app.is_super_admin', true), ''), 'false')::boolean;
$$;

COMMENT ON FUNCTION current_app_is_super_admin() IS
  'Returns true if app.is_super_admin = ''true'', false otherwise (incl. unset). Used for RLS bypass.';

-- ============================================================================
-- Grants for the `authenticated` role
-- ============================================================================
-- Critico: l'utente `postgres` con cui ci connettiamo via pooler è SUPERUSER
-- e bypassa AUTOMATICAMENTE RLS — anche con FORCE ROW LEVEL SECURITY attivo.
-- Per applicare RLS sulle query del backend FastAPI, il middleware esegue
-- `SET LOCAL ROLE authenticated` all'inizio di ogni request.
--
-- Il role `authenticated` è creato automaticamente da Supabase. Non è
-- superuser, quindi RLS si applica. Qui gli concediamo i privilegi minimi
-- per leggere/scrivere le tabelle di dominio.
-- ============================================================================

GRANT USAGE ON SCHEMA public TO authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE ON clients TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON users TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON platform_accounts TO authenticated;
-- audit_log: SELECT + INSERT only. UPDATE/DELETE bloccati ulteriormente da RLS
-- (no policy FOR UPDATE/DELETE), ma neghiamo anche il privilegio per defense-in-depth.
GRANT SELECT, INSERT ON audit_log TO authenticated;

GRANT EXECUTE ON FUNCTION current_app_client_id() TO authenticated;
GRANT EXECUTE ON FUNCTION current_app_is_super_admin() TO authenticated;
