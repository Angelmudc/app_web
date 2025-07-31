"""Add informada and exitosa to resultado_enum"""

# revision identifiers, used by Alembic.
revision = '13f06816b38c'
down_revision = 'd01045561b59'
branch_labels = None
depends_on = None

from alembic import op

def upgrade():
    # Agrega los nuevos valores al tipo ENUM existente
    op.execute("ALTER TYPE resultado_enum ADD VALUE IF NOT EXISTS 'informada'")
    op.execute("ALTER TYPE resultado_enum ADD VALUE IF NOT EXISTS 'exitosa'")

def downgrade():
    # PostgreSQL no permite remover valores de un ENUM, así que no hacemos nada.
    pass
