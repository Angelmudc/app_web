#!/usr/bin/env python3
from __future__ import annotations

import os
import random
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


def main() -> None:
    _require_local_guard()
    with app.app_context():
        db_name = _assert_local_db()
        now = utc_now_naive()
        prefix = now.strftime("%y%m%d%H%M%S")

        random.seed(prefix)
        ciudades = ["Santiago", "Puerto Plata", "La Vega", "Moca", "Santo Domingo"]
        tipo_lugar_opts = ["Casa", "Casa grande", "Apartamento"]
        modalidad_opts = ["salida", "dormida"]
        funciones_pool = [
            "limpieza",
            "cocinar",
            "lavar",
            "planchar",
            "ninos",
            "envejeciente",
        ]

        nuevos_clientes: list[Cliente] = []
        for i in range(1, 21):
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
        for i in range(1, 31):
            cliente = all_clientes[(i - 1) % len(all_clientes)] if i <= 20 else random.choice(all_clientes)
            modalidad = modalidad_opts[i % 2]
            es_santiago = (i % 3) != 0
            ciudad_sector = "Santiago / Zona sin ruta" if es_santiago else f"{random.choice(ciudades)} / Centro"
            rutas = "" if "Santiago" in ciudad_sector and i % 4 == 0 else "Ruta K, Monumental"
            funciones = random.sample(funciones_pool, k=2 if i % 5 else 3)
            ninos = 0
            edades_ninos = ""
            adultos = None if funciones == ["ninos"] else random.choice([1, 2, 3])
            if "ninos" in funciones:
                ninos = 1 if i % 2 else 2
                edades_ninos = "2, 4" if i % 2 == 0 else "8, 11"
            envejeciente_tipo = None
            envejeciente_resp = None
            envejeciente_solo = False
            envejeciente_nota = None
            if "envejeciente" in funciones:
                envejeciente_tipo = "encamado" if i % 2 == 0 else "independiente"
                envejeciente_resp = ["higiene", "medicacion"] if envejeciente_tipo == "encamado" else []
                envejeciente_solo = bool(i % 6 == 0)
                envejeciente_nota = "Control de hidratacion"

            s = Solicitud(
                cliente_id=cliente.id,
                codigo_solicitud=_mk_codigo_solicitud(i, prefix),
                fecha_solicitud=now - timedelta(days=i % 5),
                public_form_source="cliente_nuevo" if i <= 15 else "cliente_existente",
                review_status="nuevo" if i % 4 else "revisado",
                # Garantiza varios casos "flujo publico aceptado" y mantiene variedad.
                terms_accepted=True if i in {3, 8, 13, 18, 23, 28} else bool(i % 7 != 0),
                terms_version="v1.0",
                estado="proceso",
                ciudad_sector=ciudad_sector,
                rutas_cercanas=rutas,
                modalidad_trabajo=modalidad,
                experiencia="Fake: prueba masiva de flujo.",
                horario="L-V 8:00am-5:00pm" if i % 2 else "L-S 7:00am-7:00pm",
                funciones=funciones,
                funciones_otro="",
                tipo_lugar=tipo_lugar_opts[i % len(tipo_lugar_opts)],
                habitaciones=8 if i in {28, 29} else (None if i % 6 else 5),
                banos=6.5 if i in {28, 29} else (None if i % 7 else 3.5),
                dos_pisos=bool(i % 3 == 0),
                adultos=adultos,
                ninos=ninos,
                edades_ninos=edades_ninos,
                mascota="Perro" if i % 4 == 0 else "",
                sueldo="13000" if i % 5 == 0 else "20000",
                pasaje_aporte=bool(i % 2 == 0),
                nota_cliente="Nota manual fake.",
                envejeciente_tipo_cuidado=envejeciente_tipo,
                envejeciente_responsabilidades=envejeciente_resp,
                envejeciente_solo_acompanamiento=envejeciente_solo,
                envejeciente_nota=envejeciente_nota,
                areas_comunes=["sala", "cocina"] if i % 2 else ["patio"],
                area_otro="Balcón" if i % 10 == 0 else "",
            )
            solicitudes_creadas.append(s)
            db.session.add(s)

        for c in nuevos_clientes:
            c.total_solicitudes = int(Solicitud.query.filter_by(cliente_id=c.id).count() or 0)
            c.fecha_ultima_solicitud = now
            c.fecha_ultima_actividad = now

        db.session.commit()

        print("OK")
        print(f"APP_ENV=local")
        print(f"DB={db_name}")
        print(f"CLIENTES_CREADOS={len(nuevos_clientes)}")
        print(f"SOLICITUDES_CREADAS={len(solicitudes_creadas)}")
        print(f"PREFIX={prefix}")


if __name__ == "__main__":
    main()
