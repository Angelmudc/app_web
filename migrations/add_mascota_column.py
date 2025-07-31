# migrations/add_mascota_column.py

import os, sys
from sqlalchemy import text

# Añade raíz al PYTHONPATH
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

from config_app import create_app, db

def main():
    app = create_app()
    with app.app_context():
        print("🔄 Agregando columna mascota a solicitudes…")
        db.session.execute(text(
            "ALTER TABLE solicitudes "
            "ADD COLUMN mascota VARCHAR(100);"
        ))
        db.session.commit()
        print("✅ Columna mascota añadida.")

if __name__ == "__main__":
    main()
