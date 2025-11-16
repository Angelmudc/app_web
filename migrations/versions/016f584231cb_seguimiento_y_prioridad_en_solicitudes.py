"""
Seguimiento y prioridad en solicitudes

Revision ID: 016f584231cb
Revises: d2766f8b4d8a
Create Date: 2025-11-16 14:06:56.595422
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '016f584231cb'
down_revision = 'd2766f8b4d8a'
branch_labels = None
depends_on = None


def upgrade():
    """Agrega columnas necesarias para el sistema de PRIORIDAD y seguimiento."""

    # Nueva columna: fecha de inicio del seguimiento activo
    op.add_column(
        'solicitudes',
        sa.Column('fecha_inicio_seguimiento', sa.DateTime(), nullable=True)
    )

    # Nueva columna: cuántas veces ha sido activada la solicitud
    op.add_column(
        'solicitudes',
        sa.Column('veces_activada', sa.Integer(), nullable=False, server_default="0")
    )

    # Nueva columna: última vez que se cambió el estado de la solicitud
    op.add_column(
        'solicitudes',
        sa.Column('fecha_ultimo_estado', sa.DateTime(), nullable=True)
    )

    # IMPORTANTE: remover server_default después de inicialización
    op.alter_column('solicitudes', 'veces_activada', server_default=None)


def downgrade():
    """Elimina las columnas si se revierte la migración."""

    op.drop_column('solicitudes', 'fecha_ultimo_estado')
    op.drop_column('solicitudes', 'veces_activada')
    op.drop_column('solicitudes', 'fecha_inicio_seguimiento')
