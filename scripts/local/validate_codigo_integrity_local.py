#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError

from app import app
from config_app import db
from models import Cliente, Solicitud, PublicSolicitudClienteNuevoTokenUso
from admin.routes import _next_codigo_solicitud
from clientes.routes import generar_token_publico_cliente_nuevo


def _require_local() -> str:
    app_env = (os.getenv("APP_ENV") or "").strip().lower()
    if app_env != "local":
        raise RuntimeError(f"Bloqueado: APP_ENV debe ser 'local'. Actual={app_env!r}")
    with db.engine.connect() as conn:
        db_name = str(conn.execute(text("select current_database()")).scalar() or "")
    if db_name != "domestica_cibao_local":
        raise RuntimeError(f"Bloqueado: DB debe ser domestica_cibao_local. Actual={db_name!r}")
    return db_name


def _codigo_to_int(raw: str) -> int | None:
    s = str(raw or "").strip()
    if not s or not re.fullmatch(r"\d{1,3}(?:,\d{3})*|\d+", s):
        return None
    n = int(s.replace(",", ""))
    return n if n > 0 else None


def _mk_public_payload(i: int, run: str) -> dict[str, Any]:
    funcs_variants = [
        ["limpieza", "cocinar"],
        ["limpieza", "ninos"],
        ["envejeciente"],
        ["ninos", "envejeciente", "cocinar"],
    ]
    modalidad_group = "con_dormida" if i % 5 == 0 else "con_salida_diaria"
    modalidad_txt = "Con dormida - lunes a sabado" if modalidad_group == "con_dormida" else "Salida diaria - lunes a viernes"
    funcs = funcs_variants[i % len(funcs_variants)]
    data: dict[str, Any] = {
        "nombre_completo": f"Cliente Publico {run} {i:03d}",
        "email_contacto": f"pub.{run}.{i:03d}@local.test",
        "telefono_contacto": f"809{(7000000 + i):07d}",
        "ciudad_cliente": "Santiago",
        "sector_cliente": f"Sector {i:03d}",
        "ciudad_sector": "Santiago / Centro",
        "rutas_cercanas": "Ruta K",
        "modalidad_trabajo": modalidad_txt,
        "modalidad_grupo": modalidad_group,
        "modalidad_especifica": "l-v",
        "horario": "Lunes a viernes, de 8:00 AM a 5:00 PM",
        "horario_dias_trabajo": "Lunes a viernes",
        "horario_hora_entrada": "8:00 AM",
        "horario_hora_salida": "5:00 PM",
        "dormida_entrada": "Lunes 8:00 AM" if modalidad_group == "con_dormida" else "",
        "dormida_salida": "Sabado 12:00 PM" if modalidad_group == "con_dormida" else "",
        "edad_requerida": ["26-35"],
        "experiencia": "Experiencia validada en hogar.",
        "funciones": funcs,
        "tipo_lugar": "casa",
        "habitaciones": "3",
        "banos": "2",
        "adultos": "2",
        "ninos": "2" if "ninos" in funcs else "0",
        "edades_ninos": "3,7" if "ninos" in funcs else "",
        "mascota": "",
        "sueldo": "20000",
        "nota_cliente": "Solicitud de prueba local",
        "areas_comunes": ["sala", "cocina"],
        "terms_decision": "accept",
        "terms_accepted": "1",
        "pasaje_mode": "aparte" if i % 2 == 0 else "incluido",
        "pisos_selector": "2" if i % 3 == 0 else "1",
    }
    if "envejeciente" in funcs:
        data["envejeciente_tipo_cuidado"] = "encamado" if i % 2 == 0 else "independiente"
        data["envejeciente_responsabilidades"] = ["medicamentos"] if i % 2 == 0 else []
        data["envejeciente_solo_acompanamiento"] = "1" if i % 2 == 1 else ""
        data["envejeciente_nota"] = "Cuidado acompanado"
    return data


def _submit_public(client, token: str, payload: dict[str, Any]):
    return client.post(f"/clientes/solicitudes/nueva-publica/{token}", data=payload, follow_redirects=False)


@dataclass
class Report:
    db_name: str
    run_id: str
    errors: list[str] = field(default_factory=list)


