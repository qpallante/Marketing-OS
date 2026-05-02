"""invitations table

Sessione 5 — schema invitations per il flow di onboarding di nuovi client_admin.

Crea:
  - tabella invitations con FK client_id, FK invited_by_user_id, indici e
    CHECK constraints (email lowercase, role limited to client_admin/client_member)
  - UNIQUE PARTIAL INDEX: previene >1 pending invitation per (client_id, email)
  - INDEX su token_hash per accept-invite lookup (Sessione 6)
  - COMMENT ON TABLE/COLUMN per debug DB

NB: le RLS policies vivono in supabase/policies/005_invitations.sql, applicate
separatamente dopo questa migration (vedi pattern S2).

Revision ID: 0002_invitations
Revises: 0001_initial
Create Date: 2026-05-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_invitations"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invitations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "client_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM(name="user_role", create_type=False),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "invited_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invitations"),
        sa.UniqueConstraint("token_hash", name="uq_invitations_token_hash"),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_invitations_client_id_clients",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["users.id"],
            name="fk_invitations_invited_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "email = lower(email)",
            name="ck_invitations_email_lowercase",
        ),
        sa.CheckConstraint(
            "role IN ('client_admin', 'client_member')",
            name="ck_invitations_role_not_super_admin",
        ),
    )
    op.create_index("ix_invitations_client_id", "invitations", ["client_id"])

    # UNIQUE PARTIAL INDEX: previene N pending invitations per (client_id, email).
    # Permette re-invitare DOPO revoke (revoked_at IS NOT NULL) o accept
    # (accepted_at IS NOT NULL). NB: invitations expired ma non revoked NON
    # sono escluse — è voluto: super_admin deve esplicitamente revoke prima
    # di re-invitare. Comportamento safer (no auto-overwrite di stato pending).
    op.execute(
        "CREATE UNIQUE INDEX uq_invitations_client_id_email_pending "
        "ON invitations (client_id, email) "
        "WHERE accepted_at IS NULL AND revoked_at IS NULL;",
    )

    # ───────────────────────────────────────────────────────────────
    # Comments on table and columns (debug aid)
    # ───────────────────────────────────────────────────────────────
    op.execute(
        "COMMENT ON TABLE invitations IS '"
        "Pending invitations per onboarding di nuovi client_admin/client_member. "
        "Stato (pending/expired/accepted/revoked) derivato da timestamp, "
        "no enum dedicato. Vedi ADR-0006."
        "';",
    )
    op.execute(
        "COMMENT ON COLUMN invitations.token_hash IS '"
        "SHA-256 hex (64 char) del token plaintext spedito al destinatario. "
        "Il plaintext NON viene mai storato in DB (defense-in-depth contro DB leak)."
        "';",
    )
    op.execute(
        "COMMENT ON COLUMN invitations.role IS '"
        "Ruolo che l''utente assumerà all''accept. CHECK constraint impedisce "
        "super_admin via invitation (defense-in-depth)."
        "';",
    )
    op.execute(
        "COMMENT ON COLUMN invitations.expires_at IS '"
        "Scadenza dell''invito. Default 7 giorni dalla creazione (configurato lato app)."
        "';",
    )
    op.execute(
        "COMMENT ON COLUMN invitations.accepted_at IS '"
        "Timestamp accept-invite (Sessione 6). NULL = pending o revocata."
        "';",
    )
    op.execute(
        "COMMENT ON COLUMN invitations.revoked_at IS '"
        "Timestamp revoke (super_admin annulla). NULL = non revocata."
        "';",
    )


def downgrade() -> None:
    op.drop_table("invitations")
