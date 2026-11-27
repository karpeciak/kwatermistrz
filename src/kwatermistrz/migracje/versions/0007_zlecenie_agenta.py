"""S22IT1S0P2-42: zlecenia agentów, które przekroczyły dopuszczalny czas realizacji.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "zlecenie_agenta",
        sa.Column("id_zlecenia", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id_delegacji", sa.String(30), sa.ForeignKey("delegacja.id_delegacji"), nullable=False),
        sa.Column("rodzaj", sa.String(30), nullable=False),
        sa.Column("data_zlecenia", sa.DateTime(), nullable=False),
        sa.Column("liczba_ponowien", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("przewidywany_termin", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("data_zakonczenia", sa.DateTime(), nullable=True),
        sa.Column("incydent", sa.Text(), nullable=True),
    )
    op.create_index("ix_zlecenie_agenta_id_delegacji", "zlecenie_agenta", ["id_delegacji"])


def downgrade() -> None:
    op.drop_index("ix_zlecenie_agenta_id_delegacji", table_name="zlecenie_agenta")
    op.drop_table("zlecenie_agenta")
