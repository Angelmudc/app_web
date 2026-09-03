# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import re
from datetime import timedelta
from decimal import Decimal
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from typing import Optional

from flask import session, url_for
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from app import app as flask_app
from config_app import db
from models import (
    Candidata,
    CandidataWeb,
    Cliente,
    DomainOutbox,
    Entrevista,
    EntrevistaPregunta,
    EntrevistaReferencia,
    EntrevistaRespuesta,
    LlamadaCandidata,
    SeguimientoCandidataCaso,
    SeguimientoCandidataEvento,
    Solicitud,
    SolicitudCandidata,
    StaffAuditLog,
    StaffUser,
)
from admin.routes import _candidata_history_action_label, _candidata_history_datetime, _candidata_history_items
from tests.t1_testkit import ensure_sqlite_compat_tables
from utils.timezone import utc_now_naive


def _login(client, usuario: str = "Karla", clave: str = "9989"):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


@contextmanager
def _feature_flag(name: str, value: bool):
    config_key = f"FEATURE_{name.upper()}"
    old_flags = dict(flask_app.config.get("FEATURE_FLAGS") or {})
    old_value = flask_app.config.get(config_key)
    new_flags = dict(old_flags)
    new_flags[name] = bool(value)
    flask_app.config["FEATURE_FLAGS"] = new_flags
    flask_app.config[config_key] = bool(value)
    try:
        yield
    finally:
        flask_app.config["FEATURE_FLAGS"] = old_flags
        flask_app.config[config_key] = old_value


def _ensure_tables() -> None:
    ensure_sqlite_compat_tables(
        [
            Candidata,
            Entrevista,
            EntrevistaPregunta,
            EntrevistaReferencia,
            EntrevistaRespuesta,
            LlamadaCandidata,
            SeguimientoCandidataCaso,
            SeguimientoCandidataEvento,
            CandidataWeb,
            Solicitud,
            SolicitudCandidata,
            StaffAuditLog,
            StaffUser,
            DomainOutbox,
        ],
        reset=False,
    )


def _seed_center_candidate(fila: int = 990501) -> Candidata:
    now = utc_now_naive()
    StaffAuditLog.query.filter_by(entity_type="candidata", entity_id=str(fila)).delete(synchronize_session=False)
    StaffAuditLog.query.filter_by(entity_type="Candidata", entity_id=str(fila)).delete(synchronize_session=False)
    DomainOutbox.query.filter_by(aggregate_type="Candidata", aggregate_id=fila).delete(synchronize_session=False)
    SolicitudCandidata.query.filter_by(candidata_id=fila).delete(synchronize_session=False)
    Solicitud.query.filter_by(id=fila).delete(synchronize_session=False)
    CandidataWeb.query.filter_by(candidata_id=fila).delete(synchronize_session=False)
    seguimiento_ids = [row.id for row in SeguimientoCandidataCaso.query.filter_by(candidata_id=fila).all()]
    if seguimiento_ids:
        SeguimientoCandidataEvento.query.filter(SeguimientoCandidataEvento.caso_id.in_(seguimiento_ids)).delete(synchronize_session=False)
    SeguimientoCandidataCaso.query.filter_by(candidata_id=fila).delete(synchronize_session=False)
    LlamadaCandidata.query.filter_by(candidata_id=fila).delete(synchronize_session=False)
    entrevistas_ids = [row.id for row in Entrevista.query.filter_by(candidata_id=fila).all()]
    if entrevistas_ids:
        EntrevistaReferencia.query.filter(EntrevistaReferencia.entrevista_id.in_(entrevistas_ids)).delete(synchronize_session=False)
        EntrevistaRespuesta.query.filter(EntrevistaRespuesta.entrevista_id.in_(entrevistas_ids)).delete(synchronize_session=False)
    Entrevista.query.filter_by(candidata_id=fila).delete(synchronize_session=False)
    Candidata.query.filter_by(fila=fila).delete(synchronize_session=False)
    EntrevistaPregunta.query.filter(EntrevistaPregunta.clave.like("domestica.%")).delete(synchronize_session=False)
    EntrevistaPregunta.query.filter(EntrevistaPregunta.clave.like("enfermera.%")).delete(synchronize_session=False)
    EntrevistaPregunta.query.filter(EntrevistaPregunta.clave.like("empleo_general.%")).delete(synchronize_session=False)
    db.session.flush()

    cand = Candidata(
        fila=fila,
        nombre_completo="Ana Centro Operativo",
        edad="34",
        numero_telefono="809-555-0101",
        direccion_completa="Santiago",
        modalidad_trabajo_preferida="Con dormida",
        rutas_cercanas="Ruta A",
        empleo_anterior="Casa de familia",
        anos_experiencia="5",
        areas_experiencia="Limpieza, cocina",
        sabe_planchar=True,
        trabaja_con_ninos=True,
        trabaja_con_mascotas=False,
        puede_dormir_fuera=True,
        sueldo_esperado="25000",
        motivacion_trabajo="Busca estabilidad",
        disponibilidad_inicio="Inmediata",
        origen_registro="interno",
        creado_por_staff="Karla",
        creado_desde_ruta="/registro-interno",
        acepta_porcentaje_sueldo=True,
        cedula=f"402-{fila}0-{fila % 10}",
        codigo="CTR-990501",
        medio_inscripcion="WhatsApp",
        inscripcion=True,
        monto=1500,
        fecha=now.date(),
        inicio=now.date(),
        monto_total=3000,
        porciento=10,
        calificacion="Excelente",
        estado="lista_para_trabajar",
        entrevista="Legacy: entrevista histórica completa",
        grupos_empleo=["doméstica", "niñera"],
        compat_test_candidata_json={"ritmo": "activo", "nota": "Prefiere instrucciones claras"},
        compat_test_candidata_at=now,
        compat_fortalezas=["limpieza", "niños"],
        compat_ritmo_preferido="activo",
        compat_estilo_trabajo="toma_iniciativa",
        compat_orden_detalle_nivel=4,
        compat_relacion_ninos="comoda",
        compat_limites_no_negociables=["no mascotas"],
        compat_disponibilidad_dias=["Lun", "Mar"],
        compat_disponibilidad_horario="8am-5pm",
        contactos_referencias_laborales="Patrona anterior 809-111-1111",
        referencias_familiares_detalle="Hermana 809-222-2222",
        referencias_laboral="Referencia laboral verificada 809-111-1111",
        referencias_familiares="Referencia familiar verificada 809-222-2222",
        depuracion=b"depuracion-binary-not-html",
        perfil=b"perfil-binary-not-html",
        cedula1=b"cedula-front-binary-not-html",
        cedula2=b"cedula-back-binary-not-html",
        fecha_cambio_estado=now,
    )
    cand = db.session.merge(cand)
    db.session.flush()

    db.session.add_all(
        [
            EntrevistaPregunta(clave="domestica.experiencia", texto="Experiencia doméstica", tipo="texto", activa=True, orden=1),
            EntrevistaPregunta(clave="domestica.referencia_laboral", texto="Referencia laboral mencionada", tipo="texto", activa=True, orden=2),
            EntrevistaPregunta(clave="domestica.referencia_familiar", texto="Referencia familiar mencionada", tipo="texto", activa=True, orden=3),
            EntrevistaPregunta(clave="enfermera.experiencia", texto="Experiencia enfermera", tipo="texto", activa=True, orden=2),
            EntrevistaPregunta(clave="empleo_general.experiencia", texto="Experiencia general", tipo="texto", activa=True, orden=3),
        ]
    )
    entrevista = Entrevista(candidata_id=fila, tipo="domestica", estado="completa", creada_en=now - timedelta(days=1))
    db.session.add(entrevista)
    db.session.flush()
    pregunta = EntrevistaPregunta.query.filter_by(clave="domestica.experiencia").first()
    db.session.add(
        EntrevistaRespuesta(
            entrevista_id=entrevista.id,
            pregunta_id=pregunta.id,
            respuesta="Cinco años en casa de familia.",
            creada_en=now - timedelta(days=1),
        )
    )
    for i in range(7):
        db.session.add(
            LlamadaCandidata(
                candidata_id=fila,
                agente=f"Agente {i}",
                resultado="informada",
                notas=f"Nota llamada {i}",
                fecha_llamada=now - timedelta(minutes=i),
                created_at=now - timedelta(minutes=i),
            )
        )
    cliente = Cliente.query.get(1)
    if not cliente:
        cliente = Cliente(
            id=1,
            codigo="CLI-HIST",
            nombre_completo="Cliente Historial",
            email="cliente-historial@example.test",
            telefono="809-555-0000",
        )
        db.session.add(cliente)
    else:
        cliente.nombre_completo = "Cliente Historial"
        cliente.codigo = "CLI-HIST"
    seguimiento = SeguimientoCandidataCaso(
        public_id=f"SEG-{fila}",
        candidata_id=fila,
        created_by_staff_user_id=1,
        estado="en_gestion",
        prioridad="normal",
        canal_origen="whatsapp",
        proxima_accion_tipo="llamar",
        proxima_accion_detalle="Confirmar disponibilidad",
        last_movement_at=now,
        updated_at=now,
        created_at=now,
    )
    db.session.add(seguimiento)
    db.session.flush()
    db.session.add(
        SeguimientoCandidataEvento(
            caso_id=seguimiento.id,
            event_type="note_added",
            note="Evento de seguimiento completo",
            created_at=now,
        )
    )
    db.session.add(
        CandidataWeb(
            candidata_id=fila,
            visible=True,
            estado_publico="disponible",
            es_destacada=True,
            fecha_publicacion=now,
            fecha_ultima_actualizacion=now,
        )
    )
    solicitud = Solicitud(
        id=fila,
        cliente_id=1,
        codigo_solicitud=f"SOL-{fila}",
        estado="activa",
        candidata_id=fila,
        fecha_solicitud=now,
    )
    db.session.add(solicitud)
    db.session.flush()
    db.session.add(
        SolicitudCandidata(
            solicitud_id=solicitud.id,
            candidata_id=fila,
            status="enviada",
            created_at=now,
            created_by="pytest",
        )
    )
    db.session.add(
        StaffAuditLog(
            created_at=now,
            action_type="CANDIDATA_EDIT",
            entity_type="candidata",
            entity_id=str(fila),
            actor_role="secretaria",
            summary="Actualización de prueba",
        )
    )
    db.session.commit()
    return cand


def _make_descalificada_for_reactivation(
    *,
    fila: int,
    inscripcion: bool = True,
    pago: bool = True,
    documentos: bool = True,
    entrevista: bool = True,
    referencias: bool = True,
    active_assignment: bool = False,
) -> Candidata:
    _seed_center_candidate(fila=fila)
    cand = Candidata.query.get(fila)
    SolicitudCandidata.query.filter_by(candidata_id=fila).delete(synchronize_session=False)
    Solicitud.query.filter_by(id=fila).delete(synchronize_session=False)
    cand.estado = "descalificada"
    cand.nota_descalificacion = "No cumple perfil"
    cand.inscripcion = bool(inscripcion)
    cand.monto = 1500 if pago else None
    cand.fecha = utc_now_naive().date() if pago else None
    if not documentos:
        cand.cedula2 = None
    if not referencias:
        cand.contactos_referencias_laborales = ""
        cand.referencias_laboral = ""
        cand.referencias_familiares_detalle = ""
        cand.referencias_familiares = ""
    if not entrevista:
        cand.entrevista = ""
        Entrevista.query.filter_by(candidata_id=fila).delete(synchronize_session=False)
    if active_assignment:
        solicitud = Solicitud(
            id=fila,
            cliente_id=1,
            codigo_solicitud=f"SOL-{fila}",
            estado="activa",
            candidata_id=fila,
            fecha_solicitud=utc_now_naive(),
        )
        db.session.add(solicitud)
        db.session.flush()
        db.session.add(
            SolicitudCandidata(
                solicitud_id=solicitud.id,
                candidata_id=fila,
                status="enviada",
                created_at=utc_now_naive(),
                created_by="pytest",
            )
        )
    db.session.commit()
    return cand


def test_admin_candidata_historial_mapper_humano_y_fallback_no_tecnico():
    assert _candidata_history_action_label("CANDIDATA_INSCRIPTION_EDIT") == "Actualizó la inscripción"
    assert _candidata_history_action_label("CANDIDATA_DESQUALIFY") == "Descalificó a la candidata"
    assert _candidata_history_action_label("CANDIDATA_DESQUALIFICAR") == "Descalificó a la candidata"
    assert _candidata_history_action_label("CANDIDATA_CREATE_OK") == "Registró a la candidata"

    fallback = _candidata_history_action_label("CANDIDATA_LEGACY_UNKNOWN_EVENT")
    assert fallback == "Registró actividad: legacy unknown event"
    assert "CANDIDATA_LEGACY_UNKNOWN_EVENT" not in fallback


def test_admin_candidata_historial_fecha_larga_en_espanol():
    rendered = _candidata_history_datetime(
        utc_now_naive().replace(year=2026, month=8, day=18, hour=20, minute=9, second=30, microsecond=123456)
    )
    assert rendered == "18 de agosto de 2026 · 4:09 p. m."


def test_admin_candidata_historial_deduplica_descalificacion_visual_y_muestra_motivo():
    cand = Candidata(fila=991000, codigo="STRESSLOC", nombre_completo="Maria Fernanda", nota_descalificacion="sdSDsa")
    now = utc_now_naive().replace(year=2026, month=8, day=18, hour=20, minute=9, second=0, microsecond=0)
    logs = [
        StaffAuditLog(
            id=2,
            created_at=now,
            action_type="CANDIDATA_DESQUALIFY",
            entity_type="candidata",
            entity_id="991000",
            actor_role="owner",
            summary="Candidata descalificada: Maria Fernanda",
            metadata_json={"motivo": "sdSDsa"},
            success=True,
        ),
        StaffAuditLog(
            id=1,
            created_at=now,
            action_type="CANDIDATA_DESCALIFICAR",
            entity_type="Candidata",
            entity_id="991000",
            actor_role="owner",
            summary="Candidata descalificada: Maria Fernanda",
            metadata_json={"motivo": "sdSDsa"},
            success=True,
        ),
    ]

    items = _candidata_history_items(logs, cand)

    assert len(items) == 1
    assert items[0]["action"] == "Descalificó a la candidata"
    assert items[0]["subject"] == "STRESSLOC Maria Fernanda"
    assert items[0]["actor"] == "Owner"
    assert items[0]["created_at"] == "18 de agosto de 2026 · 4:09 p. m."
    assert items[0]["detail"] == "Motivo: sdSDsa"


