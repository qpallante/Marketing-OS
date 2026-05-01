-- ============================================================================
-- 002_users.sql — RLS policies for `users`
-- ============================================================================
-- Regole:
--   SELECT: super_admin vede tutti; client_admin/client_member vedono solo
--           gli utenti del proprio client (client_id = current_app_client_id()).
--           NB: super_admin users (client_id IS NULL) sono visibili solo dal
--           super_admin stesso — niente escalation di privilegi.
--   INSERT: super_admin (any); client_admin (solo per il proprio client_id,
--           e solo per ruoli client_admin/client_member, mai super_admin).
--   UPDATE: super_admin (any); user UPDATE su sé stesso o sui propri sottoposti
--           sarà gestito a livello applicativo nelle Sessioni successive.
--           Per ora: solo super_admin può fare UPDATE.
--   DELETE: solo super_admin (soft-delete via is_active=false è preferibile).
-- ============================================================================

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE users FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS users_select ON users;
CREATE POLICY users_select ON users
  FOR SELECT
  USING (
    current_app_is_super_admin()
    OR (
      client_id IS NOT NULL
      AND client_id = current_app_client_id()
    )
  );

DROP POLICY IF EXISTS users_insert ON users;
CREATE POLICY users_insert ON users
  FOR INSERT
  WITH CHECK (
    current_app_is_super_admin()
    OR (
      role IN ('client_admin', 'client_member')
      AND client_id = current_app_client_id()
    )
  );

DROP POLICY IF EXISTS users_update ON users;
CREATE POLICY users_update ON users
  FOR UPDATE
  USING (current_app_is_super_admin())
  WITH CHECK (current_app_is_super_admin());

DROP POLICY IF EXISTS users_delete ON users;
CREATE POLICY users_delete ON users
  FOR DELETE
  USING (current_app_is_super_admin());
