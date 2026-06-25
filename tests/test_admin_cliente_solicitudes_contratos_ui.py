# -*- coding: utf-8 -*-

from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import unquote_plus

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
        payment_cycle_plan="premium",
        payment_cycle_precio_total=Decimal("5000.00"),
        payment_cycle_abono_requerido=Decimal("2500.00"),
        payment_cycle_estado="pendiente",
        fecha_ultima_modificacion=None,
        fecha_cancelacion=None,
        motivo_cancelacion="",
    )


def _kpi_stub(total_solicitudes: int = 0):
    return SimpleNamespace(
        total_solicitudes=total_solicitudes,
        estados=SimpleNamespace(
            pagada=0,
            activa=0,
            proceso=total_solicitudes,
            reemplazo=0,
            cancelada=0,
            otro=0,
        ),
        monto_total_pagado_str="RD$ 0.00",
        primera_solicitud=None,
        ultima_actividad=None,
    )


def test_build_whatsapp_activation_ctx_generates_rd_link_and_message():
    solicitud = _build_solicitud(25, estado="proceso")
    cliente = SimpleNamespace(nombre_completo="Laura Gomez", telefono="(809) 555-1212")

    with patch("admin.routes.solicitud_puede_registrar_pago", return_value=True):
        ctx = admin_routes._build_whatsapp_activation_ctx(
            solicitud,
            cliente=cliente,
            payment_ctx={
                "plan_price": Decimal("5000.00"),
                "required_deposit": Decimal("2500.00"),
            },
        )

    assert ctx["enabled"] is True
    assert ctx["href"].startswith("https://wa.me/18095551212?text=")
    assert "Hola" not in ctx["message"]
    assert "Laura Gomez" not in ctx["message"]
    assert "SOL-25" not in ctx["message"]
    assert "Premium" not in ctx["message"]
    assert "RD$2500.00" in ctx["message"]
    assert "Su solicitud ya está lista para iniciar la gestión." in ctx["message"]
    assert "¿Le envío los datos para el pago?" in ctx["message"]


def test_build_whatsapp_activation_ctx_disabled_without_valid_phone():
    solicitud = _build_solicitud(26, estado="proceso")
    cliente = SimpleNamespace(nombre_completo="Cliente Sin Telefono", telefono="123")

    with patch("admin.routes.solicitud_puede_registrar_pago", return_value=True):
        ctx = admin_routes._build_whatsapp_activation_ctx(
            solicitud,
            cliente=cliente,
            payment_ctx={
                "plan_price": Decimal("5000.00"),
                "required_deposit": Decimal("2500.00"),
            },
        )

    assert ctx["enabled"] is False
    assert "teléfono válido" in ctx["title"]


def test_build_whatsapp_activation_ctx_accepts_explicit_international_plus_phone():
    solicitud = _build_solicitud(260, estado="proceso")
    cliente = SimpleNamespace(nombre_completo="Cliente Espana", telefono="+34 612 345 678")

    with patch("admin.routes.solicitud_puede_registrar_pago", return_value=True):
        ctx = admin_routes._build_whatsapp_activation_ctx(
            solicitud,
            cliente=cliente,
            payment_ctx={
                "plan_price": Decimal("5000.00"),
                "required_deposit": Decimal("2500.00"),
            },
        )

    assert ctx["enabled"] is True
    assert ctx["phone_e164"] == "+34612345678"
    assert ctx["phone_digits"] == "34612345678"
    assert ctx["href"].startswith("https://wa.me/34612345678?text=")


def test_build_whatsapp_activation_ctx_accepts_double_zero_international_phone():
    solicitud = _build_solicitud(261, estado="proceso")
    cliente = SimpleNamespace(nombre_completo="Cliente Mexico", telefono="0034 612 345 678")

    with patch("admin.routes.solicitud_puede_registrar_pago", return_value=True):
        ctx = admin_routes._build_whatsapp_activation_ctx(
            solicitud,
            cliente=cliente,
            payment_ctx={
                "plan_price": Decimal("5000.00"),
                "required_deposit": Decimal("2500.00"),
            },
        )

    assert ctx["enabled"] is True
    assert ctx["phone_e164"] == "+34612345678"
    assert ctx["phone_digits"] == "34612345678"
    assert ctx["href"].startswith("https://wa.me/34612345678?text=")


