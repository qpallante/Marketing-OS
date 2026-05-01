"""initial schema

Sessione 2 — bootstrap del modello dati multi-tenant.

Crea:
  - extension pgcrypto
  - 4 enum types (user_role, client_status, platform, platform_account_status)
  - funzione trigger set_updated_at()
  - 4 tabelle (clients, users, platform_accounts, audit_log) con CHECK constraints,
    foreign keys, indici, COMMENT ON TABLE/COLUMN
  - trigger updated_at sulle 3 tabelle che hanno updated_at

NB: le RLS policies NON sono in questa migration. Vivono in supabase/policies/
e vengono applicate separatamente (vedi ADR-0002).

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ───────────────────────────────────────────────────────────────
    # Extensions
    # ───────────────────────────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

    # ───────────────────────────────────────────────────────────────
    # Enum types (created upfront — referenced via create_type=False
    # in models, so SQLAlchemy will not try to (re)create them)
    # ───────────────────────────────────────────────────────────────
    op.execute(
        "CREATE TYPE user_role AS ENUM ('super_admin', 'client_admin', 'client_member');"
    )
    op.execute("CREATE TYPE client_status AS ENUM ('active', 'paused', 'archived');")
    op.execute("CREATE TYPE platform AS ENUM ('meta', 'tiktok', 'google', 'instagram');")
    op.execute(
        "CREATE TYPE platform_account_status AS ENUM ('connected', 'disconnected', 'error');"
    )

    # ───────────────────────────────────────────────────────────────
    # Trigger function for updated_at — installed once, used by
    # multiple tables via per-table trigger.
    # ───────────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # ───────────────────────────────────────────────────────────────
    # clients
    # ───────────────────────────────────────────────────────────────
    op.create_table(
        "clients",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="client_status", create_type=False),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_clients"),
        sa.UniqueConstraint("slug", name="uq_clients_slug"),
    )
    op.execute(
        "CREATE TRIGGER set_updated_at_clients BEFORE UPDATE ON clients "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )

    # ───────────────────────────────────────────────────────────────
    # users
    # ───────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM(name="user_role", create_type=False),
            nullable=False,
        ),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_users_client_id_clients",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "email = lower(email)", name="ck_users_email_lowercase"
        ),
        sa.CheckConstraint(
            "(role = 'super_admin' AND client_id IS NULL) OR "
            "(role IN ('client_admin', 'client_member') AND client_id IS NOT NULL)",
            name="ck_users_role_client_id_consistency",
        ),
    )
    op.create_index("ix_users_client_id", "users", ["client_id"])
    op.execute(
        "CREATE TRIGGER set_updated_at_users BEFORE UPDATE ON users "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )

    # ───────────────────────────────────────────────────────────────
    # platform_accounts
    # ───────────────────────────────────────────────────────────────
    op.create_table(
        "platform_accounts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "platform",
            postgresql.ENUM(name="platform", create_type=False),
            nullable=False,
        ),
        sa.Column("account_external_id", sa.String(length=255), nullable=False),
        sa.Column("account_name", sa.String(length=255), nullable=True),
        sa.Column("credentials_vault_key", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="platform_account_status", create_type=False),
            server_default="disconnected",
            nullable=False,
        ),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_platform_accounts"),
        sa.UniqueConstraint(
            "platform",
            "account_external_id",
            name="uq_platform_accounts_platform_external_id",
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_platform_accounts_client_id_clients",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_platform_accounts_client_id_platform",
        "platform_accounts",
        ["client_id", "platform"],
    )
    op.execute(
        "CREATE TRIGGER set_updated_at_platform_accounts BEFORE UPDATE ON platform_accounts "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )

    # ───────────────────────────────────────────────────────────────
    # audit_log (append-only — no updated_at, RLS prevents UPDATE/DELETE)
    # ───────────────────────────────────────────────────────────────
    op.create_table(
        "audit_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=True),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "event_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_audit_log_client_id_clients",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_audit_log_user_id_users",
            ondelete="SET NULL",
        ),
    )
    op.execute(
        "CREATE INDEX ix_audit_log_client_id_created_at "
        "ON audit_log (client_id, created_at DESC);"
    )
    op.execute(
        "CREATE INDEX ix_audit_log_user_id_created_at "
        "ON audit_log (user_id, created_at DESC);"
    )
    op.execute(
        "CREATE INDEX ix_audit_log_action_created_at "
        "ON audit_log (action, created_at DESC);"
    )

    # ───────────────────────────────────────────────────────────────
    # Comments on tables and columns (debug aid when querying DB direct)
    # ───────────────────────────────────────────────────────────────
    op.execute(
        "COMMENT ON TABLE clients IS '"
        "Tenant root: ogni cliente dell''agenzia. Dimensione di partizionamento "
        "per tutto il modello dati. Cascade delete su user/platform_account/audit_log."
        "';"
    )
    op.execute(
        "COMMENT ON COLUMN clients.slug IS '"
        "URL-friendly identifier univoco (es. monoloco, nightify). "
        "Mai cambiare dopo creazione: rompe URL e referenze esterne."
        "';"
    )
    op.execute(
        "COMMENT ON COLUMN clients.status IS '"
        "active=operativo, paused=onboarded ma non attivo, "
        "archived=offboarded (no delete fisica)."
        "';"
    )

    op.execute(
        "COMMENT ON TABLE users IS '"
        "Utenti del sistema. super_admin = cross-tenant (client_id NULL); "
        "client_admin/client_member = legati a un client (client_id NOT NULL). "
        "Vincolo via CHECK ck_users_role_client_id_consistency."
        "';"
    )
    op.execute(
        "COMMENT ON COLUMN users.email IS '"
        "Univoca, lowercase forzato via CHECK ck_users_email_lowercase. "
        "Lunghezza 320 = max RFC 5321."
        "';"
    )
    op.execute(
        "COMMENT ON COLUMN users.hashed_password IS '"
        "bcrypt hash via passlib. Mai memorizzare password in chiaro."
        "';"
    )
    op.execute(
        "COMMENT ON COLUMN users.role IS '"
        "Vincolo: super_admin <=> client_id NULL; "
        "client_admin/client_member <=> client_id NOT NULL."
        "';"
    )
    op.execute(
        "COMMENT ON COLUMN users.is_active IS '"
        "Soft-disable senza eliminare. Login bloccato lato app se false."
        "';"
    )

    op.execute(
        "COMMENT ON TABLE platform_accounts IS '"
        "Account esterni di piattaforme social/ads collegati a un client "
        "(Meta, TikTok, Google, Instagram). "
        "Univoco per (platform, account_external_id) cross-tenant."
        "';"
    )
    op.execute(
        "COMMENT ON COLUMN platform_accounts.account_external_id IS '"
        "ID dell''account sulla piattaforma esterna (es. Meta Ads Account ID)."
        "';"
    )
    op.execute(
        "COMMENT ON COLUMN platform_accounts.credentials_vault_key IS '"
        "Chiave logica per recuperare credenziali da Doppler/vault. "
        "NON contiene credenziali in chiaro."
        "';"
    )
    op.execute(
        "COMMENT ON COLUMN platform_accounts.status IS '"
        "connected=token valido e funzionante, "
        "disconnected=mai connesso o disconnesso esplicitamente, "
        "error=ultima chiamata fallita per auth."
        "';"
    )
    op.execute(
        "COMMENT ON COLUMN platform_accounts.last_sync_at IS '"
        "Ultimo sync OK con la piattaforma. NULL=mai sincronizzato."
        "';"
    )

    op.execute(
        "COMMENT ON TABLE audit_log IS '"
        "Log immutabile di azioni rilevanti. APPEND-ONLY: RLS blocca UPDATE e DELETE. "
        "Niente updated_at by design. Ritenzione TBD."
        "';"
    )
    op.execute(
        "COMMENT ON COLUMN audit_log.user_id IS '"
        "Chi ha eseguito l''azione. NULL per eventi di sistema senza user attribuibile "
        "o se il user è stato cancellato (FK ON DELETE SET NULL)."
        "';"
    )
    op.execute(
        "COMMENT ON COLUMN audit_log.client_id IS '"
        "Tenant scope. NULL per eventi cross-tenant (es. operazioni super_admin globali)."
        "';"
    )
    op.execute(
        "COMMENT ON COLUMN audit_log.action IS '"
        "Identificatore azione: dot-notation gerarchica "
        "(es. user.login, client.created, platform_account.connected)."
        "';"
    )
    op.execute(
        "COMMENT ON COLUMN audit_log.event_metadata IS '"
        "Payload JSON dell''evento. Strutturato in modo coerente per action."
        "';"
    )
    op.execute(
        "COMMENT ON COLUMN audit_log.ip_address IS '"
        "IP di origine della richiesta. Utile per forensics. "
        "NULL per eventi non originati da HTTP."
        "';"
    )


def downgrade() -> None:
    # Drop in reverse FK order
    op.drop_table("audit_log")
    op.drop_table("platform_accounts")
    op.drop_table("users")
    op.drop_table("clients")

    # Drop trigger function
    op.execute("DROP FUNCTION IF EXISTS set_updated_at();")

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS platform_account_status;")
    op.execute("DROP TYPE IF EXISTS platform;")
    op.execute("DROP TYPE IF EXISTS client_status;")
    op.execute("DROP TYPE IF EXISTS user_role;")

    # NB: pgcrypto is NOT dropped — likely shared with other components.
