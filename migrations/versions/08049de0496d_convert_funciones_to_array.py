"""Convert funciones to ARRAY

Revision ID: 08049de0496d
Revises: b1f2c3d4e5f6
Create Date: 2025-07-08 00:28:17.572606

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '08049de0496d'
down_revision = 'b1f2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    # Ajustamos 'role' de clientes (lo deja igual que antes)
    with op.batch_alter_table('clientes', schema=None) as batch_op:
        batch_op.alter_column(
            'role',
            existing_type=postgresql.ENUM('admin', 'cliente', name='role_cliente_enum'),
            type_=sa.String(length=20),
            comment="Valores: 'cliente' o 'admin'",
            existing_nullable=False
        )

    # Primero revertimos edad_requerida para no tocarla (ya está bien en ARRAY)
    # Ahora convertimos 'funciones' de TEXT a ARRAY(VARCHAR(50))
    with op.batch_alter_table('solicitudes', schema=None) as batch_op:
        # Asegurarnos de preservar valores comma-separated si existían
        batch_op.alter_column(
            'funciones',
            existing_type=sa.TEXT(),
            type_=postgresql.ARRAY(sa.String(length=50)),
            existing_nullable=True,
            postgresql_using=(
                "CASE "
                "  WHEN funciones IS NULL THEN ARRAY[]::VARCHAR[] "
                "  ELSE string_to_array(funciones, ',') "
                "END"
            )
        )


def downgrade():
    # Volvemos 'funciones' a TEXT
    with op.batch_alter_table('solicitudes', schema=None) as batch_op:
        batch_op.alter_column(
            'funciones',
            existing_type=postgresql.ARRAY(sa.String(length=50)),
            type_=sa.TEXT(),
            existing_nullable=True,
            postgresql_using="array_to_string(funciones, ',')"
        )

    # Restauramos 'role' a ENUM en clientes
    with op.batch_alter_table('clientes', schema=None) as batch_op:
        batch_op.alter_column(
            'role',
            existing_type=sa.String(length=20),
            type_=postgresql.ENUM('admin', 'cliente', name='role_cliente_enum'),
            comment=None,
            existing_comment="Valores: 'cliente' o 'admin'",
            existing_nullable=False
        )
