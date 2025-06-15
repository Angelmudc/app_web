from alembic import op
import sqlalchemy as sa

revision = 'xxxx'
down_revision = '3eab79c80270'
branch_labels = None
depends_on = None

def upgrade():
    # drop constraints antiguas
    op.drop_constraint('chk_acepta_porcentaje', 'candidatas', type_='check')
    op.drop_constraint('chk_porciento',         'candidatas', type_='check')
    # crea las nuevas con el rango ampliado
    op.create_check_constraint(
        'chk_acepta_porcentaje',
        'candidatas',
        'acepta_porcentaje_sueldo BETWEEN -10000.00 AND 10000.00'
    )
    op.create_check_constraint(
        'chk_porciento',
        'candidatas',
        'porciento BETWEEN -10000.00 AND 10000.00'
    )

def downgrade():
    # en rollback, restauramos original (–999.99 a 999.99)
    op.drop_constraint('chk_acepta_porcentaje', 'candidatas', type_='check')
    op.drop_constraint('chk_porciento',         'candidatas', type_='check')
    op.create_check_constraint(
        'chk_acepta_porcentaje',
        'candidatas',
        'acepta_porcentaje_sueldo BETWEEN -999.99 AND 999.99'
    )
    op.create_check_constraint(
        'chk_porciento',
        'candidatas',
        'porciento BETWEEN -999.99 AND 999.99'
    )
