"""Añadir estado con default y eliminar cols obsoletas

Revision ID: 656b3174ee61
Revises: 0e8aa0b9fe29
Create Date: 2025-06-25 18:XX:XX

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = '656b3174ee61'
down_revision = '0e8aa0b9fe29'
branch_labels = None
depends_on = None

def upgrade():
    # Ya está todo hecho por la migración inicial: solo marcamos esta revisión como pasada.
    pass

def downgrade():
    # Nada que revertir aquí.
    pass
