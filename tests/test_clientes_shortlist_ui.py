# -*- coding: utf-8 -*-

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import unquote

from sqlalchemy.exc import SQLAlchemyError

from app import app as flask_app
import clientes.routes as clientes_routes


class _SolicitudQueryOne:
    def __init__(self, solicitud):
        self.solicitud = solicitud

    def filter_by(self, **kwargs):
        return self

    def first_or_404(self):
        return self.solicitud


class _SolicitudCandidataQuery:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return self.rows


class _SolicitudCreateQuery:
    def filter_by(self, **kwargs):
        return self

    def count(self):
        return 0

    def first(self):
        return None


class _SolicitudFailingQuery:
    def filter(self, *args, **kwargs):
        return self

    def count(self):
        raise SQLAlchemyError("forced-db-error")


def _unwrap_cliente_view(fn):
    out = fn
    for _ in range(5):
        if not hasattr(out, "__wrapped__"):
            break
        out = out.__wrapped__
    return out


def _make_solicitud(**overrides):
    now = datetime(2026, 4, 10, 10, 0, 0)
    data = {
        "id": 10,
        "cliente_id": 7,
        "codigo_solicitud": "SOL-010",
        "fecha_solicitud": now - timedelta(days=3),
        "estado": "proceso",
        "estado_actual_desde": now - timedelta(days=1),
        "fecha_ultimo_estado": now - timedelta(hours=8),
        "fecha_cambio_espera_pago": None,
        "fecha_inicio_seguimiento": None,
        "fecha_cancelacion": None,
        "motivo_cancelacion": None,
        "tipo_plan": "premium",
        "abono": None,
        "candidata": None,
        "candidata_id": None,
        "reemplazos": [],
        "compat_test_cliente_json": None,
        "compat_test_cliente": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _shortlist_item(cid: int, name: str) -> dict:
    return {
        "item_id": cid * 10,
        "candidata": {
            "id": cid,
            "nombre": name,
            "edad": "30",
            "modalidad": "salida diaria",
        },
        "score_final": 82,
        "compatibility_badge": {"label": "Compatibilidad media", "tone": "warning"},
        "ubicacion_resumen": "Santiago",
        "experiencia_resumen": "5 años de experiencia en limpieza y cocina",
        "perfil_foto_data_url": "data:image/png;base64,AAAA",
        "reasons": [
            "La modalidad de trabajo es compatible con lo que solicitaste.",
            "El horario disponible coincide con tus necesidades.",
            "Tiene experiencia en funciones clave: limpieza, cocina.",
        ],
    }


class _FakeRecommendationService:
    def __init__(self, *, shortlist_payload=None, validate_calls=None, validate_ok=True):
        self.shortlist_payload = shortlist_payload or {"state": {"code": "pending", "message": ""}, "items": []}
        self.validate_calls = validate_calls if validate_calls is not None else []
        self.validate_ok = bool(validate_ok)

    def get_active_shortlist(self, solicitud_id, include_ineligible=False):
        return self.shortlist_payload

    def validate_client_selection(self, *, solicitud_id, candidata_id, selected_by):
        self.validate_calls.append(int(candidata_id))
        if self.validate_ok:
            return {"ok": True, "code": "valid", "message": "ok"}
        return {"ok": False, "code": "candidate_hard_failed", "message": "invalid"}


def test_build_shortlist_vm_states_and_visibility():
    ready_vm = clientes_routes._build_shortlist_view_model(
        {
            "state": {"code": "ready", "message": "Shortlist disponible."},
            "run": {"counts": {"eligible_count": 6}},
            "items": [
                _shortlist_item(101, "Ana"),
                _shortlist_item(102, "Luz"),
                _shortlist_item(103, "Marta"),
                _shortlist_item(104, "Carla"),
                _shortlist_item(105, "Sara"),
                _shortlist_item(106, "Diana"),
            ],
        }
    )
    assert ready_vm["state_code"] == "ready"
    assert len(ready_vm["visible_cards"]) == 5
    assert len(ready_vm["extra_cards"]) == 0
    assert ready_vm["hidden_eligible_count"] == 1
    assert ready_vm["shortlist_limit"] == 5
    assert str(ready_vm["visible_cards"][0]["profile_photo_url"] or "").startswith("data:image/png;base64,")
    assert ready_vm["visible_cards"][0]["location"] == "Santiago"

    legacy_vm = clientes_routes._build_shortlist_view_model(
        {
            "state": {"code": "ready", "message": "Shortlist disponible."},
            "run": {"counts": {"eligible_count": 1}},
            "items": [
                {
                    **_shortlist_item(201, "Noelia"),
                    "ubicacion_resumen": "Santiago · Sectores cercanos: villa, maria",
                },
            ],
        }
    )
    assert legacy_vm["visible_cards"][0]["location"] == "Santiago"

    empty_vm = clientes_routes._build_shortlist_view_model({"state": {"code": "ready", "message": "ok"}, "items": []})
    assert empty_vm["state_code"] == "empty"

    pending_vm = clientes_routes._build_shortlist_view_model({"state": {"code": "pending", "message": "espera"}, "items": []})
    assert pending_vm["state_code"] == "pending"

    error_vm = clientes_routes._build_shortlist_view_model({"state": {"code": "error", "message": "fallo"}, "items": []})
    assert error_vm["state_code"] == "error"

    stale_vm = clientes_routes._build_shortlist_view_model({"state": {"code": "stale", "message": "old"}, "items": []})
    assert stale_vm["state_code"] == "stale"


def test_nueva_solicitud_redirects_to_recomendaciones_after_create():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    fake_user = SimpleNamespace(
        id=7,
        is_authenticated=True,
        role="cliente",
        codigo="CL-007",
        total_solicitudes=0,
        fecha_ultima_solicitud=None,
        fecha_ultima_actividad=None,
    )
    target = _unwrap_cliente_view(clientes_routes.nueva_solicitud)
    fake_form = SimpleNamespace(
        areas_comunes=SimpleNamespace(choices=[], data=[]),
        funciones=SimpleNamespace(data=[]),
        edad_requerida=SimpleNamespace(choices=[], data=[]),
        dos_pisos=SimpleNamespace(data=False),
        pasaje_aporte=SimpleNamespace(data=False),
        modalidad_trabajo=SimpleNamespace(data=""),
        area_otro=SimpleNamespace(data=""),
        nota_cliente=SimpleNamespace(data=""),
        sueldo=SimpleNamespace(data=""),
    )
    fake_form.validate_on_submit = lambda: True
    fake_form.populate_obj = lambda _obj: None

    def _db_add_side_effect(obj):
        if getattr(obj, "codigo_solicitud", None) and int(getattr(obj, "id", 0) or 0) <= 0:
            setattr(obj, "id", 123)

    with flask_app.app_context():
        with patch.object(clientes_routes, "current_user", fake_user), \
             patch.object(clientes_routes, "SolicitudForm", return_value=fake_form), \
             patch.object(clientes_routes.Solicitud, "query", _SolicitudCreateQuery()), \
             patch.object(clientes_routes, "compose_codigo_solicitud", return_value="CL-007-A"), \
             patch.object(clientes_routes, "enforce_business_limit", return_value=(False, {})), \
             patch.object(clientes_routes, "enforce_min_human_interval", return_value=(False, {})), \
             patch.object(clientes_routes, "_cliente_active_solicitudes_count", return_value=0), \
             patch.object(clientes_routes, "_prevent_double_post", return_value=True), \
             patch.object(clientes_routes, "_cache_ok", return_value=False), \
             patch.object(clientes_routes, "_emit_cliente_outbox_event", return_value=None), \
             patch.object(clientes_routes, "_clear_cliente_solicitud_draft", return_value=None), \
             patch.object(clientes_routes, "flash", return_value=None), \
             patch.object(clientes_routes.db.session, "add", side_effect=_db_add_side_effect), \
             patch.object(clientes_routes.db.session, "flush", return_value=None), \
             patch.object(clientes_routes.db.session, "commit", return_value=None), \
             patch.object(clientes_routes, "_trigger_recommendation_generation_safe", return_value=None) as trigger_mock:
            with flask_app.test_request_context("/clientes/solicitudes/nueva", method="POST", data={"wizard_step": "1"}):
                resp = target()

    assert resp.status_code == 302
    assert "/clientes/solicitudes/123/recomendaciones" in (resp.location or "")
    trigger_mock.assert_called_once()
    assert trigger_mock.call_args.kwargs.get("solicitud_id") == 123


def test_nueva_solicitud_blocks_when_cliente_has_4_in_process():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    fake_user = SimpleNamespace(
        id=7,
        is_authenticated=True,
        role="cliente",
        codigo="CL-007",
        total_solicitudes=0,
        fecha_ultima_solicitud=None,
        fecha_ultima_actividad=None,
    )
    target = _unwrap_cliente_view(clientes_routes.nueva_solicitud)
    fake_form = SimpleNamespace(
        areas_comunes=SimpleNamespace(choices=[], data=[]),
        funciones=SimpleNamespace(data=[]),
        edad_requerida=SimpleNamespace(choices=[], data=[]),
        dos_pisos=SimpleNamespace(data=False),
        pasaje_aporte=SimpleNamespace(data=False),
        modalidad_trabajo=SimpleNamespace(data=""),
        area_otro=SimpleNamespace(data=""),
        nota_cliente=SimpleNamespace(data=""),
        sueldo=SimpleNamespace(data=""),
    )
    fake_form.validate_on_submit = lambda: True
    fake_form.populate_obj = lambda _obj: None

    with flask_app.app_context():
        with patch.object(clientes_routes, "current_user", fake_user), \
             patch.object(clientes_routes, "SolicitudForm", return_value=fake_form), \
             patch.object(clientes_routes.Solicitud, "query", _SolicitudCreateQuery()), \
             patch.object(clientes_routes, "compose_codigo_solicitud", return_value="CL-007-A"), \
             patch.object(clientes_routes, "enforce_business_limit", return_value=(False, {})), \
             patch.object(clientes_routes, "enforce_min_human_interval", return_value=(False, {})), \
             patch.object(clientes_routes, "_cliente_active_solicitudes_count", return_value=4), \
             patch.object(clientes_routes, "_prevent_double_post", return_value=True), \
             patch.object(clientes_routes, "_cache_ok", return_value=False), \
             patch.object(clientes_routes, "flash", return_value=None) as flash_mock, \
             patch.object(clientes_routes.db.session, "add", return_value=None), \
             patch.object(clientes_routes.db.session, "flush", return_value=None), \
             patch.object(clientes_routes.db.session, "commit", return_value=None), \
             patch.object(clientes_routes, "_trigger_recommendation_generation_safe", return_value=None):
            with flask_app.test_request_context("/clientes/solicitudes/nueva", method="POST", data={"wizard_step": "1"}):
                resp = target()

    assert resp.status_code == 302
    assert "/clientes/solicitudes" in (resp.location or "")
    flash_mock.assert_any_call("Ya tienes 4 solicitudes en proceso. Gestiona una para crear una nueva.", "warning")


def test_cliente_active_solicitudes_count_rollback_on_sqlalchemy_error():
    with flask_app.app_context():
        with patch.object(clientes_routes.Solicitud, "query", _SolicitudFailingQuery()), \
             patch.object(clientes_routes.db.session, "rollback", return_value=None) as rollback_mock:
            count = clientes_routes._cliente_active_solicitudes_count(7)

    assert count == 0
    assert rollback_mock.call_count >= 1


def test_nueva_solicitud_rolls_back_on_non_sql_error_during_create():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    fake_user = SimpleNamespace(
        id=7,
        is_authenticated=True,
        role="cliente",
        codigo="CL-007",
        total_solicitudes=0,
        fecha_ultima_solicitud=None,
        fecha_ultima_actividad=None,
    )
    target = _unwrap_cliente_view(clientes_routes.nueva_solicitud)
    fake_form = SimpleNamespace(
        areas_comunes=SimpleNamespace(choices=[], data=[]),
        funciones=SimpleNamespace(data=[]),
        edad_requerida=SimpleNamespace(choices=[], data=[]),
        dos_pisos=SimpleNamespace(data=False),
        pasaje_aporte=SimpleNamespace(data=False),
        modalidad_trabajo=SimpleNamespace(data=""),
        area_otro=SimpleNamespace(data=""),
        nota_cliente=SimpleNamespace(data=""),
        sueldo=SimpleNamespace(data=""),
    )
    fake_form.validate_on_submit = lambda: True
    fake_form.populate_obj = lambda _obj: None

    def _db_add_side_effect(obj):
        if getattr(obj, "codigo_solicitud", None) and int(getattr(obj, "id", 0) or 0) <= 0:
            setattr(obj, "id", 123)

    with flask_app.app_context():
        with patch.object(clientes_routes, "current_user", fake_user), \
             patch.object(clientes_routes, "SolicitudForm", return_value=fake_form), \
             patch.object(clientes_routes.Solicitud, "query", _SolicitudCreateQuery()), \
             patch.object(clientes_routes, "compose_codigo_solicitud", return_value="CL-007-A"), \
             patch.object(clientes_routes, "enforce_business_limit", return_value=(False, {})), \
             patch.object(clientes_routes, "enforce_min_human_interval", return_value=(False, {})), \
             patch.object(clientes_routes, "_cliente_active_solicitudes_count", return_value=0), \
             patch.object(clientes_routes, "_prevent_double_post", return_value=True), \
             patch.object(clientes_routes, "_cache_ok", return_value=False), \
             patch.object(clientes_routes, "_emit_cliente_outbox_event", side_effect=RuntimeError("forced-non-sql-error")), \
             patch.object(clientes_routes, "flash", return_value=None), \
             patch.object(clientes_routes, "render_template", return_value="ok"), \
             patch.object(clientes_routes.db.session, "add", side_effect=_db_add_side_effect), \
             patch.object(clientes_routes.db.session, "flush", return_value=None), \
             patch.object(clientes_routes.db.session, "commit", return_value=None), \
             patch.object(clientes_routes.db.session, "rollback", return_value=None) as rollback_mock:
            with flask_app.test_request_context("/clientes/solicitudes/nueva", method="POST", data={"wizard_step": "1"}):
                resp = target()

    assert resp == "ok"
    assert rollback_mock.call_count >= 1


def test_trigger_recommendation_generation_safe_rolls_back_on_error():
    with flask_app.app_context():
        with patch.object(
            clientes_routes,
            "SolicitudRecommendationService",
            return_value=SimpleNamespace(request_generation=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))),
        ), patch.object(clientes_routes.db.session, "rollback", return_value=None) as rollback_mock:
            clientes_routes._trigger_recommendation_generation_safe(
                solicitud_id=10,
                trigger_source="cliente_portal_create",
                requested_by="cliente:7",
            )

    assert rollback_mock.call_count >= 1


