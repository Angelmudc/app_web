# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import timedelta

from app import app as flask_app
from config_app import db
from models import Candidata, CandidataWeb, CatalogoPrivado
from tests.t1_testkit import ensure_sqlite_compat_tables
from utils.catalogo_privado_tokens import catalogo_privado_token_hash
from utils.timezone import utc_now_naive


def _ensure_tables() -> None:
    ensure_sqlite_compat_tables([Candidata, CandidataWeb, CatalogoPrivado], reset=False)


def _seed_mobile_data(token: str = "tok_mobile_ux") -> int:
    cat = CatalogoPrivado(
        nombre="Catalogo Mobile UX",
        token_hash=catalogo_privado_token_hash(token),
        token_hint=token[-12:],
        scope_mode="all_available_store",
        is_active=True,
        expires_at=utc_now_naive() + timedelta(days=5),
        created_by="pytest",
    )
    db.session.add(cat)
    db.session.flush()

    cand = Candidata(
        fila=995881,
        nombre_completo="Mobile UX",
        cedula="00112349999",
        codigo="MOBILE-1",
    )
    db.session.add(cand)
    db.session.flush()

    db.session.add(
        CandidataWeb(
            candidata_id=cand.fila,
            visible=True,
            estado_publico="disponible",
            nombre_publico="Perfil Mobile Uno",
            ciudad_publica="Santiago",
            modalidad_publica="Con dormida",
            tags_publicos="Limpieza general",
            experiencia_resumen="UX check",
            entrevista_publica_resumen="UX check",
            disponible_inmediato=True,
        )
    )
    db.session.commit()
    return int(cand.fila)


def test_private_store_mobile_ux_html_structure_and_realtime_hooks():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_tables()
        candidata_id = _seed_mobile_data()

    token = "tok_mobile_ux"
    list_resp = client.get(f"/tienda/{token}", follow_redirects=False)
    assert list_resp.status_code == 200
    list_html = list_resp.get_data(as_text=True)

    assert "ps-mobile-bottom-bar" in list_html
    assert "Filtros" in list_html
    assert "Selección" in list_html
    assert "Solicitar" in list_html
    assert "data-filter-drawer" in list_html
    assert "data-filter-open" in list_html
    assert "js/private_store.js" in list_html
    assert "data-store-action=\"add\"" in list_html
    assert "data-candidata-id" in list_html
    assert "data-token" in list_html
    assert "data-selection-count" in list_html
    assert "name=\"q\"" not in list_html

    client.post(
        f"/tienda/{token}/seleccion/agregar",
        data={"candidata_id": str(candidata_id), "return_to": f"/tienda/{token}"},
        follow_redirects=False,
    )

    checkout = client.get(f"/tienda/{token}/solicitar-entrevistas", follow_redirects=False)
    assert checkout.status_code == 200
    checkout_html = checkout.get_data(as_text=True)
    assert "ps-mobile-checkout-cta" in checkout_html
    assert "form=\"ps-checkout-form\"" in checkout_html
    assert "Solicitar entrevistas" in checkout_html
