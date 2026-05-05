#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, text

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app
from config_app import db
from clientes.routes import generar_token_publico_cliente_nuevo
from models import Cliente, PublicSolicitudClienteNuevoTokenUso, Solicitud


@dataclass
class Outcome:
    ok_20: bool = False
    ok_double: bool = False
    ok_conc: bool = False
    dup_client_codes: int = 0
    dup_sol_codes: int = 0
    errors: list[str] = field(default_factory=list)


def _require_local() -> str:
    env = (os.getenv("APP_ENV") or "").strip().lower()
    if env != "local":
        raise RuntimeError(f"APP_ENV debe ser local y llegó {env!r}")
    with db.engine.connect() as conn:
        db_name = str(conn.execute(text("select current_database()")).scalar() or "")
    if db_name != "domestica_cibao_local":
        raise RuntimeError(f"DB debe ser domestica_cibao_local y llegó {db_name!r}")
    return db_name


def _payload(run: str, i: int) -> dict[str, Any]:
    return {
        "nombre_completo": f"Cliente Publico {run} {i}",
        "email_contacto": f"publico.{run}.{i}@example.com",
        "telefono_contacto": f"809{(7100000 + i):07d}",
        "ciudad_cliente": "Santiago",
        "sector_cliente": f"Sector {i}",
        "ciudad_sector": "Santiago / Centro",
        "rutas_cercanas": "Ruta K",
        "modalidad_trabajo": "Salida diaria - lunes a viernes",
        "modalidad_grupo": "con_salida_diaria",
        "modalidad_especifica": "l-v",
        "horario": "Lunes a viernes, de 8:00 AM a 5:00 PM",
        "horario_dias_trabajo": "Lunes a viernes",
        "horario_hora_entrada": "8:00 AM",
        "horario_hora_salida": "5:00 PM",
        "edad_requerida": ["26-35"],
        "experiencia": "Experiencia validada en hogar",
        "funciones": ["limpieza", "cocinar"],
        "tipo_lugar": "casa",
        "habitaciones": "3",
        "banos": "2",
        "adultos": "2",
        "ninos": "0",
        "edades_ninos": "",
        "mascota": "",
        "sueldo": "20000",
        "nota_cliente": "Prueba local",
        "areas_comunes": ["sala", "cocina"],
        "terms_decision": "accept",
        "terms_accepted": "1",
        "pasaje_mode": "incluido",
        "pisos_selector": "1",
    }


def _post_token(token: str, payload: dict[str, Any], ip: str) -> tuple[int, str]:
    c = app.test_client()
    r = c.post(
        f"/clientes/solicitudes/nueva-publica/{token}",
        data=payload,
        follow_redirects=False,
        environ_overrides={"REMOTE_ADDR": ip},
    )
    return r.status_code, str(r.headers.get("Location") or "")


def _get_reuse_status(token: str, ip: str) -> int:
    c = app.test_client()
    r = c.get(
        f"/clientes/solicitudes/nueva-publica/{token}",
        follow_redirects=False,
        environ_overrides={"REMOTE_ADDR": ip},
    )
    return int(r.status_code)


def _token_usage(token: str):
    h = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return PublicSolicitudClienteNuevoTokenUso.query.filter_by(token_hash=h).first()


