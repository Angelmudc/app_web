# -*- coding: utf-8 -*-

from pathlib import Path

from app import app as flask_app


def _response_html():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    return client.get("/proceso-servicio", follow_redirects=False)


def test_public_process_service_is_isolated_and_has_official_metadata():
    response = _response_html()
    assert response.status_code == 200
    assert response.location is None
    html = response.get_data(as_text=True)
    assert "Cómo funciona nuestro proceso" in html
    assert "Proceso de servicio | Doméstica del Cibao" in html
    assert "Conoce paso a paso cómo funciona nuestro proceso de búsqueda, entrevistas, selección y colocación de personal doméstico." in html
    assert 'property="og:title" content="Cómo funciona nuestro proceso | Doméstica del Cibao"' in html
    assert 'property="og:description" content="Conoce los 10 pasos de nuestro proceso para solicitar personal doméstico."' in html
    assert 'property="og:image" content="https://www.domesticadelcibao.com/static/public/img/proceso_servicio_preview.png"' in html
    assert 'property="og:image:width" content="1200"' in html
    assert 'property="og:image:height" content="630"' in html
    assert 'name="twitter:card" content="summary"' in html
    assert 'name="twitter:image" content="https://www.domesticadelcibao.com/static/public/img/proceso_servicio_preview.png"' in html
    assert 'property="og:url" content="' in html and "/proceso-servicio" in html
    assert "Formulario seguro" not in html
    assert "/static/img/domestica-preview.png" not in html
    og_block = html.split('<meta property="og:image"', 1)[1].split('</head>', 1)[0]
    assert "/static/logo_nuevo.png" not in og_block.split('<link rel="icon"', 1)[0]
    assert "admin" not in html.lower()
    assert "sidebar" not in html.lower()
    assert "csrf-token" not in html
    assert "token" not in html.lower()
    assert html.count('class="service-process-whatsapp"') == 1
    assert 'href="tel:' not in html
    assert "/clientes" not in html
    assert "/solicitud" not in html
    assert 'href="https://wa.me/18094296892"' in html
    assert "?text=" not in html
    assert "Hola, ya leí el proceso y deseo continuar." not in html


def test_public_process_service_contains_ordered_ten_steps_and_policy_copy():
    response = _response_html()
    html = response.get_data(as_text=True)
    positions = [html.index(f">{index:02d}<") for index in range(1, 11)]
    assert positions == sorted(positions)
    for title in (
        "Información inicial",
        "Formulario de solicitud",
        "Pago de apertura de búsqueda",
        "Búsqueda y presentación de perfiles",
        "Completar el 50% del plan",
        "Entrevistas",
        "Selección de la candidata",
        "Pago final y entrega de documentos",
        "Garantía del servicio",
        "Pago por gestión y colocación de la doméstica",
    ):
        assert html.count(f"<h3>{title}</h3>") == 1
    for text in (
        "RD$1,000",
        "no es reembolsable",
        "Se descuenta del valor total del plan elegido",
        "50% del valor del plan contratado",
        "Antes de coordinar las entrevistas",
        "50% restante",
        "Antes de entregar la documentación disponible",
        "25% de su salario mensual acordado",
        "Garantía del servicio",
        "pago total del servicio",
        "inicia sus labores",
        "no se considera como un reemplazo utilizado",
        "El tiempo de búsqueda puede variar",
        "deberá cubrir el transporte correspondiente",
        "no entregar dinero de pasaje por adelantado",
        "Condiciones importantes",
    ):
        assert text in html
    assert "10 pasos" in html
    assert 'href="{{ url_for' not in html
    assert "Solicitud pública" not in html


def test_public_process_service_preview_is_public_and_form_metadata_stays_distinct():
    client = flask_app.test_client()
    preview = client.get("/static/public/img/proceso_servicio_preview.png", follow_redirects=False)
    assert preview.status_code == 200
    assert preview.mimetype == "image/png"
    assert preview.content_length or len(preview.data) > 0

    form_template = Path("templates/clientes/solicitud_form_publica.html").read_text(encoding="utf-8")
    assert 'og_title = "Doméstica del Cibao A&D — Formulario de Solicitud"' in form_template
    assert 'og_description = "Formulario oficial para completar su solicitud."' in form_template
    assert "proceso_servicio_preview.png" not in form_template


def test_public_process_service_reading_controller_is_responsive_and_accessible():
    js = Path("static/public/js/proceso_servicio.js").read_text(encoding="utf-8")
    css = Path("static/public/css/proceso_servicio.css").read_text(encoding="utf-8")
    assert "scrollHeight > window.innerHeight + 24" in js
    assert "orientationchange" in js
    assert "ResizeObserver" in js
    assert "prefers-reduced-motion" in js
    assert "aria-label=\"Desliza para continuar leyendo\"" in Path("templates/public/proceso_servicio.html").read_text(encoding="utf-8")
    assert "@media (max-width: 640px)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "overflow-x" not in css
    assert "width: clamp(360px, 30vw, 460px)" in css
    assert "width: clamp(280px, 36vw, 340px)" in css
    assert "width: clamp(220px, 60vw, 260px)" in css
