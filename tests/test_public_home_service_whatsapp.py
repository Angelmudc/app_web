# -*- coding: utf-8 -*-

from pathlib import Path

from app import app as flask_app


def test_public_home_service_picker_exposes_whatsapp_messages_and_js_logic():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()

    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200

    html = resp.get_data(as_text=True)
    expected_messages = [
        "Hola, me interesa solicitar una doméstica con dormida. Quiero recibir orientación sobre el proceso.",
        "Hola, me interesa solicitar una doméstica sin dormida. Quiero recibir orientación sobre el proceso.",
        "Hola, me interesa solicitar una persona para limpieza por días. Quiero recibir orientación sobre el proceso.",
        "Hola, me interesa solicitar una niñera. Quiero recibir orientación sobre el proceso.",
        "Hola, me interesa solicitar una persona para cuidado de adulto mayor. Quiero recibir orientación sobre el proceso.",
    ]

    assert 'data-service-whatsapp' in html
    assert "Hablar por WhatsApp" in html
    assert html.count('data-service-message="') >= 5
    assert html.count('aria-pressed="false"') >= 5
    for message in expected_messages:
        assert message in html

    js_text = Path("static/public/js/main.js").read_text(encoding="utf-8")
    assert "MSG_SERVICIO_GENERAL" in js_text
    assert "Hola, me interesa solicitar un servicio doméstico. Quiero recibir orientación sobre el proceso." in js_text
    assert "selectedServiceMessage || MSG_SERVICIO_GENERAL" in js_text
    assert 'item.setAttribute("aria-pressed", isActive ? "true" : "false")' in js_text
    assert "encodeURIComponent(text)" in js_text


def test_public_home_process_timeline_replays_on_scroll_reentry():
    js_text = Path("static/public/js/landing_motion.js").read_text(encoding="utf-8")
    main_js_text = Path("static/public/js/main.js").read_text(encoding="utf-8")
    css_text = Path("static/public/css/landing_motion.css").read_text(encoding="utf-8")
    html = Path("templates/public/index.html").read_text(encoding="utf-8")

    assert "const initGlobalRevealSystem = () => {" in js_text
    assert 'const nodes = qsa("[data-reveal]");' in js_text
    assert 'node.dataset.motionOnce === "true"' in js_text
    assert 'nodes.forEach((node) => observer.observe(node));' in js_text
    assert 'step.classList.remove("is-revealed")' in js_text
    assert "const revealedCount = syncReveal();" in js_text
    assert "clearActiveStep();" in js_text
    assert "!body.classList.contains(\"home-landing\")" in main_js_text
    assert 'observer.observe(canvas);' in main_js_text
    assert ".home-landing [data-reveal] {" in css_text
    assert ".home-landing .process-story__step.is-revealed {" in css_text
    assert "@media (prefers-reduced-motion: reduce) {" in css_text
    assert "data-reveal-group" in html
