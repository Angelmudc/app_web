#!/usr/bin/env python
# import_candidatas.py

# 0) Carga .env para desarrollo local
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent / '.env')

import os
import json
from decimal import Decimal, InvalidOperation
from datetime import datetime, date
from typing import Optional, List

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

# Google Sheets client
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from models import Candidata
from config_app import create_app, db, SPREADSHEET_ID

# construye el cliente de Sheets a partir de la clave JSON de tu env
def get_sheets_service():
    info = json.loads(os.getenv("CLAVE1_JSON"))
    creds = Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    return build("sheets", "v4", credentials=creds)

def parse_datetime(dt_str: str) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        return datetime.strptime(dt_str.strip(), "%d/%m/%Y %H:%M:%S")
    except ValueError:
        return None

def parse_decimal(s: str) -> Optional[Decimal]:
    if not s:
        return None
    try:
        return Decimal(s.strip().replace(',', '.'))
    except (InvalidOperation, AttributeError):
        return None

def parse_decimal_lim(s: str) -> Optional[Decimal]:
    dec = parse_decimal(s)
    if dec is None or abs(dec) > Decimal('10000'):
        return None
    return dec

def parse_date_iso(s: str) -> Optional[date]:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None

def parse_bool(s: str) -> bool:
    return bool(s) and s.strip().lower() in ('sí', 'si', 'true', 'yes')

def main():
    app = create_app()
    sheets_service = get_sheets_service()

    with app.app_context():
        # 1) Vaciar tabla
        db.session.execute(text('TRUNCATE TABLE candidatas RESTART IDENTITY;'))
        db.session.commit()

        # 2) Leer cédulas ya importadas (vacío tras truncate)
        existentes = set()

        # 3) Leer datos de Sheets
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Nueva hoja!A:AF"
        ).execute()
        valores = result.get('values', [])
        if len(valores) < 2:
            print("❌ No hay datos para importar.")
            return

        # 4) Construir lista de objetos, saltando duplicados por cédula
        objs: List[Candidata] = []
        for row in valores[1:]:
            row += [''] * (32 - len(row))
            ced_raw = row[14].strip() or None
            ced = ced_raw[:50] if ced_raw and len(ced_raw) > 50 else ced_raw
            if ced and ced in existentes:
                continue
            if ced:
                existentes.add(ced)

            objs.append(Candidata(
                fila=int(row[0]) if row[0].isdigit() else None,
                marca_temporal=parse_datetime(row[0]),
                nombre_completo=row[1].strip() or None,
                edad=row[2].strip() or None,
                numero_telefono=row[3].strip() or None,
                direccion_completa=row[4].strip() or None,
                modalidad_trabajo_preferida=row[5].strip() or None,
                rutas_cercanas=row[6].strip() or None,
                empleo_anterior=row[7].strip() or None,
                anos_experiencia=row[8].strip() or None,
                areas_experiencia=row[9].strip() or None,
                sabe_planchar=parse_bool(row[10]),
                contactos_referencias_laborales=row[11].strip() or None,
                referencias_familiares_detalle=row[12].strip() or None,
                acepta_porcentaje_sueldo=parse_bool(row[13]),
                cedula=ced,
                codigo=row[15].strip() or None,
                medio_inscripcion=row[16].strip() or None,
                inscripcion=parse_bool(row[17]),
                monto=parse_decimal(row[18]),
                fecha=parse_date_iso(row[19]),
                fecha_de_pago=parse_date_iso(row[20]),
                inicio=parse_date_iso(row[21]),
                monto_total=parse_decimal(row[22]),
                porciento=parse_decimal_lim(row[23]),
                calificacion=row[24].strip() or None,
                entrevista=row[25].strip() or None,
                # BLOBs → None para evitar error "can't escape str to binary"
                depuracion=None,
                perfil=None,
                cedula1=None,
                cedula2=None,
                referencias_laboral=row[30].strip() or None,
                referencias_familiares=row[31].strip() or None,
            ))

        # 5) Insertar en bloques de 500
        CHUNK = 500
        total = 0
        for i in range(0, len(objs), CHUNK):
            bloque = objs[i : i + CHUNK]
            db.session.add_all(bloque)
            try:
                db.session.commit()
                total += len(bloque)
            except IntegrityError:
                db.session.rollback()
                # inserción individual para aislar fallos
                for c in bloque:
                    try:
                        db.session.add(c)
                        db.session.commit()
                        total += 1
                    except IntegrityError:
                        db.session.rollback()

        print(f"✅ Importadas {total} candidatas. ({len(valores)-1-total} duplicados saltados)")

if __name__ == "__main__":
    main()
