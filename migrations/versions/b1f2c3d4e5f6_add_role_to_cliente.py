"""Add role column to Cliente

Revision ID: b1f2c3d4e5f6
Revises: 02fa56e88735
Create Date: 2025-07-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = 'b1f2c3d4e5f6'
down_revision = '02fa56e88735'
branch_labels = None
depends_on = None


def upgrade():
    # 1) Crear el nuevo enum en Postgres
    op.execute("CREATE TYPE role_cliente_enum AS ENUM ('admin','cliente')")
    # 2) Añadir la columna con valor por defecto 'cliente'
    op.add_column(
        'clientes',
        sa.Column(
            'role',
            sa.Enum('admin', 'cliente', name='role_cliente_enum'),
            nullable=False,
            server_default=text("'cliente'")
        )
    )
    # 3) Quitar el server_default para que no persista en el esquema
    op.alter_column('clientes', 'role', server_default=None)


def downgrade():
    # En downgrade eliminamos la columna y luego el enum
    op.drop_column('clientes', 'role')
    op.execute("DROP TYPE role_cliente_enum")
