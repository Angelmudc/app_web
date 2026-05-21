# -*- coding: utf-8 -*-

from __future__ import annotations

import re

from app import app as flask_app
from config_app import db
from models import Candidata, CandidataWeb, Entrevista
from tests.t1_testkit import ensure_sqlite_compat_tables
from werkzeug.datastructures import MultiDict


_CSRF_RE = re.compile(r'name="csrf_token"\s+value="([^"]+)"')


def _extract_csrf(html: str) -> str:
    m = _CSRF_RE.search(html or "")
    return (m.group(1) if m else "").strip()


def _login_staff(client, usuario: str = "Owner", clave: str = "admin123") -> None:
    page = client.get("/admin/login", follow_redirects=False)
    token = _extract_csrf(page.get_data(as_text=True))
    payload = {"usuario": usuario, "clave": clave}
    if token:
        payload["csrf_token"] = token
    resp = client.post("/admin/login", data=payload, follow_redirects=False)
    assert resp.status_code in (302, 303)


def _ensure_tables() -> None:
    ensure_sqlite_compat_tables([Candidata, CandidataWeb, Entrevista], reset=False)


def _seed_candidata(fila: int, has_profile: bool = False, entrevista_txt: str | None = None) -> None:
    c = Candidata(
        fila=fila,
        nombre_completo=f"Candidata QA {fila}",
        cedula=f"{fila:011d}"[-11:],
        codigo=f"QA-{fila}",
        entrevista=entrevista_txt,
    )
    if has_profile:
        c.perfil = b"\x89PNGfakebytes"
    db.session.add(c)
    db.session.commit()


