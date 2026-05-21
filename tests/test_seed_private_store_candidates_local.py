# -*- coding: utf-8 -*-

from __future__ import annotations

from app import app as flask_app
from config_app import db
from models import (
    Candidata,
    CandidataWeb,
    CatalogoPrivado,
    CatalogoPrivadoItem,
    Cliente,
    Solicitud,
    Entrevista,
    EntrevistaPregunta,
    EntrevistaRespuesta,
)
from scripts.local import seed_private_store_candidates as seed_script
from tests.t1_testkit import ensure_sqlite_compat_tables


def _ensure_tables() -> None:
    ensure_sqlite_compat_tables(
        [
            Candidata,
            CandidataWeb,
            CatalogoPrivado,
            CatalogoPrivadoItem,
            Cliente,
            Solicitud,
            Entrevista,
            EntrevistaPregunta,
            EntrevistaRespuesta,
        ],
        reset=False,
    )


def test_seed_aborta_en_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    with flask_app.app_context():
        try:
            seed_script.run_seed(reset=True)
            assert False, "Debe abortar en producción"
        except RuntimeError as exc:
            assert "APP_ENV=production" in str(exc)


def test_seed_local_crea_100_y_flujo_tienda_privacidad(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("FLASK_ENV", raising=False)

    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    try:
        with flask_app.app_context():
            _ensure_tables()
            result = seed_script.run_seed(reset=True)
            assert result.created == 100

            candidatas = (
                Candidata.query.filter(Candidata.codigo.like(f"{seed_script.SEED_PREFIX}%")).all()
            )
            assert len(candidatas) == 100
            ids = [int(c.fila) for c in candidatas]

            fichas = CandidataWeb.query.filter(CandidataWeb.candidata_id.in_(ids)).all()
            assert len(fichas) == 100

            visibles = sum(1 for f in fichas if bool(f.visible))
            disponibles = sum(1 for f in fichas if str(f.estado_publico) == "disponible")
            assert visibles >= 70
            assert disponibles >= 60

            assert all((f.entrevista_publica_resumen or "").strip() for f in fichas)
            assert all((f.experiencia_resumen or "").strip() for f in fichas)

            perfiles = [c.perfil for c in candidatas if c.perfil]
            assert len(perfiles) >= 70
            assert sum(1 for b in perfiles if b.startswith(b"\x89PNG")) >= 70
            assert all((c.entrevista or "").strip() for c in candidatas)

            entrevistas = Entrevista.query.filter(Entrevista.candidata_id.in_(ids)).all()
            assert len(entrevistas) == 100
            entrevista_ids = [int(x.id) for x in entrevistas]
            respuestas = EntrevistaRespuesta.query.filter(EntrevistaRespuesta.entrevista_id.in_(entrevista_ids)).all()
            assert len(respuestas) >= 1000
            preguntas = EntrevistaPregunta.query.filter(EntrevistaPregunta.clave.like("domestica.%")).all()
            textos = {(q.texto or "").strip().lower() for q in preguntas}
            assert "nombre completo" in textos
            assert "¿tienes hijos?" in textos
            assert "¿quién cuida a sus hijos?" in textos
            assert "¿sabes cocinar?" in textos
            assert "¿sabes planchar?" in textos
            for forbidden in [
                "modalidad preferida",
                "tipo de hogar trabajado",
                "fortaleza principal",
                "personalidad en trabajo",
                "disponibilidad declarada",
            ]:
                assert forbidden not in textos

            cat = CatalogoPrivado.query.filter_by(token_hash=seed_script._token_hash(seed_script.DEFAULT_TOKEN)).first()
            assert cat is not None
            assert cat.scope_mode == "all_available_store"
            assert bool(cat.is_active) is True

        token = seed_script.DEFAULT_TOKEN
        list_resp = client.get(f"/tienda/{token}", follow_redirects=False)
        assert list_resp.status_code == 200
        list_html = list_resp.get_data(as_text=True)
        assert "QA-TIENDA-" in list_html

        with flask_app.app_context():
            seed_web = (
                CandidataWeb.query
                .filter(CandidataWeb.candidata_id.in_(ids))
                .filter_by(visible=True, estado_publico="disponible")
                .order_by(CandidataWeb.id.asc())
                .first()
            )
            assert seed_web is not None
            cand_id = int(seed_web.candidata_id)
            entrevista = Entrevista.query.filter_by(candidata_id=cand_id).order_by(Entrevista.id.desc()).first()
            assert entrevista is not None
            rows = (
                db.session.query(EntrevistaPregunta, EntrevistaRespuesta)
                .join(EntrevistaRespuesta, EntrevistaRespuesta.pregunta_id == EntrevistaPregunta.id)
                .filter(EntrevistaRespuesta.entrevista_id == int(entrevista.id))
                .all()
            )
            by_key = {
                (q.clave or "").strip().lower(): ((q.texto or "").strip().lower(), (r.respuesta or "").strip().lower())
                for q, r in rows
            }
            assert "domestica.edad" in by_key
            assert "domestica.tienes_hijos" in by_key
            assert "domestica.sabes_cocinar" in by_key
            assert "domestica.planchar" in by_key

        detail_resp = client.get(f"/tienda/{token}/domesticas/{cand_id}", follow_redirects=False)
        assert detail_resp.status_code == 200
        detail_html = detail_resp.get_data(as_text=True)
        assert "Ver entrevista protegida" in detail_html

        interview_resp = client.get(f"/tienda/{token}/domesticas/{cand_id}/entrevista", follow_redirects=False)
        assert interview_resp.status_code == 200
        interview_html = interview_resp.get_data(as_text=True).lower()
        assert "información protegida por la agencia" in interview_html
        assert "nombre completo" in interview_html
        for key in ["domestica.edad", "domestica.tienes_hijos", "domestica.sabes_cocinar", "domestica.planchar"]:
            label, value = by_key[key]
            assert label in interview_html
            assert value in interview_html
        for forbidden in ["fortaleza principal", "tipo de hogar trabajado", "modalidad preferida"]:
            assert forbidden not in interview_html
        for visible in ["nombre completo", "¿tienes hijos?", "¿quién cuida a sus hijos?", "¿sabes cocinar?", "¿sabes planchar?"]:
            assert visible in interview_html
        for forbidden in ["calle ", "residencial qa torre", "apto "]:
            assert forbidden not in interview_html

        lowered = detail_html.lower()
        for forbidden in ["cédula", "cedula", "telefono", "dirección", "direccion", "referencias"]:
            assert forbidden not in lowered
    finally:
        with flask_app.app_context():
            seed_script._delete_seed_candidates()
            CatalogoPrivado.query.filter_by(
                token_hash=seed_script._token_hash(seed_script.DEFAULT_TOKEN)
            ).delete(synchronize_session=False)
            db.session.commit()
