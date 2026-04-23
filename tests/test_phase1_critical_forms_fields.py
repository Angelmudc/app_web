# -*- coding: utf-8 -*-

from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]


def _read_template(rel_path: str) -> str:
    return (BASE_DIR / rel_path).read_text(encoding="utf-8")


def test_cliente_detail_acciones_criticas_incluyen_row_version_e_idempotency_key():
    tpl = _read_template("templates/admin/cliente_detail.html")
    solicitudes_tpl = _read_template("templates/admin/_cliente_detail_solicitudes_region.html")
    merged = tpl + "\n" + solicitudes_tpl
    assert "poner_espera_pago_solicitud_cliente" in merged
    assert "quitar_espera_pago_solicitud_cliente" in merged
    assert "cancelar_solicitud_directa" in merged
    assert 'name="row_version"' in merged
    assert 'name="idempotency_key"' in merged


def test_solicitudes_proceso_acciones_incluye_row_version_e_idempotency_key():
    tpl = _read_template("templates/admin/_solicitudes_proceso_acciones_results.html")
    assert "cancelar_solicitud_directa" in tpl
    assert 'name="row_version"' in tpl
    assert 'name="idempotency_key"' in tpl


def test_reemplazos_forms_criticos_incluyen_row_version_e_idempotency_key():
    actions_tpl = _read_template("templates/admin/_reemplazo_actions_region.html")
    assert 'name="row_version"' in actions_tpl
    assert 'name="idempotency_key"' in actions_tpl

    inicio_tpl = _read_template("templates/admin/reemplazo_inicio.html")
    assert 'name="row_version"' in inicio_tpl
    assert 'name="idempotency_key"' in inicio_tpl

    fin_tpl = _read_template("templates/admin/reemplazo_fin.html")
    assert 'name="row_version"' in fin_tpl
    assert 'name="idempotency_key"' in fin_tpl


def test_matching_detalle_form_incluye_row_version_e_idempotency_key():
    tpl = _read_template("templates/admin/_matching_detalle_region.html")
    assert 'name="row_version"' in tpl
    assert 'name="idempotency_key"' in tpl


def test_copiar_modales_criticos_incluyen_row_version_e_idempotency_key():
    tpl = _read_template("templates/admin/solicitudes_copiar.html")
    assert 'id="cancelModalSharedRowVersion"' in tpl
    assert 'id="cancelModalSharedIdempotencyKey"' in tpl
    assert 'id="paidModalSharedRowVersion"' in tpl
    assert 'id="paidModalSharedIdempotencyKey"' in tpl
