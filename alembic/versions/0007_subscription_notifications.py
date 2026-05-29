"""subscription notification delivery markers

Revision ID: 0007_subscription_notifications
Revises: 0006_outbox_processing_retry, e22156d1c9ef
Create Date: 2026-05-29
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_subscription_notifications"
down_revision = ("0006_outbox_processing_retry", "e22156d1c9ef")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "subscription_notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("notify_type", sa.String(length=32), nullable=False),
        sa.Column("sub_end_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "notify_type", "sub_end_date", name="uq_subscription_notifications_user_type_end"),
    )
    op.create_index(op.f("ix_subscription_notifications_notify_type"), "subscription_notifications", ["notify_type"], unique=False)
    op.create_index(op.f("ix_subscription_notifications_retry_at"), "subscription_notifications", ["retry_at"], unique=False)
    op.create_index(op.f("ix_subscription_notifications_status"), "subscription_notifications", ["status"], unique=False)
    op.create_index(op.f("ix_subscription_notifications_sub_end_date"), "subscription_notifications", ["sub_end_date"], unique=False)
    op.create_index(op.f("ix_subscription_notifications_user_id"), "subscription_notifications", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_subscription_notifications_user_id"), table_name="subscription_notifications")
    op.drop_index(op.f("ix_subscription_notifications_sub_end_date"), table_name="subscription_notifications")
    op.drop_index(op.f("ix_subscription_notifications_status"), table_name="subscription_notifications")
    op.drop_index(op.f("ix_subscription_notifications_retry_at"), table_name="subscription_notifications")
    op.drop_index(op.f("ix_subscription_notifications_notify_type"), table_name="subscription_notifications")
    op.drop_table("subscription_notifications")
