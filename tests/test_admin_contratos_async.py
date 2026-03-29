# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import contratos.routes as contratos_routes


def _login_owner(client):
    return client.post("/admin/login", data={"usuario": "Owner", "clave": "admin123"}, follow_redirects=False)


class _QueryStub:
    def __init__(self, rows):
        self.rows = list(rows)
        self._offset = 0
        self._limit = None

    def options(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def filter_by(self, **kwargs):
        if "id" in kwargs:
            rid = int(kwargs["id"])
            return _QueryStub([r for r in self.rows if int(getattr(r, "id", 0) or 0) == rid])
        if "solicitud_id" in kwargs:
            sid = int(kwargs["solicitud_id"])
            return _QueryStub([r for r in self.rows if int(getattr(r, "solicitud_id", 0) or 0) == sid])
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def offset(self, n):
        self._offset = int(n or 0)
        return self

    def limit(self, n):
        self._limit = int(n)
        return self

    def count(self):
        return len(self.rows)

    def all(self):
        rows = self.rows[self._offset:]
        if self._limit is not None:
            rows = rows[:self._limit]
        return list(rows)

    def first_or_404(self):
        return self.rows[0]

    def first(self):
        return self.rows[0] if self.rows else None


class _SolicitudQueryStub:
    def __init__(self, solicitud):
        self._solicitud = solicitud

    def get_or_404(self, _id):
        return self._solicitud


class _ContratoByIdStub:
    def __init__(self, contrato):
        self._contrato = contrato

    def get_or_404(self, _id):
        return self._contrato


def _async_headers():
    return {
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-Admin-Async": "1",
    }


def _contrato_stub(*, contrato_id=333, solicitud_id=77, estado="borrador"):
    now = datetime.utcnow()
    return SimpleNamespace(
        id=contrato_id,
        solicitud_id=solicitud_id,
        cliente_id=7,
        version=2,
        estado=estado,
        snapshot_fijado_at=now,
        enviado_at=now,
        primer_visto_at=None,
        ultimo_visto_at=None,
        firmado_at=None,
        token_expira_at=now + timedelta(days=1),
        pdf_final_size_bytes=None,
        contenido_snapshot_json={"tipo_servicio": "DOMESTICA_LIMPIEZA", "modalidad_trabajo": "Con dormida", "ciudad_sector": "Santiago"},
        anulado_at=None,
        anulado_motivo=None,
        updated_at=now,
    )


def test_contratos_list_async_devuelve_region_parcial():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    contrato = _contrato_stub(contrato_id=101, solicitud_id=22, estado="enviado")
    with patch("contratos.routes._contract_admin_base_query", return_value=_QueryStub([contrato])):
        resp = client.get("/admin/contratos?q=101&estado=enviado&page=1&per_page=10", headers=_async_headers(), follow_redirects=False)

    assert resp.status_code == 200
    data = resp.get_json() or {}
    assert data.get("success") is True
    assert data.get("update_target") == "#contratosAsyncRegion"
    assert "101" in (data.get("replace_html") or "")


def test_contratos_list_muestra_estado_expirado_por_fecha():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    contrato = _contrato_stub(contrato_id=102, solicitud_id=22, estado="enviado")
    contrato.token_expira_at = datetime.utcnow() - timedelta(hours=2)
    with patch("contratos.routes._contract_admin_base_query", return_value=_QueryStub([contrato])):
        resp = client.get("/admin/contratos?page=1&per_page=10", headers=_async_headers(), follow_redirects=False)

    assert resp.status_code == 200
    data = resp.get_json() or {}
    html = data.get("replace_html") or ""
    assert "expirado" in html


def test_contrato_detail_enviar_async_actualiza_region_local_detalle():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    contrato = _contrato_stub(contrato_id=333, solicitud_id=77, estado="borrador")
    evento = SimpleNamespace(
        id=1,
        contrato_id=333,
        evento_tipo="CONTRATO_ENVIADO",
        estado_anterior="borrador",
        estado_nuevo="enviado",
        actor_tipo="staff",
        actor_staff_id=1,
        success=True,
        error_code=None,
        metadata_json={},
        created_at=datetime.utcnow(),
    )

    with flask_app.app_context():
        with patch.object(contratos_routes.ContratoDigital, "query", _ContratoByIdStub(contrato)), \
             patch("contratos.routes._send_or_reissue_contract", return_value={"ok": True, "link": "https://app.test/contratos/f/abc"}), \
             patch("contratos.routes._contract_admin_base_query", return_value=_QueryStub([contrato])), \
             patch.object(contratos_routes.ContratoEvento, "query", _QueryStub([evento])):
            resp = client.post(
                "/admin/contratos/333/enviar/ui",
                data={"next": "/admin/contratos/333/detalle"},
                headers=_async_headers(),
                follow_redirects=False,
            )

    assert resp.status_code == 200
    data = resp.get_json() or {}
    assert data.get("success") is True
    assert data.get("update_target") == "#contratoDetailAsyncRegion"
    assert "Eventos recientes" in (data.get("replace_html") or "")


def test_solicitud_detail_enviar_async_actualiza_bloque_contrato():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    contrato = _contrato_stub(contrato_id=334, solicitud_id=88, estado="enviado")
    solicitud = SimpleNamespace(id=88, cliente_id=7)

    with flask_app.app_context():
        with patch.object(contratos_routes.ContratoDigital, "query", _ContratoByIdStub(contrato)), \
             patch("contratos.routes._send_or_reissue_contract", return_value={"ok": True, "link": "https://app.test/contratos/f/xyz"}), \
             patch("contratos.routes._contract_admin_base_query", return_value=_QueryStub([contrato])), \
             patch.object(contratos_routes.Solicitud, "query", _SolicitudQueryStub(solicitud)):
            resp = client.post(
                "/admin/contratos/334/enviar/ui",
                data={"next": "/admin/clientes/7/solicitudes/88"},
                headers=_async_headers(),
                follow_redirects=False,
            )

    assert resp.status_code == 200
    data = resp.get_json() or {}
    assert data.get("success") is True
    assert data.get("update_target") == "#solicitudContratoAsyncRegion"
    assert "Historial de contratos" in (data.get("replace_html") or "")


def test_cliente_detail_enviar_async_actualiza_region_cliente_solicitudes():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    contrato = _contrato_stub(contrato_id=336, solicitud_id=92, estado="enviado")

    with flask_app.app_context():
        with patch.object(contratos_routes.ContratoDigital, "query", _ContratoByIdStub(contrato)), \
             patch("contratos.routes._send_or_reissue_contract", return_value={"ok": True, "link": "https://app.test/contratos/f/c1"}):
            resp = client.post(
                "/admin/contratos/336/enviar/ui",
                data={"next": "/admin/clientes/7#sol-92"},
                headers=_async_headers(),
                follow_redirects=False,
            )

    assert resp.status_code == 200
    data = resp.get_json() or {}
    assert data.get("success") is True
    assert data.get("update_target") == "#clienteSolicitudesAsyncRegion"
    assert data.get("redirect_url") == "/admin/clientes/7#sol-92"


def test_enviar_async_error_negocio_devuelve_200_con_mensaje_humano():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    contrato = _contrato_stub(contrato_id=335, solicitud_id=91, estado="enviado")

    with flask_app.app_context():
        with patch.object(contratos_routes.ContratoDigital, "query", _ContratoByIdStub(contrato)), \
             patch("contratos.routes._send_or_reissue_contract", return_value={"ok": False, "error": "another_active_contract_exists"}), \
             patch("contratos.routes._contract_admin_base_query", return_value=_QueryStub([contrato])), \
             patch.object(contratos_routes.ContratoEvento, "query", _QueryStub([])):
            resp = client.post(
                "/admin/contratos/335/enviar/ui",
                data={"next": "/admin/contratos/335/detalle"},
                headers=_async_headers(),
                follow_redirects=False,
            )

    assert resp.status_code == 200
    data = resp.get_json() or {}
    assert data.get("success") is False
    assert data.get("error_code") == "another_active_contract_exists"
    assert "existe otra versión activa" in (data.get("message") or "").lower()
    assert data.get("update_target") == "#contratoDetailAsyncRegion"


def test_enviar_ui_fallback_clasico_se_mantiene_redirect():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    contrato = _contrato_stub(contrato_id=400, solicitud_id=90, estado="borrador")

    with flask_app.app_context():
        with patch.object(contratos_routes.ContratoDigital, "query", _ContratoByIdStub(contrato)), \
             patch("contratos.routes._send_or_reissue_contract", return_value={"ok": True, "link": "https://app.test/contratos/f/r1"}):
            resp = client.post(
                "/admin/contratos/400/enviar/ui",
                data={"next": "/admin/contratos/400/detalle"},
                follow_redirects=False,
            )

    assert resp.status_code in (302, 303)
    assert "/admin/contratos/400/detalle" in (resp.location or "")


def test_enviar_ui_fallback_clasico_desde_cliente_detail_se_mantiene_redirect():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    contrato = _contrato_stub(contrato_id=401, solicitud_id=93, estado="borrador")

    with flask_app.app_context():
        with patch.object(contratos_routes.ContratoDigital, "query", _ContratoByIdStub(contrato)), \
             patch("contratos.routes._send_or_reissue_contract", return_value={"ok": True, "link": "https://app.test/contratos/f/r2"}):
            resp = client.post(
                "/admin/contratos/401/enviar/ui",
                data={"next": "/admin/clientes/7#sol-93"},
                follow_redirects=False,
            )

    assert resp.status_code in (302, 303)
    assert "/admin/clientes/7#sol-93" in (resp.location or "")