def test_detalle_solicitud_includes_shortlist_ready_vm():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    fake_user = SimpleNamespace(id=7, is_authenticated=True)
    solicitud = _make_solicitud()
    detalle_target = _unwrap_cliente_view(clientes_routes.detalle_solicitud)
    fake_service = _FakeRecommendationService(
        shortlist_payload={
            "state": {"code": "ready", "message": "Shortlist disponible."},
            "run": {"counts": {"eligible_count": 4}},
            "items": [
                _shortlist_item(101, "Ana"),
                _shortlist_item(102, "Luz"),
                _shortlist_item(103, "Marta"),
                _shortlist_item(104, "Carla"),
            ],
        }
    )

    with flask_app.app_context():
        with patch.object(clientes_routes, "current_user", fake_user), \
             patch.object(clientes_routes.Solicitud, "query", _SolicitudQueryOne(solicitud)), \
             patch.object(clientes_routes.SolicitudCandidata, "query", _SolicitudCandidataQuery([])), \
             patch.object(clientes_routes, "SolicitudRecommendationService", return_value=fake_service), \
             patch.object(clientes_routes, "_chat_enabled", return_value=True), \
             patch.object(clientes_routes, "render_template", side_effect=lambda template, **ctx: {"template": template, "ctx": ctx}):
            with flask_app.test_request_context("/clientes/solicitudes/10", method="GET"):
                out = detalle_target(10)

    shortlist_vm = out["ctx"]["shortlist_vm"]
    assert shortlist_vm["state_code"] == "ready"
    assert len(shortlist_vm["visible_cards"]) == 4
    assert len(shortlist_vm["extra_cards"]) == 0
    assert out["template"] == "clientes/solicitud_detail.html"


