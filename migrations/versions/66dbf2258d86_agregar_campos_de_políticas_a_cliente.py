"""Agregar campos de políticas a Cliente

Revision ID: 66dbf2258d86
Revises: fix_edad_requerida_text_array
Create Date: 2025-09-27

"""
from alembic import op
import sqlalchemy as sa

# Revisiones de Alembic
revision = '66dbf2258d86'
down_revision = 'fix_edad_requerida_text_array'
branch_labels = None
depends_on = None


def upgrade():
    # 1) Agregar con server_default para no romper filas existentes
    op.add_column(
        'clientes',
        sa.Column(
            'acepto_politicas',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),  # <-- clave en Postgres
            comment='True si el cliente ya aceptó las políticas al ingresar por primera vez.'
        )
    )
    op.add_column(
        'clientes',
        sa.Column(
            'fecha_acepto_politicas',
            sa.DateTime(),
            nullable=True,
            comment='Fecha/hora en que aceptó por primera vez.'
        )
    )

    # 2) (Opcional) quitar el server_default para que el default lo maneje la app
    #    Si prefieres dejar el default en DB, comenta estas 2 líneas.
    op.alter_column(
        'clientes',
        'acepto_politicas',
        server_default=None
    )


def downgrade():
    op.drop_column('clientes', 'fecha_acepto_politicas')
    op.drop_column('clientes', 'acepto_politicas')
