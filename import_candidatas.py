#!/usr/bin/env python
# import_candidatas.py

import os
from decimal import Decimal, InvalidOperation
from datetime import datetime, date
from typing import Optional
from sqlalchemy import text
from models import Candidata
from config_app import create_app, db, sheets_service, SPREADSHEET_ID

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
    """
    Igual a parse_decimal, pero None si |valor| > 10000,
    para evitar overflow en Numeric(8,2).
    """
    dec = parse_decimal(s)
    if dec is None:
        return None
    # ahora permitimos hasta 10000.00
    if abs(dec) > Decimal('10000'):
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

def main():
    app = create_app()
    with app.app_context():
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Nueva hoja!A:AF"
        ).execute()
        valores = result.get('values', [])
        if len(valores) < 2:
            print("❌ No hay datos en la hoja.")
            return

        # Vaciar la tabla
        db.session.execute(text('TRUNCATE TABLE candidatas RESTART IDENTITY;'))
        db.session.commit()

        total, duplicados = 0, 0
        for row in valores[1:]:
            # Aseguramos al menos 32 columnas
            row += [''] * (32 - len(row))
            fila = int(row[0]) if row[0].isdigit() else None

            cedula_raw = row[14].strip() or None
            cedula = cedula_raw[:50] if cedula_raw and len(cedula_raw) > 50 else cedula_raw

            anos_exp = row[8].strip() or None

            cand = Candidata(
                fila=fila,
                marca_temporal=parse_datetime(row[0]),
                nombre_completo=row[1].strip() or None,
                edad=row[2].strip() or None,
                numero_telefono=row[3].strip() or None,
                direccion_completa=row[4].strip() or None,
                modalidad_trabajo_preferida=row[5].strip() or None,
                rutas_cercanas=row[6].strip() or None,
                empleo_anterior=row[7].strip() or None,
                anos_experiencia=anos_exp,
                areas_experiencia=row[9].strip() or None,
                sabe_planchar=row[10].strip().lower() in ('sí','si','true','yes'),
                contactos_referencias_laborales=row[11].strip() or None,
                referencias_familiares_detalle=row[12].strip() or None,
                acepta_porcentaje_sueldo=parse_decimal_lim(row[13]),
                cedula=cedula,
                codigo=row[15].strip() or None,
                medio_inscripcion=row[16].strip() or None,
                inscripcion=row[17].strip().lower() in ('sí','si','true','yes'),
                monto=parse_decimal(row[18]),
                fecha=parse_date_iso(row[19]),
                fecha_de_pago=parse_date_iso(row[20]),
                inicio=parse_date_iso(row[21]),
                monto_total=parse_decimal(row[22]),
                porciento=parse_decimal_lim(row[23]),
                calificacion=row[24].strip() or None,
                entrevista=row[25].strip() or None,
                depuracion=row[26].strip() or None,
                perfil=row[27].strip() or None,
                cedula1=row[28].strip() or None,
                cedula2=row[29].strip() or None,
                referencias_laboral=row[30].strip() or None,
                referencias_familiares=row[31].strip() or None,
            )

            try:
                db.session.add(cand)
                db.session.commit()
                total += 1
            except Exception as e:
                db.session.rollback()
                msg = str(e).lower()
                if 'duplicate key value violates unique constraint' in msg:
                    duplicados += 1
                else:
                    print(f"❌ Error importando fila {row[0]!r}: {e}")

        print(f"✅ Importadas {total} candidatas. ({duplicados} duplicados saltados)")

if __name__ == "__main__":
    main()
