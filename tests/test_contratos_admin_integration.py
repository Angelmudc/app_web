# -*- coding: utf-8 -*-

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import contratos.routes as contratos_routes
import contratos.services as contratos_services


def _login_owner(client):
    return client.post("/admin/login", data={"usuario": "Owner", "clave": "admin123"}, follow_redirects=False)


class _QueryStub:
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

    def offset(self, *_args, **_kwargs):
        return self

    def limit(self, n):
        self._limit = int(n)
        return self

    def count(self):
        return len(self.rows)

    def all(self):
        return list(self.rows)

    def first_or_404(self):
        return self.rows[0]

    def get_or_404(self, _id):
        return self.rows[0]


def test_admin_contratos_listado_renderiza_y_muestra_estado():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    contrato = SimpleNamespace(
        id=101,
        solicitud_id=22,
        cliente_id=7,
        version=2,
        estado="enviado",
        enviado_at=None,
        primer_visto_at=None,
        firmado_at=None,
        token_expira_at=None,
        pdf_final_size_bytes=None,
    )
    with patch("contratos.routes._contract_admin_base_query", return_value=_QueryStub([contrato])):
        resp = client.get("/admin/contratos", follow_redirects=False)

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Contratos digitales" in html
    assert "enviado" in html
    assert "101" in html


def test_admin_contrato_detalle_muestra_estado_y_eventos():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    contrato = SimpleNamespace(
        id=333,
        solicitud_id=44,
        cliente_id=9,
        version=1,
        estado="borrador",
        snapshot_fijado_at=None,
        enviado_at=None,
        primer_visto_at=None,
        ultimo_visto_at=None,
        firmado_at=None,
        token_expira_at=None,
        pdf_final_size_bytes=None,
        contenido_snapshot_json={"a": 1},
    )
    evento = SimpleNamespace(
        id=1,
        contrato_id=333,
        evento_tipo="CONTRATO_CREADO",
        estado_anterior=None,
        estado_nuevo="borrador",
        actor_tipo="staff",
        actor_staff_id=1,
        success=True,
        error_code=None,
        metadata_json={},
        created_at=None,
    )
    with flask_app.app_context():
        with patch("contratos.routes._contract_admin_base_query", return_value=_QueryStub([contrato])), \
             patch.object(contratos_routes.ContratoEvento, "query", _QueryStub([evento])):
            resp = client.get("/admin/contratos/333/detalle", follow_redirects=False)

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Contrato #333" in html
    assert "borrador" in html
    assert "CONTRATO_CREADO" in html


def test_public_firmar_contrato_anulado_devuelve_410():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    resolution = contratos_services.TokenResolution(False, None, "contract_annulled")
    with patch("contratos.routes.resolver_token_publico", return_value=resolution):
        resp = client.post("/contratos/f/token/firmar", data={"signer_name": "X", "signature_data": "data:image/png;base64,AAA"})
    assert resp.status_code == 410


def test_crear_borrador_bloquea_si_existe_contrato_activo():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    solicitud = SimpleNamespace(id=77, cliente_id=7)
    contrato_activo = SimpleNamespace(
        id=901,
        solicitud_id=77,
        cliente_id=7,
        version=3,
        estado="enviado",
        snapshot_fijado_at=None,
        token_version=1,
        token_expira_at=None,
        firmado_at=None,
        anulado_at=None,
        pdf_final_size_bytes=None,
    )

    with flask_app.app_context():
        with patch.object(contratos_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
             patch.object(contratos_routes.Cliente, "query", SimpleNamespace(get_or_404=lambda _id: SimpleNamespace(id=7))), \
             patch(
                 "contratos.routes._create_or_refresh_draft_for_solicitud",
                 return_value=(contrato_activo, False, "active_contract_exists"),
             ):
            resp = client.post("/admin/contratos/solicitudes/77/borrador", follow_redirects=False)

    assert resp.status_code == 409
    body = resp.get_json() or {}
    assert body.get("ok") is False
    assert body.get("error") == "active_contract_exists"
    assert body.get("active_contract_id") == 901


def test_reemision_invalida_token_anterior():
    flask_app.config["TESTING"] = True
    contrato = SimpleNamespace(
        id=20,
        token_version=1,
        token_hash=None,
        token_generado_at=None,
        token_expira_at=None,
        token_revocado_at=None,
        anulado_at=None,
        estado="enviado",
        firmado_at=None,
        updated_at=None,
    )
    with flask_app.app_context():
        tok_old = contratos_services.emitir_nuevo_link(contrato, ttl_seconds=3600)
        _tok_new = contratos_services.emitir_nuevo_link(contrato, ttl_seconds=3600)
        query_stub = SimpleNamespace(filter_by=lambda **_kw: SimpleNamespace(first=lambda: contrato))
        with patch.object(contratos_services.ContratoDigital, "query", query_stub):
            resolved = contratos_services.resolver_token_publico(tok_old)
    assert not resolved.ok
    assert resolved.reason == "token_revoked"


def test_contrato_firmado_no_editable_en_firma_atomica():
    contrato = SimpleNamespace(
        estado="firmado",
        firmado_at=object(),
        anulado_at=None,
    )
    try:
        contratos_services.firmar_contrato_atomico(
            contrato,
            signature_data_url="data:image/png;base64,AA",
            signer_name="Cliente",
        )
    except contratos_services.ContractValidationError as exc:
        assert str(exc) == "already_signed"
    else:
        raise AssertionError("expected already_signed")


def test_descargar_pdf_admin_solo_si_corresponde():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_owner(client).status_code in (302, 303)

    sin_pdf = SimpleNamespace(id=1, firmado_at=None, pdf_final_bytea=None)
    with flask_app.app_context():
        with patch.object(contratos_routes.ContratoDigital, "query", _QueryStub([sin_pdf])):
            resp = client.get("/admin/contratos/1/pdf", follow_redirects=False)
    assert resp.status_code == 404


def test_listado_admin_no_carga_blobs_pesados():
    src = inspect.getsource(contratos_routes._contract_admin_base_query)
    assert "defer(ContratoDigital.firma_png)" in src
    assert "defer(ContratoDigital.pdf_final_bytea)" in src


def test_migracion_contratos_solo_crea_tablas_nuevas():
    with open("migrations/versions/20260324_1200_create_digital_contracts_tables.py", "r", encoding="utf-8") as f:
        text = f.read()
    assert "op.create_table(" in text
    assert "contratos_digitales" in text
    assert "contratos_eventos" in text
    assert "op.alter_table(" not in text
    assert "op.add_column(" not in text
