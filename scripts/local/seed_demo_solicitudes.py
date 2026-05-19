#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import random
import sys
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app
from config_app import db
from models import Cliente, Solicitud
from utils.codigo_solicitud import compose_codigo_solicitud
from utils.horario_mode import build_horario_from_form
from utils.timezone import utc_now_naive

DEMO_TAG = "[DEMO-MASSIVE-LOCAL]"
DEMO_NOTA = f"{DEMO_TAG} demo=true; generado por scripts/local/seed_demo_solicitudes.py"
SAFE_ENVS = {"local", "development", "dev", "test", "testing"}

CIUDADES = [
    "Santiago", "Santo Domingo", "La Vega", "Moca", "Puerto Plata", "San Francisco de Macoris", "Bonao", "San Cristobal",
]
SECTORES = [
    "Centro", "Los Jardines", "Piantini", "Bella Vista", "Cienfuegos", "Gurabo", "Villa Olga", "El Millon",
]
MODALIDADES = [
    "Salida diaria - lunes a viernes", "Salida diaria - lunes a sabado", "Con dormida - lunes a viernes", "Con dormida - lunes a sabado",
]
TIPOS_LUGAR = ["Casa", "Apartamento", "Penthouse", "Oficina pequena"]
FUNCIONES_SET = [
    ["limpiar", "cocinar"],
    ["limpiar", "lavar", "planchar"],
    ["ninos", "cocinar"],
    ["envejeciente", "limpiar"],
    ["chofer", "mensajeria"],
]
SUELDOS = ["RD$16,000", "RD$18,500", "RD$21,000", "RD$24,000", "RD$28,000"]
RUTAS = [
    "K, H, L1", "OMS, A, B", "27, N, R", "P, PA, C", "M, U, C1", "N1, C2, F",
]
NOMBRES = [
    "Ana", "Maria", "Carla", "Rosa", "Luisa", "Pedro", "Miguel", "Jose", "Laura", "Patricia",
    "Sonia", "Raquel", "Daniel", "Ramon", "Elena", "Nadia", "Teresa", "Yolanda", "Dario", "Nestor",
]
APELLIDOS = [
    "Reyes", "Perez", "Santos", "Garcia", "Morillo", "Mendez", "Vargas", "Fernandez", "Diaz", "Castillo",
]


def _guard_local_only() -> dict[str, str]:
    env = (os.getenv("APP_ENV") or "").strip().lower()
    if env not in SAFE_ENVS:
        raise RuntimeError(f"Bloqueado: APP_ENV no seguro ({env or 'vacio'}).")

    url = str(db.engine.url)
    url_low = url.lower()
    banned = ["prod", "production", "rds.amazonaws.com", "supabase.co", "render.com"]
    if any(x in url_low for x in banned):
        raise RuntimeError(f"Bloqueado: URL de base de datos parece no local/segura: {url}")

    info: dict[str, str] = {"app_env": env, "db_url": url}
    try:
        with db.engine.connect() as conn:
            dialect = str(conn.engine.dialect.name or "")
            info["dialect"] = dialect
            if dialect == "postgresql":
                db_name = str(conn.execute(text("select current_database()")).scalar() or "")
                info["db_name"] = db_name
                if any(x in db_name.lower() for x in ["prod", "production"]):
                    raise RuntimeError(f"Bloqueado: nombre de BD riesgoso: {db_name}")
    except Exception:
        # Si falla inspeccion extra, seguimos con guard principal de APP_ENV + URL.
        pass
    return info


def _mk_phone(i: int) -> str:
    return f"82991{i:05d}"[:10]


def _mk_client_code(run_id: str, i: int) -> str:
    return f"DM{run_id[-8:]}{i:03d}"


def _mk_client_name(i: int) -> str:
    n = NOMBRES[(i - 1) % len(NOMBRES)]
    a = APELLIDOS[(i * 3 - 1) % len(APELLIDOS)]
    return f"DEMO {n} {a} {i:03d}"


