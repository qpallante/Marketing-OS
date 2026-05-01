-- ============================================================================
-- 001_clients.sql — RLS policies for `clients`
-- ============================================================================
-- Regole:
--   SELECT: super_admin vede tutti; client_admin/client_member vedono solo
--           il proprio client (id = current_app_client_id()).
--   INSERT: solo super_admin (i client si creano via dashboard admin in Sessione 5).
--   UPDATE: super_admin (any) o utente del client (own client only).
--   DELETE: solo super_admin (in pratica si usa status=archived, non DELETE).
--
-- FORCE ROW LEVEL SECURITY: enforce policies anche per il role owner della tabella.
-- ============================================================================

ALTER TABLE clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE clients FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS clients_select ON clients;
CREATE POLICY clients_select ON clients
  FOR SELECT
  USING (
    current_app_is_super_admin()
    OR id = current_app_client_id()
  );

DROP POLICY IF EXISTS clients_insert ON clients;
CREATE POLICY clients_insert ON clients
  FOR INSERT
  WITH CHECK (current_app_is_super_admin());

DROP POLICY IF EXISTS clients_update ON clients;
CREATE POLICY clients_update ON clients
  FOR UPDATE
  USING (
    current_app_is_super_admin()
    OR id = current_app_client_id()
  )
  WITH CHECK (
    current_app_is_super_admin()
    OR id = current_app_client_id()
  );

DROP POLICY IF EXISTS clients_delete ON clients;
CREATE POLICY clients_delete ON clients
  FOR DELETE
  USING (current_app_is_super_admin());
