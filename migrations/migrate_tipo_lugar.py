# migrations/migrate_tipo_lugar.py

import os
import sys
from sqlalchemy import text

# 1) Asegura que el proyecto esté en el PYTHONPATH
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

# 2) Importa tu app y db
from config_app import create_app, db

def main():
    app = create_app()
    with app.app_context():
        print("🔄 Convirtiendo columna tipo_lugar de ENUM a VARCHAR...")
        # DDL para cambiar el tipo
        db.session.execute(text(
            "ALTER TABLE solicitudes "
            "ALTER COLUMN tipo_lugar TYPE VARCHAR(200) USING tipo_lugar::text;"
        ))
        print("🗑️  Eliminando el tipo ENUM obsoleto...")
        db.session.execute(text("DROP TYPE IF EXISTS tipo_lugar_enum;"))
        # Confirma los cambios
        db.session.commit()
        print("✅ Migración completada.")

if __name__ == "__main__":
    main()
