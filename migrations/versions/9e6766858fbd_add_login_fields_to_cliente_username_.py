"""Add login fields to Cliente (username, password_hash) + indices y alias nombre"""

from alembic import op
import sqlalchemy as sa

# --- Identificadores de Alembic ---
revision = "9e6766858fbd"
down_revision = "13f06816b38c"
branch_labels = None
depends_on = None


def upgrade():
    # 1) Agregar columnas como NULLABLE primero (para no romper filas existentes)
    op.add_column("clientes", sa.Column("username", sa.String(length=64), nullable=True))
    op.add_column("clientes", sa.Column("password_hash", sa.String(length=256), nullable=True))
    op.add_column("clientes", sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.text("true")))
    op.add_column("clientes", sa.Column("updated_at", sa.DateTime(), nullable=True, server_default=sa.text("NOW()")))

    # 2) Backfill de datos existentes
    # username: prioriza codigo; si no, local-part del email limpiado; para evitar choques, concatena _id
    op.execute("""
        UPDATE clientes
        SET username = COALESCE(
            NULLIF(lower(codigo), ''),
            NULLIF(regexp_replace(lower(split_part(email,'@',1)), '[^a-z0-9_]+', '_', 'g'), ''),
            'cli'
        ) || '_' || id::text
        WHERE username IS NULL;
    """)

    # password_hash temporal para forzar reset luego (tu login debe bloquear este valor)
    op.execute("""
        UPDATE clientes
        SET password_hash = 'DISABLED_RESET_REQUIRED'
        WHERE password_hash IS NULL;
    """)

    # is_active: asegurar TRUE si quedó NULL
    op.execute("""
        UPDATE clientes
        SET is_active = TRUE
        WHERE is_active IS NULL;
    """)

    # updated_at: asegurar NOW() si quedó NULL
    op.execute("""
        UPDATE clientes
        SET updated_at = NOW()
        WHERE updated_at IS NULL;
    """)

    # 3) Ahora sí: volver NOT NULL
    op.alter_column("clientes", "username", existing_type=sa.String(length=64), nullable=False)
    op.alter_column("clientes", "password_hash", existing_type=sa.String(length=256), nullable=False)
    op.alter_column("clientes", "is_active", existing_type=sa.Boolean(), nullable=False)
    op.alter_column("clientes", "updated_at", existing_type=sa.DateTime(), nullable=False)

    # 4) Índices (con IF NOT EXISTS por seguridad)
    op.execute("CREATE INDEX IF NOT EXISTS ix_clientes_username ON clientes (username);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_clientes_email ON clientes (email);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_clientes_codigo ON clientes (codigo);")

    # 5) Constraint UNIQUE en codigo (solo si no existe)
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'uq_clientes_codigo'
        ) THEN
            ALTER TABLE clientes
            ADD CONSTRAINT uq_clientes_codigo UNIQUE (codigo);
        END IF;
    END$$;
    """)


def downgrade():
    # Revertir UNIQUE si existe
    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'uq_clientes_codigo'
        ) THEN
            ALTER TABLE clientes
            DROP CONSTRAINT uq_clientes_codigo;
        END IF;
    END$$;
    """)

    # Borrar índices si existen
    op.execute("DROP INDEX IF EXISTS ix_clientes_codigo;")
    op.execute("DROP INDEX IF EXISTS ix_clientes_email;")
    op.execute("DROP INDEX IF EXISTS ix_clientes_username;")

    # Permitir NULL otra vez (orden inverso)
    op.alter_column("clientes", "updated_at", existing_type=sa.DateTime(), nullable=True)
    op.alter_column("clientes", "is_active", existing_type=sa.Boolean(), nullable=True)
    op.alter_column("clientes", "password_hash", existing_type=sa.String(length=256), nullable=True)
    op.alter_column("clientes", "username", existing_type=sa.String(length=64), nullable=True)

    # Quitar columnas
    op.drop_column("clientes", "updated_at")
    op.drop_column("clientes", "is_active")
    op.drop_column("clientes", "password_hash")
    op.drop_column("clientes", "username")