def test_admin_candidata_historial_render_operativo_sin_codigos_tecnicos():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    fila = 990590
    now = utc_now_naive().replace(year=2026, month=8, day=18, hour=20, minute=9, second=0, microsecond=0)

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=fila)
        cand = Candidata.query.get(fila)
        cand.codigo = "STRESSLOC"
        cand.nombre_completo = "Maria Fernanda de los Angeles Rodriguez Hernandez"
        cand.estado = "descalificada"
        cand.nota_descalificacion = "sdSDsa"
        StaffAuditLog.query.filter_by(entity_type="candidata", entity_id=str(fila)).delete(synchronize_session=False)
        StaffAuditLog.query.filter_by(entity_type="Candidata", entity_id=str(fila)).delete(synchronize_session=False)
        db.session.add_all(
            [
                StaffAuditLog(
                    created_at=now,
                    action_type="CANDIDATA_DESQUALIFY",
                    entity_type="candidata",
                    entity_id=str(fila),
                    actor_role="owner",
                    summary="Candidata descalificada: Maria Fernanda",
                    metadata_json={"motivo": "sdSDsa"},
                    success=True,
                ),
                StaffAuditLog(
                    created_at=now,
                    action_type="CANDIDATA_DESCALIFICAR",
                    entity_type="Candidata",
                    entity_id=str(fila),
                    actor_role="owner",
                    summary="Candidata descalificada: Maria Fernanda",
                    metadata_json={"motivo": "sdSDsa"},
                    success=True,
                ),
                StaffAuditLog(
                    created_at=now.replace(minute=10),
                    action_type="CANDIDATA_INSCRIPTION_EDIT",
                    entity_type="candidata",
                    entity_id=str(fila),
                    actor_role="staff",
                    summary="CANDIDATA_INSCRIPTION_EDIT",
                    metadata_json={},
                    success=True,
                ),
                StaffAuditLog(
                    created_at=now.replace(minute=11),
                    action_type="CANDIDATA_LEGACY_UNKNOWN_EVENT",
                    entity_type="candidata",
                    entity_id=str(fila),
                    actor_role="staff",
                    summary="CANDIDATA_LEGACY_UNKNOWN_EVENT",
                    metadata_json={},
                    success=True,
                ),
            ]
        )
        db.session.commit()

    assert _login(client).status_code in (302, 303)
    resp = client.get(f"/admin/candidatas/{fila}", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    history_html = html.split('id="historial"', 1)[1].split("</section>", 1)[0]

    assert history_html.count("Descalificó a la candidata") == 1
    assert "Actualizó la inscripción" in history_html
    assert "STRESSLOC Maria Fernanda de los Angeles Rodriguez Hernandez" in history_html
    assert "Por: <strong>Owner</strong>" in history_html
    assert "18 de agosto de 2026 · 4:09 p. m." in history_html
    assert "Motivo: sdSDsa" in history_html
    assert "Registró actividad: legacy unknown event" in history_html
    assert "CANDIDATA_DESQUALIFY" not in history_html
    assert "CANDIDATA_DESCALIFICAR" not in history_html
    assert "CANDIDATA_INSCRIPTION_EDIT" not in history_html
    assert "CANDIDATA_LEGACY_UNKNOWN_EVENT" not in history_html


def test_admin_candidatas_busqueda_por_nombre_telefono_cedula_codigo_y_no_encontrada():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate()

    assert _login(client).status_code in (302, 303)

    for q in ("Ana Centro", "8095550101", "40299050101", "CTR-990501"):
        resp = client.get(f"/admin/candidatas?q={q}", follow_redirects=False)
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "Ana Centro Operativo" in html
        assert "/admin/candidatas/990501" in html
        assert "CTR-990501" in html
        assert "9/9" in html or "8/9" in html or "completos" in html

    missing = client.get("/admin/candidatas?q=no-existe-zz", follow_redirects=False)
    assert missing.status_code == 200
    assert "No se encontraron candidatas para esta búsqueda." in missing.get_data(as_text=True)


def test_admin_candidata_ficha_readonly_flags_limites_legacy_y_sin_blobs():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        cand = _seed_center_candidate(fila=990502)
        cand.referencias_laboral = "Secretaría 809-333-3333"
        cand.referencias_familiares = "Familia 809-444-4444"
        db.session.commit()
        before = {
            "estado": cand.estado,
            "codigo": cand.codigo,
            "inscripcion": cand.inscripcion,
            "depuracion": cand.depuracion,
            "perfil": cand.perfil,
            "cedula1": cand.cedula1,
            "cedula2": cand.cedula2,
        }

    assert _login(client).status_code in (302, 303)
    with _feature_flag("llamadas", True):
        resp = client.get("/admin/candidatas/990502", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Ana Centro Operativo" in html
    assert "Lista para trabajar" in html
    assert "Patrona anterior" in html
    assert "Hermana" in html
    preparacion_html = html.split('aria-label="Preparación"', 1)[1].split("</section>", 1)[0]
    assert "Requisitos completos" in preparacion_html
    assert "9/9" in preparacion_html
    assert "Depuración" in html and "Disponible" in html
    assert "domestica" in html and "completa" in html
    assert "Confirmar disponibilidad" in html
    assert "Estado público" in html and "disponible" in html
    assert "Asignación activa" in html and "SOL-990502" in html
    assert "enviada" in html
    assert "Actualización de prueba" in html
    header_html = html.split("<header", 1)[1].split("</header>", 1)[0]
    assert ">Llamada<" in header_html
    assert "Buscar otra candidata" in header_html
    for redundant in (
        "Editar datos",
        ">Referencias<",
        ">Inscripción<",
        ">Entrevista<",
        ">Documentos<",
        ">Seguimiento<",
        ">Más<",
    ):
        assert redundant not in header_html
    assert "/buscar?candidata_id=990502&amp;next=/admin/candidatas/990502" in html
    assert "/admin/candidatas/990502/documentos" in html
    assert "Administrar documentos" in html
    assert "/admin/candidatas/990502/referencias-formulario" in html
    assert "/admin/candidatas/990502/referencias" in html
    assert "Editar formulario" in html
    assert "Editar referencias" in html
    assert "Editar inscripción" in html
    assert 'href="/referencias"' not in html
    assert 'href="/inscripcion"' not in html
    assert "Ver entrevistas" in html
    assert "Abrir seguimiento" in html
    assert "Nota llamada" not in html
    assert "Finalizar proceso" not in html
    assert "/finalizar_proceso?fila=990502&amp;next=/admin/candidatas/990502" not in html
    assert "/entrevistas/nueva/990502/domestica?next=/admin/candidatas/990502" in html
    assert "/entrevistas/nueva/990502/enfermera?next=/admin/candidatas/990502" in html
    assert "/entrevistas/nueva/990502/empleo_general?next=/admin/candidatas/990502" in html
    assert "depuracion-binary-not-html" not in html
    assert "perfil-binary-not-html" not in html
    assert "cedula-front-binary-not-html" not in html
    assert "cedula-back-binary-not-html" not in html

    with flask_app.app_context():
        db.session.expire_all()
        after = Candidata.query.get(990502)
        assert after.estado == before["estado"]
        assert after.codigo == before["codigo"]
        assert after.inscripcion == before["inscripcion"]
        assert after.depuracion == before["depuracion"]
        assert after.perfil == before["perfil"]
        assert after.cedula1 == before["cedula1"]
        assert after.cedula2 == before["cedula2"]


def test_admin_candidata_descalificada_muestra_entrevista_bloqueada_sin_link_activo():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990538)
        cand = Candidata.query.get(990538)
        cand.estado = "descalificada"
        cand.entrevista = ""
        Entrevista.query.filter_by(candidata_id=990538).delete(synchronize_session=False)
        active_domestica = EntrevistaPregunta.query.filter(
            EntrevistaPregunta.activa.is_(True),
            EntrevistaPregunta.clave.like("domestica.%"),
        ).count()
        db.session.commit()

    assert active_domestica > 0
    assert _login(client).status_code in (302, 303)

    detail = client.get("/admin/candidatas/990538", follow_redirects=False)
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "Total</dt><dd>0</dd>" in html
    assert "Nueva entrevista doméstica" in html
    assert "No se puede crear una entrevista mientras la candidata esté descalificada." in html
    assert "/entrevistas/nueva/990538/domestica?next=/admin/candidatas/990538" not in html

    lista = client.get("/entrevistas/candidata/990538?next=/admin/candidatas/990538", follow_redirects=False)
    assert lista.status_code == 200
    lista_html = lista.get_data(as_text=True)
    assert "+ Doméstica" in lista_html
    assert "No se puede crear una entrevista mientras la candidata esté descalificada." in lista_html
    assert "/entrevistas/nueva/990538/domestica?next=/admin/candidatas/990538" not in lista_html

    blocked = client.get("/entrevistas/nueva/990538/domestica?next=/admin/candidatas/990538", follow_redirects=False)
    assert blocked.status_code in (302, 303)
    assert "/entrevistas/candidata/990538" in (blocked.location or "")
    with flask_app.app_context():
        assert Entrevista.query.filter_by(candidata_id=990538).count() == 0


def test_admin_candidata_ficha_centraliza_informacion_operativa_completa():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990539)

    assert _login(client).status_code in (302, 303)
    with _feature_flag("compat", True), _feature_flag("candidatas_web", True):
        resp = client.get("/admin/candidatas/990539", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    for expected in (
        "Resumen y estado",
        "Información de la candidata",
        "Referencias y entrevista",
        "Documentos",
        "Inscripción y Porciento",
        "Información pública y actividad laboral",
        "Historial",
        "Información personal",
        "Información laboral",
        "Rutas",
        "Origen de registro",
        "Registrada por",
        "Disponibilidad/inicio",
        "Acepta porcentaje sueldo",
        "Empleo anterior",
        "Motivo para trabajar",
        "Resumen y estado",
        "Referencias y entrevista",
        "Referencias del formulario",
        "Laboral ✓",
        "Familiar ✓",
        "Ver completa",
        "Ver menos",
        "Cargando entrevistas recientes...",
        "Documentos",
        "Inscripción y Porciento",
        "Información pública y actividad laboral",
        "Cliente Historial",
        "CLI-HIST",
        "Historial limitado",
        "Compatibilidad",
        "Ver respuestas/resultado",
        "Grupos de empleo",
    ):
        assert expected in html

    assert "Cargando información pública..." in html
    assert "data-admin-lazy-fragment-url" in html
    assert "Leer respuestas" not in html

    assert html.index("Empleo anterior") < html.index("Ver datos secundarios")
    assert html.index("Información personal") < html.index('data-summary-section="inscription"')
    assert html.index("Referencias del formulario") < html.index('data-summary-section="inscription"')
    assert html.index("Documentos") < html.index('data-summary-section="inscription"')

    for anchor in ('href="#resumen"', 'href="#informacion"', 'href="#referencias"', 'href="#entrevistas"', 'href="#documentos"', 'href="#finanzas"', 'href="#actividad"', 'href="#historial"'):
        assert anchor in html
    assert html.count('href="#finanzas"') == 1

    assert "/admin/candidatas/990539/documentos/depuracion" in html
    assert "data-doc-upload-form" in html
    assert (
        "Arrastra aquí o haz clic para subir." in html
        or "Arrastra otro archivo para reemplazarlo." in html
    )
    assert "/admin/seguimiento-candidatas/casos/" in html
    assert "depuracion-binary-not-html" not in html
    assert "perfil-binary-not-html" not in html

    entrevistas_fragment = client.get("/admin/candidatas/990539/_entrevistas", follow_redirects=False)
    assert entrevistas_fragment.status_code == 200
    entrevistas_html = entrevistas_fragment.get_data(as_text=True)
    assert "Leer respuestas" in entrevistas_html
    assert "Editar" in entrevistas_html
    assert "Descargar PDF" in entrevistas_html
    assert "Referencias registradas durante entrevista" in entrevistas_html or "No se registraron referencias en esta entrevista." in entrevistas_html

    actividad_fragment = client.get("/admin/candidatas/990539/_actividad-publica", follow_redirects=False)
    assert actividad_fragment.status_code == 200
    actividad_html = actividad_fragment.get_data(as_text=True)
    assert "Perfil público" in actividad_html or "No existe perfil público." in actividad_html

    assert 'data-summary-section="inscription"' in html
    assert 'data-summary-section="porciento"' in html
    finanzas_block = html.split('id="finanzas"', 1)[1].split('id="actividad"', 1)[0]
    inscription_block = finanzas_block.split('data-summary-section="inscription"', 1)[1].split('data-summary-section="porciento"', 1)[0]
    porciento_block = finanzas_block.split('data-summary-section="porciento"', 1)[1].split('data-finance-panel="configurar"', 1)[0]

    for expected in (
        "Código",
        "Estado",
        "Medio de pago",
        "Pago de inscripción",
        "Fecha de inscripción",
        "Inscrita por",
    ):
        assert expected in inscription_block

    for unexpected in ("Monto total", "Saldo actual", "Estado derivado", "Porcentaje"):
        assert unexpected not in inscription_block

    for expected in ("Monto total", "Pagado", "Pendiente", "Último pago", "Estado"):
        assert expected in porciento_block
    assert "Pago de inscripción" not in porciento_block
    assert "Abrir módulo" not in porciento_block


def test_admin_candidata_ficha_separa_referencias_formulario_y_entrevista():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        cand = _seed_center_candidate(fila=990540)
        cand.contactos_referencias_laborales = "FORM-LAB-REAL"
        cand.referencias_familiares_detalle = "FORM-FAM-REAL"
        cand.referencias_laboral = "INT-LAB-REAL"
        cand.referencias_familiares = "INT-FAM-REAL"
        pregunta = EntrevistaPregunta.query.filter_by(clave="domestica.referencia_laboral").first()
        pregunta_familiar = EntrevistaPregunta.query.filter_by(clave="domestica.referencia_familiar").first()
        entrevista = Entrevista.query.filter_by(candidata_id=990540, tipo="domestica").first()
        db.session.add(
            EntrevistaRespuesta(
                entrevista_id=entrevista.id,
                pregunta_id=pregunta.id,
                respuesta="INT-REF-REAL",
                creada_en=utc_now_naive(),
            )
        )
        db.session.add(
            EntrevistaRespuesta(
                entrevista_id=entrevista.id,
                pregunta_id=pregunta_familiar.id,
                respuesta="INT-FAM-REAL-ENTREVISTA",
                creada_en=utc_now_naive(),
            )
        )
        db.session.commit()
        db.session.remove()

    assert _login(client).status_code in (302, 303)
    with flask_app.app_context():
        db.session.remove()
    resp = client.get("/admin/candidatas/990540", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Referencias y entrevista" in html
    assert "Referencias del formulario" in html
    assert "FORM-LAB-REAL" in html
    assert "FORM-FAM-REAL" in html
    assert "Referencias verificadas" in html
    assert "INT-LAB-REAL" in html
    assert "INT-FAM-REAL" in html
    assert "Referencia laboral declarada completa" not in html
    assert "Referencia familiar declarada completa" not in html
    assert "Referencia laboral de entrevista completa" not in html
    assert "Referencia familiar de entrevista completa" not in html
    assert "Ver completa" in html
    assert html.count("cand-ref-card") >= 4
    assert 'class="cand-grid cand-section-grid"' in html
    assert html.count('cand-span-6') >= 2
    entrevista_fragment = client.get("/admin/candidatas/990540/_entrevistas", follow_redirects=False)
    assert entrevista_fragment.status_code == 200
    entrevista_html = entrevista_fragment.get_data(as_text=True)
    assert "Referencias registradas durante entrevista" in entrevista_html
    assert "Información registrada por el entrevistador durante esta entrevista." in entrevista_html
    assert "Referencia laboral mencionada" in entrevista_html
    assert "INT-REF-REAL" in entrevista_html
    assert "Referencia familiar mencionada" in entrevista_html
    assert "INT-FAM-REAL-ENTREVISTA" in entrevista_html
    assert "Ver completa" in entrevista_html
    assert entrevista_html.count("cand-ref-card") >= 2

    with flask_app.app_context():
        db.session.expire_all()
        cand = Candidata.query.get(990540)
        assert cand.contactos_referencias_laborales == "FORM-LAB-REAL"
        assert cand.referencias_familiares_detalle == "FORM-FAM-REAL"
        assert cand.referencias_laboral == "INT-LAB-REAL"
        assert cand.referencias_familiares == "INT-FAM-REAL"

    resp = client.post(
        "/admin/candidatas/990540/referencias-formulario",
        data={
            "contactos_referencias_laborales": "FORM-LAB-555",
            "referencias_familiares_detalle": "FORM-FAM-666",
        },
        follow_redirects=False,
    )
    payload = resp.get_json() or {}
    assert resp.status_code == 200
    assert payload["ok"] is True

    with flask_app.app_context():
        db.session.expire_all()
        cand = Candidata.query.get(990540)
        assert cand.contactos_referencias_laborales == "FORM-LAB-555"
        assert cand.referencias_familiares_detalle == "FORM-FAM-666"
        assert cand.referencias_laboral == "INT-LAB-REAL"
        assert cand.referencias_familiares == "INT-FAM-REAL"

    resp = client.post(
        "/admin/candidatas/990540/referencias",
        data={
            "referencias_laboral": "INT-LAB-777",
            "referencias_familiares": "INT-FAM-888",
        },
        follow_redirects=False,
    )
    payload = resp.get_json() or {}
    assert resp.status_code == 200
    assert payload["ok"] is True

    with flask_app.app_context():
        db.session.expire_all()
        cand = Candidata.query.get(990540)
        assert cand.contactos_referencias_laborales == "FORM-LAB-555"
        assert cand.referencias_familiares_detalle == "FORM-FAM-666"
        assert cand.referencias_laboral == "INT-LAB-777"
        assert cand.referencias_familiares == "INT-FAM-888"


def test_admin_candidata_ficha_no_usa_referencias_formulario_como_fallback_de_entrevista():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        cand = _seed_center_candidate(fila=990541)
        assert cand.contactos_referencias_laborales == "Patrona anterior 809-111-1111"
        cand.referencias_laboral = "SECRETARIA-LAB"
        cand.referencias_familiares = "SECRETARIA-FAM"
        db.session.commit()
        db.session.remove()

    assert _login(client).status_code in (302, 303)
    with flask_app.app_context():
        db.session.remove()
    resp = client.get("/admin/candidatas/990541", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Referencias y entrevista" in html
    assert "Referencias del formulario" in html
    assert "Patrona anterior 809-111-1111" in html
    assert "Referencias verificadas" in html
    assert "SECRETARIA-LAB" in html
    assert "SECRETARIA-FAM" in html
    entrevista_fragment = client.get("/admin/candidatas/990541/_entrevistas", follow_redirects=False)
    assert entrevista_fragment.status_code == 200
    entrevista_html = entrevista_fragment.get_data(as_text=True)
    assert "No se registraron referencias en esta entrevista." in entrevista_html


def test_admin_candidata_ficha_no_usa_entrevista_como_fallback_de_formulario():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        cand = _seed_center_candidate(fila=990542)
        cand.contactos_referencias_laborales = ""
        cand.referencias_familiares_detalle = ""
        cand.referencias_laboral = ""
        cand.referencias_familiares = ""
        entrevista = Entrevista.query.filter_by(candidata_id=990542, tipo="domestica").first()
        pregunta = EntrevistaPregunta.query.filter_by(clave="domestica.referencia_laboral").first()
        db.session.add(
            EntrevistaRespuesta(
                entrevista_id=entrevista.id,
                pregunta_id=pregunta.id,
                respuesta="Referencia de entrevista: Sra. Laura 809-777-1212.",
                creada_en=utc_now_naive(),
            )
        )
        db.session.commit()
        db.session.remove()

    assert _login(client).status_code in (302, 303)
    with flask_app.app_context():
        db.session.remove()
    resp = client.get("/admin/candidatas/990542", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Referencias y entrevista" in html
    assert "Referencias del formulario" in html
    assert "No informado" in html
    assert "Referencias verificadas" in html
    entrevista_fragment = client.get("/admin/candidatas/990542/_entrevistas", follow_redirects=False)
    assert entrevista_fragment.status_code == 200
    entrevista_html = entrevista_fragment.get_data(as_text=True)
    assert "Referencias registradas durante entrevista" in entrevista_html
    assert "Sra. Laura 809-777-1212." in entrevista_html
    assert "No se registraron referencias en esta entrevista." not in entrevista_html


def test_admin_candidata_documentos_dedicados_sin_busqueda_y_sin_blobs():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        cand = _seed_center_candidate(fila=990512)
        cand.cedula2 = None
        db.session.commit()
        before_estado = cand.estado

    assert _login(client).status_code in (302, 303)
    resp = client.get("/admin/candidatas/990512/documentos", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Ana Centro Operativo" in html
    assert "CTR-990501" in html
    assert "Volver a candidata" in html
    assert "Buscar por nombre" not in html
    assert "/subir_fotos/imagen/990512/perfil" in html
    assert "/gestionar_archivos/descargar_uno?id=990512&amp;doc=perfil" in html
    assert "/subir_fotos?accion=subir&amp;fila=990512&amp;next=/admin/candidatas/990512#perfil" in html
    assert "/subir_fotos?accion=subir&amp;fila=990512&amp;next=/admin/candidatas/990512#cedula2" in html
    assert "Pendiente" in html
    assert "perfil-binary-not-html" not in html

    with flask_app.app_context():
        db.session.expire_all()
        after = Candidata.query.get(990512)
        assert after.estado == before_estado


def test_admin_candidata_documentos_no_reactiva_descalificada_ni_trabajando():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990516)
        _seed_center_candidate(fila=990517)
        Candidata.query.filter_by(fila=990516).update({"estado": "descalificada"}, synchronize_session=False)
        Candidata.query.filter_by(fila=990517).update({"estado": "trabajando"}, synchronize_session=False)
        db.session.commit()

    assert _login(client).status_code in (302, 303)
    assert client.get("/admin/candidatas/990516/documentos", follow_redirects=False).status_code == 200
    assert client.get("/admin/candidatas/990517/documentos", follow_redirects=False).status_code == 200

    with flask_app.app_context():
        db.session.expire_all()
        assert Candidata.query.get(990516).estado == "descalificada"
        assert Candidata.query.get(990517).estado == "trabajando"


def test_admin_candidata_documentos_rapidos_suben_reemplazan_y_actualizan_readiness():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        cand = _seed_center_candidate(fila=990543)
        cand.depuracion = None
        cand.referencias_laboral = "INT-LAB-READY"
        cand.referencias_familiares = "INT-FAM-READY"
        cand.estado = "inscrita"
        db.session.commit()
        db.session.remove()

    assert _login(client).status_code in (302, 303)
    resp = client.get("/admin/candidatas/990543", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "/admin/candidatas/990543/documentos/depuracion" in html
    assert "Pendiente" in html

    resp = client.post(
        "/admin/candidatas/990543/documentos/depuracion",
        data={"archivo": (io.BytesIO(b"\xFF\xD8\xFF\xE0" + b"quick-doc"), "depuracion.jpg")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    payload = resp.get_json() or {}
    assert resp.status_code == 200
    assert payload["ok"] is True
    assert payload["doc_flags"]["depuracion"] is True
    assert payload["readiness"]["flags"]["depuracion"] is True
    assert payload["readiness"]["ready"] is True

    with flask_app.app_context():
        db.session.expire_all()
        cand = Candidata.query.get(990543)
        assert cand.depuracion is not None
        assert cand.estado == "lista_para_trabajar"


def test_admin_candidata_documentos_rapidos_rechazan_archivo_invalido_y_campo_no_permitido():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        cand = _seed_center_candidate(fila=990544)
        before = cand.perfil
        db.session.commit()
        db.session.remove()

    assert _login(client).status_code in (302, 303)

    bad_resp = client.post(
        "/admin/candidatas/990544/documentos/perfil",
        data={"archivo": (io.BytesIO(b"bad-data"), "perfil.txt")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    bad_payload = bad_resp.get_json() or {}
    assert bad_resp.status_code == 400
    assert bad_payload["ok"] is False

    with flask_app.app_context():
        db.session.expire_all()
        cand = Candidata.query.get(990544)
        assert cand.perfil == before

    forbidden_resp = client.post(
        "/admin/candidatas/990544/documentos/otro",
        data={"archivo": (io.BytesIO(b"\xFF\xD8\xFF\xE0ok"), "otro.jpg")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    forbidden_payload = forbidden_resp.get_json() or {}
    assert forbidden_resp.status_code == 400
    assert forbidden_payload["ok"] is False


def test_admin_candidatas_seguridad_y_404():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    anon = flask_app.test_client()
    assert anon.get("/admin/candidatas", follow_redirects=False).status_code in (302, 303)
    assert "/admin/login" in (anon.get("/admin/candidatas", follow_redirects=False).headers.get("Location") or "")

    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)
    assert client.get("/admin/candidatas/99999999", follow_redirects=False).status_code == 404


def test_admin_candidatas_busqueda_rapida_json_busca_y_exige_staff():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    anon = flask_app.test_client()
    assert anon.get("/admin/candidatas/busqueda-rapida.json?q=Ana", follow_redirects=False).status_code in (302, 303)

    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990540)
        cand = Candidata.query.get(990540)
        cand.codigo = "CTR-990540"
        cand.cedula = "402-9905400-0"
        cand.cedula_norm_digits = "40299054000"
        db.session.commit()

    assert _login(client).status_code in (302, 303)
    for q in ("Ana Centro", "8095550101", "40299054000", "CTR-990540"):
        resp = client.get(f"/admin/candidatas/busqueda-rapida.json?q={q}", follow_redirects=False)
        payload = resp.get_json() or {}
        assert resp.status_code == 200
        assert payload["ok"] is True
        assert any(item["fila"] == 990540 for item in payload["items"])
        assert all("/admin/candidatas/" in item["detail_url"] for item in payload["items"])

    payload = client.get("/admin/candidatas/busqueda-rapida.json?q=809-555-0101", follow_redirects=False).get_json() or {}
    assert any(item["fila"] == 990540 for item in payload["items"])
    payload = client.get("/admin/candidatas/busqueda-rapida.json?q=402-9905400-0", follow_redirects=False).get_json() or {}
    assert any(item["fila"] == 990540 for item in payload["items"])
    payload = client.get("/admin/candidatas/busqueda-rapida.json?q=ana centro", follow_redirects=False).get_json() or {}
    assert any(item["fila"] == 990540 for item in payload["items"])
    payload = client.get("/admin/candidatas/busqueda-rapida.json?q=ANA CENTRO", follow_redirects=False).get_json() or {}
    assert any(item["fila"] == 990540 for item in payload["items"])

    first = (payload["items"] or [{}])[0]
    assert set(first) == {"fila", "nombre", "codigo", "edad", "telefono", "estado", "estado_label", "detail_url"}
    assert "cedula" not in first
    assert "direccion" not in first
    assert "depuracion" not in first
    assert len(payload["items"]) <= 8


def test_admin_candidatas_busqueda_nombre_con_acento_y_parcial():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990541)
        cand = Candidata.query.get(990541)
        cand.nombre_completo = "María José Operativa"
        cand.codigo = "CTR-990541"
        db.session.commit()

    assert _login(client).status_code in (302, 303)
    for q in ("María José", "Maria Jose", "jose oper", "MARIA"):
        resp = client.get(f"/admin/candidatas?q={q}", follow_redirects=False)
        assert resp.status_code == 200
        assert "María José Operativa" in resp.get_data(as_text=True)


def test_admin_candidatas_recientes_session_orden_maximo_y_sin_duplicados():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        for fila in range(990550, 990562):
            _seed_center_candidate(fila=fila)
            cand = Candidata.query.get(fila)
            cand.nombre_completo = f"Reciente {fila}"
            cand.codigo = f"REC-{fila}"
        db.session.commit()

    assert _login(client).status_code in (302, 303)
    for fila in list(range(990550, 990562)) + [990558]:
        assert client.get(f"/admin/candidatas/{fila}", follow_redirects=False).status_code == 200

    resp = client.get("/admin/candidatas/busqueda-rapida.json", follow_redirects=False)
    payload = resp.get_json() or {}
    ids = [item["fila"] for item in payload["items"]]
    assert payload["source"] == "recent"
    assert ids[0] == 990558
    assert len(ids) <= 8
    assert len(ids) == len(set(ids))
    assert 990550 not in ids

    other_client = flask_app.test_client()
    assert _login(other_client).status_code in (302, 303)
    other = other_client.get("/admin/candidatas/busqueda-rapida.json", follow_redirects=False).get_json() or {}
    assert all(item["fila"] not in ids for item in other.get("items", []))


def test_admin_candidatas_recientes_limpia_ids_invalidos():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990563)

    assert _login(client).status_code in (302, 303)
    assert client.get("/admin/candidatas/990563", follow_redirects=False).status_code == 200
    with client.session_transaction() as sess:
        recent_key = next(key for key in sess.keys() if str(key).startswith("admin_candidatas_recent_v1:"))
        sess[recent_key] = [99999999, 990563, 990563, "x"]

    payload = client.get("/admin/candidatas/busqueda-rapida.json", follow_redirects=False).get_json() or {}
    assert [item["fila"] for item in payload["items"]] == [990563]
    with client.session_transaction() as sess:
        assert sess[recent_key] == [990563]


def test_admin_candidatas_filtros_operativos_y_paginacion_preservan_contexto():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990570)
        ready = Candidata.query.get(990570)
        ready.nombre_completo = "Filtro Lista"
        ready.codigo = "FLT-LISTA"
        _seed_center_candidate(fila=990571)
        working = Candidata.query.get(990571)
        working.nombre_completo = "Filtro Trabajando"
        working.codigo = "FLT-TRAB"
        working.estado = "trabajando"
        _seed_center_candidate(fila=990572)
        process = Candidata.query.get(990572)
        process.nombre_completo = "Filtro Proceso"
        process.codigo = "FLT-PROC"
        process.estado = "proceso_inscripcion"
        _seed_center_candidate(fila=990573)
        incomplete = Candidata.query.get(990573)
        incomplete.nombre_completo = "Filtro Incompleta"
        incomplete.codigo = "FLT-INCOMP"
        incomplete.cedula2 = None
        incomplete.estado = "inscrita_incompleta"
        _seed_center_candidate(fila=990574)
        disq = Candidata.query.get(990574)
        disq.nombre_completo = "Filtro Descalificada"
        disq.codigo = "FLT-DESC"
        disq.estado = "descalificada"
        db.session.commit()

    assert _login(client, "Cruz", "8998").status_code in (302, 303)
    checks = [
        ("/admin/candidatas?q=Filtro&filtro=listas", "Filtro Lista", "Filtro Trabajando"),
        ("/admin/candidatas?q=Filtro&filtro=trabajando", "Filtro Trabajando", "Filtro Lista"),
        ("/admin/candidatas?q=Filtro&filtro=proceso", "Filtro Proceso", "Filtro Lista"),
        ("/admin/candidatas?q=Filtro&filtro=por_completar", "Filtro Incompleta", "Filtro Lista"),
        ("/admin/candidatas?q=Filtro&filtro=descalificadas", "Filtro Descalificada", "Filtro Lista"),
        ("/admin/candidatas?q=Filtro&documentos=pendientes", "Filtro Incompleta", "Filtro Lista"),
    ]
    for url, expected, unexpected in checks:
        resp = client.get(url, follow_redirects=False)
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert expected in html
        assert unexpected not in html

    page = client.get("/admin/candidatas?q=Filtro&filtro=por_completar&page=1", follow_redirects=False)
    html = page.get_data(as_text=True)
    assert "/admin/candidatas/990573" in html
    assert "next=" in html
    assert "Filtro" in html and "por_completar" in html and "page%3D1" in html


def test_admin_candidatas_conteos_comparten_criterios_con_listado():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        _ensure_tables()
        from admin.routes import _candidata_center_listing_rows, _candidata_center_queue_counts

        _seed_center_candidate(fila=990576)
        complete = Candidata.query.get(990576)
        complete.nombre_completo = "Conteo Centro Lista"
        complete.estado = "lista_para_trabajar"
        _seed_center_candidate(fila=990577)
        incomplete = Candidata.query.get(990577)
        incomplete.nombre_completo = "Conteo Centro Incompleta"
        incomplete.estado = "inscrita_incompleta"
        incomplete.entrevista = "pendiente"
        Entrevista.query.filter_by(candidata_id=990577).delete(synchronize_session=False)
        _seed_center_candidate(fila=990578)
        working = Candidata.query.get(990578)
        working.nombre_completo = "Conteo Centro Trabajando"
        working.estado = "trabajando"
        _seed_center_candidate(fila=990579)
        disq = Candidata.query.get(990579)
        disq.nombre_completo = "Conteo Centro Descalificada"
        disq.estado = "descalificada"
        db.session.commit()

        with flask_app.test_request_context("/admin/candidatas"):
            session["role"] = "admin"
            counts = _candidata_center_queue_counts()
            for chip in ("por_completar", "proceso", "listas", "trabajando", "descalificadas"):
                listing = _candidata_center_listing_rows("", chip, 1, {})
                assert listing["total"] == counts[chip]

            pending_interview = _candidata_center_listing_rows("", "todas", 1, {"entrevista": "no"})
            assert any(row["candidata"].fila == 990577 for row in pending_interview["rows"])


def test_admin_candidatas_listado_usa_solo_referencias_de_entrevista_para_readiness():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app_context():
        _ensure_tables()
        from admin.routes import _candidata_center_listing_rows

        _seed_center_candidate(fila=990580)
        form_only = Candidata.query.get(990580)
        form_only.nombre_completo = "Listado Ref Formulario"
        form_only.contactos_referencias_laborales = "FORM-LAB"
        form_only.referencias_familiares_detalle = "FORM-FAM"
        form_only.referencias_laboral = ""
        form_only.referencias_familiares = ""

        _seed_center_candidate(fila=990581)
        interview_ok = Candidata.query.get(990581)
        interview_ok.nombre_completo = "Listado Ref Entrevista"
        interview_ok.contactos_referencias_laborales = "FORM-LAB"
        interview_ok.referencias_familiares_detalle = "FORM-FAM"
        interview_ok.referencias_laboral = "INT-LAB"
        interview_ok.referencias_familiares = "INT-FAM"
        db.session.commit()

        with flask_app.test_request_context("/admin/candidatas"):
            session["role"] = "admin"
            listing = _candidata_center_listing_rows("", "por_completar", 1, {})
            filas = {row["candidata"].fila for row in listing["rows"]}
            assert 990580 in filas
            assert 990581 not in filas


def test_admin_candidatas_dashboard_prioriza_y_deduplica_senales_operativas():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    now = utc_now_naive()

    with flask_app.app_context():
        _ensure_tables()
        from admin.routes import _candidata_center_dashboard_summary, _candidata_center_queue_counts

        _seed_center_candidate(fila=990586)
        tracked = Candidata.query.get(990586)
        tracked.nombre_completo = "Dashboard Seguimiento"
        tracked.codigo = None
        SeguimientoCandidataCaso.query.filter_by(candidata_id=990586).update(
            {"due_at": now - timedelta(hours=2), "proxima_accion_detalle": "Llamar hoy"},
            synchronize_session=False,
        )
        _seed_center_candidate(fila=990587)
        incomplete = Candidata.query.get(990587)
        incomplete.nombre_completo = "Dashboard Incompleta"
        incomplete.cedula2 = None
        db.session.commit()

        with flask_app.test_request_context("/admin/candidatas"):
            session["role"] = "admin"
            dashboard = _candidata_center_dashboard_summary(_candidata_center_queue_counts())

        attention_keys = [item["key"] for item in dashboard["attention_kpis"]]
        assert attention_keys == ["seguimiento", "por_completar"]
        assert [item["fila"] for item in dashboard["priority_items"]].count(990586) == 1
        assert dashboard["priority_items"][0]["fila"] == 990586
        assert dashboard["priority_items"][0]["title"] == "Seguimiento vencido"
        assert any(item["fila"] == 990587 for item in dashboard["priority_items"])
        assert dashboard["secondary_kpis"][0]["key"] == "proceso"


def test_admin_candidatas_index_no_crece_linealmente_en_sql():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    def _measure(url: str) -> tuple[int, str]:
        statements: list[str] = []

        def _before_cursor_execute(_conn, _cursor, statement, _parameters, _context, _executemany):
            sql = " ".join(str(statement).split())
            if sql.startswith("PRAGMA"):
                return
            statements.append(sql)

        with flask_app.app_context():
            event.listen(db.engine, "before_cursor_execute", _before_cursor_execute)
            try:
                resp = client.get(url, follow_redirects=False)
            finally:
                event.remove(db.engine, "before_cursor_execute", _before_cursor_execute)
        assert resp.status_code == 200
        return len(statements), resp.get_data(as_text=True)

    with flask_app.app_context():
        _ensure_tables()
        for fila in (991200,):
            _seed_center_candidate(fila=fila)
        base = Candidata.query.get(991200)
        base.nombre_completo = "SQL Base"
        base.estado = "lista_para_trabajar"
        db.session.commit()

    assert _login(client).status_code in (302, 303)
    base_count, base_html = _measure("/admin/candidatas")
    assert "SQL Base" in base_html

    with flask_app.app_context():
        for fila in range(991201, 991206):
            _seed_center_candidate(fila=fila)
            cand = Candidata.query.get(fila)
            cand.nombre_completo = f"SQL Extra {fila}"
            cand.estado = "inscrita_incompleta" if fila % 2 else "lista_para_trabajar"
            if fila % 2:
                cand.cedula2 = None
        db.session.commit()

    many_count, many_html = _measure("/admin/candidatas")
    assert "SQL Base" in many_html
    assert many_count <= base_count + 2

    search_count, search_html = _measure("/admin/candidatas?q=SQL&filtro=todas&page=1")
    assert "Resultados para:" in search_html
    assert search_count < many_count


def test_admin_candidatas_operativo_index_simplifica_paneles_sin_perder_busqueda():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    now = utc_now_naive()
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990588)
        tracked = Candidata.query.get(990588)
        tracked.nombre_completo = "Dashboard Seguimiento"
        SeguimientoCandidataCaso.query.filter_by(candidata_id=990588).update(
            {"due_at": now - timedelta(hours=3), "proxima_accion_detalle": "Llamar hoy"},
            synchronize_session=False,
        )
        _seed_center_candidate(fila=990589)
        incomplete = Candidata.query.get(990589)
        incomplete.nombre_completo = "Dashboard Incompleta"
        incomplete.cedula2 = None
        db.session.commit()

    assert _login(client).status_code in (302, 303)
    normal = client.get("/admin/candidatas?filtro=todas&page=1", follow_redirects=False)
    normal_html = normal.get_data(as_text=True)
    assert normal.status_code == 200
    assert "<h2>Pendientes</h2>" in normal_html
    assert "<h2>Atender ahora</h2>" in normal_html
    assert "Limpiar búsqueda" not in normal_html

    resp = client.get("/admin/candidatas?q=Dashboard&filtro=todas&page=1", follow_redirects=False)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Resultados para:" in html
    assert "Dashboard" in html
    assert "<h2>Pendientes</h2>" not in html
    assert "<h2>Atender ahora</h2>" not in html
    assert "cand-attention-kpis" not in html
    assert "candPriorityCriteria" not in html
    assert "Listado de candidatas" in html
    assert "Dashboard Seguimiento" in html
    assert "Dashboard Incompleta" in html
    assert "Por atender" not in html
    assert "Disponibilidad" not in html
    assert "Actividad reciente" not in html
    assert "Recientes" not in html
    assert "235 candidatas" not in html
    assert "Limpiar búsqueda" in html
    assert 'placeholder="Nombre, teléfono, cédula o código"' in html
    assert "Falta" in html


def test_admin_candidatas_descalificadas_no_es_principal_para_secretaria():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990575)
        cand = Candidata.query.get(990575)
        cand.nombre_completo = "Filtro Descalificada Secretaria"
        cand.codigo = "FLT-DESC-SEC"
        cand.estado = "descalificada"
        db.session.commit()

    assert _login(client).status_code in (302, 303)
    resp = client.get("/admin/candidatas?filtro=descalificadas&q=Filtro", follow_redirects=False)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Filtro Descalificada Secretaria" in html
    assert "Descalificadas</span><strong>" not in html


def test_admin_candidatas_jornada_operativa_busqueda_contexto_y_recientes():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990580)
        maria = Candidata.query.get(990580)
        maria.nombre_completo = "María Jornada"
        maria.codigo = "JOR-MARIA"
        maria.referencias_laboral = "JOR-REF-LAB"
        maria.referencias_familiares = "JOR-REF-FAM"
        _seed_center_candidate(fila=990581)
        ana = Candidata.query.get(990581)
        ana.nombre_completo = "Ana Jornada"
        ana.codigo = "JOR-ANA"
        ana.referencias_laboral = "JOR-REF-LAB"
        ana.referencias_familiares = "JOR-REF-FAM"
        db.session.commit()

    assert _login(client).status_code in (302, 303)
    listing = client.get("/admin/candidatas?q=Maria&filtro=todas&page=1", follow_redirects=False)
    assert listing.status_code == 200
    html = listing.get_data(as_text=True)
    assert "María Jornada" in html
    assert "next=" in html and "q%3DMaria" in html and "page%3D1" in html
    assert "Inscripción incompleta" in html
    assert "data-row-url=" in html

    detail = client.get("/admin/candidatas/990580?next=/admin/candidatas?q=Maria%26filtro=todas%26page=1", follow_redirects=False)
    assert detail.status_code == 200
    detail_html = detail.get_data(as_text=True)
    assert "Volver a Domésticas" in detail_html
    assert "/admin/candidatas?q=Maria&amp;filtro=todas&amp;page=1" in detail_html
    assert "Información de la candidata" in detail_html or "Información personal" in detail_html
    preparacion_html = detail_html.split('aria-label="Preparación"', 1)[1].split("</section>", 1)[0]
    assert "Requisitos completos" in preparacion_html
    assert "9/9" in preparacion_html
    assert "Editar formulario" in detail_html
    assert "Editar referencias" in detail_html
    assert "lista_para_trabajar" not in detail_html.split("<header", 1)[1].split("</header>", 1)[0]

    assert client.get("/admin/candidatas/990581", follow_redirects=False).status_code == 200
    quick = client.get("/admin/candidatas/busqueda-rapida.json?q=Jornada", follow_redirects=False).get_json() or {}
    assert {item["fila"] for item in quick["items"]} >= {990580, 990581}

    recent = client.get("/admin/candidatas/busqueda-rapida.json", follow_redirects=False).get_json() or {}
    assert [item["fila"] for item in recent["items"][:2]] == [990581, 990580]


def test_admin_candidatas_operativo_visual_css_scoped_tokens():
    css = Path("static/css/candidatas_operativo.css").read_text(encoding="utf-8")

    assert ".cand-center" in css
    assert ".cand-docs" in css
    assert "#candDisqualifyModal" in css
    assert "--cand-surface" in css
    assert "var(--panel" in css
    assert "var(--text" in css
    assert ".cand-center details" in css
    assert ".cand-center summary" in css
    assert ".cand-center .badge" in css
    assert ".cand-center :where(.form-control" in css
    assert ".detail-info-grid--personal" in css
    assert ".detail-info-item--stacked .detail-info-label" in css
    assert ".detail-info-item--stacked .detail-info-value" in css
    assert "@media (max-width: 991.98px)" in css
    assert "@media (max-width: 575.98px)" in css
    assert "color: white !important" not in css.lower()


def test_admin_candidatas_operativo_visual_markup_legible_y_sin_badges_duplicados():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990582)
        cand = Candidata.query.get(990582)
        cand.estado = "trabajando"
        db.session.commit()

    assert _login(client).status_code in (302, 303)
    detail = client.get("/admin/candidatas/990582", follow_redirects=False)
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)

    header_html = html.split("<header", 1)[1].split("</header>", 1)[0]
    assert header_html.count(">Trabajando<") == 1
    assert "data-status-badges" in header_html
    assert "data-header-actions" in header_html
    assert "text-bg-light border" in header_html
    assert "data-cand-header=\"nombre\"" in header_html
    assert "data-cand-header=\"telefono\"" in header_html
    assert "data-edit-shortcut=\"calls\"" not in header_html
    assert "data-edit-shortcut=\"personal\"" not in header_html
    assert "data-edit-shortcut=\"references\"" not in header_html
    assert "data-edit-shortcut=\"inscription\"" not in header_html

    assert "Contacto y seguimiento" not in html
    assert "Llamadas recientes" not in html
    assert "Sin llamadas registradas." not in html
    assert "Sin acciones recientes de auditoría para esta candidata." in html or "Historial limitado" in html
    assert "Ver datos secundarios" in html
    assert "2026-05-25 19:35:08.894047" not in html
    assert ".000000" not in html


def test_admin_candidata_informacion_personal_agrupa_label_y_valor_en_misma_tarjeta():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990594)

    assert _login(client).status_code in (302, 303)
    detail = client.get("/admin/candidatas/990594", follow_redirects=False)
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)

    personal_html = html.split('aria-label="Información personal"', 1)[1].split('aria-label="Información laboral"', 1)[0]
    assert 'class="detail-info-grid cand-display detail-info-grid--personal"' in personal_html
    assert personal_html.count('class="detail-info-item detail-info-item--stacked"') >= 5
    for label in ["Nombre", "Cédula", "Edad", "Teléfono", "Dirección"]:
        assert f'<small class="detail-info-label">{label}</small>' in personal_html
    assert "Ana Centro Operativo" in personal_html
    assert "402-9905940-4" in personal_html
    assert "34" in personal_html
    assert "809-555-0101" in personal_html
    assert "Santiago" in personal_html
    assert "Información laboral" in html
    assert 'data-display="labor"' in html