def test_detalle_solicitud_includes_shortlist_pending_and_error():
    flask_app.config["TESTING"] = True
    fake_user = SimpleNamespace(id=7, is_authenticated=True)
    solicitud = _make_solicitud()
    detalle_target = _unwrap_cliente_view(clientes_routes.detalle_solicitud)

    with flask_app.app_context():
        with patch.object(clientes_routes, "current_user", fake_user), \
             patch.object(clientes_routes.Solicitud, "query", _SolicitudQueryOne(solicitud)), \
             patch.object(clientes_routes.SolicitudCandidata, "query", _SolicitudCandidataQuery([])), \
             patch.object(clientes_routes, "_chat_enabled", return_value=True), \
             patch.object(clientes_routes, "render_template", side_effect=lambda template, **ctx: {"template": template, "ctx": ctx}):

            with patch.object(
                clientes_routes,
                "SolicitudRecommendationService",
                return_value=_FakeRecommendationService(shortlist_payload={"state": {"code": "pending", "message": "progreso"}, "items": []}),
            ):
                with flask_app.test_request_context("/clientes/solicitudes/10", method="GET"):
                    out_pending = detalle_target(10)
                assert out_pending["ctx"]["shortlist_vm"]["state_code"] == "pending"

            with patch.object(
                clientes_routes,
                "SolicitudRecommendationService",
                return_value=_FakeRecommendationService(shortlist_payload={"state": {"code": "error", "message": "fallo"}, "items": []}),
            ):
                with flask_app.test_request_context("/clientes/solicitudes/10", method="GET"):
                    out_error = detalle_target(10)
                assert out_error["ctx"]["shortlist_vm"]["state_code"] == "error"


