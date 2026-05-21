"""add processed_event_id to payments

Revision ID: 0002_payment_processed_event
Revises: 0001_initial
Create Date: 2026-05-21 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_payment_processed_event"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("processed_event_id", sa.String(length=128), nullable=True))
    op.create_index(op.f("ix_payments_processed_event_id"), "payments", ["processed_event_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_payments_processed_event_id"), table_name="payments")
    op.drop_column("payments", "processed_event_id")
