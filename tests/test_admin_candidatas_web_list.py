# -*- coding: utf-8 -*-

from __future__ import annotations

import re

from app import app as flask_app
from config_app import db
from models import Candidata, CandidataWeb
from tests.t1_testkit import ensure_sqlite_compat_tables


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


def _seed_candidatas(total: int = 120) -> None:
    ensure_sqlite_compat_tables([Candidata, CandidataWeb], reset=False)
    db.session.query(CandidataWeb).filter(CandidataWeb.candidata_id >= 880000).delete(synchronize_session=False)
    db.session.query(Candidata).filter(Candidata.fila >= 880000).delete(synchronize_session=False)
    db.session.commit()
    for i in range(total):
        fila = 880000 + i
        cand = Candidata(
            fila=fila,
            nombre_completo=f"Candidata Web {i}",
            cedula=f"{fila:011d}"[-11:],
            codigo=f"WEB-{fila}",
        )
        db.session.add(cand)
        ficha = CandidataWeb(
            candidata_id=fila,
            visible=(i % 3 != 0),
            estado_publico="reservada" if i % 2 == 0 else "disponible",
            es_destacada=(i % 10 == 0),
            ciudad_publica="Santiago" if i % 4 == 0 else "Santo Domingo",
            sector_publico="Centro",
            modalidad_publica="Dormida" if i % 5 == 0 else "Salida",
            nombre_publico=f"Perfil Candidata Web {i}",
        )
        db.session.add(ficha)
    db.session.commit()


def _row_count(html: str) -> int:
    return html.count('data-cw-row="1"')


def test_candidatas_web_default_no_carga_mas_de_50_filas():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _seed_candidatas(120)

    _login_staff(client)
    resp = client.get("/admin/candidatas-web", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert _row_count(html) == 50
    assert "Mostrando perfiles recientes. Usa búsqueda para encontrar más candidatas." in html
    assert "de 120 resultado(s)" in html


def test_candidatas_web_busqueda_por_nombre_server_side():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _seed_candidatas(80)

    _login_staff(client)
    resp = client.get("/admin/candidatas-web?q=Candidata+Web+17", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Candidata Web 17" in html
    assert "Candidata Web 18" not in html


def test_candidatas_web_filtro_por_estado_publico_funciona():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _seed_candidatas(60)

    _login_staff(client)
    resp = client.get("/admin/candidatas-web?estado_publico=reservada&per_page=100", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert _row_count(html) == 30
    assert ">disponible</td>" not in html


def test_candidatas_web_paginacion_conserva_query_params():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _seed_candidatas(120)

    _login_staff(client)
    resp = client.get(
        "/admin/candidatas-web?q=Candidata&estado_publico=reservada&per_page=25&page=2",
        follow_redirects=False,
    )
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Página 2 de" in html
    assert "q=Candidata" in html
    assert "estado_publico=reservada" in html
    assert "per_page=25" in html
    assert "page=1" in html


def test_candidatas_web_endpoint_sigue_accesible_para_roles_permitidos():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _seed_candidatas(10)

    _login_staff(client)
    resp = client.get("/admin/candidatas-web", follow_redirects=False)
    assert resp.status_code == 200