def test_detalle_solicitud_includes_shortlist_selection_for_channel_cta():
    flask_app.config["TESTING"] = True
    fake_user = SimpleNamespace(id=7, is_authenticated=True)
    solicitud = _make_solicitud()
    detalle_target = _unwrap_cliente_view(clientes_routes.detalle_solicitud)

    with flask_app.app_context():
        with patch.object(clientes_routes, "current_user", fake_user), \
             patch.object(clientes_routes.Solicitud, "query", _SolicitudQueryOne(solicitud)), \
             patch.object(clientes_routes.SolicitudCandidata, "query", _SolicitudCandidataQuery([])), \
             patch.object(clientes_routes, "_chat_enabled", return_value=True), \
             patch.object(clientes_routes, "_get_saved_shortlist_selection_summary", return_value={"count": 2, "candidate_ids": [101, 102], "candidate_names": ["Ana", "Luz"], "selection_fingerprint": "fp-1"}), \
             patch.object(clientes_routes, "SolicitudRecommendationService", return_value=_FakeRecommendationService(shortlist_payload={"state": {"code": "ready", "message": "ok"}, "items": [_shortlist_item(101, "Ana")]})), \
             patch.object(clientes_routes, "render_template", side_effect=lambda template, **ctx: {"template": template, "ctx": ctx}):
            with flask_app.test_request_context("/clientes/solicitudes/10", method="GET"):
                out = detalle_target(10)

    selection = out["ctx"]["shortlist_selection"]
    assert int(selection["count"]) == 2
    assert selection["candidate_names"] == ["Ana", "Luz"]


def test_build_shortlist_channel_messages_include_request_and_candidates():
    solicitud = _make_solicitud(codigo_solicitud="SOL-ABC")
    chat_msg = clientes_routes._build_shortlist_chat_message(
        solicitud=solicitud,
        candidate_names=["Ana", "Luz"],
    )
    wa_msg = clientes_routes._build_shortlist_whatsapp_message(
        solicitud=solicitud,
        candidate_names=["Ana", "Luz"],
    )
    assert "SOL-ABC" in chat_msg
    assert "Ana, Luz" in chat_msg
    assert "continuar por este chat" in chat_msg.lower()
    assert "SOL-ABC" in wa_msg
    assert "Ana, Luz" in wa_msg


