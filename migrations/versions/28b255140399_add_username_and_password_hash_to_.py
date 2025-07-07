"""Add username and password_hash to clientes

Revision ID: 28b255140399
Revises: 705fce079125
Create Date: 2025-07-04 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from werkzeug.security import generate_password_hash

revision = '28b255140399'
down_revision = '705fce079125'
branch_labels = None
depends_on = None

def upgrade():
    # 1) Añadir las columnas como NULLABLE
    op.add_column('clientes',
        sa.Column('username', sa.String(length=50), nullable=True)
    )
    op.add_column('clientes',
        sa.Column('password_hash', sa.String(length=128), nullable=True)
    )

    # 2) Rellenar valores provisionales
    op.execute("UPDATE clientes SET username = codigo")
    # Genera hash con PBKDF2 (más corto que el default scrypt)
    default_hash = generate_password_hash('changeme', method='pbkdf2:sha256')
    op.execute(
        f"UPDATE clientes SET password_hash = '{default_hash}'"
    )

    # 3) Unicidad y NOT NULL
    op.create_unique_constraint('uq_clientes_username', 'clientes', ['username'])
    op.alter_column('clientes', 'username',
        existing_type=sa.String(length=50),
        nullable=False
    )
    op.alter_column('clientes', 'password_hash',
        existing_type=sa.String(length=128),
        nullable=False
    )

def downgrade():
    op.drop_constraint('uq_clientes_username', 'clientes', type_='unique')
    op.drop_column('clientes', 'password_hash')
    op.drop_column('clientes', 'username')