def test_admin_candidatas_operativo_muestra_solo_entrevista_historica_util():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990583)

    assert _login(client).status_code in (302, 303)

    def _set_legacy(value: Optional[str]) -> None:
        with flask_app.app_context():
            cand = Candidata.query.get(990583)
            cand.entrevista = value
            db.session.commit()

    _set_legacy("")
    html = client.get("/admin/candidatas/990583", follow_redirects=False).get_data(as_text=True)
    assert "Entrevista histórica" not in html

    _set_legacy('{"pregunta_1": "Respuesta 1", "pregunta_2": "Respuesta 2"}')
    html = client.get("/admin/candidatas/990583", follow_redirects=False).get_data(as_text=True)
    assert "Entrevista histórica" in html
    assert "Registro del sistema anterior" in html
    assert '<details' in html
    assert 'id="legacy-entrevista"' in html
    assert 'href="#legacy-entrevista"' not in html
    assert 'open' not in html.split('id="legacy-entrevista"', 1)[1].split('</details>', 1)[0]
    with flask_app.test_request_context():
        legacy_interviews_url = url_for("entrevistas_de_candidata", fila=990583)
        legacy_pdf_url = url_for("generar_pdf_entrevista", fila=990583)
    assert legacy_interviews_url in html
    assert legacy_pdf_url in html
    assert "Pregunta 1" in html
    assert "Respuesta 1" in html
    assert "Pregunta 2" in html
    assert "Respuesta 2" in html
    assert "Cargando entrevistas recientes..." in html
    assert "Leer respuestas" not in html

    fragment = client.get("/admin/candidatas/990583/_entrevistas", follow_redirects=False)
    assert fragment.status_code == 200
    fragment_html = fragment.get_data(as_text=True)
    assert "Leer respuestas" in fragment_html
    assert "Editar" in fragment_html
    assert "Descargar PDF" in fragment_html
    assert re.search(r"<dt>Total</dt>\s*<dd>1</dd>", html)

    _set_legacy("Nombre completo: Ana Perez\nExperiencia: 5 años\nObservacion libre")
    html = client.get("/admin/candidatas/990583", follow_redirects=False).get_data(as_text=True)
    assert "Entrevista histórica" in html
    assert "Nombre completo" in html
    assert "Ana Perez" in html
    assert "Experiencia" in html
    assert "5 años" in html
    assert "Observacion libre" in html

    for placeholder in ("{}", "[]", "   "):
        _set_legacy(placeholder)
        html = client.get("/admin/candidatas/990583", follow_redirects=False).get_data(as_text=True)
        assert "Entrevista histórica" not in html

    _set_legacy("{bad json")
    html = client.get("/admin/candidatas/990583", follow_redirects=False).get_data(as_text=True)
    assert "Entrevista histórica" in html
    assert "{bad json" in html


