"""expandir_precision_numeric

Revision ID: 89e54a150506
Revises: 810ba48aeab1
Create Date: 2025-06-14 12:50:26.535327
"""
from alembic import op
import sqlalchemy as sa


# Identificadores de la migración
revision = '89e54a150506'
down_revision = '810ba48aeab1'
branch_labels = None
depends_on = None


def upgrade():
    # 1) Cambiar el tipo de columna a Numeric(8,2)
    op.alter_column(
        'candidatas',
        'porciento',
        existing_type=sa.Numeric(5, 2),
        type_=sa.Numeric(8, 2),
        existing_nullable=True
    )
    op.alter_column(
        'candidatas',
        'acepta_porcentaje_sueldo',
        existing_type=sa.Numeric(5, 2),
        type_=sa.Numeric(8, 2),
        existing_nullable=True
    )

    # 2) Eliminar los constraints antiguos
    op.drop_constraint('chk_porciento', 'candidatas', type_='check')
    op.drop_constraint('chk_acepta_porcentaje', 'candidatas', type_='check')

    # 3) Crear los nuevos constraints con el rango ampliado
    op.create_check_constraint(
        'chk_porciento',
        'candidatas',
        'porciento BETWEEN -10000.00 AND 10000.00'
    )
    op.create_check_constraint(
        'chk_acepta_porcentaje',
        'candidatas',
        'acepta_porcentaje_sueldo BETWEEN -10000.00 AND 10000.00'
    )


def downgrade():
    # Revertir constraints
    op.drop_constraint('chk_porciento', 'candidatas', type_='check')
    op.drop_constraint('chk_acepta_porcentaje', 'candidatas', type_='check')

    op.create_check_constraint(
        'chk_porciento',
        'candidatas',
        'porciento BETWEEN -999.99 AND 999.99'
    )
    op.create_check_constraint(
        'chk_acepta_porcentaje',
        'candidatas',
        'acepta_porcentaje_sueldo BETWEEN -999.99 AND 999.99'
    )

    # Revertir tipo de columna a Numeric(5,2)
    op.alter_column(
        'candidatas',
        'porciento',
        existing_type=sa.Numeric(8, 2),
        type_=sa.Numeric(5, 2),
        existing_nullable=True
    )
    op.alter_column(
        'candidatas',
        'acepta_porcentaje_sueldo',
        existing_type=sa.Numeric(8, 2),
        type_=sa.Numeric(5, 2),
        existing_nullable=True
    )
