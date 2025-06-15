import os
import psycopg2
from urllib.parse import urlparse
from dotenv import load_dotenv

# 1) Carga .env
load_dotenv()

# 2) Toma la URL de la BD
url = os.getenv("NEW_DATABASE_URL")
if not url:
    print("❌ Define NEW_DATABASE_URL en tu .env")
    exit(1)

# 3) Parseamos la URL
parts = urlparse(url)
dbname = parts.path.lstrip("/")  # nombre real de la BD
# Conéctate a la BD por defecto (postgres) para poder crear la target
conn_info = {
    "dbname": "postgres",
    "user": parts.username,
    "password": parts.password,
    "host": parts.hostname,
    "port": parts.port or 5432
}

try:
    conn = psycopg2.connect(**conn_info)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(f'CREATE DATABASE "{dbname}";')
    print(f"✅ Base `{dbname}` creada exitosamente.")
    cur.close()
    conn.close()
except Exception as e:
    print("❌ No se pudo crear la base:", e)
