# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app as flask_app
from config_app import db
from models import (
    Candidata,
    Cliente,
    DomainOutbox,
    Reemplazo,
    Solicitud,
    StaffAuditLog,
    StaffUser,
    SeguimientoCandidataCaso,
)
from utils.timezone import utc_now_naive

CLIENT_PREFIX = "QA-REEMP-"
CAND_PREFIX = "QA-REEMP-CAND-"
SOL_PREFIX = "QA-REEMP-SOL-"
NOTA_TAG = "[QA-REEMP-SEED]"

SAFE_ENVS = {"local", "development", "dev", "test", "testing"}


@dataclass
class SeedSummary:
    created_clientes: int
    created_solicitudes: int
    created_reemplazos: int
    skipped_existing: bool
    db_url: str


def _db_url_text() -> str:
    try:
        return str(db.engine.url)
    except Exception:
        return str(flask_app.config.get("SQLALCHEMY_DATABASE_URI") or "").strip()


def _looks_production_db(url: str) -> bool:
    low = (url or "").strip().lower()
    if not low:
        return False
    markers = [
        "production",
        "prod",
        "rds.amazonaws.com",
        "render.com",
        "supabase.co",
        "neon.tech",
        "railway.app",
        "heroku",
    ]
    if any(m in low for m in markers):
        return True
    if low.startswith("postgres") and all(h not in low for h in ["localhost", "127.0.0.1"]):
        return True
    return False


def _assert_local_safety() -> str:
    app_env = (os.getenv("APP_ENV") or "").strip().lower()
    flask_env = (os.getenv("FLASK_ENV") or "").strip().lower()
    db_url = _db_url_text()
    prod_url = (os.getenv("DATABASE_URL") or "").strip()

    if app_env == "production":
        raise RuntimeError("Abortado: APP_ENV=production")
    if flask_env == "production":
        raise RuntimeError("Abortado: FLASK_ENV=production")
    if app_env and app_env not in SAFE_ENVS:
        raise RuntimeError(f"Abortado: APP_ENV no permitido para seed local ({app_env})")
    if prod_url and db_url and prod_url == db_url:
        raise RuntimeError("Abortado: DB actual coincide con DATABASE_URL")
    if _looks_production_db(db_url):
        raise RuntimeError(f"Abortado: URL de DB parece de producción: {db_url}")

    try:
        with db.engine.connect() as conn:
            dialect = str(conn.engine.dialect.name or "").strip().lower()
            if dialect == "postgresql":
                db_name = str(conn.execute(text("select current_database()")).scalar() or "")
                if any(x in db_name.lower() for x in ["prod", "production"]):
                    raise RuntimeError(f"Abortado: nombre de BD parece producción: {db_name}")
    except RuntimeError:
        raise
    except Exception:
        pass

    return db_url


def _mk_cedula(num: int) -> str:
    return f"{num:011d}"[-11:]


def _seed_staff_user() -> int:
    staff = StaffUser.query.filter(StaffUser.username.ilike("seedstaff")).first()
    if staff is None:
        staff = StaffUser(username="seedstaff", password_hash="x", role="admin", is_active=True)
        db.session.add(staff)
        db.session.flush()
    return int(staff.id)


def _find_seed_clientes() -> list[Cliente]:
    return (
        Cliente.query
        .filter(Cliente.codigo.like(f"{CLIENT_PREFIX}%"))
        .order_by(Cliente.id.asc())
        .all()
    )


def _find_seed_candidatas() -> list[Candidata]:
    return (
        Candidata.query
        .filter(Candidata.codigo.like(f"{CAND_PREFIX}%"))
        .order_by(Candidata.fila.asc())
        .all()
    )