def test_shortlist_continue_chat_redirects_to_conversation_with_intent_message():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    fake_user = SimpleNamespace(id=7, is_authenticated=True)
    solicitud = _make_solicitud()
    continue_target = _unwrap_cliente_view(clientes_routes.solicitud_shortlist_continue_chat)
    conv = SimpleNamespace(id=333)

    with flask_app.app_context():
        with patch.object(clientes_routes, "current_user", fake_user), \
             patch.object(clientes_routes, "_chat_enabled", return_value=True), \
             patch.object(clientes_routes, "_get_solicitud_for_shortlist_or_403", return_value=solicitud), \
             patch.object(clientes_routes, "_get_saved_shortlist_selection_summary", return_value={"count": 2, "candidate_ids": [101, 102], "candidate_names": ["Ana", "Luz"], "selection_fingerprint": "fp-1"}), \
             patch.object(clientes_routes, "_chat_get_or_create_conversation_for_cliente", return_value=conv), \
             patch.object(clientes_routes, "_post_shortlist_intent_message_to_chat", return_value={"ok": True, "created": True, "duplicate": False}), \
             patch.object(clientes_routes.db.session, "commit", return_value=None):
            with flask_app.test_request_context(
                "/clientes/solicitudes/10/shortlist/continue/chat",
                method="POST",
                data={},
            ):
                resp = continue_target(10)

    assert resp.status_code == 302
    assert "conversation_id=333" in (resp.location or "")


def test_shortlist_continue_whatsapp_builds_prefilled_message():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    prev_phone = flask_app.config.get("SUPPORT_WHATSAPP_NUMBER")
    flask_app.config["SUPPORT_WHATSAPP_NUMBER"] = "15551234567"
    fake_user = SimpleNamespace(id=7, is_authenticated=True)
    solicitud = _make_solicitud(codigo_solicitud="SOL-010")
    continue_target = _unwrap_cliente_view(clientes_routes.solicitud_shortlist_continue_whatsapp)

    try:
        with flask_app.app_context():
            with patch.object(clientes_routes, "current_user", fake_user), \
                 patch.object(clientes_routes, "_get_solicitud_for_shortlist_or_403", return_value=solicitud), \
                 patch.object(clientes_routes, "_get_saved_shortlist_selection_summary", return_value={"count": 2, "candidate_ids": [101, 102], "candidate_names": ["Ana", "Luz"], "selection_fingerprint": "fp-1"}):
                with flask_app.test_request_context(
                    "/clientes/solicitudes/10/shortlist/continue/whatsapp",
                    method="POST",
                    data={},
                ):
                    resp = continue_target(10)

        assert resp.status_code == 302
        assert "https://wa.me/15551234567?text=" in (resp.location or "")
        decoded = unquote(resp.location or "")
        assert "SOL-010" in decoded
        assert "Ana, Luz" in decoded
    finally:
        if prev_phone is None:
            flask_app.config.pop("SUPPORT_WHATSAPP_NUMBER", None)
        else:
            flask_app.config["SUPPORT_WHATSAPP_NUMBER"] = prev_phone


def test_shortlist_continue_chat_accepts_selection_in_same_request():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    fake_user = SimpleNamespace(id=7, is_authenticated=True)
    solicitud = _make_solicitud()
    continue_target = _unwrap_cliente_view(clientes_routes.solicitud_shortlist_continue_chat)
    conv = SimpleNamespace(id=333)
    captured = {}

    def _capture_chat_intent(**kwargs):
        captured["candidate_ids"] = list(kwargs.get("candidate_ids") or [])
        captured["candidate_names"] = list(kwargs.get("candidate_names") or [])
        return {"ok": True, "created": True, "duplicate": False}

    with flask_app.app_context():
        with patch.object(clientes_routes, "current_user", fake_user), \
             patch.object(clientes_routes, "_chat_enabled", return_value=True), \
             patch.object(clientes_routes, "_get_solicitud_for_shortlist_or_403", return_value=solicitud), \
             patch.object(clientes_routes, "_get_saved_shortlist_selection_summary", return_value={"count": 0, "candidate_ids": [], "candidate_names": [], "selection_fingerprint": ""}), \
             patch.object(clientes_routes, "_shortlist_validate_and_persist_selection", return_value={"requested_count": 2, "valid_count": 2, "invalid_count": 0, "valid_ids": [101, 102], "shortlist_blocked": False}), \
             patch.object(clientes_routes, "_shortlist_candidate_names", return_value=["Ana", "Luz"]), \
             patch.object(clientes_routes, "_chat_get_or_create_conversation_for_cliente", return_value=conv), \
             patch.object(clientes_routes, "_post_shortlist_intent_message_to_chat", side_effect=_capture_chat_intent), \
             patch.object(clientes_routes.db.session, "commit", return_value=None):
            with flask_app.test_request_context(
                "/clientes/solicitudes/10/shortlist/continue/chat",
                method="POST",
                data={"candidata_ids": ["101", "102"]},
            ):
                resp = continue_target(10)

    assert resp.status_code == 302
    assert "conversation_id=333" in (resp.location or "")
    assert captured["candidate_ids"] == [101, 102]
    assert captured["candidate_names"] == ["Ana", "Luz"]


