"""Fix imports for ARRAY and enums

Revision ID: 705fce079125
Revises: 20250626_1630
Create Date: 2025-06-28 10:35:04.802764
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '705fce079125'
down_revision = '20250626_1630'
branch_labels = None
depends_on = None


def upgrade():
    # 1) Rellena NULLs con arreglo vacío para evitar violación de NOT NULL
    op.execute(
        "UPDATE solicitudes "
        "SET areas_comunes = ARRAY[]::VARCHAR[] "
        "WHERE areas_comunes IS NULL"
    )

    # 2) Cambia el tipo y conserva el server_default
    with op.batch_alter_table('solicitudes', schema=None) as batch_op:
        batch_op.alter_column(
            'areas_comunes',
            existing_type=sa.VARCHAR(length=200),
            type_=postgresql.ARRAY(sa.String(length=50)),
            existing_nullable=False,
            nullable=False,
            existing_server_default=text("ARRAY[]::VARCHAR[]"),
            server_default=text("ARRAY[]::VARCHAR[]"),
            postgresql_using="areas_comunes::character varying(50)[]"
        )


def downgrade():
    # En downgrade, convierte el array a string CSV
    with op.batch_alter_table('solicitudes', schema=None) as batch_op:
        batch_op.alter_column(
            'areas_comunes',
            existing_type=postgresql.ARRAY(sa.String(length=50)),
            type_=sa.VARCHAR(length=200),
            existing_nullable=False,
            nullable=True,
            postgresql_using="array_to_string(areas_comunes, ',')"
        )
