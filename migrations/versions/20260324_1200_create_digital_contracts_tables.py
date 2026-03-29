"""create digital contracts tables

Revision ID: 20260324_1200
Revises: 20260319_1600
Create Date: 2026-03-24 12:00:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260324_1200"
down_revision = "20260319_1600"
branch_labels = None
depends_on = None


CONTRACT_STATES = ("borrador", "enviado", "visto", "firmado", "expirado", "anulado")


def upgrade():
    op.create_table(
        "contratos_digitales",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("solicitud_id", sa.Integer(), sa.ForeignKey("solicitudes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("cliente_id", sa.Integer(), sa.ForeignKey("clientes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version", sa.SmallInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("contrato_padre_id", sa.BigInteger(), sa.ForeignKey("contratos_digitales.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("estado", sa.String(length=16), nullable=False, server_default=sa.text("'borrador'")),
        sa.Column("contenido_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("snapshot_fijado_at", sa.DateTime(), nullable=True),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("token_hash", sa.String(length=64), nullable=True),
        sa.Column("token_generado_at", sa.DateTime(), nullable=True),
        sa.Column("token_expira_at", sa.DateTime(), nullable=True),
        sa.Column("token_revocado_at", sa.DateTime(), nullable=True),
        sa.Column("enviado_at", sa.DateTime(), nullable=True),
        sa.Column("primer_visto_at", sa.DateTime(), nullable=True),
        sa.Column("ultimo_visto_at", sa.DateTime(), nullable=True),
        sa.Column("primera_ip", sa.String(length=64), nullable=True),
        sa.Column("primer_user_agent", sa.String(length=512), nullable=True),
        sa.Column("firma_png", sa.LargeBinary(), nullable=True),
        sa.Column("firma_png_sha256", sa.String(length=64), nullable=True),
        sa.Column("firma_nombre", sa.String(length=180), nullable=True),
        sa.Column("firmado_at", sa.DateTime(), nullable=True),
        sa.Column("firmado_ip", sa.String(length=64), nullable=True),
        sa.Column("firmado_user_agent", sa.String(length=512), nullable=True),
        sa.Column("pdf_final_bytea", sa.LargeBinary(), nullable=True),
        sa.Column("pdf_final_sha256", sa.String(length=64), nullable=True),
        sa.Column("pdf_final_size_bytes", sa.Integer(), nullable=True),
        sa.Column("pdf_generado_at", sa.DateTime(), nullable=True),
        sa.Column("anulado_at", sa.DateTime(), nullable=True),
        sa.Column("anulado_por_staff_id", sa.Integer(), sa.ForeignKey("staff_users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("anulado_motivo", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("solicitud_id", "version", name="uq_contrato_solicitud_version"),
        sa.UniqueConstraint("token_hash", name="uq_contrato_token_hash"),
        sa.CheckConstraint(
            f"estado IN {CONTRACT_STATES}",
            name="ck_contrato_estado_valido",
        ),
        sa.CheckConstraint(
            "token_hash IS NULL OR length(token_hash) = 64",
            name="ck_contrato_token_hash_len",
        ),
        sa.CheckConstraint(
            "firma_png_sha256 IS NULL OR length(firma_png_sha256) = 64",
            name="ck_contrato_firma_hash_len",
        ),
        sa.CheckConstraint(
            "pdf_final_sha256 IS NULL OR length(pdf_final_sha256) = 64",
            name="ck_contrato_pdf_hash_len",
        ),
        sa.CheckConstraint(
            "token_expira_at IS NULL OR token_generado_at IS NULL OR token_expira_at > token_generado_at",
            name="ck_contrato_expira_gt_generado",
        ),
        sa.CheckConstraint(
            "estado <> 'firmado' OR (firmado_at IS NOT NULL AND firma_png_sha256 IS NOT NULL AND pdf_final_sha256 IS NOT NULL AND pdf_generado_at IS NOT NULL)",
            name="ck_contrato_firmado_campos_minimos",
        ),
    )
    op.create_index("ix_contrato_cliente_created", "contratos_digitales", ["cliente_id", "created_at"])
    op.create_index("ix_contrato_estado_expira", "contratos_digitales", ["estado", "token_expira_at"])
    op.create_index("ix_contrato_solicitud", "contratos_digitales", ["solicitud_id"])

    op.create_table(
        "contratos_eventos",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("contrato_id", sa.BigInteger(), sa.ForeignKey("contratos_digitales.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evento_tipo", sa.String(length=48), nullable=False),
        sa.Column("estado_anterior", sa.String(length=16), nullable=True),
        sa.Column("estado_nuevo", sa.String(length=16), nullable=True),
        sa.Column("actor_tipo", sa.String(length=16), nullable=False),
        sa.Column("actor_staff_id", sa.Integer(), sa.ForeignKey("staff_users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            f"(estado_anterior IS NULL OR estado_anterior IN {CONTRACT_STATES}) AND (estado_nuevo IS NULL OR estado_nuevo IN {CONTRACT_STATES})",
            name="ck_evento_estados_validos",
        ),
        sa.CheckConstraint(
            "actor_tipo IN ('staff','cliente_publico','sistema')",
            name="ck_evento_actor_tipo",
        ),
    )
    op.create_index("ix_evento_contrato_created", "contratos_eventos", ["contrato_id", "created_at"])
    op.create_index("ix_evento_tipo_created", "contratos_eventos", ["evento_tipo", "created_at"])
    op.create_index("ix_evento_success_created", "contratos_eventos", ["success", "created_at"])


def downgrade():
    op.drop_index("ix_evento_success_created", table_name="contratos_eventos")
    op.drop_index("ix_evento_tipo_created", table_name="contratos_eventos")
    op.drop_index("ix_evento_contrato_created", table_name="contratos_eventos")
    op.drop_table("contratos_eventos")

    op.drop_index("ix_contrato_solicitud", table_name="contratos_digitales")
    op.drop_index("ix_contrato_estado_expira", table_name="contratos_digitales")
    op.drop_index("ix_contrato_cliente_created", table_name="contratos_digitales")
    op.drop_table("contratos_digitales")
