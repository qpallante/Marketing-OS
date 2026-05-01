-- ============================================================================
-- 004_audit_log.sql — RLS policies for `audit_log`
-- ============================================================================
-- Append-only by design.
--
--   SELECT: super_admin vede tutto; client_admin/client_member vede solo
--           eventi del proprio tenant (client_id = current_app_client_id())
--           OPPURE eventi a client_id NULL ma solo se super_admin.
--   INSERT: super_admin (any) o utente del client (own tenant only).
--   UPDATE: NESSUNA POLICY. Default deny → tabella immutabile.
--   DELETE: NESSUNA POLICY. Default deny → log non si cancella.
--
-- Note: con FORCE ROW LEVEL SECURITY + nessuna policy FOR UPDATE/DELETE,
-- nemmeno il role owner della tabella può modificare o cancellare righe.
-- L'unico modo per "ripulire" il log è una migration esplicita (TRUNCATE
-- o DELETE da super-admin in un context speciale, da audit-arsi a parte).
-- ============================================================================

ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS audit_log_select ON audit_log;
CREATE POLICY audit_log_select ON audit_log
  FOR SELECT
  USING (
    current_app_is_super_admin()
    OR (
      client_id IS NOT NULL
      AND client_id = current_app_client_id()
    )
  );

DROP POLICY IF EXISTS audit_log_insert ON audit_log;
CREATE POLICY audit_log_insert ON audit_log
  FOR INSERT
  WITH CHECK (
    current_app_is_super_admin()
    OR (
      client_id IS NOT NULL
      AND client_id = current_app_client_id()
    )
  );

-- DELIBERATELY NO `FOR UPDATE` POLICY → all UPDATEs blocked.
-- DELIBERATELY NO `FOR DELETE` POLICY → all DELETEs blocked.
