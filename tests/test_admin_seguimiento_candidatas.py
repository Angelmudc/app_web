from __future__ import annotations

import re
import secrets
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import render_template, session
from app import app as flask_app
from admin import routes as admin_routes
from config_app import db
from models import (
    Cliente,
    DomainOutbox,
    SeguimientoCandidataCaso,
    SeguimientoCandidataContacto,
    SeguimientoCandidataEvento,
)


def _csrf_token_from_html(html: str) -> str:
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', html or "")
    return m.group(1) if m else ""


def _login(client, usuario: str = "Karla", clave: str = "9989"):
    return client.post("/admin/login", data={"usuario": usuario, "clave": clave}, follow_redirects=False)


def _login_with_csrf(client, usuario: str = "Karla", clave: str = "9989"):
    page = client.get("/admin/login", follow_redirects=False)
    token = _csrf_token_from_html(page.get_data(as_text=True))
    return client.post(
        "/admin/login",
        data={"usuario": usuario, "clave": clave, "csrf_token": token},
        follow_redirects=False,
    )


def _crear_caso(client, **extra):
    payload = {
        "telefono_norm": "8090001111",
        "nombre_contacto": "Ana Pérez",
        "canal_origen": "whatsapp",
        "proxima_accion_tipo": "devolver_llamada",
        "proxima_accion_detalle": "Confirmar disponibilidad",
        "due_at": "2026-05-01T15:00:00Z",
    }
    payload.update(extra)
    return client.post("/admin/seguimiento-candidatas/casos", json=payload)


def _ensure_tracking_tables():
    with flask_app.app_context():
        bind = db.session.get_bind()
        SeguimientoCandidataContacto.__table__.create(bind=bind, checkfirst=True)
        SeguimientoCandidataCaso.__table__.create(bind=bind, checkfirst=True)
        SeguimientoCandidataEvento.__table__.create(bind=bind, checkfirst=True)


def _ensure_client_table():
    with flask_app.app_context():
        bind = db.session.get_bind()
        Cliente.__table__.create(bind=bind, checkfirst=True)


def _ensure_outbox_table():
    with flask_app.app_context():
        bind = db.session.get_bind()
        DomainOutbox.__table__.create(bind=bind, checkfirst=True)
    admin_routes._DOMAIN_OUTBOX_TABLE_READY = None


