# manage.py

from flask_migrate import upgrade
from app import create_app

app = create_app()

if __name__ == '__main__':
    print("⚙️  Iniciando migraciones...")
    # No necesitamos hacer `with app.app_context()`: Alembic ya
    # crea y empuja el contexto desde migrations/env.py
    upgrade()
    print("✅ Migraciones aplicadas correctamente.")