def _mk_horario(i: int, modalidad: str) -> tuple[str, dict[str, Any]]:
    mod = str(modalidad or "").strip().lower()
    if "salida diaria" in mod:
        if "lunes a sabado" in mod or "lunes a sábado" in mod:
            dias = "Lunes a viernes / sábado hasta 1:00 PM"
            h_in, h_out = "8:00 AM", "5:30 PM"
        else:
            dias = "Lunes a viernes"
            h_in, h_out = "8:30 AM", "6:00 PM"
        horario, payload, errors = build_horario_from_form(
            modalidad_group="con_salida_diaria",
            modalidad_trabajo="Salida diaria",
            dias_trabajo=dias,
            hora_entrada=h_in,
            hora_salida=h_out,
            dormida_entrada="",
            dormida_salida="",
            horario_legacy="",
        )
        if errors:
            raise RuntimeError(f"Horario invalido para demo/salida_diaria: {errors}")
        return horario, payload

    if "con dormida" in mod:
        if "lunes a sabado" in mod or "lunes a sábado" in mod:
            entrada = "Lunes 7:00 AM"
            salida = "Sábado 1:00 PM"
        else:
            entrada = "Lunes 7:00 AM"
            salida = "Viernes 6:00 PM"
        horario, payload, errors = build_horario_from_form(
            modalidad_group="con_dormida",
            modalidad_trabajo="Con dormida",
            dias_trabajo="",
            hora_entrada="",
            hora_salida="",
            dormida_entrada=entrada,
            dormida_salida=salida,
            horario_legacy="",
        )
        if errors:
            raise RuntimeError(f"Horario invalido para demo/con_dormida: {errors}")
        return horario, payload

    raise RuntimeError(f"Modalidad no reconocida para horario demo: {modalidad}")