def test_admin_candidata_pdf_entrevista_historica_usa_solo_legacy_y_respeta_permisos():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990584)

    anon_resp = client.get("/generar_pdf_entrevista?fila=990584", follow_redirects=False)
    assert anon_resp.status_code in (302, 403)

    assert _login(client).status_code in (302, 303)

    with flask_app.app_context():
        cand = Candidata.query.get(990584)
        cand.entrevista = "Historia: solo texto legado"
        db.session.commit()

    pdf_resp = client.get("/generar_pdf_entrevista?fila=990584", follow_redirects=False)
    assert pdf_resp.status_code == 200
    assert pdf_resp.mimetype == "application/pdf"


def test_admin_candidatas_operativo_async_forms_limpian_loader_y_bloquean_doble_submit():
    template = Path("templates/admin/candidatas_operativo_detail.html").read_text(encoding="utf-8")
    js = Path("static/js/admin/candidatas_operativo_detail_ui.js").read_text(encoding="utf-8")

    assert 'data-quick-form data-no-loader="true"' in template
    assert 'data-state-action data-no-loader="true"' in template
    assert 'type="submit" data-no-loader="true"' in template
    assert "function clearGlobalLoaders()" in js
    assert "function setFormBusy(form, isBusy, submitter)" in js
    assert 'if (form.dataset.quickBusy === "1") return;' in js
    assert "fetchJsonWithTimeout" in js
    assert "finally {" in js
    assert "clearGlobalLoaders();" in js
    assert "setFormBusy(form, false, submitter);" in js


