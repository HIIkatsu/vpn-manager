"""outbox processing retry metadata

Revision ID: 0006_outbox_processing_retry
Revises: 0005_outbox_events
Create Date: 2026-05-29
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_outbox_processing_retry"
down_revision = "0005_outbox_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("outbox_events", sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("outbox_events", sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_outbox_events_retry_at"), "outbox_events", ["retry_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_outbox_events_retry_at"), table_name="outbox_events")
    op.drop_column("outbox_events", "locked_at")
    op.drop_column("outbox_events", "retry_at")
