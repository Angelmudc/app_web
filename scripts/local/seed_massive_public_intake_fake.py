#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path
from datetime import timedelta

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app
from config_app import db
from models import Cliente, Solicitud
from utils.timezone import utc_now_naive


def _require_local_guard() -> None:
    app_env = (os.getenv("APP_ENV") or "").strip().lower()
    if app_env != "local":
        raise RuntimeError(f"Bloqueado: APP_ENV debe ser 'local' y llego '{app_env or 'vacio'}'.")


def _assert_local_db() -> str:
    with db.engine.connect() as conn:
        db_name = str(conn.execute(text("select current_database()")).scalar() or "")
    if db_name != "domestica_cibao_local":
        raise RuntimeError(
            "Bloqueado: DB activa no es local. "
            f"Esperada=domestica_cibao_local, actual={db_name or 'desconocida'}."
        )
    return db_name


def _mk_codigo_cliente(i: int, prefix: str) -> str:
    return f"F{prefix[-8:]}C{i:03d}"


def _mk_codigo_solicitud(i: int, prefix: str) -> str:
    return f"FAKE-{prefix}-S{i:03d}"


def _mk_horario(i: int) -> tuple[str, str, str]:
    patterns = [
        ("Lunes, de 8:00 AM a 5:00 PM", "8:00 AM", "5:00 PM"),
        ("Martes y jueves, de 7:00 AM a 4:00 PM", "7:00 AM", "4:00 PM"),
        ("Miercoles a viernes, de 8:00 AM a 6:00 PM", "8:00 AM", "6:00 PM"),
        ("Lunes a viernes, de 7:30 AM a 5:30 PM", "7:30 AM", "5:30 PM"),
        ("Lunes a sabado, de 8:00 AM a 5:00 PM", "8:00 AM", "5:00 PM"),
    ]
    return patterns[(i - 1) % len(patterns)]


def _mk_modalidad(i: int) -> str:
    options = [
        "Salida diaria - 1 día a la semana",
        "Salida diaria - 2 días a la semana",
        "Salida diaria - 3 días a la semana",
        "Salida diaria - 4 días a la semana",
        "Salida diaria - lunes a viernes",
        "Con dormida - lunes a viernes",
        "Con dormida - lunes a sábado",
    ]
    return options[(i - 1) % len(options)]


def _extract_horas(horario: str) -> tuple[str, str]:
    txt = str(horario or "")
    parts = txt.split(" de ", 1)
    if len(parts) != 2 or " a " not in parts[1]:
        return "", ""
    span = parts[1]
    start, end = span.split(" a ", 1)
    return start.strip(), end.strip()


def _mk_case_payload(i: int, tipo: str) -> dict:
    horario, h_in, h_out = _mk_horario(i)
    base = {
        "modalidad_trabajo": _mk_modalidad(i),
        "horario": horario,
        "horario_hora_entrada": h_in,
        "horario_hora_salida": h_out,
        "tipo_lugar": "Casa" if i % 3 else "Apartamento",
        "habitaciones": None if i % 4 == 0 else (2 if i % 2 else 3),
        "banos": None if i % 5 == 0 else (1.5 if i % 2 else 2.0),
        "adultos": 2 if i % 3 else 3,
        "areas_comunes": ["sala", "cocina"] if i % 2 else ["patio"],
        "envejeciente_tipo_cuidado": None,
        "envejeciente_responsabilidades": None,
        "envejeciente_solo_acompanamiento": False,
        "envejeciente_nota": None,
        "ninos": 0,
        "edades_ninos": "",
    }
    if tipo == "solo_ninera":
        ninos = 1 if i % 2 else 2
        base.update({
            "funciones": ["ninos"],
            "ninos": ninos,
            "edades_ninos": "2, 4" if i % 3 else "8, 11",
        })
    elif tipo == "ninera_hogar_ligero":
        base.update({
            "funciones": ["ninos", "cocinar", "lavar", "planchar"] if i % 2 else ["ninos", "cocinar"],
            "ninos": 1 if i % 2 else 2,
            "edades_ninos": "3, 7" if i % 2 else "9, 12",
        })
    elif tipo == "solo_enve_independiente":
        base.update({
            "funciones": ["envejeciente"],
            "envejeciente_tipo_cuidado": "independiente",
            "envejeciente_nota": "Acompanamiento y supervision.",
        })
    elif tipo == "solo_enve_encamado":
        enc_resp = ["pampers", "medicamentos"] if i % 2 else []
        base.update({
            "funciones": ["envejeciente"],
            "envejeciente_tipo_cuidado": "encamado",
            "envejeciente_responsabilidades": enc_resp,
            "envejeciente_solo_acompanamiento": bool(i % 3 == 0 and not enc_resp),
            "envejeciente_nota": "Control de hidratacion.",
        })
    elif tipo == "enve_hogar_ligero":
        base.update({
            "funciones": ["envejeciente", "cocinar", "lavar"] if i % 2 else ["envejeciente", "cocinar", "planchar"],
            "envejeciente_tipo_cuidado": "independiente" if i % 2 else "encamado",
            "envejeciente_responsabilidades": ["medicamentos"] if i % 2 == 0 else [],
            "envejeciente_solo_acompanamiento": bool(i % 2),
            "envejeciente_nota": "Apoyo parcial diario.",
        })
    elif tipo == "mixto_ninos_enve":
        base.update({
            "funciones": ["ninos", "envejeciente", "cocinar"] if i % 2 else ["ninos", "envejeciente"],
            "ninos": 1 if i % 2 else 2,
            "edades_ninos": "4, 8",
            "envejeciente_tipo_cuidado": "independiente" if i % 2 else "encamado",
            "envejeciente_responsabilidades": ["higiene"] if i % 2 == 0 else [],
            "envejeciente_solo_acompanamiento": bool(i % 2),
            "envejeciente_nota": "Cuidado combinado.",
        })
    else:
        raise ValueError(f"Tipo no soportado: {tipo}")
    return base