def _delete_seed_data() -> dict[str, int]:
    clientes = _find_seed_clientes()
    cliente_ids = [int(c.id) for c in clientes]

    sols = []
    if cliente_ids:
        sols = Solicitud.query.filter(Solicitud.cliente_id.in_(cliente_ids)).all()
    sol_ids = [int(s.id) for s in sols]

    repl_ids = []
    if sol_ids:
        repls = Reemplazo.query.filter(Reemplazo.solicitud_id.in_(sol_ids)).all()
        repl_ids = [int(r.id) for r in repls]

    if sol_ids:
        SeguimientoCandidataCaso.query.filter(SeguimientoCandidataCaso.solicitud_id.in_(sol_ids)).delete(synchronize_session=False)
        DomainOutbox.query.filter(
            DomainOutbox.aggregate_type == "Solicitud",
            DomainOutbox.aggregate_id.in_([str(sid) for sid in sol_ids]),
            DomainOutbox.event_type.like("REEMPLAZO%"),
        ).delete(synchronize_session=False)

    if repl_ids:
        StaffAuditLog.query.filter(
            StaffAuditLog.entity_type == "Reemplazo",
            StaffAuditLog.entity_id.in_([str(rid) for rid in repl_ids]),
            StaffAuditLog.action_type.like("REEMPLAZO%"),
        ).delete(synchronize_session=False)

    if sol_ids:
        Reemplazo.query.filter(Reemplazo.solicitud_id.in_(sol_ids)).delete(synchronize_session=False)
        Solicitud.query.filter(Solicitud.id.in_(sol_ids)).delete(synchronize_session=False)

    cand_ids = [int(c.fila) for c in _find_seed_candidatas()]
    if cand_ids:
        Candidata.query.filter(Candidata.fila.in_(cand_ids)).delete(synchronize_session=False)

    if cliente_ids:
        Cliente.query.filter(Cliente.id.in_(cliente_ids)).delete(synchronize_session=False)

    db.session.commit()
    return {
        "clientes": len(cliente_ids),
        "solicitudes": len(sol_ids),
        "reemplazos": len(repl_ids),
        "candidatas": len(cand_ids),
    }


def _build_cliente(i: int, now_dt) -> Cliente:
    code = f"{CLIENT_PREFIX}{i:03d}"
    tel = f"829950{i:04d}"[:10]
    c = Cliente(
        codigo=code,
        nombre_completo=f"Cliente Demo Reemplazo {i:02d}",
        email=f"qa.reemp.{i:02d}@local.test",
        telefono=tel,
        ciudad=["Santo Domingo", "Santiago", "La Vega", "Moca"][i % 4],
        sector=["Piantini", "Gurabo", "Centro", "Villa Olga"][i % 4],
        role="cliente",
        acepto_politicas=True,
        fecha_acepto_politicas=now_dt,
        notas_admin=f"{NOTA_TAG} cliente={code}",
    )
    c.password_hash = "DISABLED_RESET_REQUIRED"
    return c


def _build_candidata(i: int, *, old: bool) -> Candidata:
    suffix = "OLD" if old else "NEW"
    n = i * 10 + (1 if old else 2)
    return Candidata(
        nombre_completo=f"Candidata {suffix} Demo {i:02d}",
        codigo=f"{CAND_PREFIX}{suffix}-{i:03d}",
        cedula=_mk_cedula(10_000_000_000 + n),
        numero_telefono=f"809770{n:04d}"[:10],
        estado="trabajando" if old else "lista_para_trabajar",
        entrevista=f"{NOTA_TAG} candidata={suffix}-{i}",
    )


def _build_solicitud(i: int, *, cliente_id: int, candidata_old_id: int, now_dt, estado: str = "reemplazo") -> Solicitud:
    modalidad = [
        "Salida diaria - lunes a viernes",
        "Con dormida - lunes a sabado",
        "Salida diaria - lunes a sabado",
        "Con dormida - lunes a viernes",
    ][i % 4]
    funciones_raw = ["limpiar", "cocinar"] if i % 2 == 0 else ["limpiar", "lavar", "planchar"]
    funciones_value = funciones_raw
    try:
        if str(db.engine.dialect.name or "").strip().lower() == "sqlite":
            funciones_value = ",".join(funciones_raw)
    except Exception:
        pass

    return Solicitud(
        cliente_id=cliente_id,
        codigo_solicitud=f"{SOL_PREFIX}{i:03d}",
        fecha_solicitud=now_dt - timedelta(days=(i % 9)),
        estado=estado,
        candidata_id=candidata_old_id,
        ciudad_sector=["Santo Domingo", "Santiago", "La Vega", "Moca"][i % 4],
        rutas_cercanas=["Lincoln", "27 de Febrero", "Estrella Sadhalá", "Duarte"][i % 4],
        modalidad_trabajo=modalidad,
        edad_requerida=["25-40", "30-45", "22-38", "28-50"][i % 4],
        experiencia=["Limpieza general y organización", "Cuidado del hogar y cocina básica", "Cocina criolla y lavado", "Planchado y mantenimiento del hogar"][i % 4],
        funciones=funciones_value,
        horario="Lunes a viernes de 8:00 AM a 5:00 PM",
        tipo_lugar=["Apartamento", "Casa", "Apartamento", "Casa"][i % 4],
        habitaciones=[2, 3, 2, 4][i % 4],
        banos=[1, 2, 1.5, 3][i % 4],
        adultos=[2, 3, 2, 4][i % 4],
        ninos=[0, 1, 2, 1][i % 4],
        edades_ninos=["", "8", "4 y 9", "6"][i % 4],
        sueldo=["RD$18,000", "RD$20,000", "RD$22,000", "RD$24,000"][i % 4],
        nota_cliente=f"{NOTA_TAG} solicitud={i:03d}",
        estado_actual_desde=now_dt - timedelta(days=max(1, i % 7)),
    )


