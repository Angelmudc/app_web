#!/usr/bin/env python3
# import_candidatas.py

import os
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy.exc import IntegrityError

# Importamos tu fábrica, la extensión `db`, el cliente de Sheets y tu función de cédula
from config_app import create_app, db, sheets, SPREADSHEET_ID, normalize_cedula
from models import Candidata

# —————— Helpers de parsing —————————————————————————————————————
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
DATE_FORMAT     = "%Y-%m-%d"

def parse_datetime(s: str) -> datetime | None:
    s = (s or "").strip()
    for fmt in (DATETIME_FORMAT, DATE_FORMAT):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None

def parse_date(s: str) -> date | None:
    s = (s or "").strip()
    try:
        return datetime.strptime(s, DATE_FORMAT).date()
    except Exception:
        return None

def parse_bool(s: str) -> bool:
    return str(s or "").strip().lower() in ("t", "true", "si", "1", "yes")

def parse_decimal(s: str) -> Decimal | None:
    s = (s or "").strip().replace(",", ".")
    try:
        return Decimal(s)
    except Exception:
        return None

# —————— Main —————————————————————————————————————————————————————
def main():
    app = create_app()
    with app.app_context():
        # 1) Leemos todas las filas (saltamos la cabecera en la fila 2)
        sheet_range = "Nueva hoja!A2:AF"  # Ajusta AF si tienes más/menos columnas
        result = sheets.spreadsheets().values() \
            .get(spreadsheetId=SPREADSHEET_ID, range=sheet_range) \
            .execute()
        rows = result.get("values", [])

        total, inserted, skipped = 0, 0, 0

        for idx, row in enumerate(rows, start=2):
            total += 1
            # Aseguramos que haya al menos 26 columnas
            if len(row) < 26:
                row += [""] * (26 - len(row))

            # Construimos el dict de datos
            data = {
                "fila":                           idx,
                "marca_temporal":                 parse_datetime(row[0]),
                "nombre_completo":                row[1].strip() or None,
                "edad":                           row[2].strip() or None,
                "numero_telefono":                row[3].strip() or None,
                "direccion_completa":             row[4].strip() or None,
                "modalidad_trabajo_preferida":    row[5].strip() or None,
                "rutas_cercanas":                 row[6].strip() or None,
                "empleo_anterior":                row[7].strip() or None,
                "anos_experiencia":               row[8].strip() or None,
                "areas_experiencia":              row[9].strip() or None,
                "sabe_planchar":                  parse_bool(row[10]),
                "contactos_referencias_laborales": row[11].strip() or None,
                "referencias_familiares_detalle":  row[12].strip() or None,
                "acepta_porcentaje_sueldo":       parse_bool(row[13]),
                "cedula":                         normalize_cedula(row[14]) or row[14].strip(),
                "codigo":                         row[15].strip() or None,
                "medio_inscripcion":              row[16].strip() or None,
                "inscripcion":                    parse_bool(row[17]),
                "monto":                          parse_decimal(row[18]),
                "fecha":                          parse_date(row[19]),
                "fecha_de_pago":                  parse_date(row[20]),
                "inicio":                         parse_date(row[21]),
                "monto_total":                    parse_decimal(row[22]),
                "porciento":                      parse_decimal(row[23]),
                "calificacion":                   row[24].strip() or None,
                "entrevista":                     row[25].strip() or None,
                # Los campos de imágenes y referencias finales los dejamos nulos
            }

            candidata = Candidata(**data)
            db.session.add(candidata)
            try:
                db.session.commit()
                inserted += 1
            except IntegrityError:
                db.session.rollback()
                print(f"⚠️ Fila {idx}: duplicado o error de integridad, saltada.")
                skipped += 1

        print(f"🏁 Fin de importación: leídas={total}, insertadas={inserted}, saltadas={skipped}")

if __name__ == "__main__":
    main()
