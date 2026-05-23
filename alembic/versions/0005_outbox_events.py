"""outbox events

Revision ID: 0005_outbox_events
Revises: 0004_payment_processing_started_at
Create Date: 2026-05-23
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_outbox_events"
down_revision = "0004_payment_processing_started_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.String(length=128), nullable=False),
        sa.Column("dedup_key", sa.String(length=128), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_outbox_events_aggregate_id"), "outbox_events", ["aggregate_id"], unique=False)
    op.create_index(op.f("ix_outbox_events_dedup_key"), "outbox_events", ["dedup_key"], unique=True)
    op.create_index(op.f("ix_outbox_events_event_type"), "outbox_events", ["event_type"], unique=False)
    op.create_index(op.f("ix_outbox_events_status"), "outbox_events", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_outbox_events_status"), table_name="outbox_events")
    op.drop_index(op.f("ix_outbox_events_event_type"), table_name="outbox_events")
    op.drop_index(op.f("ix_outbox_events_dedup_key"), table_name="outbox_events")
    op.drop_index(op.f("ix_outbox_events_aggregate_id"), table_name="outbox_events")
    op.drop_table("outbox_events")
