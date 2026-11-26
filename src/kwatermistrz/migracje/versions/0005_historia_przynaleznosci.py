"""S22IT1S0P2-9: historia przynaleznosci i snapshot jednostki delegacji.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "przynaleznosc_jednostki",
        sa.Column("id_przynaleznosci", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id_zolnierza", sa.String(20), sa.ForeignKey("zolnierz.id_zolnierza"), nullable=False),
        sa.Column("id_jednostki", sa.String(20), sa.ForeignKey("jednostka.id_jednostki"), nullable=False),
        sa.Column("data_od", sa.Date(), nullable=False),
        sa.Column("data_do", sa.Date(), nullable=True),
        sa.Column("powod", sa.Text(), nullable=True),
        sa.Column("data_utworzenia", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("id_operatora", sa.String(20), nullable=True),
        sa.Column("zrodlo", sa.String(30), nullable=True),
    )
    op.create_index("ix_przynaleznosc_jednostki_id_zolnierza", "przynaleznosc_jednostki", ["id_zolnierza"])
    op.create_index("ix_przynaleznosc_jednostki_id_jednostki", "przynaleznosc_jednostki", ["id_jednostki"])

    # To jest snapshot stanu znanego w dniu migracji, a nie proba odtworzenia nieznanej historii.
    op.execute(
        sa.text(
            "INSERT INTO przynaleznosc_jednostki "
            "(id_zolnierza, id_jednostki, data_od, data_utworzenia, zrodlo) "
            "SELECT id_zolnierza, id_jednostki, CURRENT_DATE, CURRENT_TIMESTAMP, 'migracja' FROM zolnierz"
        )
    )

    with op.batch_alter_table("delegacja") as b:
        b.add_column(sa.Column("id_jednostki", sa.String(20), nullable=True))
        b.create_foreign_key("fk_delegacja_jednostka", "jednostka", ["id_jednostki"], ["id_jednostki"])
        b.create_index("ix_delegacja_id_jednostki", ["id_jednostki"])
    op.execute(
        sa.text(
            "UPDATE delegacja SET id_jednostki = "
            "(SELECT zolnierz.id_jednostki FROM zolnierz WHERE zolnierz.id_zolnierza = delegacja.id_zolnierza) "
            "WHERE id_jednostki IS NULL"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("delegacja") as b:
        b.drop_index("ix_delegacja_id_jednostki")
        b.drop_constraint("fk_delegacja_jednostka", type_="foreignkey")
        b.drop_column("id_jednostki")
    op.drop_index("ix_przynaleznosc_jednostki_id_jednostki", table_name="przynaleznosc_jednostki")
    op.drop_index("ix_przynaleznosc_jednostki_id_zolnierza", table_name="przynaleznosc_jednostki")
    op.drop_table("przynaleznosc_jednostki")