def main() -> None:
    run = datetime.now().strftime("PEND%Y%m%d%H%M%S")
    out = Outcome()

    with app.app_context():
        db_name = _require_local()
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False

        # 1) 20 envíos exitosos
        ok_count = 0
        for i in range(1, 21):
            tok = generar_token_publico_cliente_nuevo(created_by=run)
            code, _loc = _post_token(tok, _payload(run, i), ip=f"127.0.1.{i}")
            usage = _token_usage(tok)
            if code in (302, 303) and usage is not None:
                cli = Cliente.query.get(int(getattr(usage, "cliente_id", 0) or 0))
                sol = Solicitud.query.get(int(getattr(usage, "solicitud_id", 0) or 0))
                reuse = _get_reuse_status(tok, ip=f"127.0.2.{i}")
                if cli and sol and bool(getattr(sol, "terms_accepted", False)) and reuse == 410:
                    ok_count += 1
                else:
                    out.errors.append(f"public20 invalid persistence token#{i} reuse={reuse}")
            else:
                out.errors.append(f"public20 status token#{i}={code} usage={'yes' if usage else 'no'}")
        out.ok_20 = (ok_count == 20)

        # 2) doble submit mismo token
        tok2 = generar_token_publico_cliente_nuevo(created_by=f"{run}-double")
        before_uses = db.session.query(func.count(PublicSolicitudClienteNuevoTokenUso.id)).scalar() or 0
        s1, _ = _post_token(tok2, _payload(run, 1001), ip="127.0.3.1")
        s2, _ = _post_token(tok2, _payload(run, 1001), ip="127.0.3.1")
        after_uses = db.session.query(func.count(PublicSolicitudClienteNuevoTokenUso.id)).scalar() or 0
        usage2 = _token_usage(tok2)
        one_created = bool(usage2 and getattr(usage2, "cliente_id", None) and getattr(usage2, "solicitud_id", None))
        # en esta app el segundo POST puede devolver 200 (pantalla usado), pero no debe crear otro registro
        out.ok_double = one_created and (after_uses - before_uses == 1)
        if not out.ok_double:
            out.errors.append(f"double_submit s1={s1} s2={s2} delta_uses={after_uses-before_uses}")

        # 3) concurrencia real con tokens distintos
        t1 = generar_token_publico_cliente_nuevo(created_by=f"{run}-conc1")
        t2 = generar_token_publico_cliente_nuevo(created_by=f"{run}-conc2")
        result_codes: list[int] = []

        def _w(tok: str, idx: int):
            code, _ = _post_token(tok, _payload(run, 2000 + idx), ip=f"127.0.4.{idx}")
            result_codes.append(code)

        a = threading.Thread(target=_w, args=(t1, 1))
        b = threading.Thread(target=_w, args=(t2, 2))
        a.start(); b.start(); a.join(); b.join()

        u1 = _token_usage(t1)
        u2 = _token_usage(t2)
        c1 = Cliente.query.get(int(getattr(u1, "cliente_id", 0) or 0)) if u1 else None
        c2 = Cliente.query.get(int(getattr(u2, "cliente_id", 0) or 0)) if u2 else None
        s_1 = Solicitud.query.get(int(getattr(u1, "solicitud_id", 0) or 0)) if u1 else None
        s_2 = Solicitud.query.get(int(getattr(u2, "solicitud_id", 0) or 0)) if u2 else None

        out.ok_conc = bool(
            len(result_codes) == 2
            and all(x in (302, 303) for x in result_codes)
            and c1 and c2 and s_1 and s_2
            and str(c1.codigo) != str(c2.codigo)
            and str(s_1.codigo_solicitud) != str(s_2.codigo_solicitud)
        )
        if not out.ok_conc:
            out.errors.append(f"concurrency codes={result_codes}")

        out.dup_client_codes = len(
            db.session.query(Cliente.codigo, func.count(Cliente.id))
            .group_by(Cliente.codigo)
            .having(func.count(Cliente.id) > 1)
            .all()
        )
        out.dup_sol_codes = len(
            db.session.query(Solicitud.codigo_solicitud, func.count(Solicitud.id))
            .group_by(Solicitud.codigo_solicitud)
            .having(func.count(Solicitud.id) > 1)
            .all()
        )

        print("PENDING_PUBLIC_REPORT_START")
        print(f"APP_ENV={os.getenv('APP_ENV')}")
        print(f"DB={db_name}")
        print(f"RUN={run}")
        print(f"PUBLIC_20_OK={'yes' if out.ok_20 else 'no'}")
        print(f"DOUBLE_SUBMIT_BLOCKED={'yes' if out.ok_double else 'no'}")
        print(f"CONCURRENCY_OK={'yes' if out.ok_conc else 'no'}")
        print(f"DUP_CLIENT_CODES={out.dup_client_codes}")
        print(f"DUP_SOL_CODES={out.dup_sol_codes}")
        print(f"ERRORS={len(out.errors)}")
        for e in out.errors[:30]:
            print(f"ERR={e}")
        print("PENDING_PUBLIC_REPORT_END")


if __name__ == "__main__":
    main()