def _expand_weekly(horario: str) -> dict[str, str]:
    out = {d: "" for d in ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado"]}
    low = (horario or "").lower()
    if "lunes a viernes de" in low and "/ sábado hasta " in low:
        # Lunes a viernes de 8:00 AM a 5:30 PM / sábado hasta 1:00 PM
        lhs, rhs = horario.split("/", 1)
        lv = lhs.split("de", 1)[1].strip()
        sab = rhs.split("hasta", 1)[1].strip()
        for d in ["lunes", "martes", "miercoles", "jueves", "viernes"]:
            out[d] = lv
        out["sabado"] = f"hasta {sab}"
        return out
    if "lunes a sábado de" in low or "lunes a sabado de" in low:
        span = horario.split("de", 1)[1].strip()
        for d in out:
            out[d] = span
        return out
    if "lunes a viernes" in low and "de" in low:
        span = horario.split("de", 1)[1].strip()
        for d in ["lunes", "martes", "miercoles", "jueves", "viernes"]:
            out[d] = span
        return out
    return out


def _next_codigo_for_cliente(cliente_id: int, cliente_codigo: str) -> str:
    base_count = Solicitud.query.filter_by(cliente_id=cliente_id).count()
    intento = 0
    while True:
        candidate = compose_codigo_solicitud(cliente_codigo, base_count + intento)
        if Solicitud.query.filter_by(codigo_solicitud=candidate).first() is None:
            return candidate
        intento += 1


def clear_demo() -> int:
    q = Solicitud.query.filter(Solicitud.nota_cliente.ilike(f"%{DEMO_TAG}%"))
    rows = q.all()
    if not rows:
        return 0
    by_cliente = Counter(int(r.cliente_id) for r in rows)
    for r in rows:
        db.session.delete(r)
    db.session.flush()

    # Borra clientes demo huerfanos de este script.
    for cliente_id in by_cliente:
        c = Cliente.query.filter_by(id=cliente_id).first()
        if c and str(c.codigo or "").startswith("DM") and not c.solicitudes:
            db.session.delete(c)

    db.session.commit()
    return len(rows)


def seed(total_admin: int, total_nuevo: int, total_existente: int, run_id: str) -> dict[str, Any]:
    now = utc_now_naive()
    rng = random.Random(20260519)

    plan = [
        ("admin", total_admin),
        ("cliente_nuevo", total_nuevo),
        ("cliente_existente", total_existente),
    ]

    created: list[Solicitud] = []
    source_counts = Counter()
    used_codes = set()
    used_phones = set()

    idx = 0
    for source, count in plan:
        for _ in range(count):
            idx += 1
            phone = _mk_phone(idx)
            if phone in used_phones:
                raise RuntimeError(f"Telefono duplicado interno: {phone}")
            used_phones.add(phone)

            cliente = Cliente(
                codigo=_mk_client_code(run_id, idx),
                nombre_completo=_mk_client_name(idx),
                email=f"demo.solicitud.{run_id}.{idx:03d}@local.test",
                telefono=phone,
                role="cliente",
                ciudad=CIUDADES[idx % len(CIUDADES)],
                sector=SECTORES[idx % len(SECTORES)],
                acepto_politicas=True,
                fecha_acepto_politicas=now,
                notas_admin=DEMO_NOTA,
            )
            cliente.password_hash = "DISABLED_RESET_REQUIRED"
            db.session.add(cliente)
            db.session.flush()

            codigo = _next_codigo_for_cliente(cliente.id, cliente.codigo)
            if codigo in used_codes:
                raise RuntimeError(f"Codigo duplicado interno: {codigo}")
            used_codes.add(codigo)

            funciones = list(FUNCIONES_SET[idx % len(FUNCIONES_SET)])
            modalidad = MODALIDADES[idx % len(MODALIDADES)]
            horario, horario_payload = _mk_horario(idx, modalidad)

            details = dict(horario_payload)
            details["demo"] = True
            details["demo_tag"] = DEMO_TAG
            details["demo_flow"] = source

            s = Solicitud(
                cliente_id=cliente.id,
                codigo_solicitud=codigo,
                fecha_solicitud=now - timedelta(days=(idx % 9)),
                public_form_source=source,
                review_status="nuevo" if idx % 2 else "revisado",
                terms_accepted=True,
                terms_accepted_at=now,
                terms_version="demo-v1",
                estado="proceso",
                ciudad_sector=f"{cliente.ciudad} / {cliente.sector}",
                rutas_cercanas=RUTAS[idx % len(RUTAS)],
                modalidad_trabajo=modalidad,
                experiencia=f"DEMO experiencia variada #{idx}",
                horario=horario,
                funciones=funciones,
                funciones_otro="",
                tipo_lugar=TIPOS_LUGAR[idx % len(TIPOS_LUGAR)],
                habitaciones=1 + (idx % 4),
                banos=float(1 + (idx % 3)),
                dos_pisos=bool(idx % 2),
                adultos=1 + (idx % 4),
                ninos=(idx % 3),
                edades_ninos="2, 5" if (idx % 3) else "",
                mascota="perro" if idx % 4 == 0 else "",
                sueldo=SUELDOS[idx % len(SUELDOS)],
                pasaje_aporte=bool(idx % 2),
                nota_cliente=f"{DEMO_NOTA}; lote={run_id}; idx={idx}",
                detalles_servicio=details,
                areas_comunes=["sala", "cocina"] if idx % 2 else ["patio", "balcon"],
                area_otro="",
            )
            db.session.add(s)
            db.session.flush()

            # Validacion de editar: actualiza nota y guarda.
            s.nota_cliente = f"{s.nota_cliente}; edit_ok=true"
            db.session.flush()

            # Validacion de copiar (sin insertar adicional): snapshot copyable.
            snapshot = {
                "modalidad_trabajo": s.modalidad_trabajo,
                "horario": s.horario,
                "funciones": list(s.funciones or []),
                "tipo_lugar": s.tipo_lugar,
                "sueldo": s.sueldo,
            }
            assert snapshot["horario"] and snapshot["funciones"], "copy snapshot invalido"

            cliente.total_solicitudes = 1
            cliente.fecha_ultima_solicitud = now
            cliente.fecha_ultima_actividad = now

            created.append(s)
            source_counts[source] += 1

    db.session.commit()

    # Validaciones post-creacion.
    demo_rows = Solicitud.query.filter(Solicitud.nota_cliente.ilike(f"%{DEMO_TAG}%")).all()
    if len(demo_rows) < len(created):
        raise RuntimeError("Validacion fallo: solicitudes demo persistidas incompletas")

    codes = [str(x.codigo_solicitud or "") for x in created]
    phones = [str(x.cliente.telefono or "") for x in created]
    if len(codes) != len(set(codes)):
        raise RuntimeError("Validacion fallo: codigos no unicos")
    if len(phones) != len(set(phones)):
        raise RuntimeError("Validacion fallo: telefonos no unicos")

    # "Aparecen en admin" = existen en tabla operativa y estado proceso.
    visibles_admin = Solicitud.query.filter(
        Solicitud.id.in_([s.id for s in created]),
        Solicitud.estado.in_(["proceso", "activa", "espera_pago", "pagada", "reemplazo"]),
    ).count()

    # Variedad.
    variety = {
        "ciudades": len({(s.cliente.ciudad or "").strip() for s in created}),
        "sectores": len({(s.cliente.sector or "").strip() for s in created}),
        "modalidades": len({(s.modalidad_trabajo or "").strip() for s in created}),
        "horarios": len({(s.horario or "").strip() for s in created}),
        "tipo_lugar": len({(s.tipo_lugar or "").strip() for s in created}),
        "funciones_combo": len({"|".join(sorted(s.funciones or [])) for s in created}),
        "sueldos": len({(s.sueldo or "").strip() for s in created}),
        "rutas": len({(s.rutas_cercanas or "").strip() for s in created}),
        "nombres": len({(s.cliente.nombre_completo or "").strip() for s in created}),
    }

    horario_examples = []
    split_horario_checked = 0
    incoherencias = []
    for s in created[:6]:
        expanded = _expand_weekly(s.horario or "")
        horario_examples.append({
            "codigo": s.codigo_solicitud,
            "horario": s.horario,
            "lunes": expanded["lunes"],
            "sabado": expanded["sabado"],
        })
    for s in created:
        txt = str(s.horario or "")
        if "/ sábado hasta " in txt.lower() or "/ sabado hasta " in txt.lower():
            expanded = _expand_weekly(txt)
            if not expanded["lunes"] or not expanded["sabado"].startswith("hasta "):
                raise RuntimeError(f"Horario separado invalido en {s.codigo_solicitud}: {txt}")
            split_horario_checked += 1
        mod = str(s.modalidad_trabajo or "").lower()
        if "salida diaria - lunes a viernes" in mod and ("lunes a sábado" in txt.lower() or "lunes a sabado" in txt.lower()):
            incoherencias.append((s.codigo_solicitud, s.modalidad_trabajo, s.horario))
        if "salida diaria - lunes a sabado" in mod or "salida diaria - lunes a sábado" in mod:
            if "sábado" not in txt.lower() and "sabado" not in txt.lower():
                incoherencias.append((s.codigo_solicitud, s.modalidad_trabajo, s.horario))
        if "con dormida - lunes a viernes" in mod and "viernes" not in txt.lower():
            incoherencias.append((s.codigo_solicitud, s.modalidad_trabajo, s.horario))
        if ("con dormida - lunes a sabado" in mod or "con dormida - lunes a sábado" in mod) and ("sábado" not in txt.lower() and "sabado" not in txt.lower()):
            incoherencias.append((s.codigo_solicitud, s.modalidad_trabajo, s.horario))
    if split_horario_checked <= 0:
        raise RuntimeError("No se generaron casos de horario separado L-V y sabado para validar.")
    if incoherencias:
        raise RuntimeError(f"Incoherencias modalidad/horario detectadas: {incoherencias[:5]}")

    samples = [
        {
            "codigo": s.codigo_solicitud,
            "flujo": s.public_form_source,
            "cliente": s.cliente.nombre_completo,
            "telefono": s.cliente.telefono,
            "ciudad": s.cliente.ciudad,
            "sector": s.cliente.sector,
            "modalidad": s.modalidad_trabajo,
            "horario": s.horario,
        }
        for s in created[:10]
    ]

    return {
        "total": len(created),
        "source_counts": dict(source_counts),
        "variety": variety,
        "duplicates": {"codes": len(codes) - len(set(codes)), "phones": len(phones) - len(set(phones))},
        "visibles_admin": visibles_admin,
        "horario_examples": horario_examples,
        "split_horario_checked": split_horario_checked,
        "incoherencias": len(incoherencias),
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Seeder local controlado para 100 solicitudes DEMO.")
    parser.add_argument("--admin", type=int, default=35)
    parser.add_argument("--cliente-nuevo", type=int, default=35)
    parser.add_argument("--cliente-existente", type=int, default=30)
    parser.add_argument("--clear-demo", action="store_true", help="Elimina SOLO solicitudes demo generadas por este script.")
    args = parser.parse_args()

    with app.app_context():
        info = _guard_local_only()
        print(f"SAFE_GUARD_OK app_env={info.get('app_env')} db_url={info.get('db_url')}")
        if args.clear_demo:
            deleted = clear_demo()
            print(f"CLEAR_DEMO_OK deleted={deleted}")
            return

        total_target = int(args.admin) + int(args.cliente_nuevo) + int(args.cliente_existente)
        if total_target != 100:
            raise RuntimeError(f"Se requiere total exacto=100, recibido={total_target}")

        run_id = utc_now_naive().strftime("%Y%m%d%H%M%S")
        out = seed(int(args.admin), int(args.cliente_nuevo), int(args.cliente_existente), run_id)

        print("SEED_OK")
        print(f"TOTAL={out['total']}")
        print(f"ADMIN={out['source_counts'].get('admin', 0)}")
        print(f"CLIENTE_NUEVO={out['source_counts'].get('cliente_nuevo', 0)}")
        print(f"CLIENTE_EXISTENTE={out['source_counts'].get('cliente_existente', 0)}")
        print(f"VISIBLES_ADMIN={out['visibles_admin']}")
        print(f"DUP_CODES={out['duplicates']['codes']}")
        print(f"DUP_PHONES={out['duplicates']['phones']}")
        print("VARIEDAD=" + ", ".join(f"{k}:{v}" for k, v in out["variety"].items()))
        print("HORARIOS_EJEMPLO=")
        for e in out["horario_examples"]:
            print(f"- {e['codigo']} :: {e['horario']} :: lunes={e['lunes']} :: sabado={e['sabado']}")
        print(f"HORARIO_SPLIT_VALIDADOS={out['split_horario_checked']}")
        print(f"INCOHERENCIAS_MODALIDAD_HORARIO={out['incoherencias']}")
        print("SOLICITUDES_EJEMPLO=")
        for s in out["samples"]:
            print(
                f"- {s['codigo']} [{s['flujo']}] {s['cliente']} tel={s['telefono']} "
                f"{s['ciudad']}/{s['sector']} | {s['modalidad']} | {s['horario']}"
            )


if __name__ == "__main__":
    main()
