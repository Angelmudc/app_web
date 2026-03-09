# -*- coding: utf-8 -*-

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_public_admin_and_client_templates_use_shared_core_partial():
    public_tpl = _read("templates/clientes/solicitud_form_publica.html")
    admin_tpl = _read("templates/admin/solicitud_form.html")
    cliente_tpl = _read("templates/clientes/solicitud_form.html")

    include_stmt = "{% include 'clientes/_solicitud_form_fields.html' %}"
    assert include_stmt in public_tpl
    assert include_stmt in admin_tpl
    assert include_stmt in cliente_tpl
    assert 'class="admin-solicitud-form"' in admin_tpl


def test_shared_partial_keeps_core_order_aligned():
    partial = _read("templates/clientes/_solicitud_form_fields.html")

    idx_ciudad = partial.find("{{ render_field(form.ciudad_sector) }}")
    idx_rutas = partial.find("{{ render_field(form.rutas_cercanas) }}")
    idx_modalidad = partial.find("{{ render_field(form.modalidad_trabajo")
    idx_horario = partial.find("{{ render_field(form.horario")
    assert -1 not in (idx_ciudad, idx_rutas, idx_modalidad, idx_horario)
    assert idx_ciudad < idx_rutas < idx_modalidad < idx_horario

    idx_edad = partial.find("{{ render_field(form.edad_requerida")
    idx_exp = partial.find("{{ render_field(form.experiencia")
    idx_func = partial.find("{{ render_field(form.funciones")
    assert -1 not in (idx_edad, idx_exp, idx_func)
    assert idx_edad < idx_exp < idx_func

    idx_tl = partial.find("{{ render_field(form.tipo_lugar) }}")
    idx_hab = partial.find("{{ render_field(form.habitaciones) }}")
    idx_banos = partial.find("{{ render_field(form.banos) }}")
    idx_pisos = partial.find("Cantidad de pisos")
    idx_areas = partial.find("{{ render_field(form.areas_comunes")
    idx_ad = partial.find("{{ render_field(form.adultos) }}")
    idx_ni = partial.find("{{ render_field(form.ninos) }}")
    idx_ed = partial.find("{{ render_field(form.edades_ninos) }}")
    idx_mas = partial.find("{{ render_field(form.mascota) }}")
    assert -1 not in (idx_tl, idx_hab, idx_banos, idx_pisos, idx_areas, idx_ad, idx_ni, idx_ed, idx_mas)
    assert idx_tl < idx_hab < idx_banos < idx_pisos < idx_areas < idx_ad < idx_ni < idx_ed < idx_mas


def test_shared_partial_renders_pasaje_three_options_and_otro_field():
    partial = _read("templates/clientes/_solicitud_form_fields.html")
    assert "name=\"pasaje_mode\" value=\"incluido\"" in partial
    assert "name=\"pasaje_mode\" value=\"aparte\"" in partial
    assert "name=\"pasaje_mode\" value=\"otro\"" in partial
    assert "name=\"pasaje_otro_text\"" in partial
