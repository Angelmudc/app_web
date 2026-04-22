# -*- coding: utf-8 -*-

from flask import render_template

from app import app as flask_app


def _render_base(path: str, **ctx) -> str:
    with flask_app.test_request_context(path, method="GET"):
        return render_template("base.html", **ctx)


def test_base_loads_jquery_and_datatables_only_on_legacy_datatable_views():
    html = _render_base("/buscar")
    assert "code.jquery.com/jquery-3.6.0.min.js" in html
    assert "cdn.datatables.net/1.13.4/js/jquery.dataTables.min.js" in html
    assert "cdn.datatables.net/1.13.4/css/jquery.dataTables.min.css" in html
    assert "select2@4.1.0" not in html


def test_base_omits_jquery_select2_datatables_on_light_views():
    html_login = _render_base("/login")
    html_admin = _render_base("/admin/solicitudes")
    for html in (html_login, html_admin):
        assert "code.jquery.com/jquery-3.6.0.min.js" not in html
        assert "select2@4.1.0" not in html
        assert "cdn.datatables.net/1.13.4/js/jquery.dataTables.min.js" not in html
        assert "cdn.datatables.net/1.13.4/css/jquery.dataTables.min.css" not in html


def test_base_supports_explicit_select2_opt_in_without_datatables():
    html = _render_base("/admin/usuarios", requires_select2=True)
    assert "code.jquery.com/jquery-3.6.0.min.js" in html
    assert "select2@4.1.0" in html
    assert "cdn.datatables.net/1.13.4/js/jquery.dataTables.min.js" not in html
    assert "cdn.datatables.net/1.13.4/css/jquery.dataTables.min.css" not in html


def test_base_loads_solicitud_detail_ui_only_in_solicitudes_clientes_scope():
    html_solicitudes = _render_base("/admin/solicitudes")
    html_clientes = _render_base("/admin/clientes/17")
    html_monitoreo = _render_base("/admin/monitoreo")

    assert "js/core/admin_lazy_scripts.js" in html_solicitudes
    assert "js/core/admin_lazy_scripts.js" in html_clientes
    for html in (html_solicitudes, html_clientes):
        assert "data-lazy-script-solicitud-detail-ui=" in html
        assert '<script src="/static/js/admin/solicitud_detail_ui.js"' not in html
    assert "js/core/admin_lazy_scripts.js" not in html_monitoreo
    assert '<script src="/static/js/admin/solicitud_detail_ui.js"' not in html_monitoreo


def test_base_loads_entrevistas_ui_only_on_entrevistas_paths():
    html_admin_entrevistas = _render_base("/admin/entrevistas")
    html_admin_solicitudes = _render_base("/admin/solicitudes")
    html_entrevistas_public = _render_base("/entrevistas/lista")

    assert "js/entrevistas/entrevistas.js" in html_admin_entrevistas
    assert "js/entrevistas/entrevistas.js" not in html_admin_solicitudes
    assert "js/entrevistas/entrevistas.js" not in html_entrevistas_public


def test_base_form_helpers_and_live_refresh_are_opt_in():
    html_default = _render_base("/admin/solicitudes")
    assert "js/forms/autosave.js" not in html_default
    assert "js/forms/validate.js" not in html_default
    assert "js/ui/search.js" not in html_default
    assert '<script src="/static/js/core/live-refresh.js"' not in html_default
    assert "js/core/admin_lazy_scripts.js" in html_default
    assert "data-lazy-script-live-refresh=" in html_default

    html_opt_in = _render_base(
        "/admin/solicitudes",
        requires_autosave=True,
        requires_validate=True,
        requires_search_ui=True,
    )
    assert "js/forms/autosave.js" in html_opt_in
    assert "js/forms/validate.js" in html_opt_in
    assert "js/ui/search.js" in html_opt_in
    assert '<script src="/static/js/core/live-refresh.js"' not in html_opt_in


def test_base_can_enable_lazy_loader_for_live_refresh_opt_in_pages():
    html = _render_base("/admin/monitoreo", requires_live_refresh=True)
    assert "js/core/admin_lazy_scripts.js" in html
    assert "data-lazy-script-live-refresh=" in html


def test_admin_lazy_loader_declares_expected_markers_and_targets():
    with open("static/js/core/admin_lazy_scripts.js", "r", encoding="utf-8") as fh:
        txt = fh.read()

    assert "#resumenCliente" in txt
    assert ".copy-btn-interno" in txt
    assert ".js-copy-contract-link" in txt
    assert "[data-live-refresh='1']" in txt
    assert "data-lazy-script-solicitud-detail-ui" in txt
    assert "data-lazy-script-live-refresh" in txt
