import os
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import create_engine, pool
from alembic import context
from dotenv import load_dotenv

# ─── 1) Carga de .env ────────────────────────────────────────
dotenv_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path, override=True)

# ─── 2) Config de Alembic y override de URL ─────────────────
config = context.config
db_url = os.getenv("DATABASE_URL", "").strip()
if not db_url:
    raise RuntimeError("❌ No encontré DATABASE_URL en tu .env")
config.set_main_option("sqlalchemy.url", db_url)

# ─── 3) Logging según alembic.ini ───────────────────────────
fileConfig(config.config_file_name)

# ─── 4) Importa tus modelos para metadata ────────────────────
from config_app import db
import models  # asegura que aquí se importen todos tus modelos
target_metadata = db.metadata

def run_migrations_offline():
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    """Run migrations in 'online' mode."""
    connectable = create_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
