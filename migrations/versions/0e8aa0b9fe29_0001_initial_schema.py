"""0001 initial schema

Revision ID: 0e8aa0b9fe29
Revises: 
Create Date: 2025-06-25 16:54:44.628309

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '0e8aa0b9fe29'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # 1) Borrar tipo enum viejo si existe (para evitar valores inválidos)
    op.execute("DROP TYPE IF EXISTS estado_solicitud_enum;")

    # 2) Crear el ENUM con los valores correctos
    op.execute(
        "CREATE TYPE estado_solicitud_enum AS ENUM "
        "('proceso','activa','pagada','cancelada');"
    )

    # 3) Alter table: agregar columna estado con default, cambiar abono y eliminar cols obsoletas
    with op.batch_alter_table('solicitudes', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'estado',
                postgresql.ENUM(
                    'proceso', 'activa', 'pagada', 'cancelada',
                    name='estado_solicitud_enum'
                ),
                nullable=False,
                server_default=sa.text("'proceso'::estado_solicitud_enum")
            )
        )
        batch_op.alter_column(
            'abono',
            existing_type=sa.VARCHAR(length=20),
            type_=sa.String(length=100),
            existing_nullable=True
        )
        batch_op.drop_column('fecha_ultima_actividad')
        batch_op.drop_column('estado_deposito')

    # 4) (Opcional) Quitar el default permanente ahora que ya todos los registros están poblados
    op.alter_column('solicitudes', 'estado', server_default=None)


def downgrade():
    # 1) Recrear columnas obsoletas
    with op.batch_alter_table('solicitudes', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'fecha_ultima_actividad',
                postgresql.TIMESTAMP(),
                nullable=True
            )
        )
        batch_op.add_column(
            sa.Column(
                'estado_deposito',
                sa.VARCHAR(length=20),
                nullable=False,
                server_default=sa.text("'proceso'")
            )
        )
        batch_op.alter_column(
            'abono',
            existing_type=sa.String(length=100),
            type_=sa.VARCHAR(length=20),
            existing_nullable=True
        )
        batch_op.drop_column('estado')

    # 2) Eliminar el tipo ENUM
    op.execute("DROP TYPE IF EXISTS estado_solicitud_enum;")