def test_build_whatsapp_activation_ctx_normalizes_dominican_10_digit_phone_and_encodes_message():
    solicitud = _build_solicitud(27, estado="proceso")
    cliente = SimpleNamespace(nombre_completo="Ana Perez", telefono="8095559999")

    with patch("admin.routes.solicitud_puede_registrar_pago", return_value=True):
        ctx = admin_routes._build_whatsapp_activation_ctx(
            solicitud,
            cliente=cliente,
            payment_ctx={
                "plan_price": Decimal("7200.00"),
                "required_deposit": Decimal("3600.00"),
            },
        )

    assert ctx["enabled"] is True
    assert ctx["phone_digits"] == "18095559999"
    assert ctx["href"].startswith("https://wa.me/18095559999?text=")
    encoded_message = ctx["href"].split("?text=", 1)[1]
    assert unquote_plus(encoded_message) == ctx["message"]
    assert "Hola" not in ctx["message"]
    assert "Ana Perez" not in ctx["message"]
    assert "SOL-27" not in ctx["message"]
    assert "Premium" not in ctx["message"]
    assert "RD$7200.00" not in ctx["message"]
    assert "50%" in ctx["message"]
    assert "RD$3600.00" in ctx["message"]
    assert "lista para iniciar la gestión" in ctx["message"]
    assert "Luego de recibirlo, comenzaremos con la gestión de su solicitud." in ctx["message"]


def test_build_whatsapp_activation_ctx_disabled_outside_proceso():
    solicitud = _build_solicitud(28, estado="activa")
    cliente = SimpleNamespace(nombre_completo="Cliente Activo", telefono="8095551111")

    with patch("admin.routes.solicitud_puede_registrar_pago", return_value=True):
        ctx = admin_routes._build_whatsapp_activation_ctx(
            solicitud,
            cliente=cliente,
            payment_ctx={
                "plan_price": Decimal("5000.00"),
                "required_deposit": Decimal("2500.00"),
            },
        )

    assert ctx["enabled"] is False
    assert "solicitudes en proceso" in ctx["title"]
    assert any("proceso" in reason for reason in ctx["missing_reasons"])


def test_build_whatsapp_activation_ctx_disabled_without_valid_plan_or_amounts():
    solicitud = _build_solicitud(29, estado="proceso")
    solicitud.tipo_plan = "plan-raro"
    solicitud.payment_cycle_plan = "plan-raro"
    cliente = SimpleNamespace(nombre_completo="Cliente Sin Plan", telefono="8095552222")

    with patch("admin.routes.solicitud_puede_registrar_pago", return_value=True):
        ctx = admin_routes._build_whatsapp_activation_ctx(
            solicitud,
            cliente=cliente,
            payment_ctx={
                "plan_price": Decimal("0.00"),
                "required_deposit": Decimal("0.00"),
            },
        )

    assert ctx["enabled"] is False
    assert "plan válido" in ctx["title"]
    assert "abono inicial" in ctx["title"]
    assert any("plan válido" in reason for reason in ctx["missing_reasons"])
    assert any("abono inicial" in reason for reason in ctx["missing_reasons"])


def test_build_whatsapp_activation_ctx_disabled_with_garbage_phone_explains_reason():
    solicitud = _build_solicitud(291, estado="proceso")
    cliente = SimpleNamespace(nombre_completo="Cliente Basura", telefono="abc xyz")

    with patch("admin.routes.solicitud_puede_registrar_pago", return_value=True):
        ctx = admin_routes._build_whatsapp_activation_ctx(
            solicitud,
            cliente=cliente,
            payment_ctx={
                "plan_price": Decimal("5000.00"),
                "required_deposit": Decimal("2500.00"),
            },
        )

    assert ctx["enabled"] is False
    assert ctx["phone_e164"] is None
    assert any("teléfono válido" in reason for reason in ctx["missing_reasons"])


