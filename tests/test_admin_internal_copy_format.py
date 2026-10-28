# -*- coding: utf-8 -*-
import re
from types import SimpleNamespace

import pytest
from app import app as flask_app
import admin.routes as admin_routes


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
        tipo_servicio="DOMESTICA_LIMPIEZA",
        detalles_servicio={"tipo": "DOMESTICA_LIMPIEZA"},
        cliente=None,
        envejeciente_tipo_cuidado="",
        envejeciente_responsabilidades=[],
        envejeciente_solo_acompanamiento=False,
        envejeciente_nota="",
    )
    with flask_app.test_request_context("/admin/clientes/7/solicitudes/10/_heavy"):
        return admin_routes._admin_build_order_text_for_copiar(solicitud)


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
    modalidad_visible = modalidad
    assert texto == (
        "Disponible ( SOL-010 )\n"
        "📍 Santiago\n"
        "Ruta más cercana: 27 de Febrero\n\n"
        "*DOMÉSTICA*\n\n"
        f"{modalidad_visible}\n\n"
        "Edad: 25 en adelante\n"
        "Dominicana\n"
        "Que sepa leer y escribir\n"
        "Experiencia en: *Doméstica completa*\n"
        "Horario: Entrada: lunes 8:00 AM / Salida: sábado 12:00 PM\n\n"
        "Funciones: Limpieza General\n\n"
        "Casa - 3 habitaciones, 2 baños\n\n"
        "Composición del hogar:\n"
        "Adultos: 2\n\n"
        "Sueldo: $25,000 mensual, Pasaje incluido"
    )
    assert "Ruta más cercana: 27 de Febrero\n\n" in texto
    assert re.search(r"\n\n[^\n]+\n\nEdad: 25 en adelante\n", texto)
    assert "\n\n\nEdad:" not in texto
    assert texto.endswith("Sueldo: $25,000 mensual, Pasaje incluido")
    assert "Dominicana" in texto
    assert "Que sepa leer y escribir" in texto
    assert "Experiencia en: *Doméstica completa*\nHorario: Entrada: lunes 8:00 AM / Salida: sábado 12:00 PM" in texto


@pytest.mark.parametrize(
    ("routes", "other_text", "expected_routes"),
    [
        (["dentro_ciudad", "entre_ciudades"], "", "Dentro de la ciudad, Entre ciudades"),
        (["dentro_ciudad", "otro"], "Aeropuerto y zonas rurales", "Dentro de la ciudad, Aeropuerto y zonas rurales"),
        (["otro"], "Viajes interprovinciales", "Viajes interprovinciales"),
        (["otro"], "", "Otro"),
    ],
    ids=["rutas-normales", "ruta-otro-con-rutas-normales", "solo-otro-descrito", "otro-sin-descripcion"],
)
def test_chofer_publication_replaces_other_route_label_without_losing_other_answers(routes, other_text, expected_routes):
    solicitud = SimpleNamespace(
        codigo_solicitud="SOL-CHOFER-OTRO",
        ciudad_sector="Santiago",
        rutas_cercanas="27 de Febrero",
        modalidad_trabajo="Salida diaria",
        edad_requerida=["26-35"],
        experiencia="Chofer",
        horario="Lunes a viernes, 8:00 AM a 5:00 PM",
        funciones=[], funciones_otro="", tipo_lugar="", habitaciones=0, banos=0,
        areas_comunes=[], area_otro="", dos_pisos=False, nota_cliente="",
        adultos=0, ninos=0, edades_ninos="", mascota="", pasaje_aporte=False,
        sueldo="30000", estado="activa", tipo_servicio="CHOFER",
        detalles_servicio={
            "tipo": "CHOFER", "finalidad": "personal_familiar", "vehiculo": "cliente",
            "tipo_vehiculo": "carro", "rutas": routes, "rutas_otro": other_text,
            "categoria_licencia_requerida": "categoria_02", "responsabilidades": "Esperar durante diligencias",
        },
        cliente=None, envejeciente_tipo_cuidado="", envejeciente_responsabilidades=[],
        envejeciente_solo_acompanamiento=False, envejeciente_nota="",
    )
    with flask_app.test_request_context("/admin/solicitudes/copiar"):
        internal_copy = admin_routes._admin_build_order_text_for_copiar(solicitud)
        publication_copy = admin_routes._admin_build_public_copy_text(solicitud)

    assert internal_copy == publication_copy
    route_line = f"Rutas o desplazamientos: {expected_routes}"
    assert route_line in publication_copy
    assert publication_copy.count("Rutas o desplazamientos:") == 1
    assert "Otros desplazamientos:" not in publication_copy
    assert "Finalidad del empleo: Chofer personal o familiar" in publication_copy
    assert "Vehículo: del cliente" in publication_copy
    assert "Tipo de vehículo: Carro" in publication_copy
    assert "Categoría de licencia requerida:" in publication_copy
    assert "Responsabilidades:\nEsperar durante diligencias" in publication_copy
    assert "Experiencia en: *Chofer*" in publication_copy
    assert "Horario: Lunes a viernes, 8:00 AM a 5:00 PM" in publication_copy
