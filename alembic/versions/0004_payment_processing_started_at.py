"""add processing_started_at to payments

Revision ID: 0004_payment_processing_started_at
Revises: 0003_user_traffic_counters
Create Date: 2026-05-23 00:00:01.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_payment_processing_started_at"
down_revision: Union[str, None] = "0003_user_traffic_counters"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_payments_processing_started_at"), "payments", ["processing_started_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_payments_processing_started_at"), table_name="payments")
    op.drop_column("payments", "processing_started_at")
