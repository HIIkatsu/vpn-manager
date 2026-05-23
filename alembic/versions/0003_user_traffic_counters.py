"""add persistent traffic counters

Revision ID: 0003_user_traffic_counters
Revises: 0002_payment_processed_event
Create Date: 2026-05-23 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_user_traffic_counters"
down_revision: Union[str, None] = "0002_payment_processed_event"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("traffic_total_bytes", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("traffic_last_live_bytes", sa.BigInteger(), nullable=False, server_default="0"))
    op.alter_column("users", "traffic_total_bytes", server_default=None)
    op.alter_column("users", "traffic_last_live_bytes", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "traffic_last_live_bytes")
    op.drop_column("users", "traffic_total_bytes")
