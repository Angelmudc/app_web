"""Change acepta_porcentaje_sueldo to Boolean (manual copy)

Revision ID: ade340025a54
Revises: f8be62737073
Create Date: 2025-06-15 12:52:12.380766
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'ade340025a54'
down_revision = 'f8be62737073'
branch_labels = None
depends_on = None

def upgrade():
    # 1) Añadimos la columna temporal (no null, default false)
    op.add_column('candidatas',
        sa.Column('acepta_porcentaje_tmp', sa.Boolean(), nullable=False, server_default=sa.false())
    )
    # 2) Migramos datos: TRUE si el valor original <> 0
    op.execute("""
        UPDATE candidatas
           SET acepta_porcentaje_tmp = (acepta_porcentaje_sueldo IS NOT NULL AND acepta_porcentaje_sueldo <> 0)
    """)
    # 3) Quitamos el default de la columna nueva
    op.alter_column('candidatas', 'acepta_porcentaje_tmp', server_default=None)
    # 4) Borramos la columna antigua
    op.drop_column('candidatas', 'acepta_porcentaje_sueldo')
    # 5) Renombramos la temporal
    op.alter_column('candidatas', 'acepta_porcentaje_tmp',
        new_column_name='acepta_porcentaje_sueldo',
        existing_type=sa.Boolean(),
        nullable=False,
        comment='Si acepta que se cobre un porcentaje de su sueldo (true=Sí, false=No)'
    )


def downgrade():
    # 1) Volvemos a crear la columna original numérica
    op.add_column('candidatas',
        sa.Column('acepta_porcentaje_sueldo', sa.Numeric(8,2), nullable=True)
    )
    # 2) Migramos de booleano a numérico (TRUE->1, FALSE->0)
    op.execute("""
        UPDATE candidatas
           SET acepta_porcentaje_sueldo = CASE WHEN acepta_porcentaje_tmp THEN 1 ELSE 0 END
    """)
    # 3) Borramos la columna temporal
    op.drop_column('candidatas', 'acepta_porcentaje_tmp')
    # 4) (Opcional) Restaurar comment, nullability, etc.
    op.alter_column('candidatas', 'acepta_porcentaje_sueldo',
        comment=None,
        nullable=True
    )
