"""invitation accepted_by_user_id

Sessione 6 — accept-invite flow. Aggiunge `accepted_by_user_id` UUID NULL FK
users(id) ON DELETE SET NULL alla tabella `invitations` per linkare l'utente
appena creato all'invitation che lo ha generato.

NULL su row esistenti (pending invitations di S5 senza accept ancora avvenuto)
— intenzionale. Quando un'invitation viene accettata in S6+, popoleremo
`accepted_at` E `accepted_by_user_id` insieme nella stessa transazione.

Vedi ADR-0007 per il flow completo.

Revision ID: 0003_invitation_accepted_by
Revises: 0002_invitations
Create Date: 2026-05-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_invitation_accepted_by"
down_revision: str | Sequence[str] | None = "0002_invitations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add column nullable: rows pending esistenti restano NULL fino all'accept.
    op.add_column(
        "invitations",
        sa.Column(
            "accepted_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )

    # FK con ON DELETE SET NULL: se l'utente accettatore viene cancellato in
    # futuro, perdiamo il link ma manteniamo l'invitation row come record
    # storico di accept. Coerente con `invited_by_user_id` (stesso pattern).
    op.create_foreign_key(
        "fk_invitations_accepted_by_user_id_users",
        "invitations",
        "users",
        ["accepted_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.execute(
        "COMMENT ON COLUMN invitations.accepted_by_user_id IS '"
        "ID dell''utente creato durante accept-invite (Sessione 6). NULL = "
        "invitation pending o revocata. Linkato 1:1 con accepted_at: "
        "entrambi NULL o entrambi popolati."
        "';",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_invitations_accepted_by_user_id_users",
        "invitations",
        type_="foreignkey",
    )
    op.drop_column("invitations", "accepted_by_user_id")
