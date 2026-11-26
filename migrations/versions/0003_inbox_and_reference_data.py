"""add read state to notifications and who set each exchange rate

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-27 09:00:00
"""

import sqlalchemy as sa
from alembic import op

from greenlight.infrastructure.persistence import tables

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("notifications") as batch:
        batch.add_column(sa.Column("read_at", tables.UtcDateTime(), nullable=True))
        batch.add_column(
            sa.Column("channel", sa.String(length=40), nullable=False, server_default="")
        )
    with op.batch_alter_table("exchange_rates") as batch:
        batch.add_column(sa.Column("updated_by_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_exchange_rates_updated_by_id_employees", "employees", ["updated_by_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("exchange_rates") as batch:
        batch.drop_constraint("fk_exchange_rates_updated_by_id_employees", type_="foreignkey")
        batch.drop_column("updated_by_id")
    with op.batch_alter_table("notifications") as batch:
        batch.drop_column("channel")
        batch.drop_column("read_at")
