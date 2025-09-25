"""Convert solicitudes.edad_requerida to TEXT[]"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# OJO: si tu última revisión NO es esta, cambia down_revision
revision = "fix_edad_requerida_text_array"
down_revision = "9e6766858fbd"
branch_labels = None
depends_on = None

def upgrade():
    conn = op.get_bind()

    # 1) Crear columna temporal con tipo correcto
    op.add_column(
        "solicitudes",
        sa.Column("edad_requerida_tmp", postgresql.ARRAY(sa.Text()), nullable=True),
    )

    # 2) Migrar datos desde la columna vieja (que era VARCHAR)
    #    - Si estaba NULL -> []
    #    - Si tenía un string -> [ese string]
    conn.execute(sa.text("""
        UPDATE solicitudes
        SET edad_requerida_tmp = CASE
            WHEN edad_requerida IS NULL THEN ARRAY[]::text[]
            ELSE ARRAY[edad_requerida::text]
        END
    """))

    # 3) Quitar columna vieja y renombrar la temporal
    try:
        op.drop_column("solicitudes", "edad_requerida")
    except Exception:
        pass

    op.alter_column(
        "solicitudes",
        "edad_requerida_tmp",
        new_column_name="edad_requerida",
        existing_type=postgresql.ARRAY(sa.Text()),
        nullable=False
    )

    # 4) Default a []
    conn.execute(sa.text("""
        ALTER TABLE solicitudes
        ALTER COLUMN edad_requerida SET DEFAULT ARRAY[]::text[]
    """))

def downgrade():
    # No se recomienda volver a VARCHAR
    pass
