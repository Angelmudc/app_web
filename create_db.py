#!/usr/bin/env python3
import os
import sys
import psycopg2
from psycopg2 import sql
from urllib.parse import urlparse
from dotenv import load_dotenv

def main():
    # ─── 1) Carga de variables de entorno ──────────────────────
    load_dotenv()
    raw_url = os.getenv("DATABASE_URL") or os.getenv("NEW_DATABASE_URL")
    if not raw_url:
        print("❌ Error: define DATABASE_URL (o NEW_DATABASE_URL) en tu .env")
        sys.exit(1)

    # ─── 2) Parseamos la URL de conexión ───────────────────────
    parts = urlparse(raw_url)
    target_db = parts.path.lstrip("/")
    admin_conn_info = {
        "dbname": "postgres",                 # conectarse a la BD 'postgres' para gestionar DBs
        "user": parts.username,
        "password": parts.password,
        "host": parts.hostname or "localhost",
        "port": parts.port or 5432,
    }

    # ─── 3) Conexión al servidor para gestionar bases ───────────
    try:
        admin_conn = psycopg2.connect(**admin_conn_info)
        admin_conn.autocommit = True
        cur = admin_conn.cursor()
    except Exception as e:
        print(f"❌ No pude conectarme al servidor Postgres: {e}")
        sys.exit(1)

    # ─── 4) Comprobamos si la base ya existe ───────────────────
    cur.execute(
        "SELECT 1 FROM pg_database WHERE datname = %s;",
        (target_db,)
    )
    exists = cur.fetchone() is not None

    if exists:
        print(f"⚠️ La base de datos `{target_db}` ya existe. No se crea de nuevo.")
    else:
        # ─── 5) Creamos la base de datos ─────────────────────────
        try:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(
                sql.Identifier(target_db)
            ))
            print(f"✅ Base de datos `{target_db}` creada correctamente.")
        except Exception as e:
            print(f"❌ Error al crear la base `{target_db}`: {e}")
            cur.close()
            admin_conn.close()
            sys.exit(1)

    # ─── 6) Cierra conexiones y sale ───────────────────────────
    cur.close()
    admin_conn.close()
    sys.exit(0)

if __name__ == "__main__":
    main()