def test_shortlist_continue_whatsapp_accepts_selection_in_same_request():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    prev_phone = flask_app.config.get("SUPPORT_WHATSAPP_NUMBER")
    flask_app.config["SUPPORT_WHATSAPP_NUMBER"] = "15551234567"
    fake_user = SimpleNamespace(id=7, is_authenticated=True)
    solicitud = _make_solicitud(codigo_solicitud="SOL-010")
    continue_target = _unwrap_cliente_view(clientes_routes.solicitud_shortlist_continue_whatsapp)

    try:
        with flask_app.app_context():
            with patch.object(clientes_routes, "current_user", fake_user), \
                 patch.object(clientes_routes, "_get_solicitud_for_shortlist_or_403", return_value=solicitud), \
                 patch.object(clientes_routes, "_get_saved_shortlist_selection_summary", return_value={"count": 0, "candidate_ids": [], "candidate_names": [], "selection_fingerprint": ""}), \
                 patch.object(clientes_routes, "_shortlist_validate_and_persist_selection", return_value={"requested_count": 1, "valid_count": 1, "invalid_count": 0, "valid_ids": [101], "shortlist_blocked": False}), \
                 patch.object(clientes_routes, "_shortlist_candidate_names", return_value=["Ana"]):
                with flask_app.test_request_context(
                    "/clientes/solicitudes/10/shortlist/continue/whatsapp",
                    method="POST",
                    data={"candidata_ids": ["101"]},
                ):
                    resp = continue_target(10)

        assert resp.status_code == 302
        decoded = unquote(resp.location or "")
        assert "https://wa.me/15551234567?text=" in (resp.location or "")
        assert "SOL-010" in decoded
        assert "Ana" in decoded
    finally:
        if prev_phone is None:
            flask_app.config.pop("SUPPORT_WHATSAPP_NUMBER", None)
        else:
            flask_app.config["SUPPORT_WHATSAPP_NUMBER"] = prev_phone


def test_shortlist_continue_channel_without_selection_redirects_back():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    fake_user = SimpleNamespace(id=7, is_authenticated=True)
    solicitud = _make_solicitud()
    chat_target = _unwrap_cliente_view(clientes_routes.solicitud_shortlist_continue_chat)
    wa_target = _unwrap_cliente_view(clientes_routes.solicitud_shortlist_continue_whatsapp)

    with flask_app.app_context():
        with patch.object(clientes_routes, "current_user", fake_user), \
             patch.object(clientes_routes, "_chat_enabled", return_value=True), \
             patch.object(clientes_routes, "_get_solicitud_for_shortlist_or_403", return_value=solicitud), \
             patch.object(clientes_routes, "_get_saved_shortlist_selection_summary", return_value={"count": 0, "candidate_ids": [], "candidate_names": [], "selection_fingerprint": ""}):
            with flask_app.test_request_context("/clientes/solicitudes/10/shortlist/continue/chat", method="POST", data={}):
                resp_chat = chat_target(10)
            with flask_app.test_request_context("/clientes/solicitudes/10/shortlist/continue/whatsapp", method="POST", data={}):
                resp_wa = wa_target(10)

    assert resp_chat.status_code == 302
    assert "shortlist-recomendaciones" in (resp_chat.location or "")
    assert resp_wa.status_code == 302
    assert "shortlist-recomendaciones" in (resp_wa.location or "")


def test_shortlist_intent_duplicate_detection_uses_fingerprint():
    recent = [
        SimpleNamespace(meta={"kind": "shortlist_selection_intent", "solicitud_id": 10, "selection_fingerprint": "abc"}),
        SimpleNamespace(meta={"kind": "other_kind", "solicitud_id": 10, "selection_fingerprint": "abc"}),
    ]
    assert clientes_routes._shortlist_intent_is_duplicate(
        recent_messages=recent,
        solicitud_id=10,
        selection_fingerprint="abc",
    ) is True
    assert clientes_routes._shortlist_intent_is_duplicate(
        recent_messages=recent,
        solicitud_id=10,
        selection_fingerprint="zzz",
    ) is False


def test_shortlist_submit_selection_multiple_records_intent():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    fake_user = SimpleNamespace(id=7, is_authenticated=True)
    solicitud = _make_solicitud()
    submit_target = _unwrap_cliente_view(clientes_routes.solicitud_shortlist_submit_selection)
    calls = []
    fake_service = _FakeRecommendationService(
        shortlist_payload={"state": {"code": "ready", "message": "ok"}, "items": [_shortlist_item(101, "Ana"), _shortlist_item(102, "Luz")]},
        validate_calls=calls,
        validate_ok=True,
    )

    with flask_app.app_context():
        with patch.object(clientes_routes, "current_user", fake_user), \
             patch.object(clientes_routes, "_get_solicitud_for_shortlist_or_403", return_value=solicitud), \
             patch.object(clientes_routes, "SolicitudRecommendationService", return_value=fake_service):
            with flask_app.test_request_context(
                "/clientes/solicitudes/10/shortlist/select",
                method="POST",
                data={"candidata_ids": ["101", "102", "101"]},
            ):
                resp = submit_target(10)

    assert resp.status_code == 302
    assert "shortlist-recomendaciones" in (resp.location or "")
    assert calls == [101, 102]


