"""harden public token usage traceability

Revision ID: 20260601_1545
Revises: 20260526_1100_add_pendiente_servicio_to_estado_solicitud_enum
Create Date: 2026-06-01 15:45:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260601_1545"
down_revision = "20260526_1100"
branch_labels = None
depends_on = None


TABLES = (
    "public_solicitud_tokens_usados",
    "public_solicitud_cliente_nuevo_tokens_usados",
)

IDX_REASON = {
    "public_solicitud_tokens_usados": "ix_pub_tok_used_reason",
    "public_solicitud_cliente_nuevo_tokens_usados": "ix_pub_new_tok_used_reason",
}
IDX_SOURCE = {
    "public_solicitud_tokens_usados": "ix_pub_tok_used_source",
    "public_solicitud_cliente_nuevo_tokens_usados": "ix_pub_new_tok_used_source",
}


def _cols(bind, table_name: str) -> set[str]:
    insp = sa.inspect(bind)
    return {c["name"] for c in insp.get_columns(table_name)}


def _idx(bind, table_name: str) -> set[str]:
    insp = sa.inspect(bind)
    return {i["name"] for i in insp.get_indexes(table_name)}


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    for table_name in TABLES:
        if not insp.has_table(table_name):
            continue
        cols = _cols(bind, table_name)

        if "consumption_reason" not in cols:
            op.add_column(
                table_name,
                sa.Column("consumption_reason", sa.String(length=40), nullable=False, server_default=sa.text("'submitted'")),
            )
        if "public_form_source" not in cols:
            op.add_column(table_name, sa.Column("public_form_source", sa.String(length=30), nullable=True))
        if "request_ip" not in cols:
            op.add_column(table_name, sa.Column("request_ip", sa.String(length=64), nullable=True))
        if "request_user_agent" not in cols:
            op.add_column(table_name, sa.Column("request_user_agent", sa.String(length=512), nullable=True))

        idx = _idx(bind, table_name)
        if IDX_REASON[table_name] not in idx:
            op.create_index(IDX_REASON[table_name], table_name, ["consumption_reason"], unique=False)
        if IDX_SOURCE[table_name] not in idx:
            op.create_index(IDX_SOURCE[table_name], table_name, ["public_form_source"], unique=False)


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    for table_name in TABLES:
        if not insp.has_table(table_name):
            continue
        idx = _idx(bind, table_name)
        if IDX_SOURCE[table_name] in idx:
            op.drop_index(IDX_SOURCE[table_name], table_name=table_name)
        if IDX_REASON[table_name] in idx:
            op.drop_index(IDX_REASON[table_name], table_name=table_name)

        cols = _cols(bind, table_name)
        if "request_user_agent" in cols:
            op.drop_column(table_name, "request_user_agent")
        if "request_ip" in cols:
            op.drop_column(table_name, "request_ip")
        if "public_form_source" in cols:
            op.drop_column(table_name, "public_form_source")
        if "consumption_reason" in cols:
            op.drop_column(table_name, "consumption_reason")
