"""Remove username and password_hash from clientes

Revision ID: 72c072e0308d
Revises: d8a149e64813
Create Date: 2025-11-14 11:22:03.358548

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '72c072e0308d'
down_revision = 'd8a149e64813'
branch_labels = None
depends_on = None


def upgrade():
    # Eliminamos las columnas de login que ya no usas
    with op.batch_alter_table('clientes') as batch_op:
        batch_op.drop_column('username')
        batch_op.drop_column('password_hash')


def downgrade():
    # En caso de rollback, las volvemos a crear
    # OJO: las dejo nullable=True para que no reviente si hay filas existentes.
    with op.batch_alter_table('clientes') as batch_op:
        batch_op.add_column(
            sa.Column('username', sa.String(length=64), nullable=True, index=False)
        )
        batch_op.add_column(
            sa.Column('password_hash', sa.String(length=256), nullable=True)
        )
