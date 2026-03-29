"""add concurrency and outbox phase1

Revision ID: 20260328_1200
Revises: 20260327_1100
Create Date: 2026-03-28 12:00:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260328_1200"
down_revision = "20260327_1100"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("solicitudes", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1"))
        )

    op.create_table(
        "request_idempotency_keys",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("scope", sa.String(length=80), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=True),
        sa.Column("entity_type", sa.String(length=50), nullable=True),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_code", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("scope", "idempotency_key", name="uq_request_idempotency_scope_key"),
    )
    op.create_index("ix_request_idempotency_scope", "request_idempotency_keys", ["scope"])
    op.create_index("ix_request_idempotency_scope_actor", "request_idempotency_keys", ["scope", "actor_id"])
    op.create_index("ix_request_idempotency_actor", "request_idempotency_keys", ["actor_id"])
    op.create_index("ix_request_idempotency_entity_type", "request_idempotency_keys", ["entity_type"])
    op.create_index("ix_request_idempotency_entity_id", "request_idempotency_keys", ["entity_id"])
    op.create_index("ix_request_idempotency_created_at", "request_idempotency_keys", ["created_at"])
    op.create_index("ix_request_idempotency_last_seen_at", "request_idempotency_keys", ["last_seen_at"])

    op.create_table(
        "domain_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("aggregate_type", sa.String(length=80), nullable=False),
        sa.Column("aggregate_id", sa.String(length=64), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=True),
        sa.Column("region", sa.String(length=40), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("event_id", name="uq_domain_outbox_event_id"),
    )
    op.create_index("ix_domain_outbox_event_id", "domain_outbox", ["event_id"])
    op.create_index("ix_domain_outbox_event_type", "domain_outbox", ["event_type"])
    op.create_index("ix_domain_outbox_aggregate_type", "domain_outbox", ["aggregate_type"])
    op.create_index("ix_domain_outbox_aggregate_id", "domain_outbox", ["aggregate_id"])
    op.create_index("ix_domain_outbox_aggregate_version", "domain_outbox", ["aggregate_version"])
    op.create_index("ix_domain_outbox_occurred_at", "domain_outbox", ["occurred_at"])
    op.create_index("ix_domain_outbox_actor_id", "domain_outbox", ["actor_id"])
    op.create_index("ix_domain_outbox_published_at", "domain_outbox", ["published_at"])
    op.create_index("ix_domain_outbox_created_at", "domain_outbox", ["created_at"])
    op.create_index("ix_domain_outbox_aggregate", "domain_outbox", ["aggregate_type", "aggregate_id"])
    op.create_index("ix_domain_outbox_published_created", "domain_outbox", ["published_at", "created_at"])


def downgrade():
    op.drop_index("ix_domain_outbox_published_created", table_name="domain_outbox")
    op.drop_index("ix_domain_outbox_aggregate", table_name="domain_outbox")
    op.drop_index("ix_domain_outbox_created_at", table_name="domain_outbox")
    op.drop_index("ix_domain_outbox_published_at", table_name="domain_outbox")
    op.drop_index("ix_domain_outbox_actor_id", table_name="domain_outbox")
    op.drop_index("ix_domain_outbox_occurred_at", table_name="domain_outbox")
    op.drop_index("ix_domain_outbox_aggregate_version", table_name="domain_outbox")
    op.drop_index("ix_domain_outbox_aggregate_id", table_name="domain_outbox")
    op.drop_index("ix_domain_outbox_aggregate_type", table_name="domain_outbox")
    op.drop_index("ix_domain_outbox_event_type", table_name="domain_outbox")
    op.drop_index("ix_domain_outbox_event_id", table_name="domain_outbox")
    op.drop_table("domain_outbox")

    op.drop_index("ix_request_idempotency_last_seen_at", table_name="request_idempotency_keys")
    op.drop_index("ix_request_idempotency_created_at", table_name="request_idempotency_keys")
    op.drop_index("ix_request_idempotency_entity_id", table_name="request_idempotency_keys")
    op.drop_index("ix_request_idempotency_entity_type", table_name="request_idempotency_keys")
    op.drop_index("ix_request_idempotency_actor", table_name="request_idempotency_keys")
    op.drop_index("ix_request_idempotency_scope_actor", table_name="request_idempotency_keys")
    op.drop_index("ix_request_idempotency_scope", table_name="request_idempotency_keys")
    op.drop_table("request_idempotency_keys")

    with op.batch_alter_table("solicitudes", schema=None) as batch_op:
        batch_op.drop_column("row_version")