def test_build_whatsapp_activation_ctx_is_pure_and_does_not_mutate_state_or_register_payment():
    solicitud = _build_solicitud(30, estado="proceso")
    solicitud.estado = "proceso"
    solicitud.payment_cycle_estado = "pendiente"
    cliente = SimpleNamespace(nombre_completo="Cliente Inmutable", telefono="8095553333")

    before_estado = solicitud.estado
    before_payment_state = solicitud.payment_cycle_estado

    with patch("admin.routes.solicitud_puede_registrar_pago", return_value=True), \
         patch.object(admin_routes.db.session, "commit") as commit_mock:
        ctx = admin_routes._build_whatsapp_activation_ctx(
            solicitud,
            cliente=cliente,
            payment_ctx={
                "plan_price": Decimal("5000.00"),
                "required_deposit": Decimal("2500.00"),
            },
        )

    assert ctx["enabled"] is True
    assert solicitud.estado == before_estado
    assert solicitud.payment_cycle_estado == before_payment_state
    commit_mock.assert_not_called()


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
             patch("admin.routes.solicitud_puede_registrar_pago", return_value=True), \
             patch("admin.routes._build_payment_summary_ctx", return_value={"plan_price": Decimal("5000.00"), "required_deposit": Decimal("2500.00")}), \
             patch("admin.routes._build_cliente_summary_kpi", return_value=_kpi_stub(6)), \
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
    assert 'data-testid="cliente-solicitud-whatsapp-activation-10"' in html
    assert "https://wa.me/18090000000?text=" in html


def test_detalle_solicitud_inyecta_estado_y_snapshot_de_contrato():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    solicitud = _build_solicitud(77)
    solicitud.cliente_id = 7
    solicitud.cliente = SimpleNamespace(nombre_completo="Cliente Contratos", telefono="809-555-7788")
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
             patch("admin.routes._pasaje_operativo_phrase_from_solicitud", return_value=""), \
             patch("admin.routes._build_payment_summary_ctx", return_value={"plan_price": Decimal("5000.00"), "required_deposit": Decimal("2500.00")}), \
             patch("admin.routes.solicitud_puede_registrar_pago", return_value=True), \
             patch("admin.routes.render_template", side_effect=_fake_render):
            resp = client.get("/admin/clientes/7/solicitudes/77", follow_redirects=False)

    assert resp.status_code == 200
    assert captured.get("template") == "admin/solicitud_detail.html"
    assert captured["whatsapp_activation"]["enabled"] is True
    assert captured["whatsapp_activation"]["href"].startswith("https://wa.me/18095557788?text=")
    with open("templates/admin/solicitud_detail.html", "r", encoding="utf-8") as fh:
        tpl = fh.read()
    assert "solicitudDetailHeavyAsyncRegion" in tpl
    assert "data-admin-lazy-fragment-url" in tpl
    assert "solicitud_detail_heavy_fragment" in tpl

    with open("templates/admin/_solicitud_contract_block.html", "r", encoding="utf-8") as fh:
        contract_tpl = fh.read()
    assert "Contrato digital" in contract_tpl
    assert "Historial de contratos" in contract_tpl
    assert 'data-testid="solicitud-contract-state"' in contract_tpl
    assert "Ver detalle y eventos" in contract_tpl


def test_detalle_solicitud_renderiza_cta_sin_commit_ni_cambios_de_estado():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    solicitud = _build_solicitud(88, estado="proceso")
    solicitud.cliente_id = 7
    solicitud.cliente = SimpleNamespace(nombre_completo="Cliente Final", telefono="809-555-8899")
    solicitud.candidata = None
    solicitud.reemplazos = []
    estado_original = solicitud.estado

    captured = {}

    def _fake_render(template_name, **ctx):
        captured["template"] = template_name
        captured.update(ctx)
        return "OK"

    with flask_app.app_context():
        with patch.object(admin_routes.Solicitud, "query", _SingleQueryStub(solicitud)), \
             patch("admin.routes.build_resumen_cliente_solicitud", return_value="Resumen"), \
             patch("admin.routes._pasaje_operativo_phrase_from_solicitud", return_value=""), \
             patch("admin.routes._build_payment_summary_ctx", return_value={"plan_price": Decimal("5000.00"), "required_deposit": Decimal("2500.00")}), \
             patch("admin.routes.solicitud_puede_registrar_pago", return_value=True), \
             patch("admin.routes.crear_pago_solicitud") as crear_pago_mock, \
             patch("admin.routes.open_new_payment_cycle") as open_cycle_mock, \
             patch("admin.routes.recalcular_estado_pago_solicitud") as recalc_mock, \
             patch("admin.routes.render_template", side_effect=_fake_render):
            resp = client.get("/admin/clientes/7/solicitudes/88", follow_redirects=False)

    assert resp.status_code == 200
    assert captured.get("template") == "admin/solicitud_detail.html"
    assert captured["whatsapp_activation"]["enabled"] is True
    assert captured["whatsapp_activation"]["href"].startswith("https://wa.me/18095558899?text=")
    assert solicitud.estado == estado_original
    crear_pago_mock.assert_not_called()
    open_cycle_mock.assert_not_called()
    recalc_mock.assert_not_called()
