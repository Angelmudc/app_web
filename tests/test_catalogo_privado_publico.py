# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
from datetime import timedelta

from app import app as flask_app
from config_app import db
from models import Candidata, CandidataWeb, CatalogoPrivado, CatalogoPrivadoItem, Cliente, Solicitud
from tests.t1_testkit import ensure_sqlite_compat_tables
from utils.timezone import utc_now_naive


def _token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _ensure_tables() -> None:
    ensure_sqlite_compat_tables(
        [Candidata, CandidataWeb, CatalogoPrivado, CatalogoPrivadoItem, Cliente, Solicitud],
        reset=False,
    )


def _seed_candidate(fila: int) -> Candidata:
    cand = Candidata(
        fila=fila,
        nombre_completo=f"Candidata Privada {fila}",
        cedula=f"{fila:011d}"[-11:],
        codigo=f"CP-{fila}",
        numero_telefono="809-555-7788",
        direccion_completa="Calle Privada 123",
        contactos_referencias_laborales="Ref laboral confidencial",
        referencias_familiares_detalle="Ref familiar confidencial",
        entrevista="Nota interna: no publicar",
        modalidad_trabajo_preferida="Con dormida",
        anos_experiencia="4",
        areas_experiencia="Limpieza, cocina",
        empleo_anterior="Casa de familia",
        disponibilidad_inicio="Inmediata",
    )
    db.session.add(cand)
    return cand


def _seed_catalog(
    token: str,
    *,
    active: bool = True,
    expires_delta_days: int = 7,
    include_item: bool = True,
    fila: int = 990001,
) -> tuple[CatalogoPrivado, int]:
    now = utc_now_naive()
    cand = _seed_candidate(fila=fila)

    cat = CatalogoPrivado(
        nombre=f"Catalogo Privado {fila}",
        descripcion="Solo para cliente autorizado",
        token_hash=_token_hash(token),
        token_hint=token[-12:],
        is_active=active,
        expires_at=now + timedelta(days=expires_delta_days),
        created_by="pytest",
    )
    db.session.add(cat)
    db.session.flush()

    if include_item:
        db.session.add(
            CatalogoPrivadoItem(
                catalogo_id=cat.id,
                candidata_id=cand.fila,
                orden=1,
                is_visible=True,
            )
        )

    db.session.add(
        CandidataWeb(
            candidata_id=cand.fila,
            nombre_publico="Ana Perfil Público",
            experiencia_resumen="Experiencia editorial corta",
            experiencia_detallada="Experiencia editorial ampliada",
            entrevista_publica_resumen="Resumen público de entrevista",
            tags_publicos="Limpieza, Cocina",
            modalidad_publica="Con dormida",
            ciudad_publica="Santiago",
            sector_publico="Centro",
            estado_publico="disponible",
            sueldo_texto_publico="RD$ 22,000",
        )
    )

    db.session.commit()
    return cat, int(cand.fila)


def _assert_forbidden_data_not_present(html: str) -> None:
    forbidden = [
        "809-555-7788",
        "00000990011",
        "Calle Privada 123",
        "Ref laboral confidencial",
        "Ref familiar confidencial",
        "Nota interna: no publicar",
        "score",
        "/admin",
    ]
    lower_html = html.lower()
    for token in forbidden:
        assert token.lower() not in lower_html


def test_catalogo_publico_valido_200_muestra_editorial_y_oculta_datos_prohibidos():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_catalog("tok_valido_publico", fila=990011)

    resp = client.get("/catalogo/tok_valido_publico", follow_redirects=False)
    assert resp.status_code == 200

    html = resp.get_data(as_text=True)
    assert "Ana Perfil Público" in html
    assert "Experiencia editorial corta" in html
    _assert_forbidden_data_not_present(html)


def test_detalle_publico_valido_200_muestra_editorial_y_oculta_datos_sensibles_y_admin_links():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _, fila = _seed_catalog("tok_detalle_valido", fila=990021)

    resp = client.get(f"/catalogo/tok_detalle_valido/candidata/{fila}", follow_redirects=False)
    assert resp.status_code == 200

    html = resp.get_data(as_text=True)
    assert "Ana Perfil Público" in html
    assert "Resumen público de entrevista" in html
    assert "Experiencia editorial ampliada" in html

    forbidden = [
        "809-555-7788",
        "cedula",
        "Calle Privada 123",
        "Ref laboral confidencial",
        "Ref familiar confidencial",
        "Nota interna: no publicar",
        "score",
        "/admin",
    ]
    lower_html = html.lower()
    for token in forbidden:
        assert token.lower() not in lower_html


def test_detalle_candidata_no_incluida_devuelve_404():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_catalog("tok_sin_incluir", include_item=False, fila=990031)

    resp = client.get("/catalogo/tok_sin_incluir/candidata/990031", follow_redirects=False)
    assert resp.status_code == 404


def test_catalogo_token_invalido_devuelve_404():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    resp = client.get("/catalogo/token-invalido", follow_redirects=False)
    assert resp.status_code == 404


def test_catalogo_inactivo_devuelve_410():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_catalog("tok_inactivo", active=False, fila=990041)

    resp = client.get("/catalogo/tok_inactivo", follow_redirects=False)
    assert resp.status_code == 410


def test_catalogo_expirado_devuelve_410():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_catalog("tok_expirado", expires_delta_days=-1, fila=990051)

    resp = client.get("/catalogo/tok_expirado", follow_redirects=False)
    assert resp.status_code == 410


def test_html_publico_base_limpio_no_expone_rutas_internas_ni_tokens():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _, fila = _seed_catalog("tok_html_limpio", fila=990061)

    list_resp = client.get("/catalogo/tok_html_limpio", follow_redirects=False)
    detail_resp = client.get(f"/catalogo/tok_html_limpio/candidata/{fila}", follow_redirects=False)
    assert list_resp.status_code == 200
    assert detail_resp.status_code == 200

    html = (list_resp.get_data(as_text=True) + "\n" + detail_resp.get_data(as_text=True)).lower()
    for forbidden in ["/admin", "/clientes", "/login", "csrf_token", "token_hash", "token_hint"]:
        assert forbidden not in html
