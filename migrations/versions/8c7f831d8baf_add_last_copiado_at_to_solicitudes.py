"""Add last_copiado_at to solicitudes

Revision ID: 8c7f831d8baf
Revises: 705fce079125
Create Date: 2025-06-28 11:08:46.342740

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8c7f831d8baf'
down_revision = '705fce079125'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('solicitudes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_copiado_at', sa.DateTime(), nullable=True))

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('solicitudes', schema=None) as batch_op:
        batch_op.drop_column('last_copiado_at')

    # ### end Alembic commands ###
