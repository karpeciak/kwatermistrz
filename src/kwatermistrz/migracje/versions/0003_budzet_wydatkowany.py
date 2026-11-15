"""S22IT1S0P2-36 (podzadanie S22IT1S0P2-66): kwota wydatkowana budżetu delegacji jednostki.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jednostka") as b:
        b.add_column(sa.Column("budzet_wydatkowany", sa.Numeric(12, 2), server_default="0", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("jednostka") as b:
        b.drop_column("budzet_wydatkowany")
