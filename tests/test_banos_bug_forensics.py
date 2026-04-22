# -*- coding: utf-8 -*-

from __future__ import annotations

import re
from decimal import Decimal
from types import SimpleNamespace

from flask import render_template_string, request
from flask_wtf import FlaskForm
from wtforms import DecimalField, FloatField

from app import app as flask_app
from config_app import db
from models import Cliente, Solicitud
import clientes.routes as clientes_routes
import admin.routes as admin_routes
from clientes.forms import SolicitudForm
from admin.forms import AdminSolicitudForm
from tests.t1_testkit import ensure_sqlite_compat_tables


def _extract_input_value(html: str) -> str:
    m = re.search(r'value="([^"]*)"', html or "")
    return (m.group(1) if m else "").strip()


def _detail_banos_text(value) -> str:
    with flask_app.app_context():
        return render_template_string(
            "{% set _banos_txt = ((s.banos|string)|trim)|replace('.0', '') if (s.banos is not none and (s.banos|string)|trim != '') else '0' %}{{ _banos_txt }}",
            s=SimpleNamespace(banos=value),
        ).strip()


def _trace_flow(form_cls, apply_fn, raw_value: str) -> dict:
    with flask_app.test_request_context("/trace", method="POST", data={"banos": raw_value}):
        form = form_cls(meta={"csrf": False})
        model = SimpleNamespace()
        form.populate_obj(model)
        saved_by_populate = getattr(model, "banos", None)
        apply_fn(model, form)
        saved_final = getattr(model, "banos", None)
        edit_form = form_cls(obj=SimpleNamespace(banos=saved_final), meta={"csrf": False})
        edit_html = str(edit_form.banos())
        return {
            "input_written": raw_value,
            "request_form": request.form.get("banos"),
            "form_data": getattr(form.banos, "data", None),
            "model_after_populate": saved_by_populate,
            "model_saved": saved_final,
            "edit_render_value": _extract_input_value(edit_html),
            "detail_render": _detail_banos_text(saved_final),
        }


def test_legacy_forms_do_not_reproduce_one_to_half_conversion():
    class _LegacyClienteForm(FlaskForm):
        banos = FloatField("Baños")

    class _LegacyAdminForm(FlaskForm):
        banos = DecimalField("Baños", places=1)

    with flask_app.test_request_context("/legacy", method="POST", data={"banos": "1"}):
        f_cli = _LegacyClienteForm(meta={"csrf": False})
        f_adm = _LegacyAdminForm(meta={"csrf": False})
        assert float(f_cli.banos.data) == 1.0
        assert Decimal(f_adm.banos.data) == Decimal("1")


def test_traceability_cliente_banos_edges():
    cases = [
        ("0.5", 0.5, "0.5"),
        ("1", 1.0, "1"),
        ("1.5", 1.5, "1.5"),
        ("2", 2.0, "2"),
        ("2.0", 2.0, "2"),
        ("5", 5.0, "5"),
        ("5.0", 5.0, "5"),
        ("5.5", 5.5, "5.5"),
        ("10", 10.0, "10"),
        (" 5 ", 5.0, "5"),
    ]
    for raw, expected_saved, expected_detail in cases:
        trace = _trace_flow(SolicitudForm, clientes_routes._apply_banos_from_request, raw)
        assert trace["input_written"] == raw
        assert trace["request_form"] == raw
        assert float(trace["model_saved"]) == expected_saved
        assert trace["detail_render"] == expected_detail


def test_traceability_admin_banos_edges():
    cases = [
        ("0.5", 0.5, "0.5"),
        ("1", 1.0, "1"),
        ("1.5", 1.5, "1.5"),
        ("2", 2.0, "2"),
        ("2.0", 2.0, "2"),
        ("5", 5.0, "5"),
        ("5.0", 5.0, "5"),
        ("5.5", 5.5, "5.5"),
        ("10", 10.0, "10"),
        (" 5 ", 5.0, "5"),
    ]
    for raw, expected_saved, expected_detail in cases:
        trace = _trace_flow(AdminSolicitudForm, admin_routes._apply_banos_from_request, raw)
        assert trace["input_written"] == raw
        assert trace["request_form"] == raw
        assert float(trace["model_saved"]) == expected_saved
        assert trace["detail_render"] == expected_detail


def test_invalid_banos_abc_does_not_break_and_stays_none():
    trace_cli = _trace_flow(SolicitudForm, clientes_routes._apply_banos_from_request, "abc")
    trace_adm = _trace_flow(AdminSolicitudForm, admin_routes._apply_banos_from_request, "abc")
    assert trace_cli["model_saved"] is None
    assert trace_adm["model_saved"] is None


def test_entrypoints_with_banos_are_covered_by_apply_helper():
    with open("clientes/routes.py", "r", encoding="utf-8") as fh:
        clientes_src = fh.read()
    with open("admin/routes.py", "r", encoding="utf-8") as fh:
        admin_src = fh.read()

    # Clientes: create, edit, pública cliente nuevo, pública cliente existente
    assert clientes_src.count("_apply_banos_from_request(s, form)") >= 4
    # Admin: create + edit (incluye flujo async porque comparte ruta)
    assert admin_src.count("_apply_banos_from_request(s, form)") >= 2


def test_db_persistence_and_rerender_integer_and_decimal():
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with flask_app.app_context():
        ensure_sqlite_compat_tables([Cliente, Solicitud], reset=True)
        try:
            c = Cliente(
                codigo="CL-BANOS-01",
                role="cliente",
                nombre_completo="Cliente Banos",
                email="banos@test.local",
                telefono="8090000101",
                username="cliente_banos",
                password_hash="test-hash",
            )
            db.session.add(c)
            db.session.flush()

            s_int = Solicitud(
                cliente_id=int(c.id),
                codigo_solicitud="SOL-BANOS-INT",
                banos=5.0,
            )
            s_dec = Solicitud(
                cliente_id=int(c.id),
                codigo_solicitud="SOL-BANOS-DEC",
                banos=5.5,
            )
            db.session.add_all([s_int, s_dec])
            db.session.commit()

            row_int = Solicitud.query.filter_by(codigo_solicitud="SOL-BANOS-INT").first()
            row_dec = Solicitud.query.filter_by(codigo_solicitud="SOL-BANOS-DEC").first()

            assert float(row_int.banos) == 5.0
            assert float(row_dec.banos) == 5.5

            edit_form_int = SolicitudForm(obj=row_int, meta={"csrf": False})
            edit_form_dec = SolicitudForm(obj=row_dec, meta={"csrf": False})
            assert edit_form_int.banos.data is not None
            assert edit_form_dec.banos.data is not None

            assert _detail_banos_text(row_int.banos) == "5"
            assert _detail_banos_text(row_dec.banos) == "5.5"
        finally:
            db.session.rollback()