def test_shortlist_submit_selection_pending_is_blocked():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    fake_user = SimpleNamespace(id=7, is_authenticated=True)
    solicitud = _make_solicitud()
    submit_target = _unwrap_cliente_view(clientes_routes.solicitud_shortlist_submit_selection)
    calls = []
    fake_service = _FakeRecommendationService(
        shortlist_payload={"state": {"code": "pending", "message": "progreso"}, "items": []},
        validate_calls=calls,
        validate_ok=True,
    )

    with flask_app.app_context():
        with patch.object(clientes_routes, "current_user", fake_user), \
             patch.object(clientes_routes, "_get_solicitud_for_shortlist_or_403", return_value=solicitud), \
             patch.object(clientes_routes, "SolicitudRecommendationService", return_value=fake_service):
            with flask_app.test_request_context(
                "/clientes/solicitudes/10/shortlist/select",
                method="POST",
                data={"candidata_ids": ["101"]},
            ):
                resp = submit_target(10)

    assert resp.status_code == 302
    assert calls == []


def test_trigger_recommendation_generation_safe_uses_async_request():
    flask_app.config["TESTING"] = True
    captured = {}

    class _Svc:
        def request_generation(self, solicitud_id, **kwargs):
            captured["solicitud_id"] = int(solicitud_id)
            captured["kwargs"] = dict(kwargs)
            return None

    with flask_app.app_context():
        with patch.object(clientes_routes, "SolicitudRecommendationService", return_value=_Svc()):
            clientes_routes._trigger_recommendation_generation_safe(
                solicitud_id=10,
                trigger_source="pytest",
                requested_by="pytest:user",
            )

    assert captured["solicitud_id"] == 10
    assert captured["kwargs"].get("synchronous") is False
    assert captured["kwargs"].get("dispatch_async") is True


def test_shortlist_endpoint_sets_polling_headers_for_pending():
    flask_app.config["TESTING"] = True
    fake_user = SimpleNamespace(id=7, is_authenticated=True)
    solicitud = _make_solicitud()
    endpoint = _unwrap_cliente_view(clientes_routes.solicitud_shortlist)
    fake_service = _FakeRecommendationService(
        shortlist_payload={"state": {"code": "pending", "message": "progreso"}, "items": []},
    )

    with flask_app.app_context():
        with patch.object(clientes_routes, "current_user", fake_user), \
             patch.object(clientes_routes, "_get_solicitud_for_shortlist_or_403", return_value=solicitud), \
             patch.object(clientes_routes, "SolicitudRecommendationService", return_value=fake_service):
            with flask_app.test_request_context("/clientes/solicitudes/10/shortlist", method="GET"):
                response, status_code = endpoint(10)

    assert status_code == 200
    assert response.headers.get("Retry-After")
    assert response.headers.get("X-Shortlist-Poll-Base-Ms")
    assert response.headers.get("X-Shortlist-Poll-Max-Attempts")


def test_shortlist_endpoint_returns_500_on_error():
    flask_app.config["TESTING"] = True
    fake_user = SimpleNamespace(id=7, is_authenticated=True)
    solicitud = _make_solicitud()
    endpoint = _unwrap_cliente_view(clientes_routes.solicitud_shortlist)
    fake_service = _FakeRecommendationService(
        shortlist_payload={"state": {"code": "error", "message": "fallo"}, "items": []},
    )

    with flask_app.app_context():
        with patch.object(clientes_routes, "current_user", fake_user), \
             patch.object(clientes_routes, "_get_solicitud_for_shortlist_or_403", return_value=solicitud), \
             patch.object(clientes_routes, "SolicitudRecommendationService", return_value=fake_service):
            with flask_app.test_request_context("/clientes/solicitudes/10/shortlist", method="GET"):
                _response, status_code = endpoint(10)

    assert status_code == 500


def test_detalle_solicitud_shortlist_view_increments_usage_counter():
    flask_app.config["TESTING"] = True
    fake_user = SimpleNamespace(id=7, is_authenticated=True)
    solicitud = _make_solicitud()
    detalle_target = _unwrap_cliente_view(clientes_routes.detalle_solicitud)
    fake_service = _FakeRecommendationService(
        shortlist_payload={"state": {"code": "ready", "message": "ok"}, "items": [_shortlist_item(101, "Ana")]},
    )

    with flask_app.app_context():
        with patch.object(clientes_routes, "current_user", fake_user), \
             patch.object(clientes_routes.Solicitud, "query", _SolicitudQueryOne(solicitud)), \
             patch.object(clientes_routes.SolicitudCandidata, "query", _SolicitudCandidataQuery([])), \
             patch.object(clientes_routes, "SolicitudRecommendationService", return_value=fake_service), \
             patch.object(clientes_routes, "_chat_enabled", return_value=True), \
             patch.object(clientes_routes, "_rec_obs_counter", return_value=1) as counter_mock, \
             patch.object(clientes_routes, "_rec_obs_event", return_value=None), \
             patch.object(clientes_routes, "render_template", side_effect=lambda template, **ctx: {"template": template, "ctx": ctx}):
            with flask_app.test_request_context("/clientes/solicitudes/10", method="GET"):
                _ = detalle_target(10)

    called_metrics = [str(c.args[0]) for c in counter_mock.call_args_list if c.args]
    assert "rec:usage:shortlist_view_count" in called_metrics


