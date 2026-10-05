# -*- coding: utf-8 -*-
import re
from types import SimpleNamespace

import pytest
from flask import render_template

from app import app as flask_app


def _internal_copy_text(modalidad: str) -> str:
    solicitud = SimpleNamespace(
        codigo_solicitud="SOL-010",
        ciudad_sector="Santiago",
        rutas_cercanas="27 de Febrero",
        modalidad_trabajo=modalidad,
        edad_requerida=["25 en adelante"],
        experiencia="Doméstica completa",
        horario="Entrada: lunes 8:00 AM / Salida: sábado 12:00 PM",
        funciones=["limpieza"],
        funciones_otro="",
        tipo_lugar="Casa",
        habitaciones=3,
        banos=2,
        areas_comunes=[],
        area_otro="",
        dos_pisos=False,
        nota_cliente="",
        adultos=2,
        ninos=0,
        edades_ninos="",
        mascota="",
        pasaje_aporte=False,
        sueldo="25000",
        estado="activa",
    )
    with flask_app.test_request_context("/admin/clientes/7/solicitudes/10/_heavy"):
        html = render_template(
            "admin/_solicitud_detail_heavy_region.html",
            solicitud=solicitud,
            composition_flags={
                "show_house": True,
                "show_adultos": True,
                "show_ninos": True,
                "show_child_help": True,
                "show_envejeciente": True,
            },
            envejeciente_copy_lines=[],
            specific_service_copy_lines=[],
            child_help_copy_suffix="",
            sueldo_copy="25000",
            pasaje_copy_mode="incluido",
            pasaje_copy_other_text="",
            envios=[],
            reemplazos=[],
        )
    match = re.search(r"<textarea[^>]*>(.*?)</textarea>", html, flags=re.DOTALL)
    assert match is not None
    return match.group(1)


@pytest.mark.parametrize(
    "modalidad",
    [
        "Con dormida 💤 lunes a sábado",
        "Salida diaria - lunes a viernes",
        "Salida diaria - viernes a lunes",
    ],
)
def test_copiar_interno_separa_modalidad_de_edad_sin_cambiar_el_resto(modalidad):
    texto = _internal_copy_text(modalidad)
    modalidad_visible = (
        "Salida diaria - fin de semana"
        if modalidad == "Salida diaria - viernes a lunes"
        else modalidad
    )
    assert texto == (
        "Disponible ( SOL-010 )\n"
        "📍 Santiago\n"
        "Ruta más cercana: 27 de Febrero\n\n"
        f"{modalidad_visible}\n\n"
        "Edad: 25 en adelante\n"
        "Dominicana\n"
        "Que sepa leer y escribir\n"
        "Experiencia en: *Doméstica completa*\n"
        "Horario: Entrada: lunes 8:00 AM / Salida: sábado 12:00 PM\n\n"
        "Funciones: Limpieza General\n\n"
        "Casa - 3 habitaciones, 2 baños\n\n"
        "Adultos: 2\n\n"
        "Sueldo: 25000 mensual, Pasaje incluido"
    )
    assert "Ruta más cercana: 27 de Febrero\n\n" in texto
    assert re.search(r"\n\n[^\n]+\n\nEdad: 25 en adelante\n", texto)
    assert "\n\n\nEdad:" not in texto
    assert texto.endswith("Sueldo: 25000 mensual, Pasaje incluido")
    assert "Dominicana\nQue sepa leer y escribir\nExperiencia en: *Doméstica completa*\n" in texto
    assert "Horario: Entrada: lunes 8:00 AM / Salida: sábado 12:00 PM" in texto