def test_admin_candidata_detalle_tiene_identidad_sticky_compacta():
    template = Path("templates/admin/candidatas_operativo_detail.html").read_text(encoding="utf-8")

    assert 'data-cand-identity-sticky' in template
    assert 'data-cand-identity-name' in template
    assert 'data-cand-identity-code' in template
    assert 'data-cand-identity-state' in template
    assert 'data-cand-identity-call' in template
    assert 'data-cand-breadcrumb-name' in template
    assert "card_key='form-laboral'" in template
    assert "card_key='secretary-laboral'" in template
    assert '<header class="detail-hero">' in template
    assert 'data-cand-header="nombre"' in template
    assert 'data-lazy-script-candidata-detail-ui' in template
    assert 'data-admin-nav-back="true"' in template
    assert 'setupStickyIdentityBar' not in template
    assert 'function clearGlobalLoaders()' not in template
    assert 'admin:navigation-complete' not in template
    assert 'refreshReferenceCards(display)' not in template


def test_admin_candidata_ficha_no_selecciona_columnas_blob():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990503)

    assert _login(client).status_code in (302, 303)
    statements: list[str] = []

    def _before_cursor_execute(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(str(statement))

    with flask_app.app_context():
        event.listen(db.engine, "before_cursor_execute", _before_cursor_execute)
        try:
            resp = client.get("/admin/candidatas/990503", follow_redirects=False)
        finally:
            event.remove(db.engine, "before_cursor_execute", _before_cursor_execute)

    assert resp.status_code == 200
    candidate_selects = [
        s.lower()
        for s in statements
        if "from candidatas" in s.lower() and "where candidatas.fila" in s.lower()
    ]
    assert candidate_selects
    main_select = candidate_selects[0]
    select_clause = main_select.split(" from candidatas", 1)[0]
    assert "candidatas.depuracion," not in select_clause
    assert "candidatas.perfil," not in select_clause
    assert "candidatas.cedula1," not in select_clause
    assert "candidatas.cedula2," not in select_clause


def test_registrar_llamada_acepta_next_seguro_del_centro():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990504)

    assert _login(client).status_code in (302, 303)
    with _feature_flag("llamadas", True):
        resp = client.post(
            "/candidatas/990504/llamar?next=/admin/candidatas/990504",
            data={"resultado": "informada", "duracion_minutos": "1", "notas": "ok"},
            follow_redirects=False,
        )
    assert resp.status_code in (302, 303)
    assert resp.headers.get("Location") == "/admin/candidatas/990504"


def test_admin_candidata_update_datos_personales_json_telefono_cedula_y_auditoria():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990505)

    assert _login(client).status_code in (302, 303)
    resp = client.post(
        "/admin/candidatas/990505/datos",
        data={
            "nombre": "Ana Centro Editada",
            "edad": "35",
            "telefono": "809-777-1234",
            "direccion": "",
            "cedula": "40299050505",
        },
        follow_redirects=False,
    )
    payload = resp.get_json() or {}
    assert resp.status_code == 200
    assert payload["ok"] is True
    assert payload["header"]["nombre"] == "Ana Centro Editada"
    assert payload["header"]["telefono"] == "809-777-1234"
    assert payload["values"]["personal"]["direccion"] == ""
    assert payload["invalidate_snapshots"] == ["/admin/candidatas"]

    with flask_app.app_context():
        db.session.expire_all()
        cand = Candidata.query.get(990505)
        assert cand.nombre_completo == "Ana Centro Editada"
        assert cand.edad == "35"
        assert cand.numero_telefono == "809-777-1234"
        assert cand.telefono_e164 == "+18097771234"
        assert cand.direccion_completa is None
        assert cand.cedula == "402-9905050-5"
        assert cand.cedula_norm_digits == "40299050505"
        audit = StaffAuditLog.query.filter_by(entity_type="candidata", entity_id="990505").order_by(StaffAuditLog.id.desc()).first()
        assert audit.action_type == "CANDIDATA_CENTER_EDIT"
        assert audit.success is True
        assert "numero_telefono" in (audit.changes_json or {})


