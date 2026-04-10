# -*- coding: utf-8 -*-

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

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


def _unwrap_cliente_view(fn):
    out = fn
    for _ in range(2):  # login_required + cliente_required
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
        "ubicacion_resumen": "Santiago · Sectores cercanos: villa, maria",
        "experiencia_resumen": "5 años de experiencia en limpieza y cocina",
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
            "items": [
                _shortlist_item(101, "Ana"),
                _shortlist_item(102, "Luz"),
                _shortlist_item(103, "Marta"),
                _shortlist_item(104, "Carla"),
            ],
        }
    )
    assert ready_vm["state_code"] == "ready"
    assert len(ready_vm["visible_cards"]) == 3
    assert len(ready_vm["extra_cards"]) == 1

    empty_vm = clientes_routes._build_shortlist_view_model({"state": {"code": "ready", "message": "ok"}, "items": []})
    assert empty_vm["state_code"] == "empty"

    pending_vm = clientes_routes._build_shortlist_view_model({"state": {"code": "pending", "message": "espera"}, "items": []})
    assert pending_vm["state_code"] == "pending"

    error_vm = clientes_routes._build_shortlist_view_model({"state": {"code": "error", "message": "fallo"}, "items": []})
    assert error_vm["state_code"] == "error"

    stale_vm = clientes_routes._build_shortlist_view_model({"state": {"code": "stale", "message": "old"}, "items": []})
    assert stale_vm["state_code"] == "stale"


def test_detalle_solicitud_includes_shortlist_ready_vm():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    fake_user = SimpleNamespace(id=7, is_authenticated=True)
    solicitud = _make_solicitud()
    detalle_target = _unwrap_cliente_view(clientes_routes.detalle_solicitud)
    fake_service = _FakeRecommendationService(
        shortlist_payload={
            "state": {"code": "ready", "message": "Shortlist disponible."},
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
    assert len(shortlist_vm["visible_cards"]) == 3
    assert len(shortlist_vm["extra_cards"]) == 1
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
             patch.object(clientes_routes, "_get_solicitud_cliente_or_404", return_value=solicitud), \
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
             patch.object(clientes_routes, "_get_solicitud_cliente_or_404", return_value=solicitud), \
             patch.object(clientes_routes, "SolicitudRecommendationService", return_value=fake_service):
            with flask_app.test_request_context(
                "/clientes/solicitudes/10/shortlist/select",
                method="POST",
                data={"candidata_ids": ["101"]},
            ):
                resp = submit_target(10)

    assert resp.status_code == 302
    assert calls == []
