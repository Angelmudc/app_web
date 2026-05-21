# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import re
from datetime import timedelta

from app import app as flask_app
from config_app import db
from models import Candidata, CatalogoPrivado, CatalogoPrivadoItem, Cliente, Solicitud
from tests.t1_testkit import ensure_sqlite_compat_tables
from utils.timezone import utc_now_naive


_CSRF_RE = re.compile(r'name="csrf_token"\s+value="([^"]+)"')


def _token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _ensure_tables() -> None:
    ensure_sqlite_compat_tables(
        [Candidata, CatalogoPrivado, CatalogoPrivadoItem, Cliente, Solicitud],
        reset=False,
    )


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


def _seed_catalog_for_editorial(token: str, fila: int) -> None:
    now = utc_now_naive()
    cand = Candidata(
        fila=fila,
        nombre_completo=f"Candidata Editorial {fila}",
        cedula=f"{fila:011d}"[-11:],
        codigo=f"ED-{fila}",
    )
    db.session.add(cand)

    cat = CatalogoPrivado(
        nombre=f"Catalogo Editorial {fila}",
        token_hash=_token_hash(token),
        token_hint=token[-12:],
        is_active=True,
        expires_at=now + timedelta(days=7),
        created_by="pytest",
    )
    db.session.add(cat)
    db.session.flush()

    db.session.add(
        CatalogoPrivadoItem(
            catalogo_id=cat.id,
            candidata_id=cand.fila,
            orden=1,
            is_visible=True,
        )
    )
    db.session.commit()


def test_panel_editorial_guarda_datos_publicos_y_catalogo_publico_refleja_valor():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_catalog_for_editorial("tok_editorial_reflejo", fila=990071)

    _login_staff(client)

    payload = {
        "estado_publico": "disponible",
        "nombre_publico": "Perfil Editorial",
        "modalidad_publica": "Con dormida",
        "tags_publicos": "Limpieza general",
        "return_to": "/admin/candidatas-web/990071",
    }
    post_resp = client.post("/admin/candidatas-web/990071", data=payload, follow_redirects=False)
    assert post_resp.status_code in (302, 303)

    public_detail = client.get("/catalogo/tok_editorial_reflejo/candidata/990071", follow_redirects=False)
    assert public_detail.status_code == 200

    html = public_detail.get_data(as_text=True)
    assert "Perfil Editorial" in html
    assert "Con dormida" in html
    assert "/admin" not in html.lower()