def main() -> None:
    _require_local_guard()
    with app.app_context():
        db_name = _assert_local_db()
        now = utc_now_naive()
        prefix = now.strftime("%y%m%d%H%M%S")
        ciudades = ["Santiago", "Puerto Plata", "La Vega", "Moca", "Santo Domingo"]

        nuevos_clientes: list[Cliente] = []
        for i in range(1, 41):
            ciudad = ciudades[(i - 1) % len(ciudades)]
            c = Cliente(
                codigo=_mk_codigo_cliente(i, prefix),
                nombre_completo=f"Cliente Fake {i:02d}",
                email=f"cliente.fake.{prefix}.{i:02d}@local.test",
                telefono=f"8097{i:07d}"[-10:],
                ciudad=ciudad,
                sector=f"Sector Fake {i:02d}",
                role="cliente",
                acepto_politicas=True,
                fecha_acepto_politicas=now,
            )
            c.password_hash = "DISABLED_RESET_REQUIRED"
            nuevos_clientes.append(c)
            db.session.add(c)

        db.session.flush()

        solicitudes_creadas: list[Solicitud] = []
        all_clientes = list(nuevos_clientes)
        case_types = (
            ["solo_ninera"] * 10
            + ["ninera_hogar_ligero"] * 8
            + ["solo_enve_independiente"] * 6
            + ["solo_enve_encamado"] * 6
            + ["enve_hogar_ligero"] * 5
            + ["mixto_ninos_enve"] * 5
        )
        for i, case_type in enumerate(case_types, start=1):
            cliente = all_clientes[i - 1]
            ciudad_sector = f"{ciudades[(i - 1) % len(ciudades)]} / Centro"
            payload = _mk_case_payload(i, case_type)

            s = Solicitud(
                cliente_id=cliente.id,
                codigo_solicitud=_mk_codigo_solicitud(i, prefix),
                fecha_solicitud=now - timedelta(days=i % 5),
                public_form_source="cliente_nuevo" if i <= 15 else "cliente_existente",
                review_status="nuevo" if i % 4 else "revisado",
                terms_accepted=True,
                terms_version="v1.0",
                estado="proceso",
                ciudad_sector=ciudad_sector,
                rutas_cercanas="Ruta K, Monumental",
                modalidad_trabajo=payload["modalidad_trabajo"],
                experiencia=f"Fake: validacion sueldo sugerido {case_type}.",
                horario=payload["horario"],
                funciones=payload["funciones"],
                funciones_otro="",
                tipo_lugar=payload["tipo_lugar"],
                habitaciones=payload["habitaciones"],
                banos=payload["banos"],
                dos_pisos=False,
                adultos=payload["adultos"],
                ninos=payload["ninos"],
                edades_ninos=payload["edades_ninos"],
                mascota="",
                sueldo="19000",
                pasaje_aporte=bool(i % 2 == 0),
                nota_cliente="Nota manual fake.",
                envejeciente_tipo_cuidado=payload["envejeciente_tipo_cuidado"],
                envejeciente_responsabilidades=payload["envejeciente_responsabilidades"],
                envejeciente_solo_acompanamiento=payload["envejeciente_solo_acompanamiento"],
                envejeciente_nota=payload["envejeciente_nota"],
                areas_comunes=payload["areas_comunes"],
                area_otro="",
            )
            solicitudes_creadas.append(s)
            db.session.add(s)

        for c in nuevos_clientes:
            c.total_solicitudes = int(Solicitud.query.filter_by(cliente_id=c.id).count() or 0)
            c.fecha_ultima_solicitud = now
            c.fecha_ultima_actividad = now

        db.session.commit()

        # Validacion obligatoria del endpoint sobre estas 40 solicitudes.
        app.config["TESTING"] = True
        client = app.test_client()
        by_group = {"ninera": [], "envejeciente": [], "mixto": []}
        no_suggest = []
        reason_blocked = []
        for s in solicitudes_creadas:
            h_in, h_out = _extract_horas(s.horario or "")
            q = [
                ("modalidad_trabajo", s.modalidad_trabajo or ""),
                ("horario", s.horario or ""),
                ("horario_hora_entrada", h_in),
                ("horario_hora_salida", h_out),
                ("tipo_lugar", s.tipo_lugar or ""),
                ("habitaciones", "" if s.habitaciones is None else str(s.habitaciones)),
                ("banos", "" if s.banos is None else str(s.banos)),
                ("ninos", "" if s.ninos is None else str(s.ninos)),
                ("edades_ninos", s.edades_ninos or ""),
                ("adultos", "" if s.adultos is None else str(s.adultos)),
                ("sueldo", s.sueldo or ""),
                ("envejeciente_tipo_cuidado", s.envejeciente_tipo_cuidado or ""),
                ("envejeciente_nota", s.envejeciente_nota or ""),
            ]
            q.extend(("funciones", str(f)) for f in (s.funciones or []))
            q.extend(("envejeciente_responsabilidades", str(r)) for r in (s.envejeciente_responsabilidades or []))
            resp = client.get("/clientes/api/sueldo-sugerido", query_string=q)
            result = resp.get_json() or {}
            if not result.get("can_suggest"):
                no_suggest.append(s.codigo_solicitud)
                reason_blocked.append(str(result.get("reason_no_suggestion") or result.get("message") or ""))
            funcs = {str(x).strip().lower() for x in (s.funciones or [])}
            row = {
                "codigo": s.codigo_solicitud,
                "funciones": sorted(funcs),
                "min": result.get("suggested_min"),
                "max": result.get("suggested_max"),
            }
            if "ninos" in funcs and "envejeciente" not in funcs:
                by_group["ninera"].append(row)
            elif "envejeciente" in funcs and "ninos" not in funcs:
                by_group["envejeciente"].append(row)
            else:
                by_group["mixto"].append(row)

        print("OK")
        print(f"APP_ENV=local")
        print(f"DB={db_name}")
        print(f"CLIENTES_CREADOS={len(nuevos_clientes)}")
        print(f"SOLICITUDES_CREADAS={len(solicitudes_creadas)}")
        print(f"PREFIX={prefix}")
        total = len(solicitudes_creadas)
        suggested = total - len(no_suggest)
        pct = (suggested * 100.0 / total) if total else 0.0
        print(f"SUGERENCIAS_OK={suggested}/{total} ({pct:.2f}%)")
        print(f"SUGERENCIAS_FALLIDAS={len(no_suggest)}")
        if no_suggest:
            print("FALLIDAS_CODIGOS=" + ",".join(no_suggest))
            print("FALLIDAS_RAZONES=" + " | ".join(reason_blocked))
        for key in ("ninera", "envejeciente", "mixto"):
            print(f"EJEMPLOS_{key.upper()}:")
            for item in by_group[key][:3]:
                print(f"- {item['codigo']} funciones={item['funciones']} rango={item['min']}-{item['max']}")


if __name__ == "__main__":
    main()
