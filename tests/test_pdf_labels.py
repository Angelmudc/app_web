# -*- coding: utf-8 -*-

from utils.pdf_labels import humanize_pdf_label


def test_pdf_label_humaniza_campos_tecnicos_principales():
    assert humanize_pdf_label("tiene_hijos") == "¿Tiene hijos?"
    assert humanize_pdf_label("anos_experiencia") == "Años de experiencia"
    assert humanize_pdf_label("acepta_porcentaje_sueldo") == "¿Acepta porcentaje del sueldo?"
    assert humanize_pdf_label("modalidad_trabajo_preferida") == "Modalidad de trabajo preferida"
    assert humanize_pdf_label("compat_orden_detalle_nivel") == "Nivel de orden y detalle"


def test_pdf_label_fallback_para_clave_no_mapeada():
    assert humanize_pdf_label("compat_nivel_comunicacion_hogar") == "Nivel comunicacion hogar"


def test_pdf_label_no_rompe_texto_ya_humano():
    assert humanize_pdf_label("¿Tiene hijos?") == "¿Tiene hijos?"
    assert humanize_pdf_label("Nivel de orden y detalle") == "Nivel de orden y detalle"


def test_pdf_label_quita_prefijo_de_tipo_entrevista():
    assert humanize_pdf_label("domestica.tienes_hijos") == "¿Tiene hijos?"
