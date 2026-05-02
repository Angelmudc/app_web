"""add envejeciente fields to solicitudes

Revision ID: 20260502_1400
Revises: 20260502_1200
Create Date: 2026-05-02 14:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = '20260502_1400'
down_revision = '20260502_1200'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('solicitudes', sa.Column('envejeciente_tipo_cuidado', sa.String(length=20), nullable=True))
    op.add_column('solicitudes', sa.Column('envejeciente_responsabilidades', sa.JSON(), nullable=True))
    op.add_column('solicitudes', sa.Column('envejeciente_solo_acompanamiento', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('solicitudes', sa.Column('envejeciente_nota', sa.Text(), nullable=True))


def downgrade():
    # No destructivo por politica del proyecto en este cambio.
    pass