def test_modalidad_solo_permite_opciones_controladas():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    fila = 990501

    with flask_app.app_context():
        _ensure_tables()
        _seed_candidata(fila)

    _login_staff(client)
    resp = client.post(
        f"/admin/candidatas-web/{fila}",
        data={"estado_publico": "disponible", "modalidad_publica": "Por horas"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    with flask_app.app_context():
        ficha = CandidataWeb.query.filter_by(candidata_id=fila).first()
        assert ficha is None


def test_tags_multiples_se_guardan_como_csv():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    fila = 990502

    with flask_app.app_context():
        _ensure_tables()
        _seed_candidata(fila)

    _login_staff(client)
    payload = MultiDict(
        [
            ("estado_publico", "disponible"),
            ("modalidad_publica", "Con dormida"),
            ("experiencia_resumen", "Resumen corto editorial"),
            ("tags_publicos", "Limpieza general"),
            ("tags_publicos", "Cocinar"),
            ("tags_publicos", "Lavar"),
        ]
    )
    resp = client.post(f"/admin/candidatas-web/{fila}", data=payload, follow_redirects=False)
    assert resp.status_code in (302, 303)
    with flask_app.app_context():
        ficha = CandidataWeb.query.filter_by(candidata_id=fila).first()
        assert ficha is not None
        assert ficha.tags_publicos == "Limpieza general,Cocinar,Lavar"
        assert ficha.experiencia_resumen == "Resumen corto editorial"


def test_get_editor_renderiza_checkboxes_tags_y_precarga_checked():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    fila = 990512

    with flask_app.app_context():
        _ensure_tables()
        _seed_candidata(fila)
        db.session.add(CandidataWeb(candidata_id=fila, tags_publicos="Limpieza general,Cocinar"))
        db.session.commit()

    _login_staff(client)
    html = client.get(f"/admin/candidatas-web/{fila}", follow_redirects=False).get_data(as_text=True)
    assert 'type="checkbox"' in html
    assert 'name="tags_publicos"' in html
    assert re.search(r'name="tags_publicos"[^>]*value="Limpieza general"[^>]*checked', html)
    assert re.search(r'name="tags_publicos"[^>]*value="Cocinar"[^>]*checked', html)
    assert "Tags públicos (coma)" not in html


def test_post_tags_invalidos_se_ignoran():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    fila = 990513

    with flask_app.app_context():
        _ensure_tables()
        _seed_candidata(fila)

    _login_staff(client)
    payload = MultiDict(
        [
            ("estado_publico", "disponible"),
            ("modalidad_publica", "Con dormida"),
            ("tags_publicos", "Limpieza general"),
            ("tags_publicos", "Hacker tag"),
            ("tags_publicos", "Cocinar"),
        ]
    )
    resp = client.post(f"/admin/candidatas-web/{fila}", data=payload, follow_redirects=False)
    assert resp.status_code in (302, 303)
    with flask_app.app_context():
        ficha = CandidataWeb.query.filter_by(candidata_id=fila).first()
        assert ficha is not None
        assert ficha.tags_publicos == "Limpieza general,Cocinar"


def test_form_muestra_estado_foto_disponible_y_no_disponible():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    fila_yes = 990503
    fila_no = 990504

    with flask_app.app_context():
        _ensure_tables()
        _seed_candidata(fila_yes, has_profile=True)
        _seed_candidata(fila_no, has_profile=False)

    _login_staff(client)
    html_yes = client.get(f"/admin/candidatas-web/{fila_yes}", follow_redirects=False).get_data(as_text=True)
    html_no = client.get(f"/admin/candidatas-web/{fila_no}", follow_redirects=False).get_data(as_text=True)
    assert "Perfil/foto: Disponible" in html_yes
    assert "Perfil/foto: No disponible" in html_no


def test_form_muestra_estado_entrevista_realizada_y_no_realizada():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    fila_txt = 990505
    fila_struct = 990506
    fila_none = 990507

    with flask_app.app_context():
        _ensure_tables()
        _seed_candidata(fila_txt, entrevista_txt="Entrevista legacy completa")
        _seed_candidata(fila_struct)
        _seed_candidata(fila_none)
        db.session.add(Entrevista(candidata_id=fila_struct, tipo="domestica", estado="completa"))
        db.session.commit()

    _login_staff(client)
    html_txt = client.get(f"/admin/candidatas-web/{fila_txt}", follow_redirects=False).get_data(as_text=True)
    html_struct = client.get(f"/admin/candidatas-web/{fila_struct}", follow_redirects=False).get_data(as_text=True)
    html_none = client.get(f"/admin/candidatas-web/{fila_none}", follow_redirects=False).get_data(as_text=True)
    assert "Entrevista: Realizada" in html_txt
    assert "Entrevista: Realizada" in html_struct
    assert "Entrevista: No realizada" in html_none


def test_form_no_muestra_campos_retirados_ni_preview_legacy():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    fila = 990508

    with flask_app.app_context():
        _ensure_tables()
        _seed_candidata(fila)

    _login_staff(client)
    html = client.get(f"/admin/candidatas-web/{fila}", follow_redirects=False).get_data(as_text=True)
    assert "Foto pública URL" not in html
    assert "name=\"foto_publica_url\"" not in html
    assert "Entrevista pública resumida" not in html
    assert "name=\"entrevista_publica_resumen\"" not in html
    assert "Sin resumen editorial de entrevista." not in html
    assert "Experiencia detallada" not in html
    assert "name=\"experiencia_detallada\"" not in html


def test_editor_contiene_hooks_de_preview_realtime():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    fila = 990514

    with flask_app.app_context():
        _ensure_tables()
        _seed_candidata(fila)

    _login_staff(client)
    html = client.get(f"/admin/candidatas-web/{fila}", follow_redirects=False).get_data(as_text=True)
    assert 'data-preview-card="1"' in html
    assert 'data-preview-field="nombre"' in html
    assert 'data-preview-field="funciones"' in html
    assert 'data-preview-photo-state' in html
    assert 'data-preview-interview-state' in html


def test_editor_foto_y_estado_entrevista_en_bloque_debajo_del_preview():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    fila_with = 990515
    fila_without = 990516

    with flask_app.app_context():
        _ensure_tables()
        _seed_candidata(fila_with, has_profile=True, entrevista_txt="Entrevista OK")
        _seed_candidata(fila_without, has_profile=False)

    _login_staff(client)
    html_with = client.get(f"/admin/candidatas-web/{fila_with}", follow_redirects=False).get_data(as_text=True)
    html_without = client.get(f"/admin/candidatas-web/{fila_without}", follow_redirects=False).get_data(as_text=True)
    assert "Foto del perfil" in html_with
    assert f'/perfil_candidata?fila={fila_with}' in html_with
    assert "Perfil/foto: Disponible" in html_with
    assert "Entrevista: Realizada" in html_with
    assert "Perfil/foto: No disponible" in html_without
    assert "Entrevista: No realizada" in html_without
    assert "Entrevista OK" not in html_with


def test_endpoint_perfil_candidata_requiere_acceso_y_devuelve_imagen():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    fila = 990517

    with flask_app.app_context():
        _ensure_tables()
        _seed_candidata(fila, has_profile=True)

    resp_noauth = client.get(f"/perfil_candidata?fila={fila}", follow_redirects=False)
    assert resp_noauth.status_code in (302, 401, 403)

    _login_staff(client)
    resp = client.get(f"/perfil_candidata?fila={fila}", follow_redirects=False)
    assert resp.status_code == 200
    assert (resp.content_type or "").startswith("image/")


def test_listado_candidatas_web_sigue_operativo():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    fila = 990509

    with flask_app.app_context():
        _ensure_tables()
        _seed_candidata(fila)

    _login_staff(client)
    resp = client.get("/admin/candidatas-web", follow_redirects=False)
    assert resp.status_code == 200