def _add_audit_and_outbox(*, actor_id: int, solicitud_id: int, reemplazo_id: int, action: str, now_dt) -> None:
    db.session.add(
        StaffAuditLog(
            actor_user_id=actor_id,
            actor_role="admin",
            action_type=action,
            entity_type="Reemplazo",
            entity_id=str(reemplazo_id),
            summary=f"{NOTA_TAG} {action} reemplazo={reemplazo_id}",
            metadata_json={"seed": True, "solicitud_id": solicitud_id, "reemplazo_id": reemplazo_id},
            success=True,
            created_at=now_dt,
        )
    )
    db.session.add(
        DomainOutbox(
            event_id=uuid.uuid4().hex,
            event_type=action,
            aggregate_type="Solicitud",
            aggregate_id=str(solicitud_id),
            occurred_at=now_dt,
            payload={"seed": True, "solicitud_id": solicitud_id, "reemplazo_id": reemplazo_id},
            relay_status="pending",
        )
    )


def run_seed(*, reset: bool = False) -> SeedSummary:
    db_url = _assert_local_safety()

    existing = _find_seed_clientes()
    if existing and not reset:
        sol_ids = [int(s.id) for s in Solicitud.query.filter(Solicitud.cliente_id.in_([int(c.id) for c in existing])).all()]
        return SeedSummary(
            created_clientes=0,
            created_solicitudes=0,
            created_reemplazos=0,
            skipped_existing=True,
            db_url=db_url,
        )

    if reset:
        _delete_seed_data()

    now_dt = utc_now_naive()
    actor_id = _seed_staff_user()

    created_clientes = 0
    created_solicitudes = 0
    created_reemplazos = 0

    clientes: list[Cliente] = []
    for i in range(1, 13):
        c = _build_cliente(i, now_dt)
        db.session.add(c)
        clientes.append(c)
    db.session.flush()
    created_clientes += len(clientes)

    # Cliente 11 tendrá múltiples reemplazos históricos; cliente 12 activo + cerrado.
    scenarios = [
        {"idx": 1, "dias": 0, "kind": "activo_sin_new", "label": "1_nuevo_hoy", "prio": "media"},
        {"idx": 2, "dias": 1, "kind": "activo_sin_new", "label": "2_abierto_1_dia", "prio": "media"},
        {"idx": 3, "dias": 3, "kind": "activo_sin_new", "label": "3_abierto_3_dias", "prio": "alta"},
        {"idx": 4, "dias": 8, "kind": "activo_sin_new", "label": "4_abierto_8_dias", "prio": "urgente"},
        {"idx": 5, "dias": 15, "kind": "activo_sin_new", "label": "5_critico_14_mas", "prio": "critica"},
        {"idx": 6, "dias": 5, "kind": "activo_sin_new", "label": "6_sin_candidata_nueva", "prio": "alta"},
        {"idx": 7, "dias": 2, "kind": "activo_con_new", "label": "7_con_new_en_coordinacion", "prio": "alta"},
        {"idx": 8, "dias": 6, "kind": "cerrado_exitoso", "label": "8_cerrado_exitoso", "prio": "alta"},
        {"idx": 9, "dias": 10, "kind": "cerrado_fallido", "label": "9_cerrado_fallido", "prio": "urgente"},
        {"idx": 10, "dias": 4, "kind": "cancelado", "label": "10_cancelado", "prio": "alta"},
        {"idx": 11, "dias": 12, "kind": "historico_multi", "label": "11_varios_historicos", "prio": "urgente"},
        {"idx": 12, "dias": 2, "kind": "activo_y_cerrado", "label": "12_activo_y_cerrado", "prio": "alta"},
    ]

    for sc in scenarios:
        idx = int(sc["idx"])
        cliente = clientes[idx - 1]

        old_cand = _build_candidata(idx, old=True)
        new_cand = _build_candidata(idx, old=False)
        db.session.add_all([old_cand, new_cand])
        db.session.flush()

        solicitud_estado = "reemplazo"
        if sc["kind"] in {"cerrado_exitoso", "cerrado_fallido", "cancelado"}:
            solicitud_estado = "activa"

        sol = _build_solicitud(
            idx,
            cliente_id=int(cliente.id),
            candidata_old_id=int(old_cand.fila),
            now_dt=now_dt,
            estado=solicitud_estado,
        )
        db.session.add(sol)
        db.session.flush()
        created_solicitudes += 1

        inicio = now_dt - timedelta(days=int(sc["dias"]))
        motivo = {
            "cerrado_fallido": "La candidata entrante no superó el período de prueba",
            "cancelado": "Cliente decidió pausar el servicio temporalmente",
        }.get(sc["kind"], "La candidata saliente no se presentó al horario acordado")

        repl = Reemplazo(
            solicitud_id=int(sol.id),
            candidata_old_id=int(old_cand.fila),
            candidata_new_id=(int(new_cand.fila) if sc["kind"] in {"activo_con_new", "cerrado_exitoso"} else None),
            motivo_fallo=motivo,
            oportunidad_nueva=sc["kind"] not in {"cerrado_exitoso", "cerrado_fallido", "cancelado"},
            fecha_fallo=inicio - timedelta(hours=3),
            fecha_inicio_reemplazo=inicio,
            nota_adicional=f"{NOTA_TAG} case={sc['label']}",
            motivo_reemplazo_code="inasistencia" if "presentó" in motivo else "decision_cliente",
            prioridad=str(sc["prio"]),
            responsable_id=actor_id,
            fecha_reporte=inicio,
            estado_previo_solicitud="activa",
        )

        if sc["kind"] == "cerrado_exitoso":
            repl.fecha_fin_reemplazo = inicio + timedelta(days=2)
            repl.resultado_final = "cerrado_exitoso"
            repl.fecha_resolucion = repl.fecha_fin_reemplazo
        elif sc["kind"] == "cerrado_fallido":
            repl.fecha_fin_reemplazo = inicio + timedelta(days=3)
            repl.resultado_final = "cerrado_fallido"
            repl.fecha_resolucion = repl.fecha_fin_reemplazo
        elif sc["kind"] == "cancelado":
            repl.fecha_fin_reemplazo = inicio + timedelta(days=1)
            repl.resultado_final = "cancelado"
            repl.fecha_resolucion = repl.fecha_fin_reemplazo
        db.session.add(repl)
        db.session.flush()
        created_reemplazos += 1

        if sc["kind"] == "activo_sin_new" and int(sc["dias"]) >= 8:
            seg = SeguimientoCandidataCaso(
                public_id=f"QA-REEMP-CASE-{idx:03d}",
                candidata_id=int(old_cand.fila),
                solicitud_id=int(sol.id),
                canal_origen="whatsapp",
                estado="en_gestion",
                prioridad="alta",
                created_by_staff_user_id=actor_id,
                owner_staff_user_id=actor_id,
                proxima_accion_tipo="contactar",
                due_at=now_dt - timedelta(days=1),
            )
            db.session.add(seg)

        action = "REEMPLAZO_ABIERTO"
        if sc["kind"] == "cerrado_exitoso":
            action = "REEMPLAZO_CERRADO_ASIGNANDO"
        elif sc["kind"] == "cerrado_fallido":
            action = "REEMPLAZO_CERRAR_FALLIDO"
        elif sc["kind"] == "cancelado":
            action = "REEMPLAZO_CANCELADO"
        _add_audit_and_outbox(actor_id=actor_id, solicitud_id=int(sol.id), reemplazo_id=int(repl.id), action=action, now_dt=now_dt)

        if sc["kind"] == "historico_multi":
            # Dos históricos extra para el mismo cliente.
            for extra_idx, extra_kind in enumerate(["cerrado_exitoso", "cerrado_fallido"], start=1):
                sol_h = _build_solicitud(
                    100 + extra_idx,
                    cliente_id=int(cliente.id),
                    candidata_old_id=int(old_cand.fila),
                    now_dt=now_dt - timedelta(days=20 + extra_idx),
                    estado="activa",
                )
                db.session.add(sol_h)
                db.session.flush()
                created_solicitudes += 1

                repl_h = Reemplazo(
                    solicitud_id=int(sol_h.id),
                    candidata_old_id=int(old_cand.fila),
                    candidata_new_id=int(new_cand.fila) if extra_kind == "cerrado_exitoso" else None,
                    motivo_fallo="Rotación operativa histórica",
                    oportunidad_nueva=False,
                    fecha_fallo=now_dt - timedelta(days=30 + extra_idx),
                    fecha_inicio_reemplazo=now_dt - timedelta(days=28 + extra_idx),
                    fecha_fin_reemplazo=now_dt - timedelta(days=26 + extra_idx),
                    resultado_final="cerrado_exitoso" if extra_kind == "cerrado_exitoso" else "cerrado_fallido",
                    nota_adicional=f"{NOTA_TAG} case=11_hist_extra_{extra_idx}",
                    prioridad="media",
                    responsable_id=actor_id,
                    estado_previo_solicitud="activa",
                )
                db.session.add(repl_h)
                db.session.flush()
                created_reemplazos += 1
                _add_audit_and_outbox(
                    actor_id=actor_id,
                    solicitud_id=int(sol_h.id),
                    reemplazo_id=int(repl_h.id),
                    action="REEMPLAZO_HISTORICO",
                    now_dt=now_dt,
                )

        if sc["kind"] == "activo_y_cerrado":
            sol_c = _build_solicitud(
                200,
                cliente_id=int(cliente.id),
                candidata_old_id=int(old_cand.fila),
                now_dt=now_dt - timedelta(days=12),
                estado="activa",
            )
            db.session.add(sol_c)
            db.session.flush()
            created_solicitudes += 1

            repl_c = Reemplazo(
                solicitud_id=int(sol_c.id),
                candidata_old_id=int(old_cand.fila),
                candidata_new_id=int(new_cand.fila),
                motivo_fallo="Caso previo ya resuelto",
                oportunidad_nueva=False,
                fecha_fallo=now_dt - timedelta(days=12),
                fecha_inicio_reemplazo=now_dt - timedelta(days=11),
                fecha_fin_reemplazo=now_dt - timedelta(days=9),
                resultado_final="cerrado_exitoso",
                nota_adicional=f"{NOTA_TAG} case=12_cerrado_extra",
                prioridad="media",
                responsable_id=actor_id,
                estado_previo_solicitud="activa",
            )
            db.session.add(repl_c)
            db.session.flush()
            created_reemplazos += 1
            _add_audit_and_outbox(
                actor_id=actor_id,
                solicitud_id=int(sol_c.id),
                reemplazo_id=int(repl_c.id),
                action="REEMPLAZO_CERRADO_ASIGNANDO",
                now_dt=now_dt,
            )

    db.session.commit()

    return SeedSummary(
        created_clientes=created_clientes,
        created_solicitudes=created_solicitudes,
        created_reemplazos=created_reemplazos,
        skipped_existing=False,
        db_url=db_url,
    )


def _print_summary(summary: SeedSummary) -> None:
    print("[seed_reemplazos_demo] DB usada:", summary.db_url)
    print("[seed_reemplazos_demo] clientes_creados:", summary.created_clientes)
    print("[seed_reemplazos_demo] solicitudes_creadas:", summary.created_solicitudes)
    print("[seed_reemplazos_demo] reemplazos_creados:", summary.created_reemplazos)
    if summary.skipped_existing:
        print("[seed_reemplazos_demo] idempotencia: datos QA-REEMP existentes, no se duplicó")
    print("http://127.0.0.1:5001/admin/reemplazos")
    print("http://127.0.0.1:5001/admin/clientes")
    print("http://127.0.0.1:5001/admin/reemplazos/nuevo")


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed local QA para panel de reemplazos")
    parser.add_argument("--reset", action="store_true", help="Borra solo datos QA-REEMP antes de crear")
    args = parser.parse_args()

    with flask_app.app_context():
        summary = run_seed(reset=bool(args.reset))
        _print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