def test_admin_candidata_update_datos_devuelve_payload_parcial_sin_ficha_completa():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990525)

    assert _login(client).status_code in (302, 303)
    with patch("admin.routes._candidata_center_detail_context", side_effect=AssertionError("no debe reconstruirse la ficha completa")):
        resp = client.post(
            "/admin/candidatas/990525/datos",
            data={"nombre": "Ana Centro Parcial", "telefono": "809-888-0000"},
            follow_redirects=False,
        )

    payload = resp.get_json() or {}
    assert resp.status_code == 200
    assert payload["ok"] is True
    assert payload["header"]["nombre"] == "Ana Centro Parcial"
    assert payload["header"]["telefono"] == "809-888-0000"
    assert payload["candidate"]["codigo"] == "CTR-990501"
    assert payload["status_badges"]["inscrita"] is True
    assert payload["display"]["personal"]["Nombre"] == "Ana Centro Parcial"
    assert payload["values"]["personal"]["telefono"] == "809-888-0000"
    for forbidden in ("readiness", "state_capabilities", "recent_calls", "inscription", "finance_event", "doc_flags"):
        assert forbidden not in payload


def test_admin_candidata_update_datos_rechaza_cedula_invalida_o_duplicada_sin_pisarla():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990506)
        _seed_center_candidate(fila=990507)
        dup = Candidata.query.get(990507)
        dup.nombre_completo = "Duplicada Antigua"
        dup.cedula = "402-9905070-7"
        db.session.commit()

    assert _login(client).status_code in (302, 303)
    invalid = client.post(
        "/admin/candidatas/990506/datos",
        data={"nombre": "Ana Centro Operativo", "cedula": "---"},
        follow_redirects=False,
    )
    assert invalid.status_code == 400
    assert "cedula" in ((invalid.get_json() or {}).get("errors") or {})

    duplicate = client.post(
        "/admin/candidatas/990506/datos",
        data={"nombre": "Ana Centro Operativo Cambiada", "cedula": "40299050707"},
        follow_redirects=False,
    )
    duplicate_payload = duplicate.get_json() or {}
    assert duplicate.status_code == 409
    assert duplicate_payload["ok"] is False
    assert duplicate_payload["error_code"] == "conflict"
    assert "Esta cédula ya está registrada" in duplicate_payload["message"]
    assert "Duplicada Antigua" in duplicate_payload["message"]
    assert "ficha #990507" in duplicate_payload["message"]
    assert "Esta cédula ya está registrada" in duplicate_payload["errors"]["cedula"]

    with flask_app.app_context():
        db.session.expire_all()
        cand = Candidata.query.get(990506)
        assert cand.nombre_completo == "Ana Centro Operativo"
        assert cand.cedula == "402-9905060-6"


def test_admin_candidata_update_datos_permitemisma_cedula_de_la_misma_candidata():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990509)

    assert _login(client).status_code in (302, 303)
    resp = client.post(
        "/admin/candidatas/990509/datos",
        data={
            "nombre": "Ana Centro Misma Cédula",
            "cedula": "402-9905090-9",
        },
        follow_redirects=False,
    )
    payload = resp.get_json() or {}
    assert resp.status_code == 200
    assert payload["ok"] is True
    assert payload["message"] == "Guardado."

    with flask_app.app_context():
        db.session.expire_all()
        cand = Candidata.query.get(990509)
        assert cand.nombre_completo == "Ana Centro Misma Cédula"
        assert cand.cedula == "402-9905090-9"


def test_admin_candidata_update_datos_integrityerror_constraint_devuelve_409_sin_500():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990510)

    assert _login(client).status_code in (302, 303)
    with flask_app.app_context():
        with patch(
            "core.services.candidata_quick_edit.execute_robust_save",
            return_value=SimpleNamespace(
                ok=False,
                attempts=1,
                error_message="duplicate key value violates unique constraint ux_candidatas_cedula_norm_digits",
                exception=IntegrityError(
                    "stmt",
                    "params",
                    Exception("duplicate key value violates unique constraint ux_candidatas_cedula_norm_digits"),
                ),
            ),
        ):
            resp = client.post(
                "/admin/candidatas/990510/datos",
                data={"nombre": "Ana Centro Operativo", "cedula": "40299051009"},
                follow_redirects=False,
            )

    payload = resp.get_json() or {}
    assert resp.status_code == 409
    assert payload["ok"] is False
    assert payload["error_code"] == "conflict"
    assert "Esta cédula ya está registrada" in payload["message"]


def test_admin_candidata_update_datos_other_integrityerror_no_se_disfraza_como_duplicado():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990511)

    assert _login(client).status_code in (302, 303)
    with flask_app.app_context():
        with patch(
            "core.services.candidata_quick_edit.execute_robust_save",
            return_value=SimpleNamespace(
                ok=False,
                attempts=1,
                error_message="check constraint violation on otro_constraint",
                exception=IntegrityError(
                    "stmt",
                    "params",
                    Exception("check constraint violation on otro_constraint"),
                ),
            ),
        ):
            resp = client.post(
                "/admin/candidatas/990511/datos",
                data={"nombre": "Ana Centro Operativo Cambiada", "cedula": "40299051101"},
                follow_redirects=False,
            )

    payload = resp.get_json() or {}
    assert resp.status_code == 500
    assert payload["ok"] is False
    assert payload["error_code"] == "persist_error"
    assert "Intenta de nuevo" in payload["message"]
    with flask_app.app_context():
        db.session.expire_all()
        cand = Candidata.query.get(990511)
        assert cand.nombre_completo == "Ana Centro Operativo"


def test_admin_candidata_update_perfil_laboral_limpia_opcionales_y_bool_null():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990508)

    assert _login(client).status_code in (302, 303)
    resp = client.post(
        "/admin/candidatas/990508/perfil-laboral",
        data={
            "modalidad": "Salida diaria",
            "rutas": "",
            "disponibilidad_inicio": "esta_semana",
            "empleo_anterior": "Hotel",
            "anos_experiencia": "6",
            "areas_experiencia": "",
            "sabe_planchar": "no",
            "trabaja_con_ninos": "",
            "trabaja_con_mascotas": "si",
            "puede_dormir_fuera": "no",
            "acepta_porcentaje_sueldo": "no",
            "sueldo_esperado": "",
            "motivacion_trabajo": "",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload["ok"] is True
    assert payload["values"]["labor"]["rutas"] == ""

    with flask_app.app_context():
        db.session.expire_all()
        cand = Candidata.query.get(990508)
        assert cand.modalidad_trabajo_preferida == "Salida diaria"
        assert cand.rutas_cercanas is None
        assert cand.empleo_anterior == "Hotel"
        assert cand.disponibilidad_inicio == "esta_semana"
        assert cand.areas_experiencia is None
        assert cand.sabe_planchar is False
        assert cand.trabaja_con_ninos is None
        assert cand.trabaja_con_mascotas is True
        assert cand.puede_dormir_fuera is False
        assert cand.acepta_porcentaje_sueldo is False
        assert cand.calificacion == "Excelente"


def test_admin_candidata_update_referencias_mantiene_independencia_entrevista_y_formulario():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990509)

    assert _login(client).status_code in (302, 303)
    resp = client.post(
        "/admin/candidatas/990509/referencias",
        data={
            "referencias_laboral": "Supervisora anterior Maria 809-333-3333 confirma puntualidad.",
            "referencias_familiares": "Hermano Jose 809-444-4444 confirma disponibilidad familiar.",
        },
        follow_redirects=False,
    )
    payload = resp.get_json() or {}
    assert resp.status_code == 200
    assert payload["ok"] is True
    assert payload["readiness"]["flags"]["referencias_laboral"] is True
    assert payload["readiness"]["flags"]["referencias_familiares"] is True

    with flask_app.app_context():
        db.session.expire_all()
        cand = Candidata.query.get(990509)
        assert cand.contactos_referencias_laborales == "Patrona anterior 809-111-1111"
        assert cand.referencias_familiares_detalle == "Hermana 809-222-2222"
        assert cand.referencias_laboral == "Supervisora anterior Maria 809-333-3333 confirma puntualidad."
        assert cand.referencias_familiares == "Hermano Jose 809-444-4444 confirma disponibilidad familiar."
        audit = StaffAuditLog.query.filter_by(entity_type="candidata", entity_id="990509").order_by(StaffAuditLog.id.desc()).first()
        assert audit.action_type == "CANDIDATA_REFERENCES_EDIT"
        assert audit.success is True
        assert "referencias_laboral" in (audit.changes_json or {})


def test_admin_candidata_update_referencias_devuelve_solo_regiones_necesarias():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990526)
        cand = Candidata.query.get(990526)
        cand.referencias_laboral = "INT-LAB-ORIGINAL"
        cand.referencias_familiares = "INT-FAM-ORIGINAL"
        db.session.commit()

    assert _login(client).status_code in (302, 303)
    with patch("admin.routes._candidata_center_detail_context", side_effect=AssertionError("no debe reconstruirse la ficha completa")):
        resp = client.post(
            "/admin/candidatas/990526/referencias",
            data={
                "referencias_laboral": "INT-LAB-RENOVADA",
                "referencias_familiares": "INT-FAM-RENOVADA",
            },
            follow_redirects=False,
        )

    payload = resp.get_json() or {}
    assert resp.status_code == 200
    assert payload["ok"] is True
    assert payload["display"]["secretary_references"]["laboral"] == "INT-LAB-RENOVADA"
    assert payload["display"]["secretary_references"]["familiar"] == "INT-FAM-RENOVADA"
    assert payload["readiness"]["flags"]["referencias_laboral"] is True
    assert payload["readiness"]["flags"]["referencias_familiares"] is True
    assert payload["state_capabilities"]["assignment"]
    assert payload["invalidate_snapshots"] == ["/admin/candidatas"]
    assert "personal" not in payload.get("display", {})
    assert "labor" not in payload.get("display", {})
    assert "references" not in payload.get("display", {})
    assert "inscription" not in payload.get("display", {})
    assert "recent_calls" not in payload
    assert "finance_event" not in payload
    assert "doc_flags" not in payload


def test_admin_candidata_update_inscripcion_devuelve_payload_parcial_con_readiness():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990527)
        cand = Candidata.query.get(990527)
        cand.codigo = None
        cand.inscripcion = False
        cand.monto = None
        cand.fecha = None
        db.session.commit()

    assert _login(client).status_code in (302, 303)
    with patch("admin.routes._candidata_center_detail_context", side_effect=AssertionError("no debe reconstruirse la ficha completa")):
        with patch("core.legacy_handlers.generar_codigo_unico", return_value="CTR-PARCIAL-527"):
            resp = client.post(
                "/admin/candidatas/990527/inscripcion",
                data={
                    "medio": "WhatsApp",
                    "estado": "si",
                    "monto": "1750.50",
                    "fecha": "2026-08-18",
                },
                follow_redirects=False,
            )

    payload = resp.get_json() or {}
    assert resp.status_code == 200
    assert payload["ok"] is True
    assert payload["inscription"]["codigo"] == "CTR-PARCIAL-527"
    assert payload["inscription"]["inscrita"] is True
    assert payload["readiness"]["flags"]["inscripcion"] is True
    assert payload["state_capabilities"]["process"]["label"]
    assert payload["display"]["inscription"]["Código"] == "CTR-PARCIAL-527"
    assert payload["values"]["inscription"]["medio"] == "WhatsApp"
    assert payload["invalidate_snapshots"] == ["/admin/candidatas"]
    assert "personal" not in payload.get("display", {})
    assert "labor" not in payload.get("display", {})
    assert "references" not in payload.get("display", {})
    assert "secretary_references" not in payload.get("display", {})
    assert "recent_calls" not in payload
    assert "finance_event" not in payload
    assert "doc_flags" not in payload


def test_admin_candidata_registrar_llamada_devuelve_payload_minimo_sin_ficha_completa():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990528)

    assert _login(client).status_code in (302, 303)
    with patch("admin.routes._candidata_center_detail_context", side_effect=AssertionError("no debe reconstruirse la ficha completa")):
        resp = client.post(
            "/admin/candidatas/990528/llamadas",
            data={"resultado": "exitosa", "duracion_minutos": "3", "notas": "Seguimiento parcial"},
            follow_redirects=False,
        )

    payload = resp.get_json() or {}
    assert resp.status_code == 200
    assert payload["ok"] is True
    assert payload["recent_calls"][0]["resultado"] == "exitosa"
    assert payload["recent_calls"][0]["notas"] == "Seguimiento parcial"
    assert payload["invalidate_snapshots"] == ["/admin/candidatas"]
    assert "display" not in payload
    assert "readiness" not in payload
    assert "state_capabilities" not in payload
    assert "inscription" not in payload
    assert "finance_event" not in payload


def test_admin_candidata_update_referencias_formulario_mantiene_independencia_de_entrevista():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990511)
        cand = Candidata.query.get(990511)
        cand.referencias_laboral = "INT-LAB-333"
        cand.referencias_familiares = "INT-FAM-444"
        db.session.commit()

    assert _login(client).status_code in (302, 303)
    resp = client.post(
        "/admin/candidatas/990511/referencias-formulario",
        data={
            "contactos_referencias_laborales": "FORM-LAB-111",
            "referencias_familiares_detalle": "FORM-FAM-222",
        },
        follow_redirects=False,
    )
    payload = resp.get_json() or {}
    assert resp.status_code == 200
    assert payload["ok"] is True

    with flask_app.app_context():
        db.session.expire_all()
        cand = Candidata.query.get(990511)
        assert cand.contactos_referencias_laborales == "FORM-LAB-111"
        assert cand.referencias_familiares_detalle == "FORM-FAM-222"
        assert cand.referencias_laboral == "INT-LAB-333"
        assert cand.referencias_familiares == "INT-FAM-444"


def test_admin_candidata_update_referencias_rechaza_placeholders():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990510)

    assert _login(client).status_code in (302, 303)
    resp = client.post(
        "/admin/candidatas/990510/referencias",
        data={"referencias_laboral": "none", "referencias_familiares": "--"},
        follow_redirects=False,
    )
    payload = resp.get_json() or {}
    assert resp.status_code == 400
    assert payload["ok"] is False
    assert "referencias_laboral" in (payload.get("errors") or {})


