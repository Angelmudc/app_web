"""Crear tabla reemplazos

Revision ID: c84be1d1f02e
Revises: d5a00156bd65
Create Date: 2025-06-28 12:15:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c84be1d1f02e'
down_revision = 'd5a00156bd65'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        'reemplazos',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('solicitud_id', sa.Integer, sa.ForeignKey('solicitudes.id'), nullable=False),
        sa.Column('candidata_old_id', sa.Integer, sa.ForeignKey('candidatas.fila'), nullable=False),
        sa.Column('motivo_fallo', sa.Text, nullable=False),
        sa.Column('fecha_fallo', sa.DateTime, nullable=False, server_default=sa.text('now()')),
        sa.Column('oportunidad_nueva', sa.Boolean, nullable=False, server_default=sa.text('false')),
        sa.Column('fecha_inicio_reemplazo', sa.DateTime, nullable=True),
        sa.Column('fecha_fin_reemplazo', sa.DateTime, nullable=True),
        sa.Column('candidata_new_id', sa.Integer, sa.ForeignKey('candidatas.fila'), nullable=True),
        sa.Column('nota_adicional', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('now()'))
    )

def downgrade():
    op.drop_table('reemplazos')