def test_shortlist_submit_selection_updates_usage_counters():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    fake_user = SimpleNamespace(id=7, is_authenticated=True)
    solicitud = _make_solicitud()
    submit_target = _unwrap_cliente_view(clientes_routes.solicitud_shortlist_submit_selection)
    fake_service = _FakeRecommendationService(
        shortlist_payload={"state": {"code": "ready", "message": "ok"}, "items": [_shortlist_item(101, "Ana")]},
        validate_ok=True,
    )

    with flask_app.app_context():
        with patch.object(clientes_routes, "current_user", fake_user), \
             patch.object(clientes_routes, "_get_solicitud_for_shortlist_or_403", return_value=solicitud), \
             patch.object(clientes_routes, "SolicitudRecommendationService", return_value=fake_service), \
             patch.object(clientes_routes, "_rec_obs_counter", return_value=1) as counter_mock, \
             patch.object(clientes_routes, "_rec_obs_event", return_value=None):
            with flask_app.test_request_context(
                "/clientes/solicitudes/10/shortlist/select",
                method="POST",
                data={"candidata_ids": ["101"]},
            ):
                resp = submit_target(10)

    assert resp.status_code == 302
    called_metrics = [str(c.args[0]) for c in counter_mock.call_args_list if c.args]
    assert "rec:usage:selection_submit_count" in called_metrics
    assert "rec:usage:selected_candidates_sum" in called_metrics


def test_shortlist_continue_whatsapp_updates_usage_counter():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    fake_user = SimpleNamespace(id=7, is_authenticated=True)
    solicitud = _make_solicitud(codigo_solicitud="SOL-010")
    target = _unwrap_cliente_view(clientes_routes.solicitud_shortlist_continue_whatsapp)

    with flask_app.app_context():
        with patch.object(clientes_routes, "current_user", fake_user), \
             patch.object(clientes_routes, "_get_solicitud_for_shortlist_or_403", return_value=solicitud), \
             patch.object(clientes_routes, "_get_saved_shortlist_selection_summary", return_value={"count": 1, "candidate_ids": [101], "candidate_names": ["Ana"], "selection_fingerprint": "fp-1"}), \
             patch.object(clientes_routes, "_rec_obs_counter", return_value=1) as counter_mock, \
             patch.object(clientes_routes, "_rec_obs_event", return_value=None):
            with flask_app.test_request_context(
                "/clientes/solicitudes/10/shortlist/continue/whatsapp",
                method="POST",
                data={},
            ):
                resp = target(10)

    assert resp.status_code == 302
    called_metrics = [str(c.args[0]) for c in counter_mock.call_args_list if c.args]
    assert "rec:usage:continue_whatsapp_count" in called_metrics


def test_public_shortlist_submit_selection_with_temporary_access():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    fake_user = SimpleNamespace(id=0, is_authenticated=False, role=None)
    solicitud = _make_solicitud()
    submit_target = _unwrap_cliente_view(clientes_routes.solicitud_shortlist_submit_selection)
    calls = []
    fake_service = _FakeRecommendationService(
        shortlist_payload={"state": {"code": "ready", "message": "ok"}, "items": [_shortlist_item(101, "Ana")]},
        validate_calls=calls,
        validate_ok=True,
    )

    with flask_app.app_context():
        with patch.object(clientes_routes, "current_user", fake_user), \
             patch.object(clientes_routes.Solicitud, "query", _SolicitudQueryOne(solicitud)), \
             patch.object(clientes_routes, "SolicitudRecommendationService", return_value=fake_service):
            with flask_app.test_request_context(
                "/clientes/solicitudes/10/shortlist/select",
                method="POST",
                data={"candidata_ids": ["101"]},
            ):
                clientes_routes.session[clientes_routes._PUBLIC_RECOMMENDATION_ACCESS_SESSION_KEY] = [10]
                resp = submit_target(10)

    assert resp.status_code == 302
    assert "shortlist-recomendaciones" in (resp.location or "")
    assert calls == [101]


def test_public_shortlist_continue_whatsapp_with_temporary_access():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    prev_phone = flask_app.config.get("SUPPORT_WHATSAPP_NUMBER")
    flask_app.config["SUPPORT_WHATSAPP_NUMBER"] = "15551234567"
    fake_user = SimpleNamespace(id=0, is_authenticated=False, role=None)
    solicitud = _make_solicitud(codigo_solicitud="SOL-010")
    target = _unwrap_cliente_view(clientes_routes.solicitud_shortlist_continue_whatsapp)

    try:
        with flask_app.app_context():
            with patch.object(clientes_routes, "current_user", fake_user), \
                 patch.object(clientes_routes.Solicitud, "query", _SolicitudQueryOne(solicitud)), \
                 patch.object(clientes_routes, "_get_saved_shortlist_selection_summary", return_value={"count": 1, "candidate_ids": [101], "candidate_names": ["Ana"], "selection_fingerprint": "fp-1"}):
                with flask_app.test_request_context(
                    "/clientes/solicitudes/10/shortlist/continue/whatsapp",
                    method="POST",
                    data={},
                ):
                    clientes_routes.session[clientes_routes._PUBLIC_RECOMMENDATION_ACCESS_SESSION_KEY] = [10]
                    resp = target(10)

        assert resp.status_code == 302
        assert "https://wa.me/15551234567?text=" in (resp.location or "")
        assert "SOL-010" in unquote(resp.location or "")
    finally:
        if prev_phone is None:
            flask_app.config.pop("SUPPORT_WHATSAPP_NUMBER", None)
        else:
            flask_app.config["SUPPORT_WHATSAPP_NUMBER"] = prev_phone
