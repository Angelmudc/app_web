"""Convert edad_requerida to ARRAY

Revision ID: 66413e61f206
Revises: 2ad3eff67cf4
Create Date: 2025-07-07 22:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = '66413e61f206'
down_revision = '2ad3eff67cf4'
branch_labels = None
depends_on = None


def upgrade():
    # cambiar la columna a ARRAY, envolviendo cada valor en un array de un solo elemento
    op.alter_column(
        'solicitudes',
        'edad_requerida',
        existing_type=sa.String(length=50),
        type_=postgresql.ARRAY(sa.String(length=50)),
        postgresql_using="ARRAY[edad_requerida]",
        nullable=False,
        server_default=text("ARRAY[]::VARCHAR[]")
    )


def downgrade():
    # revertir a String(50), extrayendo el primer elemento del array
    op.alter_column(
        'solicitudes',
        'edad_requerida',
        existing_type=postgresql.ARRAY(sa.String(length=50)),
        type_=sa.String(length=50),
        postgresql_using="edad_requerida[1]",
        nullable=True,
        existing_server_default=None
    )
