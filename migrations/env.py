# migrations/env.py

import os
import sys
from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool

# ─── Asegura que Alembic encuentre tu proyecto ──────────────────────────
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# ─── Importa tu extensión de base de datos y configuración ─────────────
from config_app import db
from dotenv import load_dotenv

# ─── Carga las variables de entorno desde .env ─────────────────────────
dotenv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '.env'))
load_dotenv(dotenv_path)

# ─── Obtiene la URL de la base (ajusta la clave si tu .env usa otro nombre) ─
database_url = os.getenv('DATABASE_URL') or os.getenv('SQLALCHEMY_DATABASE_URI')
if not database_url:
    raise RuntimeError("No se encontró DATABASE_URL o SQLALCHEMY_DATABASE_URI en .env")

# ─── Configuración de Alembic ──────────────────────────────────────────
config = context.config
fileConfig(config.config_file_name)
config.set_main_option('sqlalchemy.url', database_url)

# ─── Metadata para autogenerar migraciones ─────────────────────────────
target_metadata = db.metadata

def run_migrations_offline():
    """Ejecuta migraciones en modo offline (sin conexión)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"}
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    """Ejecuta migraciones en modo online (con conexión)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata
        )
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