def _force_login_cliente_session(client):
    suffix = secrets.token_hex(4)
    _ensure_client_table()
    with flask_app.app_context():
        row = Cliente(
            codigo=f"CLT-SEG-{suffix}",
            nombre_completo="Cliente QA",
            email=f"cliente.seg.qa.{suffix}@example.com",
            telefono="8090009999",
        )
        row.password_hash = "DISABLED_RESET_REQUIRED"
        db.session.add(row)
        db.session.commit()
        cid = int(row.id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(cid)
        sess["_fresh"] = True
        sess.pop("is_admin_session", None)
    return cid


def _render_base_as(path: str, *, role: str = "admin", authenticated: bool = True) -> str:
    fake_user = SimpleNamespace(
        is_authenticated=bool(authenticated),
        role=role,
        username="qa_staff",
    )
    with flask_app.app_context():
        with patch("flask_login.utils._get_user", return_value=fake_user):
            with flask_app.test_request_context(path, method="GET"):
                if authenticated:
                    session["role"] = role
                else:
                    session.pop("role", None)
                return render_template("base.html")


def test_crear_caso_ok():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _ensure_tracking_tables()
    client = flask_app.test_client()
    _login(client)
    resp = _crear_caso(client)
    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("ok") is True
    assert int((payload.get("case") or {}).get("id") or 0) > 0


def test_crear_caso_invalido_sin_proxima_accion():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _ensure_tracking_tables()
    client = flask_app.test_client()
    _login(client)
    resp = _crear_caso(client, proxima_accion_tipo="")
    assert resp.status_code == 400
    payload = resp.get_json() or {}
    assert payload.get("error") == "proxima_accion_required"


def test_crear_caso_sin_due_at_aplica_vencimiento_automatico_7_dias():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _ensure_tracking_tables()
    client = flask_app.test_client()
    _login(client)
    resp = _crear_caso(client, due_at="")
    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("ok") is True
    assert payload.get("auto_due_applied") is True
    case = payload.get("case") or {}
    due_raw = str(case.get("due_at") or "")
    assert due_raw
    with flask_app.app_context():
        row = SeguimientoCandidataCaso.query.get(int(case.get("id") or 0))
        assert row is not None
        now = admin_routes._seg_now()
        delta_seconds = abs((row.due_at - (now + admin_routes.timedelta(days=7))).total_seconds())
        assert delta_seconds < 300


def test_dedupe_por_telefono_normalizado_informa_existente():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _ensure_tracking_tables()
    client = flask_app.test_client()
    _login(client)
    resp_a = _crear_caso(client, telefono_norm="(809) 111-2222")
    assert resp_a.status_code == 200
    case_a = (resp_a.get_json() or {}).get("case") or {}
    resp_b = _crear_caso(client, telefono_norm="8091112222", nombre_contacto="Otro nombre")
    assert resp_b.status_code == 200
    payload_b = resp_b.get_json() or {}
    assert payload_b.get("duplicate_detected") is True
    assert int(payload_b.get("existing_case_id") or 0) == int(case_a.get("id") or 0)


def test_dedupe_por_candidata_informa_existente():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _ensure_tracking_tables()
    client = flask_app.test_client()
    _login(client)
    resp_a = _crear_caso(client, telefono_norm="8097771111", candidata_id=12345)
    assert resp_a.status_code == 200
    case_a = (resp_a.get_json() or {}).get("case") or {}
    resp_b = _crear_caso(client, telefono_norm="8097772222", candidata_id=12345)
    assert resp_b.status_code == 200
    payload_b = resp_b.get_json() or {}
    assert payload_b.get("duplicate_detected") is True
    assert int(payload_b.get("existing_case_id") or 0) == int(case_a.get("id") or 0)


def test_tomar_caso_ok_y_takeover_auditado():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _ensure_tracking_tables()
    client = flask_app.test_client()
    _login(client)
    created = _crear_caso(client, telefono_norm="8093334444").get_json() or {}
    case_id = int(((created.get("case") or {}).get("id")) or 0)
    take_resp = client.post(f"/admin/seguimiento-candidatas/casos/{case_id}/tomar")
    assert take_resp.status_code == 200
    with flask_app.app_context():
        ev = (
            SeguimientoCandidataEvento.query
            .filter_by(caso_id=case_id, event_type="case_taken")
            .order_by(SeguimientoCandidataEvento.id.desc())
            .first()
        )
        assert ev is not None
        assert ev.note in ("take", "takeover")


def test_cambiar_estado_setea_waiting_since_y_status_changed():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _ensure_tracking_tables()
    client = flask_app.test_client()
    _login(client)
    created = _crear_caso(client, telefono_norm="8095556666").get_json() or {}
    case_id = int(((created.get("case") or {}).get("id")) or 0)
    resp = client.post(f"/admin/seguimiento-candidatas/casos/{case_id}/estado", json={"estado": "esperando_candidata"})
    assert resp.status_code == 200
    with flask_app.app_context():
        case = SeguimientoCandidataCaso.query.get(case_id)
        assert case is not None
        assert case.estado == "esperando_candidata"
        assert case.waiting_since_at is not None
        assert case.status_changed_at is not None


def test_cerrar_sin_razon_falla():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _ensure_tracking_tables()
    client = flask_app.test_client()
    _login(client)
    created = _crear_caso(client, telefono_norm="8091231231").get_json() or {}
    case_id = int(((created.get("case") or {}).get("id")) or 0)
    resp = client.post(f"/admin/seguimiento-candidatas/casos/{case_id}/cerrar", json={"estado": "cerrado_no_exitoso", "close_reason": ""})
    assert resp.status_code == 400
    payload = resp.get_json() or {}
    assert payload.get("error") == "close_reason_required"


def test_cerrar_por_estado_directo_bloqueado():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _ensure_tracking_tables()
    client = flask_app.test_client()
    _login(client)
    created = _crear_caso(client, telefono_norm="8091231232").get_json() or {}
    case_id = int(((created.get("case") or {}).get("id")) or 0)
    resp = client.post(f"/admin/seguimiento-candidatas/casos/{case_id}/estado", json={"estado": "cerrado_no_exitoso"})
    assert resp.status_code == 400
    payload = resp.get_json() or {}
    assert payload.get("error") == "use_close_endpoint"


def test_reabrir_limpia_datos_de_cierre():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _ensure_tracking_tables()
    client = flask_app.test_client()
    _login(client)
    created = _crear_caso(client, telefono_norm="8098881234").get_json() or {}
    case_id = int(((created.get("case") or {}).get("id")) or 0)
    close_resp = client.post(
        f"/admin/seguimiento-candidatas/casos/{case_id}/cerrar",
        json={"estado": "cerrado_no_exitoso", "close_reason": "sin respuesta"},
    )
    assert close_resp.status_code == 200
    reopen_resp = client.post(f"/admin/seguimiento-candidatas/casos/{case_id}/reabrir")
    assert reopen_resp.status_code == 200
    with flask_app.app_context():
        case = SeguimientoCandidataCaso.query.get(case_id)
        assert case is not None
        assert case.estado == "en_gestion"
        assert case.closed_at is None
        assert case.close_reason is None
        assert case.closed_by_staff_user_id is None


def test_badge_json_ok():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _ensure_tracking_tables()
    client = flask_app.test_client()
    _login(client)
    resp = client.get("/admin/seguimiento-candidatas/badge.json")
    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("ok") is True
    assert isinstance(payload.get("overdue_count"), int)


def test_permisos_requiere_login():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    resp = client.get("/admin/seguimiento-candidatas/cola", follow_redirects=False)
    assert resp.status_code in (301, 302)


def test_permisos_cliente_no_accede_admin_endpoints_ni_badge():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    _force_login_cliente_session(client)
    resp = client.get("/admin/seguimiento-candidatas/cola", follow_redirects=False)
    assert resp.status_code in (301, 302)
    resp_badge = client.get("/admin/seguimiento-candidatas/badge.json", follow_redirects=False)
    assert resp_badge.status_code in (301, 302)


def test_badge_island_no_visible_en_home_publico_y_cliente():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    public_client = flask_app.test_client()
    public_home = public_client.get("/", follow_redirects=False)
    assert b"Seguimiento candidatas" not in public_home.data

    cliente_client = flask_app.test_client()
    _force_login_cliente_session(cliente_client)
    cliente_home = cliente_client.get("/clientes", follow_redirects=False)
    assert b"Seguimiento candidatas" not in cliente_home.data


def test_mutacion_requiere_csrf():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = True
    _ensure_tracking_tables()
    client = flask_app.test_client()
    login_resp = _login_with_csrf(client)
    assert login_resp.status_code in (302, 303)
    resp = client.post(
        "/admin/seguimiento-candidatas/casos",
        json={
            "telefono_norm": "8094441111",
            "proxima_accion_tipo": "devolver_llamada",
            "due_at": "2026-05-01T15:00:00Z",
        },
    )
    assert resp.status_code == 400


def test_outbox_emitido_en_create_update_take_close():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _ensure_tracking_tables()
    _ensure_outbox_table()
    client = flask_app.test_client()
    _login(client)
    created = _crear_caso(client, telefono_norm="8094141414").get_json() or {}
    case_id = int(((created.get("case") or {}).get("id")) or 0)
    assert case_id > 0
    assert client.post(f"/admin/seguimiento-candidatas/casos/{case_id}/tomar").status_code == 200
    assert client.post(
        f"/admin/seguimiento-candidatas/casos/{case_id}/estado",
        json={"estado": "esperando_staff"},
    ).status_code == 200
    assert client.post(
        f"/admin/seguimiento-candidatas/casos/{case_id}/cerrar",
        json={"estado": "cerrado_no_exitoso", "close_reason": "no contesta"},
    ).status_code == 200

    with flask_app.app_context():
        rows = (
            DomainOutbox.query
            .filter(DomainOutbox.aggregate_type == "SeguimientoCandidataCaso")
            .filter(DomainOutbox.aggregate_id == str(case_id))
            .all()
        )
        event_types = {str(r.event_type or "") for r in rows}
        assert "staff.case_tracking.created" in event_types
        assert "staff.case_tracking.taken" in event_types
        assert "staff.case_tracking.updated" in event_types
        assert "staff.case_tracking.closed" in event_types


def test_cola_buckets_y_timeline():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _ensure_tracking_tables()
    client = flask_app.test_client()
    _login(client)
    c1 = _crear_caso(client, telefono_norm="8097000001", due_at="2020-01-01T10:00:00Z").get_json() or {}
    c1_id = int(((c1.get("case") or {}).get("id")) or 0)
    assert c1_id > 0
    c2 = _crear_caso(client, telefono_norm="8097000002").get_json() or {}
    c2_id = int(((c2.get("case") or {}).get("id")) or 0)
    assert c2_id > 0
    assert client.post(f"/admin/seguimiento-candidatas/casos/{c2_id}/estado", json={"estado": "en_gestion"}).status_code == 200
    cola = client.get("/admin/seguimiento-candidatas/cola.json")
    assert cola.status_code == 200
    payload = cola.get_json() or {}
    buckets = payload.get("buckets") or {}
    assert isinstance(buckets.get("vencidos"), list)
    assert isinstance(buckets.get("en_gestion"), list)
    detail = client.get(f"/admin/seguimiento-candidatas/casos/{c2_id}", follow_redirects=False)
    assert detail.status_code == 200
    assert b"Timeline" in detail.data
    assert b"state_changed" in detail.data


def test_row_version_incrementa_en_mutaciones():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _ensure_tracking_tables()
    client = flask_app.test_client()
    _login(client)
    created = _crear_caso(client, telefono_norm="8099991111").get_json() or {}
    case_id = int(((created.get("case") or {}).get("id")) or 0)
    with flask_app.app_context():
        row = SeguimientoCandidataCaso.query.get(case_id)
        v1 = int(getattr(row, "row_version", 0) or 0)
    assert client.post(f"/admin/seguimiento-candidatas/casos/{case_id}/nota", json={"note": "seguimiento 1"}).status_code == 200
    with flask_app.app_context():
        row2 = SeguimientoCandidataCaso.query.get(case_id)
        v2 = int(getattr(row2, "row_version", 0) or 0)
    assert v2 > v1


def test_takeover_no_falla_y_queda_evento_takeover():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    _ensure_tracking_tables()
    _ensure_outbox_table()
    client = flask_app.test_client()
    _login(client)
    created = _crear_caso(client, telefono_norm="8099090901").get_json() or {}
    case_id = int(((created.get("case") or {}).get("id")) or 0)
    with flask_app.app_context():
        case = SeguimientoCandidataCaso.query.get(case_id)
        case.owner_staff_user_id = 99999
        db.session.add(case)
        db.session.commit()
    take = client.post(f"/admin/seguimiento-candidatas/casos/{case_id}/tomar")
    assert take.status_code == 200
    with flask_app.app_context():
        ev = (
            SeguimientoCandidataEvento.query
            .filter_by(caso_id=case_id, event_type="case_taken")
            .order_by(SeguimientoCandidataEvento.id.desc())
            .first()
        )
        assert ev is not None
        assert ev.note == "takeover"
        takeover_event = (
            DomainOutbox.query
            .filter_by(aggregate_type="SeguimientoCandidataCaso", aggregate_id=str(case_id), event_type="staff.case_tracking.taken")
            .order_by(DomainOutbox.id.desc())
            .first()
        )
        assert takeover_event is not None


def test_isla_seguimiento_visibilidad_positiva_en_rutas_internas_clave():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    paths = [
        "/admin/seguimiento-candidatas/cola",
        "/admin/solicitudes",
        "/buscar",
        "/finalizar_proceso",
        "/referencias",
    ]
    for path in paths:
        html = _render_base_as(path, role="admin", authenticated=True)
        assert "seg-candidatas-island" in html, path
        assert "segCandidatasDrawer" in html, path


def test_isla_seguimiento_visibilidad_negativa_en_publico_cliente_y_login_root():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    hidden_for_staff_paths = ["/clientes/ayuda", "/cliente/demo", "/login", "/"]
    for path in hidden_for_staff_paths:
        html = _render_base_as(path, role="admin", authenticated=True)
        assert 'class="seg-candidatas-island' not in html, path
        assert 'id="segCandidatasDrawer"' not in html, path

    html_non_staff = _render_base_as("/admin/solicitudes", role="cliente", authenticated=True)
    assert 'class="seg-candidatas-island' not in html_non_staff
    assert 'id="segCandidatasDrawer"' not in html_non_staff
    assert "seguimiento_candidatas_island.js" not in html_non_staff


def test_isla_seguimiento_contrato_html_y_carga_condicional_js():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    html = _render_base_as("/admin/solicitudes", role="secretaria", authenticated=True)

    assert 'class="seg-candidatas-island' in html
    assert 'id="segCandidatasDrawer"' in html
    assert 'id="segCandidatasBackdrop"' in html
    assert 'aria-controls="segCandidatasDrawer"' in html
    assert 'role="dialog"' in html
    assert 'href="/admin/seguimiento-candidatas/cola"' in html
    assert "js/core/seguimiento_candidatas_island.js" in html

    html_home_staff = _render_base_as("/home", role="secretaria", authenticated=True)
    assert "js/core/seguimiento_candidatas_island.js" in html_home_staff


def test_js_isla_seguimiento_maneja_error_y_no_navega_en_click_isla():
    js = Path("static/js/core/seguimiento_candidatas_island.js").read_text(encoding="utf-8", errors="ignore")
    assert "showError(" in js
    assert "No se pudo cargar seguimiento de candidatas" in js
    assert "btn.addEventListener(\"click\"" in js
    assert "openDrawer();" in js
    assert "window.location" not in js


def test_isla_copiar_formulario_cliente_visible_solo_owner_admin():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    html_owner = _render_base_as("/admin/solicitudes", role="owner", authenticated=True)
    assert 'id="clientPublicMessageIslandBtn"' in html_owner
    assert "js/core/client_public_form_message.js" in html_owner
    assert "js/core/client_public_message_island.js" in html_owner

    html_admin = _render_base_as("/admin/solicitudes", role="admin", authenticated=True)
    assert 'id="clientPublicMessageIslandBtn"' in html_admin
    assert "js/core/client_public_form_message.js" in html_admin
    assert "js/core/client_public_message_island.js" in html_admin

    html_secretaria = _render_base_as("/admin/solicitudes", role="secretaria", authenticated=True)
    assert 'id="clientPublicMessageIslandBtn"' not in html_secretaria
    assert "js/core/client_public_form_message.js" not in html_secretaria
    assert "js/core/client_public_message_island.js" not in html_secretaria


def test_js_isla_copiar_formulario_cliente_tiene_bloqueo_anti_doble_click():
    js = Path("static/js/core/client_public_message_island.js").read_text(encoding="utf-8", errors="ignore")
    assert "var inFlight = false;" in js
    assert "var cooldownUntilMs = 0;" in js
    assert "if (inFlight) return;" in js
    assert "if (Date.now() < cooldownUntilMs) return;" in js
    assert "Generando enlace..." in js
    assert "Mensaje copiado" in js
    assert "No se pudo copiar" in js
    helper_js = Path("static/js/core/client_public_form_message.js").read_text(encoding="utf-8", errors="ignore")
    assert "Este es el formulario de Doméstica del Cibao A&D para registrar tu solicitud." in helper_js
    assert "Ahí puedes colocar tus datos y lo que necesitas, para poder ayudarte mejor." in helper_js
    assert "Cuando lo completes, envíame tu nombre y dime que ya terminaste." in helper_js
    assert "Hola, gracias por comunicarte con Doméstica del Cibao A&D." not in helper_js
