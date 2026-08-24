# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import os
import uuid
from copy import deepcopy
from unittest.mock import patch

import pytest
from sqlalchemy import delete

from app import app as flask_app
from config_app import db
from clientes.routes import _public_link_token_hash_storage
from models import Cliente, DomainOutbox, PublicSolicitudClienteNuevoTokenUso, Solicitud
from tests.t1_testkit import ensure_sqlite_compat_tables


COUNT = 100


def _fingerprint(payload: dict, *, exclude_identity: bool = False) -> str:
    drop = {
        "terms_accepted_at",
    }
    if exclude_identity:
        drop.update({
            "nombre_completo",
            "email_contacto",
            "telefono_contacto",
            "token",
        })
    clean = {}
    for key, value in payload.items():
        if key in drop:
            continue
        if isinstance(value, list):
            clean[key] = list(value)
        else:
            clean[key] = value
    raw = json.dumps(clean, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _expected_horario(payload: dict) -> str:
    if payload["modalidad_grupo"] == "con_dormida":
        return f"Entrada: {payload['horario_dormida_entrada']} / Salida: {payload['horario_dormida_salida']}"
    dias = payload["horario_dias_trabajo"]
    entrada = payload["horario_hora_entrada"]
    salida = payload["horario_hora_salida"]
    if dias.lower() in {"lunes a sábado", "lunes a sabado"}:
        return f"Lunes a sábado de {entrada} a {salida}"
    return f"{dias}, de {entrada} a {salida}"


def _case_payload(i: int, *, run_id: str = "bulk") -> dict:
    ciudades = ["Santiago", "Moca", "La Vega", "Puerto Plata", "Bonao", "San Francisco", "San José de las Matas"]
    sectores = ["Los Jardines", "Centro", "Gurabo", "Villa Olga", "El Dorado", "Reparto Peralta", "La Piña"]
    rutas = ["Ruta K", "Av. Estrella Sadhala", "Ruta M", "Av. 27 Febrero", "Carretera principal", "Calle Ñico Lora / Los Próceres"]
    modalidad_cases = [
        ("con_salida_diaria", "Salida diaria - lunes a viernes", "Lunes a viernes", "8:00 AM", "5:00 PM", "", ""),
        ("con_salida_diaria", "Salida diaria - lunes a sábado", "Lunes a sábado", "7:30 AM", "4:30 PM", "", ""),
        ("con_salida_diaria", "Salida diaria - 3 días a la semana", "Lunes, miercoles y viernes", "9:00 AM", "3:00 PM", "", ""),
        ("con_salida_diaria", "Salida diaria - fin de semana", "Sabado y domingo", "8:00 AM", "2:00 PM", "", ""),
        ("con_dormida", "Con dormida 💤 lunes a viernes", "", "", "", "Lunes 8:00 AM", "Viernes 5:00 PM"),
        ("con_dormida", "Con dormida 💤 lunes a sábado", "", "", "", "Lunes 7:00 AM", "Sabado 12:00 PM"),
        ("con_dormida", "Con dormida 💤 quincenal", "", "", "", "Lunes 8:00 AM", "Viernes quincenal 12:00 PM"),
    ]
    tipo_lugar_cases = [
        ("casa", ""),
        ("apto", ""),
        ("oficina", ""),
        ("otro", "Villa familiar"),
    ]
    function_cases = [
        (["limpieza"], ""),
        (["limpieza", "cocinar"], ""),
        (["limpieza", "lavar", "planchar"], ""),
        (["limpieza", "ninos"], ""),
        (["limpieza", "ninos", "cocinar"], ""),
        (["limpieza", "envejeciente"], ""),
        (["limpieza", "envejeciente", "lavar"], ""),
        (["limpieza", "otro"], "Organizar despensa"),
        (["ninos"], ""),
        (["envejeciente"], ""),
    ]
    areas_cases = [
        (["sala"], ""),
        (["sala", "comedor"], ""),
        (["cocina", "patio"], ""),
        (["terraza", "jardin"], ""),
        (["todas_anteriores"], ""),
        (["otro"], "Gazebo"),
    ]
    edad_cases = [
        (["18-25"], ""),
        (["26-35"], ""),
        (["25 en adelante"], ""),
        (["Mayor de 45"], ""),
        (["otro"], "30 a 42"),
    ]
    mascotas = ["", "Perro pequeno", "Gato", "Perro y gato", "Ave domestica"]

    modalidad = modalidad_cases[i % len(modalidad_cases)]
    tipo_lugar, tipo_lugar_otro = tipo_lugar_cases[i % len(tipo_lugar_cases)]
    funciones, funciones_otro = function_cases[i % len(function_cases)]
    areas, area_otro = areas_cases[i % len(areas_cases)]
    edad, edad_otro = edad_cases[i % len(edad_cases)]
    has_limpieza = "limpieza" in funciones
    household = bool({"limpieza", "cocinar", "lavar", "planchar"}.intersection(funciones))
    ninos = (i % 4) + 1 if "ninos" in funciones else (i % 3 if i % 11 == 0 else 0)
    edades_ninos = {
        0: "",
        1: "4 anos",
        2: "3 y 8 anos",
        3: "2, 6 y 10 anos",
        4: "1, 5, 7 y 12 anos",
    }[ninos]
    sueldo = 14000 + ((i * 750) % 28000)
    nota_base = (
        f"Observacion caso {i:03d}: prioridad en puntualidad, referencias y comunicacion clara. "
        "Validar detalle antes de coordinar entrevista."
    )

    return {
        "terms_decision": "accept",
        "terms_accepted": "1",
        "terms_accepted_at": f"2026-08-18T12:{i % 60:02d}:00Z",
        "nombre_completo": f"Cliente Bulk {run_id} {i:03d}",
        "email_contacto": f"bulk-{run_id}-{i:03d}@example.com",
        "telefono_contacto": f"809-55{i % 10}-{i:04d}",
        "ciudad_cliente": ciudades[i % len(ciudades)],
        "sector_cliente": sectores[(i * 2) % len(sectores)],
        "ciudad_sector": f"{ciudades[(i + 2) % len(ciudades)]} / {sectores[(i + 3) % len(sectores)]}",
        "rutas_cercanas": rutas[(i * 3) % len(rutas)],
        "modalidad_grupo": modalidad[0],
        "modalidad_especifica": modalidad[1],
        "modalidad_trabajo": modalidad[1],
        "horario": "Se reemplaza por horario guiado",
        "horario_dias_trabajo": modalidad[2],
        "horario_hora_entrada": modalidad[3],
        "horario_hora_salida": modalidad[4],
        "horario_dormida_entrada": modalidad[5],
        "horario_dormida_salida": modalidad[6],
        "edad_requerida": edad,
        "edad_otro": edad_otro,
        "experiencia": f"Experiencia comprobable en {', '.join(funciones)} para caso {i:03d}; manejo de área común, niños y comunicación con doña María.",
        "funciones": funciones,
        "funciones_otro": funciones_otro,
        "envejeciente_tipo_cuidado": "encamado" if "envejeciente" in funciones and i % 2 else ("independiente" if "envejeciente" in funciones else ""),
        "envejeciente_responsabilidades": ["pampers", "higiene"] if "envejeciente" in funciones and i % 2 else [],
        "envejeciente_solo_acompanamiento": "" if "envejeciente" in funciones and i % 2 else ("y" if "envejeciente" in funciones else ""),
        "envejeciente_nota": f"Nota envejeciente {i:03d}" if "envejeciente" in funciones else "",
        "tipo_lugar": tipo_lugar,
        "tipo_lugar_otro": tipo_lugar_otro,
        "habitaciones": str((i % 6) + 1 if has_limpieza else 0),
        "banos": ["1", "1.5", "2", "2.5", "3", "4"][i % 6] if has_limpieza else "0",
        "pisos_selector": ["1", "2", "3+"][i % 3],
        "dos_pisos": "y" if i % 3 else "",
        "areas_comunes": areas,
        "area_otro": area_otro,
        "adultos": str((i % 5) + 1) if household else "",
        "ninos": str(ninos),
        "edades_ninos": edades_ninos,
        "ayuda_cuidado_ninos": "con_ayuda" if ninos and i % 2 else ("sin_ayuda" if ninos else ""),
        "mascota": mascotas[(i * 2) % len(mascotas)],
        "sueldo": f"{sueldo:,}",
        "pasaje_mode": ["incluido", "aparte", "otro"][i % 3],
        "pasaje_otro_text": "Pasaje semanal coordinado" if i % 3 == 2 else "",
        "nota_cliente": f"{nota_base} Sector con acentos: Piantini/La Zurza, señor Núñez." if i % 5 else "",
        "lead_source": ["instagram", "facebook", "tiktok", "google", "direct"][i % 5],
    }


def _expected_funciones(payload: dict) -> list[str]:
    funciones = [v for v in payload["funciones"] if v != "otro"]
    if "otro" in payload["funciones"] and payload["funciones_otro"]:
        funciones.append(payload["funciones_otro"])
    return funciones


def _expected_areas(payload: dict) -> list[str]:
    if "limpieza" not in payload["funciones"]:
        return []
    if payload["areas_comunes"] == ["todas_anteriores"]:
        return [
            "sala", "comedor", "cocina", "salon_juegos", "terraza", "jardin",
            "estudio", "patio", "piscina", "marquesina",
        ]
    return [v for v in payload["areas_comunes"] if v != "todas_anteriores"]


def _expected_edad(payload: dict) -> list[str]:
    if payload["edad_requerida"] == ["otro"]:
        return [payload["edad_otro"]]
    return list(payload["edad_requerida"])


def _assert_postgresql_local_test_database() -> dict:
    assert os.environ.get("APP_ENV") == "test"
    assert os.environ.get("APP_WEB_ALLOW_LOCAL_POSTGRESQL_TESTS") == "1"
    with flask_app.app_context():
        engine_url = db.engine.url
        dialect = db.engine.dialect.name
        host = engine_url.host or ""
        database = engine_url.database or ""
        username = engine_url.username or ""
        port = engine_url.port or 5432
        rendered_url = engine_url.render_as_string(hide_password=True)
    assert dialect == "postgresql"
    assert host in {"localhost", "127.0.0.1", "::1"}
    assert int(port) == 5432
    assert any(marker in database.lower() for marker in ("test", "pytest", "pgtest"))
    assert "render.com" not in rendered_url.lower()
    assert "supabase" not in rendered_url.lower()
    assert "railway" not in rendered_url.lower()
    production_url = os.environ.get("DATABASE_URL") or ""
    selected_url = os.environ.get("DATABASE_URL_TEST") or ""
    assert selected_url and selected_url != production_url
    return {
        "dialect": dialect,
        "host": host,
        "port": port,
        "database": database,
        "username": username,
        "url": rendered_url,
    }


def _database_report() -> dict:
    with flask_app.app_context():
        engine_url = db.engine.url
        return {
            "dialect": db.engine.dialect.name,
            "host": engine_url.host or "",
            "port": engine_url.port or "",
            "database": engine_url.database or "",
            "username": engine_url.username or "",
            "url": engine_url.render_as_string(hide_password=True),
        }


def _cleanup_created_rows(created_ids: list[int], run_id: str) -> None:
    emails = [f"bulk-{run_id}-{i:03d}@example.com" for i in range(COUNT)]
    token_hashes = [
        _public_link_token_hash_storage(f"bulk-token-{run_id}-{i:03d}")
        for i in range(COUNT)
    ]
    with flask_app.app_context():
        client_ids = [
            row[0]
            for row in db.session.query(Cliente.id)
            .filter(Cliente.email_norm.in_(emails))
            .all()
        ]
        db.session.execute(
            delete(PublicSolicitudClienteNuevoTokenUso).where(
                PublicSolicitudClienteNuevoTokenUso.token_hash.in_(token_hashes)
            )
        )
        if created_ids:
            db.session.execute(delete(Solicitud).where(Solicitud.id.in_(created_ids)))
        if client_ids:
            db.session.execute(delete(Cliente).where(Cliente.id.in_(client_ids)))
        db.session.commit()
        db.session.remove()


def _validate_persisted_rows(created_ids: list[int], payloads: list[dict], start_count: int, *, expect_count_delta: bool) -> None:
    with flask_app.app_context():
        db.session.remove()
        rows = {s.id: s for s in Solicitud.query.filter(Solicitud.id.in_(created_ids)).all()}
        assert len(rows) == COUNT
        if expect_count_delta:
            assert Solicitud.query.count() - start_count == COUNT

        for solicitud_id, payload in zip(created_ids, payloads):
            row = rows[solicitud_id]
            assert row.atractivo_score is None
            assert row.atractivo_label is None
            assert row.atractivo_motivos is None
            assert row.atractivo_version is None
            assert row.atractivo_calculated_at is None
            assert row.public_form_source == "cliente_nuevo"
            assert row.review_status == "nuevo"
            assert row.terms_accepted is True
            assert row.lead_source == payload["lead_source"]
            assert row.ciudad_sector == payload["ciudad_sector"]
            assert row.rutas_cercanas == (payload["rutas_cercanas"] or None)
            assert row.modalidad_trabajo == payload["modalidad_trabajo"]
            assert row.horario == _expected_horario(payload)
            assert row.edad_requerida == _expected_edad(payload)
            assert row.experiencia == payload["experiencia"]
            assert row.funciones == _expected_funciones(payload)
            assert row.funciones_otro == (payload["funciones_otro"] or None)
            assert row.sueldo == payload["sueldo"].replace(",", "")
            assert row.ninos == int(payload["ninos"])
            assert row.edades_ninos == payload["edades_ninos"]
            assert row.mascota == (payload["mascota"] or None)

            if "limpieza" in payload["funciones"]:
                expected_tipo = payload["tipo_lugar_otro"] if payload["tipo_lugar"] == "otro" else payload["tipo_lugar"]
                assert row.tipo_lugar == expected_tipo
                assert row.habitaciones == int(payload["habitaciones"])
                assert row.banos == float(payload["banos"])
                assert row.areas_comunes == _expected_areas(payload)
                assert row.area_otro == payload["area_otro"]
                assert row.dos_pisos is (payload["pisos_selector"] in {"2", "3+"})
            else:
                assert row.tipo_lugar is None
                assert row.habitaciones is None
                assert row.banos is None
                assert row.areas_comunes == []
                assert row.area_otro in ("", None)
                assert row.dos_pisos is False

            if {"limpieza", "cocinar", "lavar", "planchar"}.intersection(payload["funciones"]):
                assert row.adultos == int(payload["adultos"])
            else:
                assert row.adultos is None

            detalles = row.detalles_servicio or {}
            if payload["ayuda_cuidado_ninos"]:
                assert detalles.get("ayuda_cuidado_ninos") == payload["ayuda_cuidado_ninos"]
            if payload["pasaje_mode"] == "aparte":
                assert row.pasaje_aporte is True
            else:
                assert row.pasaje_aporte is False
            if payload["pasaje_mode"] == "otro":
                assert detalles.get("pasaje") == {
                    "mode": "otro",
                    "text": payload["pasaje_otro_text"],
                }
            else:
                assert detalles.get("pasaje") == {"mode": payload["pasaje_mode"]}

            if "envejeciente" in payload["funciones"]:
                assert row.envejeciente_tipo_cuidado == payload["envejeciente_tipo_cuidado"]
                if payload["envejeciente_tipo_cuidado"] == "encamado":
                    assert row.envejeciente_responsabilidades == payload["envejeciente_responsabilidades"]
                    assert row.envejeciente_solo_acompanamiento is False
                else:
                    assert row.envejeciente_responsabilidades is None
                    assert row.envejeciente_solo_acompanamiento is False
                assert row.envejeciente_nota == payload["envejeciente_nota"]
            else:
                assert row.envejeciente_tipo_cuidado is None
                assert row.envejeciente_responsabilidades is None
                assert row.envejeciente_solo_acompanamiento is False
                assert row.envejeciente_nota is None

            cliente = Cliente.query.filter_by(id=row.cliente_id).first()
            assert cliente is not None
            assert cliente.nombre_completo == payload["nombre_completo"]
            assert cliente.email_norm == payload["email_contacto"]
            assert cliente.ciudad == payload["ciudad_cliente"]
            assert cliente.sector == payload["sector_cliente"]


def _run_bulk_roundtrip(*, run_id: str, expect_count_delta: bool = True) -> dict:
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    flask_app.config["ENABLE_ATTRACTIVENESS_SCORE"] = False

    with flask_app.app_context():
        if db.engine.dialect.name == "sqlite":
            ensure_sqlite_compat_tables([Cliente, Solicitud, PublicSolicitudClienteNuevoTokenUso, DomainOutbox], reset=True)
        start_count = Solicitud.query.count()

    payloads = [_case_payload(i, run_id=run_id) for i in range(COUNT)]
    payload_fingerprints = [_fingerprint(p) for p in payloads]
    semantic_fingerprints = [_fingerprint(p, exclude_identity=True) for p in payloads]
    assert len(set(payload_fingerprints)) == COUNT
    assert len(set(semantic_fingerprints)) == COUNT

    client = flask_app.test_client()
    created_ids: list[int] = []
    failures: list[tuple[int, int, str]] = []

    try:
        with patch("clientes.routes._resolve_public_new_link_token", return_value=(True, "", {})), \
             patch("clientes.routes.enforce_business_limit", return_value=(False, None)), \
             patch("clientes.routes.enforce_min_human_interval", return_value=(False, None)), \
             patch("clientes.routes._trigger_recommendation_generation_safe", return_value=None):
            for i, original_payload in enumerate(payloads):
                token = f"bulk-token-{run_id}-{i:03d}"
                post_payload = deepcopy(original_payload)
                resp = client.post(
                    f"/clientes/solicitudes/nueva-publica/{token}",
                    data=post_payload,
                    follow_redirects=False,
                )
                if resp.status_code not in (302, 303):
                    failures.append((i, resp.status_code, resp.get_data(as_text=True)[:500]))
                    continue
                token_hash = _public_link_token_hash_storage(token)
                with flask_app.app_context():
                    db.session.remove()
                    usage = PublicSolicitudClienteNuevoTokenUso.query.filter_by(token_hash=token_hash).first()
                    assert usage is not None
                    created_ids.append(int(usage.solicitud_id))

        assert failures == []
        assert len(created_ids) == COUNT
        assert len(set(created_ids)) == COUNT
        _validate_persisted_rows(created_ids, payloads, start_count, expect_count_delta=expect_count_delta)
        return {
            "attempted": COUNT,
            "saved": len(created_ids),
            "failed": len(failures),
            "full_unique": len(set(payload_fingerprints)),
            "semantic_unique": len(set(semantic_fingerprints)),
            "duplicates": COUNT - len(set(payload_fingerprints)),
            "database": _database_report(),
        }
    finally:
        with flask_app.app_context():
            is_postgresql = db.engine.dialect.name == "postgresql"
        if is_postgresql:
            _cleanup_created_rows(created_ids, run_id)


def test_public_new_request_bulk_100_unique_payloads_persist_and_roundtrip():
    assert os.environ.get("APP_ENV") == "test"
    assert flask_app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:////")
    assert "app_web_pytest_" in flask_app.config["SQLALCHEMY_DATABASE_URI"]
    _run_bulk_roundtrip(run_id="sqlite")


@pytest.mark.postgresql
def test_public_new_request_bulk_100_unique_payloads_persist_and_roundtrip_postgresql():
    if not os.environ.get("APP_WEB_PYTEST_POSTGRESQL_DATABASE_URL"):
        pytest.skip("requiere APP_WEB_PYTEST_POSTGRESQL_DATABASE_URL apuntando a PostgreSQL local de test")
    db_info = _assert_postgresql_local_test_database()
    result = _run_bulk_roundtrip(run_id=f"pg{uuid.uuid4().hex[:12]}", expect_count_delta=False)
    assert result["attempted"] == COUNT
    assert result["saved"] == COUNT
    assert result["failed"] == 0
    assert result["full_unique"] == COUNT
    assert result["semantic_unique"] == COUNT
    assert result["duplicates"] == 0
    print(
        "\n"
        f"database dialect: {db_info['dialect']}\n"
        f"database host: {db_info['host']}\n"
        f"database port: {db_info['port']}\n"
        f"database name: {db_info['database']}\n"
        f"database user: {db_info['username']}\n"
        "production database used: NO\n"
        f"attempted: {result['attempted']}\n"
        f"saved: {result['saved']}\n"
        f"failed: {result['failed']}\n"
        f"full unique payloads: {result['full_unique']}\n"
        f"semantic unique payloads: {result['semantic_unique']}\n"
        f"duplicates: {result['duplicates']}\n"
    )
