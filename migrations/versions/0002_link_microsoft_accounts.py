"""link employees to their Microsoft account

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-26 08:10:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("employees") as batch:
        batch.add_column(sa.Column("microsoft_object_id", sa.String(length=64), nullable=True))
        batch.create_unique_constraint(
            "uq_employees_microsoft_object_id", ["microsoft_object_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("employees") as batch:
        batch.drop_constraint("uq_employees_microsoft_object_id", type_="unique")
        batch.drop_column("microsoft_object_id")
