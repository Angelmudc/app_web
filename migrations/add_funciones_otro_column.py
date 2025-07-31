# migrations/add_funciones_otro_column.py

import os, sys
from sqlalchemy import text

# 1) Asegura que el proyecto esté en PYTHONPATH
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

from config_app import create_app, db

def main():
    app = create_app()
    with app.app_context():
        print("🔄 Agregando columna funciones_otro a solicitudes…")
        db.session.execute(text(
            "ALTER TABLE solicitudes "
            "ADD COLUMN funciones_otro VARCHAR(200);"
        ))
        db.session.commit()
        print("✅ Columna funciones_otro añadida.")

if __name__ == "__main__":
    main()
