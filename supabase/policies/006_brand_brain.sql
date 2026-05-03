-- ============================================================================
-- 006_brand_brain.sql — RLS policies per Brand Brain (Sessione 7)
-- ============================================================================
-- Pattern standard ADR-0002: client_id-scoped + super_admin override.
-- Defense-in-depth a 3 layer per le richieste autenticate verso /api/v1/clients/{id}/brand/*:
--   1. HTTP dep `require_client_access(client_id_path, user)` → 403 prima di RLS
--   2. RLS qui sotto → 0 rows se HTTP layer bypassato
--   3. FK CASCADE su clients.id → coerenza referenziale
--
-- Tabelle:
--   - brand_assets: SELECT/INSERT/UPDATE/DELETE (status mutabile, user delete)
--   - brand_chunks: SELECT/INSERT/DELETE (chunks immutabili, no UPDATE policy)
--   - brand_form_data: SELECT/INSERT/UPDATE/DELETE (upsert pattern)
--   - brand_generations: SELECT/INSERT (append-only, pattern audit_log — no UPDATE/DELETE)
--
-- Idempotente: DROP POLICY IF EXISTS prima di ogni CREATE POLICY.
-- Vedi ADR-0008 §RLS contract per il quadro completo.
-- ============================================================================

-- ─── ENABLE + FORCE RLS ─────────────────────────────────────────────────────

ALTER TABLE brand_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE brand_assets FORCE ROW LEVEL SECURITY;

ALTER TABLE brand_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE brand_chunks FORCE ROW LEVEL SECURITY;

ALTER TABLE brand_form_data ENABLE ROW LEVEL SECURITY;
ALTER TABLE brand_form_data FORCE ROW LEVEL SECURITY;

ALTER TABLE brand_generations ENABLE ROW LEVEL SECURITY;
ALTER TABLE brand_generations FORCE ROW LEVEL SECURITY;

-- ─── GRANTs al role `authenticated` ─────────────────────────────────────────
-- 000_helpers.sql ha già GRANT USAGE schema public + funzioni helper.
-- Qui granti table-specific. Per brand_chunks niente UPDATE (immutable);
-- per brand_generations niente UPDATE/DELETE (append-only, defense-in-depth
-- via privilege grant + RLS no-policy).

GRANT SELECT, INSERT, UPDATE, DELETE ON brand_assets TO authenticated;
GRANT SELECT, INSERT, DELETE ON brand_chunks TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON brand_form_data TO authenticated;
GRANT SELECT, INSERT ON brand_generations TO authenticated;

GRANT USAGE ON TYPE brand_indexing_status TO authenticated;
GRANT USAGE ON TYPE brand_generation_status TO authenticated;

-- ─── REVOKE espliciti per defense-in-depth ──────────────────────────────────
-- GOTCHA SUPABASE: `pg_default_acl` su schema `public` concede TUTTI i privilegi
-- DML+DDL (arwdDxtm) a authenticated/anon/service_role su ogni tabella nuova
-- creata da `postgres`. Il GRANT esplicito sopra è ridondante; UPDATE/DELETE
-- che NON elenchiamo sono concessi comunque dal default ACL.
--
-- Per ottenere effettiva defense-in-depth (privilege layer + RLS no-policy
-- entrambi attivi), facciamo REVOKE espliciti su tabelle append-only:
--
--   - brand_chunks: niente UPDATE (chunks immutable)
--   - brand_generations: niente UPDATE/DELETE (audit-style, DELETE solo via CASCADE)
--
-- Senza questi REVOKE: il blocco è solo RLS no-policy (CASO b). Con REVOKE:
-- defense-in-depth (CASO c) — più robusto a futuri misconfig di policy.
-- Vedi ADR-0008 §"Append-only enforcement: 2-layer + Supabase default ACL gotcha".

REVOKE UPDATE ON brand_chunks FROM authenticated, anon, service_role;
REVOKE UPDATE, DELETE, TRUNCATE ON brand_generations FROM authenticated, anon, service_role;

-- ─── brand_assets policies ──────────────────────────────────────────────────

DROP POLICY IF EXISTS brand_assets_select ON brand_assets;
CREATE POLICY brand_assets_select ON brand_assets
  FOR SELECT
  USING (
    current_app_is_super_admin()
    OR client_id = current_app_client_id()
  );

DROP POLICY IF EXISTS brand_assets_insert ON brand_assets;
CREATE POLICY brand_assets_insert ON brand_assets
  FOR INSERT
  WITH CHECK (
    current_app_is_super_admin()
    OR client_id = current_app_client_id()
  );

DROP POLICY IF EXISTS brand_assets_update ON brand_assets;
CREATE POLICY brand_assets_update ON brand_assets
  FOR UPDATE
  USING (
    current_app_is_super_admin()
    OR client_id = current_app_client_id()
  )
  WITH CHECK (
    current_app_is_super_admin()
    OR client_id = current_app_client_id()
  );

DROP POLICY IF EXISTS brand_assets_delete ON brand_assets;
CREATE POLICY brand_assets_delete ON brand_assets
  FOR DELETE
  USING (
    current_app_is_super_admin()
    OR client_id = current_app_client_id()
  );

-- ─── brand_chunks policies ──────────────────────────────────────────────────
-- Append-only-ish: SELECT + INSERT + DELETE (per cleanup esplicito).
-- Niente policy FOR UPDATE → default deny anche su privilege.
-- DELETE via CASCADE su brand_assets bypassa RLS (motore DB), corretto.

DROP POLICY IF EXISTS brand_chunks_select ON brand_chunks;
CREATE POLICY brand_chunks_select ON brand_chunks
  FOR SELECT
  USING (
    current_app_is_super_admin()
    OR client_id = current_app_client_id()
  );

DROP POLICY IF EXISTS brand_chunks_insert ON brand_chunks;
CREATE POLICY brand_chunks_insert ON brand_chunks
  FOR INSERT
  WITH CHECK (
    current_app_is_super_admin()
    OR client_id = current_app_client_id()
  );

DROP POLICY IF EXISTS brand_chunks_delete ON brand_chunks;
CREATE POLICY brand_chunks_delete ON brand_chunks
  FOR DELETE
  USING (
    current_app_is_super_admin()
    OR client_id = current_app_client_id()
  );

-- ─── brand_form_data policies ───────────────────────────────────────────────

DROP POLICY IF EXISTS brand_form_data_select ON brand_form_data;
CREATE POLICY brand_form_data_select ON brand_form_data
  FOR SELECT
  USING (
    current_app_is_super_admin()
    OR client_id = current_app_client_id()
  );

DROP POLICY IF EXISTS brand_form_data_insert ON brand_form_data;
CREATE POLICY brand_form_data_insert ON brand_form_data
  FOR INSERT
  WITH CHECK (
    current_app_is_super_admin()
    OR client_id = current_app_client_id()
  );

DROP POLICY IF EXISTS brand_form_data_update ON brand_form_data;
CREATE POLICY brand_form_data_update ON brand_form_data
  FOR UPDATE
  USING (
    current_app_is_super_admin()
    OR client_id = current_app_client_id()
  )
  WITH CHECK (
    current_app_is_super_admin()
    OR client_id = current_app_client_id()
  );

DROP POLICY IF EXISTS brand_form_data_delete ON brand_form_data;
CREATE POLICY brand_form_data_delete ON brand_form_data
  FOR DELETE
  USING (
    current_app_is_super_admin()
    OR client_id = current_app_client_id()
  );

-- ─── brand_generations policies (append-only, pattern audit_log) ────────────
-- SELECT + INSERT only. UPDATE/DELETE bloccati a 2 livelli:
--   (1) GRANT non concede UPDATE/DELETE a authenticated
--   (2) Niente policy FOR UPDATE/DELETE → default deny anche se privilegio
-- DELETE solo via CASCADE su clients (DB engine bypassa RLS check).

DROP POLICY IF EXISTS brand_generations_select ON brand_generations;
CREATE POLICY brand_generations_select ON brand_generations
  FOR SELECT
  USING (
    current_app_is_super_admin()
    OR client_id = current_app_client_id()
  );

DROP POLICY IF EXISTS brand_generations_insert ON brand_generations;
CREATE POLICY brand_generations_insert ON brand_generations
  FOR INSERT
  WITH CHECK (
    current_app_is_super_admin()
    OR client_id = current_app_client_id()
  );
