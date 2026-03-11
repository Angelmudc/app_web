"""Add public token usage tables for one-time public request links.

Revision ID: 20260311_1100
Revises: 20260310_1730
Create Date: 2026-03-11 11:00:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260311_1100"
down_revision = "20260310_1730"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def _ensure_table(
        table_name: str,
        *,
        cliente_nullable: bool,
        create_fk_cliente: bool = True,
        create_fk_solicitud: bool = True,
    ) -> None:
        if not inspector.has_table(table_name):
            op.create_table(
                table_name,
                sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
                sa.Column("token_hash", sa.String(length=64), nullable=False),
                sa.Column("cliente_id", sa.Integer(), nullable=cliente_nullable),
                sa.Column("solicitud_id", sa.Integer(), nullable=True),
                sa.Column("used_at", sa.DateTime(), nullable=False),
                sa.Column("created_at", sa.DateTime(), nullable=False),
            )

        local_inspector = sa.inspect(bind)
        cols = {c["name"] for c in local_inspector.get_columns(table_name)}

        if "token_hash" not in cols:
            op.add_column(table_name, sa.Column("token_hash", sa.String(length=64), nullable=False))
        if "cliente_id" not in cols:
            op.add_column(table_name, sa.Column("cliente_id", sa.Integer(), nullable=cliente_nullable))
        if "solicitud_id" not in cols:
            op.add_column(table_name, sa.Column("solicitud_id", sa.Integer(), nullable=True))
        if "used_at" not in cols:
            op.add_column(table_name, sa.Column("used_at", sa.DateTime(), nullable=False))
        if "created_at" not in cols:
            op.add_column(table_name, sa.Column("created_at", sa.DateTime(), nullable=False))

        idx_by_name = {idx["name"]: idx for idx in local_inspector.get_indexes(table_name)}
        if f"ix_{table_name}_token_hash" not in idx_by_name:
            op.create_index(f"ix_{table_name}_token_hash", table_name, ["token_hash"], unique=True)
        if f"ix_{table_name}_cliente_id" not in idx_by_name:
            op.create_index(f"ix_{table_name}_cliente_id", table_name, ["cliente_id"], unique=False)
        if f"ix_{table_name}_solicitud_id" not in idx_by_name:
            op.create_index(f"ix_{table_name}_solicitud_id", table_name, ["solicitud_id"], unique=False)
        if f"ix_{table_name}_used_at" not in idx_by_name:
            op.create_index(f"ix_{table_name}_used_at", table_name, ["used_at"], unique=False)

        fk_cols = {tuple(fk.get("constrained_columns") or []) for fk in local_inspector.get_foreign_keys(table_name)}
        if create_fk_cliente and ("cliente_id",) not in fk_cols:
            op.create_foreign_key(
                f"{table_name}_cliente_id_fkey",
                table_name,
                "clientes",
                ["cliente_id"],
                ["id"],
            )
        if create_fk_solicitud and ("solicitud_id",) not in fk_cols:
            op.create_foreign_key(
                f"{table_name}_solicitud_id_fkey",
                table_name,
                "solicitudes",
                ["solicitud_id"],
                ["id"],
            )

    _ensure_table("public_solicitud_tokens_usados", cliente_nullable=False)
    _ensure_table("public_solicitud_cliente_nuevo_tokens_usados", cliente_nullable=True)


def downgrade():
    op.drop_index(
        "ix_public_solicitud_cliente_nuevo_tokens_usados_used_at",
        table_name="public_solicitud_cliente_nuevo_tokens_usados",
    )
    op.drop_index(
        "ix_public_solicitud_cliente_nuevo_tokens_usados_solicitud_id",
        table_name="public_solicitud_cliente_nuevo_tokens_usados",
    )
    op.drop_index(
        "ix_public_solicitud_cliente_nuevo_tokens_usados_cliente_id",
        table_name="public_solicitud_cliente_nuevo_tokens_usados",
    )
    op.drop_index(
        "ix_public_solicitud_cliente_nuevo_tokens_usados_token_hash",
        table_name="public_solicitud_cliente_nuevo_tokens_usados",
    )
    op.drop_table("public_solicitud_cliente_nuevo_tokens_usados")

    op.drop_index(
        "ix_public_solicitud_tokens_usados_used_at",
        table_name="public_solicitud_tokens_usados",
    )
    op.drop_index(
        "ix_public_solicitud_tokens_usados_solicitud_id",
        table_name="public_solicitud_tokens_usados",
    )
    op.drop_index(
        "ix_public_solicitud_tokens_usados_cliente_id",
        table_name="public_solicitud_tokens_usados",
    )
    op.drop_index(
        "ix_public_solicitud_tokens_usados_token_hash",
        table_name="public_solicitud_tokens_usados",
    )
    op.drop_table("public_solicitud_tokens_usados")
