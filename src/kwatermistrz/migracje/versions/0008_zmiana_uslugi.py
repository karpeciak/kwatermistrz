"""S22IT1S0P2-34: zmiany usług w trakcie podróży i osoba dyżurna jednostki.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jednostka", sa.Column("id_dyzurnego", sa.String(20), nullable=True))
    op.create_table(
        "zmiana_uslugi",
        sa.Column("id_zmiany", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id_delegacji", sa.String(30), sa.ForeignKey("delegacja.id_delegacji"), nullable=False),
        sa.Column("usluga", sa.String(20), nullable=False),
        sa.Column("rodzaj_zdarzenia", sa.String(20), nullable=False),
        sa.Column("id_przejazdu", sa.Integer(), sa.ForeignKey("przejazd.id_przejazdu"), nullable=True),
        sa.Column("id_zakwaterowania", sa.Integer(), sa.ForeignKey("zakwaterowanie.id_zakwaterowania"), nullable=True),
        sa.Column("data_zdarzenia", sa.Date(), nullable=False),
        sa.Column("opis", sa.Text(), nullable=True),
        sa.Column("data_zgloszenia", sa.DateTime(), nullable=False),
        sa.Column("id_przejazdu_zastepczego", sa.Integer(), sa.ForeignKey("przejazd.id_przejazdu"), nullable=True),
        sa.Column(
            "id_zakwaterowania_zastepczego",
            sa.Integer(),
            sa.ForeignKey("zakwaterowanie.id_zakwaterowania"),
            nullable=True,
        ),
        sa.Column("koszt_zastepczy", sa.Numeric(10, 2), nullable=True),
        sa.Column("zgodny_z_limitem", sa.Boolean(), nullable=True),
        sa.Column("termin_odpowiedzi", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("data_odpowiedzi", sa.DateTime(), nullable=True),
        sa.Column("id_dyzurnego", sa.String(20), nullable=True),
    )
    op.create_index("ix_zmiana_uslugi_id_delegacji", "zmiana_uslugi", ["id_delegacji"])


def downgrade() -> None:
    op.drop_index("ix_zmiana_uslugi_id_delegacji", table_name="zmiana_uslugi")
    op.drop_table("zmiana_uslugi")
    with op.batch_alter_table("jednostka") as tabela:
        tabela.drop_column("id_dyzurnego")
