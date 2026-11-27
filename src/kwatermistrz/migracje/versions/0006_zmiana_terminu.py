"""S22IT1S0P2-41: rejestr zmian terminu zadania i aktualizacji planu delegacji.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "zmiana_terminu",
        sa.Column("id_zmiany", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id_delegacji", sa.String(30), sa.ForeignKey("delegacja.id_delegacji"), nullable=False),
        sa.Column(
            "id_przedsiewziecia", sa.String(20), sa.ForeignKey("przedsiewziecie.id_przedsiewziecia"), nullable=True
        ),
        sa.Column("data_zgloszenia", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("powod", sa.Text(), nullable=True),
        sa.Column("poczatek_zadania_przed", sa.Date(), nullable=False),
        sa.Column("koniec_zadania_przed", sa.Date(), nullable=False),
        sa.Column("poczatek_zadania", sa.Date(), nullable=False),
        sa.Column("koniec_zadania", sa.Date(), nullable=False),
        sa.Column("stary_wyjazd", sa.Date(), nullable=True),
        sa.Column("stary_powrot", sa.Date(), nullable=True),
        sa.Column("nowy_wyjazd", sa.Date(), nullable=False),
        sa.Column("nowy_powrot", sa.Date(), nullable=False),
        sa.Column("stary_plan", sa.JSON(), nullable=False),
        sa.Column("nowy_plan", sa.JSON(), nullable=True),
        sa.Column("roznica_kosztu", sa.Numeric(10, 2), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("data_decyzji", sa.DateTime(), nullable=True),
        sa.Column("uzasadnienie", sa.Text(), nullable=True),
    )
    op.create_index("ix_zmiana_terminu_id_delegacji", "zmiana_terminu", ["id_delegacji"])


def downgrade() -> None:
    op.drop_index("ix_zmiana_terminu_id_delegacji", table_name="zmiana_terminu")
    op.drop_table("zmiana_terminu")
