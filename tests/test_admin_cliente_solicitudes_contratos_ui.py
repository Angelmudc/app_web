# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import admin.routes as admin_routes


def _login_owner(client):
    return client.post("/admin/login", data={"usuario": "Owner", "clave": "admin123"}, follow_redirects=False)


class _ListQueryStub:
    def __init__(self, rows):
        self.rows = list(rows)

    def options(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def filter_by(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self.rows)


class _SingleQueryStub:
    def __init__(self, row):
        self.row = row

    def options(self, *_args, **_kwargs):
        return self

    def filter_by(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def first_or_404(self):
        return self.row

    def first(self):
        return self.row

    def all(self):
        return [self.row] if self.row is not None else []


def _build_solicitud(sol_id: int, estado: str = "proceso"):
    return SimpleNamespace(
        id=sol_id,
        cliente_id=7,
        codigo_solicitud=f"SOL-{sol_id}",
        estado=estado,
        fecha_solicitud=datetime(2026, 3, 20, 10, 0, 0),
        ciudad_sector="Santiago",
        tipo_servicio="DOMESTICA_LIMPIEZA",
        modalidad_trabajo="Con dormida",
        tipo_plan="premium",
        candidata_id=None,
        candidata=None,
        reemplazos=[],
        last_copiado_at=None,
        monto_pagado="",
        fecha_ultima_modificacion=None,
        fecha_cancelacion=None,
        motivo_cancelacion="",
    )


def test_cliente_detail_lista_muestra_estado_contrato_y_acciones_por_solicitud():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    cliente = SimpleNamespace(
        id=7,
        nombre_completo="Cliente Contratos",
        codigo="CL-007",
        fecha_registro=datetime(2026, 3, 1, 9, 0, 0),
        email="demo@test.com",
        telefono="8090000000",
        ciudad="Santiago",
        sector="Centro",
        notas_admin="",
        fecha_ultima_actividad=None,
    )

    s0 = _build_solicitud(10)  # sin contrato
    s1 = _build_solicitud(11)  # borrador
    s2 = _build_solicitud(12)  # enviado
    s3 = _build_solicitud(13)  # firmado
    s4 = _build_solicitud(14)  # expirado
    s5 = _build_solicitud(15)  # anulado

    now = datetime.utcnow()
    contratos = [
        SimpleNamespace(id=201, solicitud_id=11, version=1, estado="borrador", enviado_at=None, firmado_at=None, token_expira_at=None, pdf_final_size_bytes=None, anulado_at=None, anulado_motivo=None),
        SimpleNamespace(id=202, solicitud_id=12, version=2, estado="enviado", enviado_at=now, firmado_at=None, token_expira_at=now + timedelta(days=1), pdf_final_size_bytes=None, anulado_at=None, anulado_motivo=None),
        SimpleNamespace(id=203, solicitud_id=13, version=3, estado="firmado", enviado_at=now - timedelta(days=2), firmado_at=now - timedelta(days=1), token_expira_at=now + timedelta(days=1), pdf_final_size_bytes=1024, anulado_at=None, anulado_motivo=None),
        SimpleNamespace(id=204, solicitud_id=14, version=1, estado="enviado", enviado_at=now - timedelta(days=4), firmado_at=None, token_expira_at=now - timedelta(hours=2), pdf_final_size_bytes=None, anulado_at=None, anulado_motivo=None),
        SimpleNamespace(id=205, solicitud_id=15, version=1, estado="anulado", enviado_at=now - timedelta(days=3), firmado_at=None, token_expira_at=now + timedelta(days=1), pdf_final_size_bytes=None, anulado_at=now - timedelta(days=1), anulado_motivo="Cliente solicitó anulación"),
    ]

    with client.session_transaction() as sess:
        sess["contract_links"] = {"202": "https://app.test/contratos/f/abc"}

    with flask_app.app_context():
        with patch.object(admin_routes.Cliente, "query", SimpleNamespace(get_or_404=lambda _cid: cliente)), \
             patch.object(admin_routes.Solicitud, "query", _ListQueryStub([s0, s1, s2, s3, s4, s5])), \
             patch.object(admin_routes.ContratoDigital, "query", _ListQueryStub(contratos)), \
             patch.object(admin_routes.TareaCliente, "query", _ListQueryStub([])), \
             patch("admin.routes._active_reemplazo_for_solicitud", return_value=None):
            resp = client.get("/admin/clientes/7", follow_redirects=False)

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert 'data-testid="contract-state-10"' in html
    assert 'data-testid="contract-action-10-create"' in html

    assert 'data-testid="contract-action-11-refresh"' in html
    assert 'data-testid="contract-action-11-send"' in html

    assert 'data-testid="contract-action-12-copy"' in html
    assert 'data-testid="contract-action-12-reissue"' in html
    assert 'data-testid="contract-action-12-annul"' in html

    assert 'data-testid="contract-action-13-view"' in html
    assert 'data-testid="contract-action-13-pdf"' in html

    assert 'data-testid="contract-action-14-reissue"' in html
    assert 'data-testid="contract-action-15-detail"' in html

    # No mezcla entre solicitudes: firmado no debe mostrar acciones de enviado.
    assert 'data-testid="contract-action-13-annul"' not in html
    assert 'action="/admin/contratos/solicitudes/11/borrador/ui" class="d-inline"' in html
    assert 'action="/admin/contratos/202/enviar/ui" class="d-inline"' in html
    assert 'action="/admin/contratos/202/anular/ui" class="d-inline"' in html
    assert 'data-admin-async-form' in html
    assert 'data-async-target="#clienteSolicitudesAsyncRegion"' in html
    assert 'data-async-busy-container="#clienteSolicitudesAsyncScope"' in html
    assert 'data-async-preserve-scroll="true"' in html
    assert 'data-async-fallback="native"' in html
    assert 'name="_async_target" value="#clienteSolicitudesAsyncRegion"' in html


def test_detalle_solicitud_inyecta_estado_y_snapshot_de_contrato():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    solicitud = _build_solicitud(77)
    solicitud.cliente_id = 7
    solicitud.candidata = None
    solicitud.reemplazos = []

    contrato = SimpleNamespace(
        id=901,
        solicitud_id=77,
        cliente_id=7,
        version=4,
        estado="enviado",
        snapshot_fijado_at=None,
        token_expira_at=datetime.utcnow() + timedelta(days=1),
        enviado_at=datetime.utcnow(),
        primer_visto_at=None,
        firmado_at=None,
        pdf_final_size_bytes=None,
        contenido_snapshot_json={"tipo_servicio": "DOMESTICA_LIMPIEZA", "modalidad_trabajo": "Con dormida", "ciudad_sector": "Santiago"},
        anulado_at=None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    captured = {}

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured.update(ctx)
        return "OK"

    with flask_app.app_context():
        with patch.object(admin_routes.Solicitud, "query", _SingleQueryStub(solicitud)), \
             patch.object(admin_routes.ContratoDigital, "query", _SingleQueryStub(contrato)), \
             patch("admin.routes.build_resumen_cliente_solicitud", return_value="Resumen"), \
             patch("admin.routes._pasaje_copy_phrase_from_solicitud", return_value=""), \
             patch("admin.routes.render_template", side_effect=_fake_render):
            resp = client.get("/admin/clientes/7/solicitudes/77", follow_redirects=False)

    assert resp.status_code == 200
    assert captured.get("template") == "admin/solicitud_detail.html"
    assert captured.get("contract_effective_state") == "enviado"
    assert "Tipo:" in (captured.get("contract_snapshot_summary") or "")
    assert captured.get("latest_contract").id == 901
    assert isinstance(captured.get("contract_history"), list)
    assert captured.get("contract_history")[0]["contract"].id == 901

    with open("templates/admin/solicitud_detail.html", "r", encoding="utf-8") as fh:
        tpl = fh.read()
    assert "_solicitud_contract_block.html" in tpl

    with open("templates/admin/_solicitud_contract_block.html", "r", encoding="utf-8") as fh:
        contract_tpl = fh.read()
    assert "Contrato digital" in contract_tpl
    assert "Historial de contratos" in contract_tpl
    assert 'data-testid="solicitud-contract-state"' in contract_tpl
    assert "Ver detalle y eventos" in contract_tpl
