"""Make client-portal fields nullable

Revision ID: d4f3e2a1b6c7
Revises: 2ad3eff67cf4
Create Date: 2025-07-04 14:10:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'd4f3e2a1b6c7'
down_revision = '2ad3eff67cf4'
branch_labels = None
depends_on = None

def upgrade():
    op.alter_column('solicitudes', 'ciudad_sector',
        existing_type=sa.String(length=200),
        nullable=True)
    op.alter_column('solicitudes', 'rutas_cercanas',
        existing_type=sa.String(length=200),
        nullable=True)
    op.alter_column('solicitudes', 'modalidad_trabajo',
        existing_type=sa.String(length=100),
        nullable=True)
    op.alter_column('solicitudes', 'edad_requerida',
        existing_type=sa.String(length=50),
        nullable=True)
    op.alter_column('solicitudes', 'experiencia',
        existing_type=sa.Text(),
        nullable=True)
    op.alter_column('solicitudes', 'horario',
        existing_type=sa.String(length=100),
        nullable=True)
    op.alter_column('solicitudes', 'funciones',
        existing_type=sa.Text(),
        nullable=True)
    op.alter_column('solicitudes', 'tipo_lugar',
        existing_type=sa.Enum('casa','oficina','apto','otro', name='tipo_lugar_enum'),
        nullable=True)
    op.alter_column('solicitudes', 'habitaciones',
        existing_type=sa.Integer(),
        nullable=True)
    op.alter_column('solicitudes', 'banos',
        existing_type=sa.Float(),
        nullable=True)
    op.alter_column('solicitudes', 'adultos',
        existing_type=sa.Integer(),
        nullable=True)
    op.alter_column('solicitudes', 'ninos',
        existing_type=sa.Integer(),
        nullable=True)
    op.alter_column('solicitudes', 'edades_ninos',
        existing_type=sa.String(length=100),
        nullable=True)
    op.alter_column('solicitudes', 'sueldo',
        existing_type=sa.String(length=100),
        nullable=True)
    op.alter_column('solicitudes', 'pasaje_aporte',
        existing_type=sa.Boolean(),
        nullable=True)

def downgrade():
    op.alter_column('solicitudes', 'ciudad_sector', nullable=False)
    op.alter_column('solicitudes', 'rutas_cercanas', nullable=False)
    op.alter_column('solicitudes', 'modalidad_trabajo', nullable=False)
    op.alter_column('solicitudes', 'edad_requerida', nullable=False)
    op.alter_column('solicitudes', 'experiencia', nullable=False)
    op.alter_column('solicitudes', 'horario', nullable=False)
    op.alter_column('solicitudes', 'funciones', nullable=False)
    op.alter_column('solicitudes', 'tipo_lugar', nullable=False)
    op.alter_column('solicitudes', 'habitaciones', nullable=False)
    op.alter_column('solicitudes', 'banos', nullable=False)
    op.alter_column('solicitudes', 'adultos', nullable=False)
    op.alter_column('solicitudes', 'ninos', nullable=False)
    op.alter_column('solicitudes', 'edades_ninos', nullable=False)
    op.alter_column('solicitudes', 'sueldo', nullable=False)
    op.alter_column('solicitudes', 'pasaje_aporte', nullable=False)