def main() -> None:
    run_id = datetime.now().strftime("%Y%m%d%H%M%S")
    with app.app_context():
        db_name = _require_local()
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        rep = Report(db_name=db_name, run_id=run_id)

        before_clients = db.session.query(func.count(Cliente.id)).scalar() or 0
        before_sols = db.session.query(func.count(Solicitud.id)).scalar() or 0

        # manual low codes
        low_codes = ["100", "300", "583"]
        for code in low_codes:
            if Cliente.query.filter_by(codigo=code).first() is None:
                c = Cliente(
                    codigo=code,
                    nombre_completo=f"Cliente Manual Bajo {code} {run_id}",
                    email=f"manual.{run_id}.{code}@local.test",
                    telefono=f"829{int(code):07d}"[-10:],
                    ciudad="Santiago",
                    sector="Centro",
                    role="cliente",
                    is_active=True,
                )
                c.password_hash = "DISABLED_RESET_REQUIRED"
                db.session.add(c)
        db.session.commit()

        # 2 solicitudes admin flow (using same generator as admin route)
        admin_client = Cliente.query.filter(Cliente.codigo.in_(low_codes)).order_by(Cliente.id.desc()).first()
        for _ in range(2):
            code = _next_codigo_solicitud(admin_client)
            s = Solicitud(cliente_id=admin_client.id, codigo_solicitud=code)
            s.modalidad_trabajo = "Salida diaria - lunes a viernes"
            s.horario = "Lunes a viernes, de 8:00 AM a 5:00 PM"
            s.funciones = ["limpieza", "cocinar"]
            s.tipo_lugar = "Casa"
            s.habitaciones = 3
            s.banos = 2
            s.adultos = 2
            s.ninos = 0
            s.sueldo = "19000"
            s.areas_comunes = ["sala", "cocina"]
            s.terms_accepted = True
            db.session.add(s)
        db.session.commit()

        # public: 20 new clients from public form
        client = app.test_client()
        public_status_codes: list[int] = []
        public_tokens: list[str] = []
        for i in range(1, 21):
            token = generar_token_publico_cliente_nuevo(created_by=f"script-{run_id}")
            public_tokens.append(token)
            resp = _submit_public(client, token, _mk_public_payload(i, run_id))
            public_status_codes.append(resp.status_code)
            if resp.status_code not in (302, 303):
                rep.errors.append(f"public20 item {i} status={resp.status_code}")

        # additional 45 varied cases
        for i in range(21, 66):
            if i % 3 == 0:
                token = generar_token_publico_cliente_nuevo(created_by=f"script-{run_id}")
                public_tokens.append(token)
                resp = _submit_public(client, token, _mk_public_payload(i, run_id))
                if resp.status_code not in (302, 303):
                    rep.errors.append(f"public_mass item {i} status={resp.status_code}")
            else:
                # admin-like create on mixed clients
                target = Cliente.query.order_by(Cliente.id.desc()).first()
                code = _next_codigo_solicitud(target)
                s = Solicitud(cliente_id=target.id, codigo_solicitud=code)
                s.modalidad_trabajo = "Con dormida - lunes a sabado" if i % 5 == 0 else "Salida diaria - lunes a viernes"
                s.horario = "Entrada: Lunes 8:00 AM / Salida: Sabado 12:00 PM" if i % 5 == 0 else "Lunes a viernes, de 8:00 AM a 5:00 PM"
                s.funciones = ["limpieza", "ninos"] if i % 4 == 0 else (["envejeciente"] if i % 7 == 0 else ["limpieza"])
                s.tipo_lugar = "Casa"
                s.habitaciones = 2
                s.banos = 1.5
                s.adultos = 2
                s.ninos = 1 if "ninos" in (s.funciones or []) else 0
                s.edades_ninos = "6" if s.ninos else ""
                if "envejeciente" in (s.funciones or []):
                    s.envejeciente_tipo_cuidado = "independiente"
                    s.envejeciente_nota = "apoyo"
                s.sueldo = "22000"
                s.areas_comunes = ["sala"]
                s.terms_accepted = True
                db.session.add(s)
                try:
                    db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    rep.errors.append(f"admin_mass item {i} error={type(e).__name__}")

        # concurrency: 2 public submits at same time
        conc_results: list[int] = []
        def _worker(n: int):
            with app.app_context():
                c = app.test_client()
                tok = generar_token_publico_cliente_nuevo(created_by=f"conc-{run_id}-{n}")
                resp = _submit_public(c, tok, _mk_public_payload(100 + n, run_id))
                conc_results.append(resp.status_code)

        t1 = threading.Thread(target=_worker, args=(1,))
        t2 = threading.Thread(target=_worker, args=(2,))
        t1.start(); t2.start(); t1.join(); t2.join()

        # double submit same token
        dup_token = generar_token_publico_cliente_nuevo(created_by=f"dup-{run_id}")
        dup_payload = _mk_public_payload(999, run_id)
        r1 = _submit_public(app.test_client(), dup_token, dup_payload)
        r2 = _submit_public(app.test_client(), dup_token, dup_payload)

        after_clients = db.session.query(func.count(Cliente.id)).scalar() or 0
        after_sols = db.session.query(func.count(Solicitud.id)).scalar() or 0

        run_clients = Cliente.query.filter(Cliente.email.like(f"%.{run_id}.%@local.test")).all()
        run_client_ids = [c.id for c in run_clients]
        run_sols = Solicitud.query.filter(Solicitud.cliente_id.in_(run_client_ids)).all() if run_client_ids else []

        numeric_codes = []
        for c in Cliente.query.with_entities(Cliente.codigo).all():
            n = _codigo_to_int(c[0])
            if n is not None:
                numeric_codes.append(n)

        no_client_code = db.session.query(func.count(Cliente.id)).filter((Cliente.codigo.is_(None)) | (Cliente.codigo == "")).scalar() or 0
        no_sol_code = db.session.query(func.count(Solicitud.id)).filter((Solicitud.codigo_solicitud.is_(None)) | (Solicitud.codigo_solicitud == "")).scalar() or 0
        dup_client_codes = db.session.query(Cliente.codigo, func.count(Cliente.id)).group_by(Cliente.codigo).having(func.count(Cliente.id) > 1).all()
        dup_sol_codes = db.session.query(Solicitud.codigo_solicitud, func.count(Solicitud.id)).group_by(Solicitud.codigo_solicitud).having(func.count(Solicitud.id) > 1).all()

        token_hash = __import__("hashlib").sha256(dup_token.encode("utf-8")).hexdigest()
        tok_use = PublicSolicitudClienteNuevoTokenUso.query.filter_by(token_hash=token_hash).first()

        print("VALIDATION_REPORT_START")
        print(f"APP_ENV={os.getenv('APP_ENV')}")
        print(f"DB={db_name}")
        print(f"RUN_ID={run_id}")
        print(f"CLIENTES_BEFORE={before_clients}")
        print(f"SOLICITUDES_BEFORE={before_sols}")
        print(f"CLIENTES_AFTER={after_clients}")
        print(f"SOLICITUDES_AFTER={after_sols}")
        print(f"CLIENTES_CREATED_RUN={len(run_clients)}")
        print(f"SOLICITUDES_CREATED_RUN={len(run_sols)}")
        print(f"LOW_CODES_TESTED={','.join(low_codes)}")
        print(f"PUBLIC20_STATUS_OK={sum(1 for x in public_status_codes if x in (302,303))}/20")
        print(f"CONCURRENCY_PUBLIC_STATUSES={conc_results}")
        print(f"DOUBLE_SUBMIT_STATUSES={[r1.status_code, r2.status_code]}")
        print(f"DOUBLE_SUBMIT_TOKEN_CONSUMED={'yes' if tok_use else 'no'}")
        print(f"NO_CLIENT_CODE={no_client_code}")
        print(f"NO_SOL_CODE={no_sol_code}")
        print(f"DUP_CLIENT_CODES={len(dup_client_codes)}")
        print(f"DUP_SOL_CODES={len(dup_sol_codes)}")
        print(f"MAX_NUMERIC_CLIENT_CODE={max(numeric_codes) if numeric_codes else 0}")
        print(f"ERROR_COUNT={len(rep.errors)}")
        for e in rep.errors[:20]:
            print(f"ERROR_ITEM={e}")
        print("VALIDATION_REPORT_END")


if __name__ == "__main__":
    main()