def test_admin_candidata_update_inscripcion_json_genera_codigo_estado_readiness_y_audit():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990512)
        cand = Candidata.query.get(990512)
        cand.codigo = None
        cand.inscripcion = False
        cand.monto = None
        cand.fecha = None
        cand.monto_total = Decimal("3000.00")
        cand.porciento = Decimal("750.00")
        cand.estado = "proceso_inscripcion"
        db.session.commit()

    assert _login(client).status_code in (302, 303)
    with patch("core.legacy_handlers.generar_codigo_unico", return_value="CTR-INS-512"):
        resp = client.post(
            "/admin/candidatas/990512/inscripcion",
            data={
                "medio": "Vía Oficina",
                "estado": "si",
                "monto": "1750.50",
                "fecha": "2026-08-18",
            },
            follow_redirects=False,
        )
    payload = resp.get_json() or {}
    assert resp.status_code == 200
    assert payload["ok"] is True
    assert payload["candidate"]["codigo"] == "CTR-INS-512"
    assert payload["inscription"]["inscrita"] is True
    assert payload["inscription"]["estado"] in {"inscrita", "lista_para_trabajar"}
    assert payload["readiness"]["flags"]["inscripcion"] is True

    with flask_app.app_context():
        db.session.expire_all()
        cand = Candidata.query.get(990512)
        assert cand.codigo == "CTR-INS-512"
        assert cand.medio_inscripcion == "Vía Oficina"
        assert cand.inscripcion is True
        assert str(cand.monto) == "1750.50"
        assert cand.fecha.isoformat() == "2026-08-18"
        assert str(cand.monto_total) == "3000.00"
        assert str(cand.porciento) == "750.00"
        audit = StaffAuditLog.query.filter_by(entity_type="candidata", entity_id="990512").order_by(StaffAuditLog.id.desc()).first()
        assert audit.action_type == "CANDIDATA_INSCRIPTION_EDIT"
        assert audit.success is True
        assert (audit.metadata_json or {}).get("generated_code") is True
        assert "codigo" in (audit.changes_json or {})


def test_admin_candidata_porciento_inline_configura_paga_y_mantiene_historial():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990520)
        cand = Candidata.query.get(990520)
        cand.monto_total = None
        cand.porciento = None
        cand.fecha_de_pago = None
        cand.inicio = None
        db.session.commit()

    assert _login(client).status_code in (302, 303)
    resp = client.get("/admin/candidatas/990520", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Sin cálculo de porciento" in html

    resp = client.post(
        "/admin/candidatas/990520/porciento",
        data={
            "idempotency_key": "finance-config-990520",
            "monto_total": "2000.00",
            "fecha_pago": "2026-08-20",
            "fecha_inicio": "2026-08-25",
        },
        follow_redirects=False,
    )
    payload = resp.get_json() or {}
    assert resp.status_code == 200
    assert payload["ok"] is True
    assert payload["porciento"]["state"] == "Pendiente"
    assert payload["porciento"]["monto_total"] == "2000.00"
    assert payload["porciento"]["pagado"] == "0.00"
    assert payload["porciento"]["pendiente"] == "500.00"
    assert payload["porciento_history"]

    resp = client.post(
        "/admin/candidatas/990520/porciento/pagos",
        data={
            "idempotency_key": "finance-payment-990520-1",
            "monto_pagado": "100.50",
            "calificacion": "Pago parcial",
        },
        follow_redirects=False,
    )
    payload = resp.get_json() or {}
    assert resp.status_code == 200
    assert payload["ok"] is True
    assert payload["porciento"]["state"] == "Pendiente"
    assert payload["porciento"]["pagado"] == "100.50"
    assert payload["porciento"]["pendiente"] == "399.50"
    assert payload["porciento_history"][0]["monto"] == "100.50"
    assert payload["porciento_history"][0]["actor"] == "Karla"

    resp = client.post(
        "/admin/candidatas/990520/porciento/pagos",
        data={
            "idempotency_key": "finance-payment-overpay",
            "monto_pagado": "9999.00",
            "calificacion": "Pago inválido",
        },
        follow_redirects=False,
    )
    payload = resp.get_json() or {}
    assert resp.status_code == 400
    assert payload["ok"] is False
    assert "supera el saldo" in payload["message"]

    with flask_app.app_context():
        db.session.expire_all()
        cand = Candidata.query.get(990520)
        assert str(cand.monto_total) == "2000.00"
        assert str(cand.porciento) == "399.50"


def test_admin_candidata_update_inscripcion_invalida_devuelve_json_y_no_genera_codigo():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990516)
        cand = Candidata.query.get(990516)
        cand.codigo = None
        cand.monto = 1500
        cand.fecha = utc_now_naive().date()
        db.session.commit()
        before_fecha = cand.fecha

    assert _login(client).status_code in (302, 303)
    with patch("core.legacy_handlers.generar_codigo_unico", return_value="CTR-NO-DEBE-USARSE") as generator:
        resp = client.post(
            "/admin/candidatas/990516/inscripcion",
            data={
                "medio": "Vía Oficina",
                "estado": "si",
                "monto": "abc",
                "fecha": "18/08/2026",
            },
            headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
            follow_redirects=False,
        )

    payload = resp.get_json() or {}
    assert resp.status_code == 400
    assert resp.content_type.startswith("application/json")
    assert payload["ok"] is False
    assert payload["error_code"] == "validation_error"
    assert payload["errors"]["monto"] == "Monto inválido."
    assert payload["errors"]["fecha"] == "Fecha inválida."
    generator.assert_not_called()

    with flask_app.app_context():
        db.session.expire_all()
        cand = Candidata.query.get(990516)
        assert cand.codigo is None
        assert str(cand.monto) == "1500.00"
        assert cand.fecha == before_fecha


def test_admin_candidata_update_inscripcion_no_encontrada_devuelve_json_404():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    assert _login(client).status_code in (302, 303)
    resp = client.post(
        "/admin/candidatas/123456789/inscripcion",
        data={"medio": "Vía Oficina", "estado": "si", "monto": "1500", "fecha": "2026-08-18"},
        headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
        follow_redirects=False,
    )
    payload = resp.get_json() or {}
    assert resp.status_code == 404
    assert resp.content_type.startswith("application/json")
    assert payload["ok"] is False
    assert payload["errors"]["candidata"] == "No existe."


def test_admin_candidata_update_inscripcion_no_reactiva_descalificada_ni_trabajando():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990513)
        _seed_center_candidate(fila=990514)
        descalificada = Candidata.query.get(990513)
        trabajando = Candidata.query.get(990514)
        descalificada.estado = "descalificada"
        descalificada.inscripcion = False
        trabajando.estado = "trabajando"
        trabajando.inscripcion = True
        db.session.commit()

    assert _login(client).status_code in (302, 303)
    for fila, estado_form in ((990513, "si"), (990514, "no")):
        resp = client.post(
            f"/admin/candidatas/{fila}/inscripcion",
            data={
                "medio": "Transferencia Bancaria",
                "estado": estado_form,
                "monto": "1000",
                "fecha": "2026-08-18",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 200

    with flask_app.app_context():
        db.session.expire_all()
        assert Candidata.query.get(990513).estado == "descalificada"
        assert Candidata.query.get(990514).estado == "trabajando"


def test_admin_candidata_operaciones_permitidas_preservan_descalificada():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _make_descalificada_for_reactivation(fila=990536, documentos=False)

    assert _login(client).status_code in (302, 303)
    actions = [
        ("get", "/admin/candidatas/990536"),
        ("get", "/admin/candidatas/990536/documentos"),
        ("post", "/admin/candidatas/990536/datos", {"nombre": "Ana Descalificada", "telefono": "809-555-9999"}),
        ("post", "/admin/candidatas/990536/referencias", {
            "referencias_laboral": "Patrona Valida 809-111-3333",
            "referencias_familiares": "Hermana Valida 809-222-3333",
        }),
        ("post", "/admin/candidatas/990536/inscripcion", {
            "medio": "WhatsApp",
            "estado": "si",
            "monto": "1500",
            "fecha": "2026-08-18",
        }),
        ("post", "/admin/candidatas/990536/llamadas", {
            "resultado": "informada",
            "duracion_minutos": "2",
            "notas": "Seguimiento permitido",
        }),
        ("get", "/entrevistas/nueva/990536/domestica?next=/admin/candidatas/990536"),
        ("get", "/candidatas/990536/llamar?next=/admin/candidatas/990536"),
    ]

    with _feature_flag("llamadas", True):
        for method, url, *payload in actions:
            if method == "post":
                resp = client.post(url, data=payload[0], follow_redirects=False)
            else:
                resp = client.get(url, follow_redirects=False)
            assert resp.status_code in (200, 302, 303, 409)
            with flask_app.app_context():
                db.session.expire_all()
                assert Candidata.query.get(990536).estado == "descalificada"


def test_admin_candidata_operaciones_permitidas_preservan_trabajando():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990537)
        cand = Candidata.query.get(990537)
        cand.estado = "trabajando"
        cand.fecha_cambio_estado = utc_now_naive()
        db.session.commit()

    assert _login(client).status_code in (302, 303)
    actions = [
        ("get", "/admin/candidatas/990537"),
        ("get", "/admin/candidatas/990537/documentos"),
        ("post", "/admin/candidatas/990537/datos", {"nombre": "Ana Trabajando", "telefono": "809-555-8888"}),
        ("post", "/admin/candidatas/990537/referencias", {
            "referencias_laboral": "Patrona Valida 809-111-4444",
            "referencias_familiares": "Hermana Valida 809-222-4444",
        }),
        ("post", "/admin/candidatas/990537/inscripcion", {
            "medio": "WhatsApp",
            "estado": "si",
            "monto": "1500",
            "fecha": "2026-08-18",
        }),
        ("post", "/admin/candidatas/990537/llamadas", {
            "resultado": "informada",
            "duracion_minutos": "2",
            "notas": "Seguimiento permitido",
        }),
        ("get", "/entrevistas/nueva/990537/domestica?next=/admin/candidatas/990537"),
        ("get", "/candidatas/990537/llamar?next=/admin/candidatas/990537"),
    ]

    with _feature_flag("llamadas", True):
        for method, url, *payload in actions:
            if method == "post":
                resp = client.post(url, data=payload[0], follow_redirects=False)
            else:
                resp = client.get(url, follow_redirects=False)
            assert resp.status_code in (200, 302, 303, 409)
            with flask_app.app_context():
                db.session.expire_all()
                assert Candidata.query.get(990537).estado == "trabajando"


def test_admin_candidata_get_detail_sin_side_effects_de_estado_auditoria_outbox():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990538)

    assert _login(client).status_code in (302, 303)
    with flask_app.app_context():
        cand = Candidata.query.get(990538)
        before = {
            "estado": cand.estado,
            "codigo": cand.codigo,
            "inscripcion": cand.inscripcion,
            "fecha_cambio_estado": cand.fecha_cambio_estado,
            "usuario_cambio_estado": cand.usuario_cambio_estado,
            "assignments": SolicitudCandidata.query.filter_by(candidata_id=990538).count(),
            "audit": StaffAuditLog.query.count(),
            "outbox": DomainOutbox.query.count(),
        }

    resp = client.get("/admin/candidatas/990538", follow_redirects=False)
    assert resp.status_code == 200

    with flask_app.app_context():
        db.session.expire_all()
        cand = Candidata.query.get(990538)
        assert cand.estado == before["estado"]
        assert cand.codigo == before["codigo"]
        assert cand.inscripcion == before["inscripcion"]
        assert cand.fecha_cambio_estado == before["fecha_cambio_estado"]
        assert cand.usuario_cambio_estado == before["usuario_cambio_estado"]
        assert SolicitudCandidata.query.filter_by(candidata_id=990538).count() == before["assignments"]
        assert StaffAuditLog.query.count() == before["audit"]
        assert DomainOutbox.query.count() == before["outbox"]


def test_admin_candidata_registrar_llamada_json_actualiza_recientes_y_audit():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990515)

    assert _login(client).status_code in (302, 303)
    resp = client.post(
        "/admin/candidatas/990515/llamadas",
        data={"resultado": "exitosa", "duracion_minutos": "3", "notas": "Confirmó disponibilidad inmediata."},
        follow_redirects=False,
    )
    payload = resp.get_json() or {}
    assert resp.status_code == 200
    assert payload["ok"] is True
    assert payload["recent_calls"][0]["resultado"] == "exitosa"
    assert payload["recent_calls"][0]["notas"] == "Confirmó disponibilidad inmediata."

    with flask_app.app_context():
        db.session.expire_all()
        call = LlamadaCandidata.query.filter_by(candidata_id=990515).order_by(LlamadaCandidata.id.desc()).first()
        assert call.resultado == "exitosa"
        assert call.duracion_segundos == 180
        audit = StaffAuditLog.query.filter_by(entity_type="candidata", entity_id="990515").order_by(StaffAuditLog.id.desc()).first()
        assert audit.action_type == "CANDIDATA_CALL_REGISTER"
        assert audit.success is True


def test_admin_candidata_update_json_404_y_anon_redirect():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    anon = flask_app.test_client()
    assert anon.post("/admin/candidatas/123/datos", data={}, follow_redirects=False).status_code in (302, 303)

    client = flask_app.test_client()
    assert _login(client).status_code in (302, 303)
    resp = client.post("/admin/candidatas/123456789/datos", data={"nombre": "No"}, follow_redirects=False)
    assert resp.status_code == 404
    assert (resp.get_json() or {})["ok"] is False


def test_admin_candidata_update_csrf_invalido_devuelve_json_estructurado():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = True
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990511)

    # Login con CSRF deshabilitado solo para preparar sesión staff; el endpoint bajo prueba queda protegido.
    flask_app.config["WTF_CSRF_ENABLED"] = False
    assert _login(client).status_code in (302, 303)
    flask_app.config["WTF_CSRF_ENABLED"] = True
    try:
        resp = client.post(
            "/admin/candidatas/990511/datos",
            data={"nombre": "Ana Centro Operativo"},
            headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
            follow_redirects=False,
        )
        payload = resp.get_json() or {}
        assert resp.status_code == 400
        assert payload["ok"] is False
        assert payload["error_code"] == "csrf"
    finally:
        flask_app.config["WTF_CSRF_ENABLED"] = False


