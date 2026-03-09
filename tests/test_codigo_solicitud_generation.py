# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path

from utils.codigo_solicitud import compose_codigo_solicitud


ROOT = Path(__file__).resolve().parents[1]


def test_compose_codigo_solicitud_first_is_base_without_trailing_dash():
    assert compose_codigo_solicitud("2,142", 0) == "2,142"


def test_compose_codigo_solicitud_second_is_base_dash_b():
    assert compose_codigo_solicitud("2,142", 1) == "2,142 - B"


def test_compose_codigo_solicitud_third_is_base_dash_c():
    assert compose_codigo_solicitud("2,142", 2) == "2,142 - C"


def test_clientes_and_admin_routes_use_shared_compose_helper_for_codigo_generation():
    clientes_src = (ROOT / "clientes/routes.py").read_text(encoding="utf-8")
    admin_src = (ROOT / "admin/routes.py").read_text(encoding="utf-8")

    assert "from utils.codigo_solicitud import compose_codigo_solicitud" in clientes_src
    assert "from utils.codigo_solicitud import compose_codigo_solicitud" in admin_src
    assert "compose_codigo_solicitud(str(current_user.codigo or \"\"), idx)" in clientes_src
    assert "compose_codigo_solicitud(str(c.codigo or \"\"), idx)" in clientes_src
    assert "compose_codigo_solicitud(prefix, base_count + intento)" in admin_src
