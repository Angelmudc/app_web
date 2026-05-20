# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
from datetime import timedelta

from app import app as flask_app
from config_app import db
from models import Candidata, CandidataWeb, CatalogoPrivado
from tests.t1_testkit import ensure_sqlite_compat_tables
from utils.timezone import utc_now_naive


def _token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _ensure_tables() -> None:
    ensure_sqlite_compat_tables([Candidata, CandidataWeb, CatalogoPrivado], reset=False)


def _seed_catalog(token: str, *, is_active: bool = True, expires_days: int = 7) -> CatalogoPrivado:
    cat = CatalogoPrivado(
        nombre="Store privada realtime",
        token_hash=_token_hash(token),
        token_hint=token[-12:],
        scope_mode="all_available_store",
        is_active=is_active,
        expires_at=utc_now_naive() + timedelta(days=expires_days),
        created_by="pytest",
    )
    db.session.add(cat)
    db.session.commit()
    return cat


def _seed_candidates(seed: int = 41):
    base = 996000 + seed * 10
    c1 = Candidata(fila=base + 1, nombre_completo="Ana R", cedula=f"{base+1:011d}", codigo=f"RTA-{seed}-1")
    c2 = Candidata(fila=base + 2, nombre_completo="Berta R", cedula=f"{base+2:011d}", codigo=f"RTA-{seed}-2")
    db.session.add_all([c1, c2])
    db.session.flush()
    db.session.add_all([
        CandidataWeb(candidata_id=c1.fila, visible=True, estado_publico="disponible", nombre_publico="Ana Publica", modalidad_publica="Con dormida", tags_publicos="Limpieza", experiencia_resumen="Cocina", experiencia_detallada="Planchar", disponible_inmediato=True),
        CandidataWeb(candidata_id=c2.fila, visible=True, estado_publico="disponible", nombre_publico="Berta Publica", modalidad_publica="Salida diaria", tags_publicos="Cocina", experiencia_resumen="Lavar", experiencia_detallada="Ninos", disponible_inmediato=False),
    ])
    db.session.commit()
    return int(c1.fila), int(c2.fila)


def _json_headers():
    return {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}


def test_realtime_json_add_remove_clear_and_html_fallback():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_catalog("tok_rt_ops")
        c1, _c2 = _seed_candidates(seed=41)

    add_resp = client.post(f"/tienda/tok_rt_ops/seleccion/agregar", data={"candidata_id": str(c1)}, headers=_json_headers())
    assert add_resp.status_code == 200
    add_data = add_resp.get_json()
    assert add_data["ok"] is True
    assert add_data["selection_count"] == 1
    assert c1 in add_data["selected_ids"]

    remove_resp = client.post(f"/tienda/tok_rt_ops/seleccion/quitar", data={"candidata_id": str(c1)}, headers=_json_headers())
    assert remove_resp.status_code == 200
    remove_data = remove_resp.get_json()
    assert remove_data["ok"] is True
    assert remove_data["selection_count"] == 0

    clear_resp = client.post(f"/tienda/tok_rt_ops/seleccion/limpiar", data={}, headers=_json_headers())
    assert clear_resp.status_code == 200
    clear_data = clear_resp.get_json()
    assert clear_data["ok"] is True
    assert clear_data["selected_ids"] == []

    html_fallback = client.post(f"/tienda/tok_rt_ops/seleccion/agregar", data={"candidata_id": str(c1), "return_to": "/tienda/tok_rt_ops"}, follow_redirects=False)
    assert html_fallback.status_code in (302, 303)


def test_realtime_estado_json_valid_invalid_expired_and_privacy():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_catalog("tok_rt_state")
        _seed_catalog("tok_rt_expired", expires_days=-1)
        c1, _c2 = _seed_candidates(seed=42)

    client.post(f"/tienda/tok_rt_state/seleccion/agregar", data={"candidata_id": str(c1)}, headers=_json_headers())
    state_resp = client.get(f"/tienda/tok_rt_state/estado.json", headers=_json_headers())
    assert state_resp.status_code == 200
    data = state_resp.get_json()
    assert data["ok"] is True
    assert "catalogo_id" in data
    assert "stats" in data
    assert "telefono" not in str(data).lower()
    assert "cedula" not in str(data).lower()
    assert "direccion" not in str(data).lower()
    assert "score" not in str(data).lower()
    assert "token_hash" not in str(data).lower()

    invalid_resp = client.get(f"/tienda/tok_rt_invalid/estado.json", headers=_json_headers())
    assert invalid_resp.status_code == 404
    assert invalid_resp.get_json()["ok"] is False

    expired_resp = client.get(f"/tienda/tok_rt_expired/estado.json", headers=_json_headers())
    assert expired_resp.status_code == 410
    assert expired_resp.get_json()["ok"] is False


def test_realtime_estado_removes_unavailable_selected_ids():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        _seed_catalog("tok_rt_cleanup")
        c1, _c2 = _seed_candidates(seed=43)

    client.post(f"/tienda/tok_rt_cleanup/seleccion/agregar", data={"candidata_id": str(c1)}, headers=_json_headers())

    with flask_app.app_context():
        row = CandidataWeb.query.filter_by(candidata_id=int(c1)).first()
        row.visible = False
        db.session.commit()

    state_resp = client.get(f"/tienda/tok_rt_cleanup/estado.json", headers=_json_headers())
    assert state_resp.status_code == 200
    payload = state_resp.get_json()
    assert payload["ok"] is True
    assert c1 in payload["removed_unavailable_ids"]
    assert c1 not in payload["selected_ids"]