def test_admin_candidata_estado_capabilities_separa_materiales_y_operativos():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990520)
        cand = Candidata.query.get(990520)
        cand.estado = "trabajando"
        cand.inscripcion = True
        db.session.commit()

    assert _login(client).status_code in (302, 303)
    resp = client.get("/admin/candidatas/990520", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "8/8 materiales" in html
    assert "Estado trabajando." in html
    assert "Trabajando" in html


def test_admin_candidata_proxima_accion_prioriza_estado_seguimiento_y_preparacion():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    now = utc_now_naive()
    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990582)
        tracked = Candidata.query.get(990582)
        tracked.nombre_completo = "Accion Seguimiento"
        SeguimientoCandidataCaso.query.filter_by(candidata_id=990582).update(
            {"due_at": now - timedelta(days=1), "proxima_accion_detalle": "Llamar por vencimiento"},
            synchronize_session=False,
        )
        _seed_center_candidate(fila=990583)
        incomplete = Candidata.query.get(990583)
        incomplete.nombre_completo = "Accion Incompleta"
        incomplete.codigo = None
        _seed_center_candidate(fila=990584)
        disq = Candidata.query.get(990584)
        disq.nombre_completo = "Accion Descalificada"
        disq.estado = "descalificada"
        disq.cedula2 = None
        _seed_center_candidate(fila=990585)
        working = Candidata.query.get(990585)
        working.nombre_completo = "Accion Trabajando"
        working.estado = "trabajando"
        working.cedula2 = None
        db.session.commit()

    assert _login(client, "Cruz", "8998").status_code in (302, 303)
    tracked_html = client.get("/admin/candidatas/990582", follow_redirects=False).get_data(as_text=True)
    assert "Seguimiento pendiente" in tracked_html
    assert "Llamar por vencimiento" in tracked_html

    incomplete_html = client.get("/admin/candidatas/990583", follow_redirects=False).get_data(as_text=True)
    assert "Falta código" in incomplete_html
    assert "Completar inscripción" in incomplete_html

    disq_html = client.get("/admin/candidatas/990584", follow_redirects=False).get_data(as_text=True)
    assert "Descalificada" in disq_html
    assert "Revisar motivo operativo antes de continuar preparación." in disq_html
    assert "Subir cédula trasera" not in disq_html

    working_html = client.get("/admin/candidatas/990585", follow_redirects=False).get_data(as_text=True)
    assert "Trabajando" in working_html
    assert "No hay acción de preparación como prioridad mientras está trabajando." in working_html
    assert "Subir cédula trasera" not in working_html


def test_admin_candidata_estado_lista_json_usa_invariant_audit_y_outbox():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990521)
        SolicitudCandidata.query.filter_by(candidata_id=990521).delete(synchronize_session=False)
        Solicitud.query.filter_by(id=990521).delete(synchronize_session=False)
        cand = Candidata.query.get(990521)
        cand.estado = "inscrita"
        db.session.commit()

    assert _login(client).status_code in (302, 303)
    with patch("admin.routes._emit_domain_outbox_event") as outbox_mock:
        resp = client.post("/admin/candidatas/990521/estado/lista", data={}, follow_redirects=False)

    payload = resp.get_json() or {}
    assert resp.status_code == 200
    assert payload["ok"] is True
    assert payload["candidate"]["estado"] == "lista_para_trabajar"
    assert payload["state_capabilities"]["actions"]["can_mark_ready"] is False
    outbox_mock.assert_called_once()

    with flask_app.app_context():
        db.session.expire_all()
        cand = Candidata.query.get(990521)
        assert cand.estado == "lista_para_trabajar"
        assert cand.usuario_cambio_estado
        audit = StaffAuditLog.query.filter_by(entity_type="Candidata", entity_id="990521", action_type="CANDIDATA_ESTADO_LISTA").all()
        assert len(audit) == 1


def test_admin_candidata_estado_lista_json_bloquea_incompleta_con_reasons():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990522)
        SolicitudCandidata.query.filter_by(candidata_id=990522).delete(synchronize_session=False)
        Solicitud.query.filter_by(id=990522).delete(synchronize_session=False)
        cand = Candidata.query.get(990522)
        cand.estado = "inscrita"
        cand.cedula2 = None
        db.session.commit()

    assert _login(client).status_code in (302, 303)
    with patch("admin.routes._emit_domain_outbox_event") as outbox_mock:
        resp = client.post("/admin/candidatas/990522/estado/lista", data={}, follow_redirects=False)

    payload = resp.get_json() or {}
    assert resp.status_code == 409
    assert payload["ok"] is False
    assert payload["state_capabilities"]["actions"]["can_mark_ready"] is False
    assert any("cedula2" in r for r in payload["state_capabilities"]["reasons"]["can_mark_ready"])
    outbox_mock.assert_not_called()

    with flask_app.app_context():
        db.session.expire_all()
        assert Candidata.query.get(990522).estado == "inscrita"


def test_admin_candidata_estado_descalificar_y_reactivar_json():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990523)
        SolicitudCandidata.query.filter_by(candidata_id=990523).delete(synchronize_session=False)
        Solicitud.query.filter_by(id=990523).delete(synchronize_session=False)
        db.session.commit()

    assert _login(client, "Cruz", "8998").status_code in (302, 303)
    with patch("admin.routes._emit_domain_outbox_event") as outbox_mock:
        desc = client.post(
            "/admin/candidatas/990523/estado/descalificar",
            data={"motivo": "No cumple perfil"},
            follow_redirects=False,
        )
        react = client.post("/admin/candidatas/990523/estado/reactivar", data={}, follow_redirects=False)

    assert desc.status_code == 200
    assert (desc.get_json() or {})["state_capabilities"]["situation"]["descalificada"] is True
    assert react.status_code == 200
    assert (react.get_json() or {})["candidate"]["estado"] == "lista_para_trabajar"
    assert outbox_mock.call_count == 2

    with flask_app.app_context():
        db.session.expire_all()
        cand = Candidata.query.get(990523)
        assert cand.estado == "lista_para_trabajar"
        assert cand.nota_descalificacion is None


def test_admin_candidata_reactivar_completa_vuelve_lista_para_trabajar():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _make_descalificada_for_reactivation(fila=990528)

    assert _login(client, "Cruz", "8998").status_code in (302, 303)
    with patch("admin.routes._emit_domain_outbox_event") as outbox_mock:
        resp = client.post("/admin/candidatas/990528/estado/reactivar", data={}, follow_redirects=False)

    payload = resp.get_json() or {}
    assert resp.status_code == 200
    assert payload["candidate"]["estado"] == "lista_para_trabajar"
    assert payload["state_capabilities"]["preparation"]["label"] == "8/8"
    outbox_mock.assert_called_once()
    assert outbox_mock.call_args.kwargs["payload"]["to"] == "lista_para_trabajar"

    with flask_app.app_context():
        db.session.expire_all()
        cand = Candidata.query.get(990528)
        assert cand.estado == "lista_para_trabajar"
        assert cand.nota_descalificacion is None


def test_admin_candidata_reactivar_incompleta_no_finge_lista():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    cases = [
        (990529, {"documentos": False}, "cedula2"),
        (990530, {"entrevista": False}, "entrevista"),
        (990531, {"referencias": False}, "referencias_laboral"),
    ]
    with flask_app.app_context():
        _ensure_tables()
        for fila, kwargs, _ in cases:
            _make_descalificada_for_reactivation(fila=fila, **kwargs)

    assert _login(client, "Cruz", "8998").status_code in (302, 303)
    with patch("admin.routes._emit_domain_outbox_event") as outbox_mock:
        responses = [
            (fila, missing_key, client.post(f"/admin/candidatas/{fila}/estado/reactivar", data={}, follow_redirects=False))
            for fila, _, missing_key in cases
        ]

    assert outbox_mock.call_count == len(cases)
    for fila, missing_key, resp in responses:
        payload = resp.get_json() or {}
        assert resp.status_code == 200
        assert payload["candidate"]["estado"] == "inscrita_incompleta"
        assert payload["state_capabilities"]["situation"]["label"] == "No disponible para enviar"
        assert missing_key in payload["state_capabilities"]["preparation"]["missing"]

    with flask_app.app_context():
        db.session.expire_all()
        for fila, _, _ in cases:
            cand = Candidata.query.get(fila)
            assert cand.estado == "inscrita_incompleta"
            assert cand.nota_descalificacion is None


def test_admin_candidata_reactivar_inscripcion_incompleta_y_sin_inscripcion():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _make_descalificada_for_reactivation(fila=990532, pago=False)
        _make_descalificada_for_reactivation(fila=990533, inscripcion=False)

    assert _login(client, "Cruz", "8998").status_code in (302, 303)
    with patch("admin.routes._emit_domain_outbox_event") as outbox_mock:
        incomplete = client.post("/admin/candidatas/990532/estado/reactivar", data={}, follow_redirects=False)
        no_inscription = client.post("/admin/candidatas/990533/estado/reactivar", data={}, follow_redirects=False)

    assert incomplete.status_code == 200
    assert no_inscription.status_code == 200
    assert (incomplete.get_json() or {})["candidate"]["estado"] == "inscrita_incompleta"
    assert (no_inscription.get_json() or {})["candidate"]["estado"] == "proceso_inscripcion"
    assert [call.kwargs["payload"]["to"] for call in outbox_mock.call_args_list] == [
        "inscrita_incompleta",
        "proceso_inscripcion",
    ]


def test_admin_candidata_reactivar_bloqueada_por_asignacion_no_limpia_nota_ni_emite():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _make_descalificada_for_reactivation(fila=990534, active_assignment=True)

    assert _login(client, "Cruz", "8998").status_code in (302, 303)
    with patch("admin.routes._emit_domain_outbox_event") as outbox_mock:
        resp = client.post("/admin/candidatas/990534/estado/reactivar", data={}, follow_redirects=False)

    payload = resp.get_json() or {}
    assert resp.status_code == 409
    assert "asignación activa" in (payload.get("message") or "")
    outbox_mock.assert_not_called()

    with flask_app.app_context():
        db.session.expire_all()
        cand = Candidata.query.get(990534)
        assert cand.estado == "descalificada"
        assert cand.nota_descalificacion == "No cumple perfil"


def test_admin_candidata_reactivar_legacy_audita_y_outbox_exactamente_una_vez():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _make_descalificada_for_reactivation(fila=990535, documentos=False)

    assert _login(client, "Cruz", "8998").status_code in (302, 303)
    resp = client.post("/admin/candidatas/990535/reactivar", data={}, follow_redirects=False)
    assert resp.status_code in (302, 303)

    with flask_app.app_context():
        db.session.expire_all()
        cand = Candidata.query.get(990535)
        assert cand.estado == "inscrita_incompleta"
        audits = StaffAuditLog.query.filter_by(
            entity_type="Candidata",
            entity_id="990535",
            action_type="CANDIDATA_REACTIVAR",
        ).all()
        outbox = DomainOutbox.query.filter_by(
            aggregate_type="Candidata",
            aggregate_id=990535,
            event_type="CANDIDATA_ESTADO_CAMBIADO",
        ).all()
        assert len(audits) == 1
        assert len(outbox) == 1
        assert (outbox[0].payload or {}).get("to") == "inscrita_incompleta"


def test_admin_candidata_estado_descalificar_exige_motivo_y_assignment_guard():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990524)

    assert _login(client, "Cruz", "8998").status_code in (302, 303)
    missing = client.post("/admin/candidatas/990524/estado/descalificar", data={"motivo": ""}, follow_redirects=False)
    blocked = client.post("/admin/candidatas/990524/estado/descalificar", data={"motivo": "No"}, follow_redirects=False)

    assert missing.status_code == 400
    assert blocked.status_code == 409
    assert "asignación activa" in ((blocked.get_json() or {}).get("message") or "")

    with flask_app.app_context():
        db.session.expire_all()
        assert Candidata.query.get(990524).estado == "lista_para_trabajar"


def test_admin_candidata_estado_trabajando_requiere_asignacion_valida():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990525)
        _seed_center_candidate(fila=990526)
        SolicitudCandidata.query.filter_by(candidata_id=990526).delete(synchronize_session=False)
        Solicitud.query.filter_by(id=990526).delete(synchronize_session=False)
        db.session.commit()

    assert _login(client).status_code in (302, 303)
    with patch("admin.routes._emit_domain_outbox_event") as outbox_mock:
        ok = client.post("/admin/candidatas/990525/estado/trabajando", data={}, follow_redirects=False)
        blocked = client.post("/admin/candidatas/990526/estado/trabajando", data={}, follow_redirects=False)

    assert ok.status_code == 200
    assert (ok.get_json() or {})["candidate"]["estado"] == "trabajando"
    assert blocked.status_code == 409
    assert "asignación activa" in ((blocked.get_json() or {}).get("message") or "")
    outbox_mock.assert_called_once()

    with flask_app.app_context():
        db.session.expire_all()
        assert Candidata.query.get(990525).estado == "trabajando"
        assert Candidata.query.get(990526).estado == "lista_para_trabajar"


def test_admin_candidata_estado_json_csrf_invalido():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = True
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_center_candidate(fila=990527)

    flask_app.config["WTF_CSRF_ENABLED"] = False
    assert _login(client).status_code in (302, 303)
    flask_app.config["WTF_CSRF_ENABLED"] = True
    try:
        resp = client.post(
            "/admin/candidatas/990527/estado/trabajando",
            data={},
            headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
            follow_redirects=False,
        )
        payload = resp.get_json() or {}
        assert resp.status_code == 400
        assert payload["ok"] is False
        assert payload["error_code"] == "csrf"
    finally:
        flask_app.config["WTF_CSRF_ENABLED"] = False
