"""Añadir columnas areas_comunes y area_otro a solicitudes

Revision ID: 20250626_1630
Revises: 8b7cf2048363
Create Date: 2025-06-26 16:30:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20250626_1630'
down_revision = '8b7cf2048363'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column(
        'solicitudes',
        sa.Column('areas_comunes', sa.String(length=200), nullable=True)
    )
    op.add_column(
        'solicitudes',
        sa.Column('area_otro', sa.String(length=200), nullable=True)
    )

def downgrade():
    op.drop_column('solicitudes', 'area_otro')
    op.drop_column('solicitudes', 'areas_comunes')
