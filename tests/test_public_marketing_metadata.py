# -*- coding: utf-8 -*-

from app import app as flask_app


def test_public_home_has_professional_preview_metadata_and_trust_signals():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    old_base = flask_app.config.get("PUBLIC_BASE_URL")
    flask_app.config["PUBLIC_BASE_URL"] = "https://domestica.example.com"
    try:
        resp = client.get("/", follow_redirects=False)
    finally:
        flask_app.config["PUBLIC_BASE_URL"] = old_base

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert '<meta property="og:title"' in html
    assert '<meta property="og:description"' in html
    assert '<meta property="og:image"' in html
    assert '<meta property="og:url" content="https://domestica.example.com/"' in html
    assert '<meta property="og:type" content="website"' in html
    assert '<meta name="twitter:card" content="summary_large_image">' in html
    assert '<meta name="twitter:title"' in html
    assert '<meta name="twitter:description"' in html
    assert '<meta name="twitter:image"' in html
    assert '<link rel="canonical" href="https://domestica.example.com/"' in html
    assert "Sitio oficial:" in html
    assert "Canal seguro:" in html


def test_public_services_page_keeps_consistent_brand_preview():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()

    resp = client.get("/servicios", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Servicios - Doméstica del Cibao A&D" in html
    assert '<meta property="og:title"' in html
    assert 'property="og:image"' in html
    assert 'name="twitter:image"' in html
    assert 'rel="canonical"' in html


def test_recruitment_landing_has_preview_metadata_and_official_identity():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    old_base = flask_app.config.get("PUBLIC_BASE_URL")
    flask_app.config["PUBLIC_BASE_URL"] = "https://domestica.example.com"
    try:
        resp = client.get("/trabaja-con-nosotros", follow_redirects=False)
    finally:
        flask_app.config["PUBLIC_BASE_URL"] = old_base

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert '<meta property="og:title"' in html
    assert '<meta property="og:description"' in html
    assert '<meta property="og:image"' in html
    assert '<meta name="twitter:card" content="summary_large_image">' in html
    assert '<meta name="twitter:title"' in html
    assert '<meta name="twitter:description"' in html
    assert '<meta name="twitter:image"' in html
    assert '<link rel="canonical" href="https://domestica.example.com/trabaja-con-nosotros"' in html
    assert "Portal oficial:" in html
    assert "Aplicación segura:" in html
