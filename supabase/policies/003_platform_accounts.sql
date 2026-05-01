-- ============================================================================
-- 003_platform_accounts.sql — RLS policies for `platform_accounts`
-- ============================================================================
-- Regole:
--   Tutte le operazioni: super_admin (any tenant) o utente del client owner
--   (client_id = current_app_client_id()).
--
--   Niente policy speciali per ruolo (client_admin vs client_member): la
--   distinzione granulare verrà fatta a livello applicativo (es. solo
--   client_admin può fare DELETE / disconnect). RLS qui blocca solo
--   il cross-tenant.
-- ============================================================================

ALTER TABLE platform_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform_accounts FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS platform_accounts_select ON platform_accounts;
CREATE POLICY platform_accounts_select ON platform_accounts
  FOR SELECT
  USING (
    current_app_is_super_admin()
    OR client_id = current_app_client_id()
  );

DROP POLICY IF EXISTS platform_accounts_insert ON platform_accounts;
CREATE POLICY platform_accounts_insert ON platform_accounts
  FOR INSERT
  WITH CHECK (
    current_app_is_super_admin()
    OR client_id = current_app_client_id()
  );

DROP POLICY IF EXISTS platform_accounts_update ON platform_accounts;
CREATE POLICY platform_accounts_update ON platform_accounts
  FOR UPDATE
  USING (
    current_app_is_super_admin()
    OR client_id = current_app_client_id()
  )
  WITH CHECK (
    current_app_is_super_admin()
    OR client_id = current_app_client_id()
  );

DROP POLICY IF EXISTS platform_accounts_delete ON platform_accounts;
CREATE POLICY platform_accounts_delete ON platform_accounts
  FOR DELETE
  USING (
    current_app_is_super_admin()
    OR client_id = current_app_client_id()
  );
