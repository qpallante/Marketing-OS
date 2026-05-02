-- ============================================================================
-- 005_invitations.sql — RLS policies for `invitations`
-- ============================================================================
-- Regole (Sessione 5 baseline):
--   SELECT: super_admin only (vede tutti gli inviti di tutti i client).
--   INSERT: super_admin only (crea inviti per qualsiasi client).
--   UPDATE: super_admin only (revoke principalmente).
--   DELETE: super_admin only (raro; preferire revoke via UPDATE settando
--           revoked_at, mantiene la storia per audit).
--
-- NB: le policy per client_admin (invitare il proprio team) arriveranno in
-- Sessione 6 quando implementeremo invitations da non-super. Per ora il
-- super_admin è l'unico che crea inviti, dal flow onboarding cliente.
--
-- Accept-invite (Sessione 6): l'utente non autenticato che clicca il link
-- userà unauthenticated_db (postgres role bypassa RLS), coerente con login —
-- vedi ADR-0002 §"Connessioni amministrative bypassano RLS".
-- ============================================================================

ALTER TABLE invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE invitations FORCE ROW LEVEL SECURITY;

-- Grants per il role `authenticated` (pattern table-local: ogni nuovo file
-- RLS porta i propri grants, evitando di toccare 000_helpers.sql ad ogni
-- aggiunta tabella). Vedi ADR-0002 §"Contratto del middleware FastAPI"
-- per perché il role authenticated è necessario (postgres è SUPERUSER e
-- bypassa RLS automaticamente).
GRANT SELECT, INSERT, UPDATE, DELETE ON invitations TO authenticated;

DROP POLICY IF EXISTS invitations_select ON invitations;
CREATE POLICY invitations_select ON invitations
  FOR SELECT
  USING (current_app_is_super_admin());

DROP POLICY IF EXISTS invitations_insert ON invitations;
CREATE POLICY invitations_insert ON invitations
  FOR INSERT
  WITH CHECK (current_app_is_super_admin());

DROP POLICY IF EXISTS invitations_update ON invitations;
CREATE POLICY invitations_update ON invitations
  FOR UPDATE
  USING (current_app_is_super_admin())
  WITH CHECK (current_app_is_super_admin());

DROP POLICY IF EXISTS invitations_delete ON invitations;
CREATE POLICY invitations_delete ON invitations
  FOR DELETE
  USING (current_app_is_super_admin());
