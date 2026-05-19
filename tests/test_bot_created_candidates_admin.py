# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from app import app as flask_app
from config_app import db
from models import BotCandidateDraft, Candidata, Cliente, StaffAuditLog
from sqlalchemy import text
from services.bot_rate_limit_service import reset_rate_limits


@pytest.fixture(autouse=True)
def _reset_bot_rate_limits_each_test():
    reset_rate_limits()
    yield
    reset_rate_limits()


def _login_staff(client, usuario: str = "Owner", clave: str = "admin123") -> None:
    data = {"usuario": usuario, "clave": clave}
    if bool(flask_app.config.get("WTF_CSRF_ENABLED")):
        login_page = client.get("/admin/login", follow_redirects=False)
        assert login_page.status_code == 200
        data["csrf_token"] = _extract_csrf(login_page.get_data(as_text=True))
    resp = client.post("/admin/login", data=data, follow_redirects=False)
    assert resp.status_code in (302, 303)


def _extract_csrf(html: str) -> str:
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert m is not None, "No se encontró csrf_token en login."
    return m.group(1)


def _seed_candidate(
    *,
    nombre: str,
    cedula: str,
    estado: str = "en_proceso",
    origen_registro: str | None = None,
    creado_desde_ruta: str | None = None,
    telefono: str = "8095550000",
    direccion: str = "Santo Domingo",
) -> Candidata:
    cand = Candidata(
        nombre_completo=nombre,
        cedula=cedula,
        estado=estado,
        numero_telefono=telefono,
        direccion_completa=direccion,
    )
    if origen_registro is not None:
        setattr(cand, "origen_registro", origen_registro)
    if creado_desde_ruta is not None:
        setattr(cand, "creado_desde_ruta", creado_desde_ruta)
    db.session.add(cand)
    return cand


def _count_candidatas_web() -> int | None:
    try:
        return int(db.session.execute(text("SELECT COUNT(*) FROM candidatas_web")).scalar() or 0)
    except Exception:
        return None


def test_bot_created_candidates_route_requires_staff():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    anon = client.get("/admin/bot/candidatas-creadas", follow_redirects=False)
    assert anon.status_code in (302, 303)
    assert "/admin/login" in (anon.headers.get("Location") or "")

    with flask_app.app_context():
        cliente = Cliente(
            codigo="CL-BOT-CREATED-ROUTE-1",
            nombre_completo="Cliente No Staff",
            email="cliente.no.staff.bot.created@example.com",
            telefono="8095551111",
            password_hash="DISABLED_RESET_REQUIRED",
        )
        db.session.add(cliente)
        db.session.commit()
        cliente_id = int(cliente.id)

    with client.session_transaction() as sess:
        sess["_user_id"] = str(cliente_id)
        sess["_fresh"] = True
        sess["is_admin_session"] = False

    non_staff = client.get("/admin/bot/candidatas-creadas", follow_redirects=False)
    assert non_staff.status_code in (302, 303)
    assert "/admin/login" in (non_staff.headers.get("Location") or "")


def test_bot_created_candidates_lists_only_bot_created_and_handles_malformed_route():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    c1 = SimpleNamespace(
        fila=110,
        nombre_completo="Candidata Bot 1",
        telefono_e164=None,
        numero_telefono="8095552001",
        direccion_completa="Santo Domingo",
        estado="en_proceso",
        origen_registro="bot_draft",
        creado_desde_ruta="bot_draft:77",
        marca_temporal="2026-05-09 10:00:00",
    )
    c2 = SimpleNamespace(
        fila=111,
        nombre_completo="Candidata Bot 2",
        telefono_e164=None,
        numero_telefono="8095552002",
        direccion_completa="Santiago",
        estado="descalificada",
        origen_registro=None,
        creado_desde_ruta="bot_draft:not-an-int",
        marca_temporal="2026-05-09 10:05:00",
    )

    class _CandidateQuery:
        def filter(self, *_a, **_k):
            return self

        def order_by(self, *_a, **_k):
            return self

        def limit(self, *_a, **_k):
            return self

        def all(self):
            return [c1, c2]

    class _DraftQuery:
        def with_entities(self, *_a, **_k):
            return self

        def filter_by(self, *_a, **_k):
            return self

        def first(self):
            return SimpleNamespace(metadata_json={}, reviewer_user=None)

        def filter(self, *_a, **_k):
            return self

        def all(self):
            return [(77, 901)]

    class _Expr:
        def like(self, *_a, **_k):
            return self

        def in_(self, *_a, **_k):
            return self

        def desc(self):
            return self

        def __eq__(self, _other):
            return self

    _login_staff(client)
    with patch(
        "admin.bot_routes.Candidata",
        SimpleNamespace(
            query=_CandidateQuery(),
            origen_registro=_Expr(),
            creado_desde_ruta=_Expr(),
            marca_temporal=_Expr(),
            fila=_Expr(),
        ),
    ), patch(
        "admin.bot_routes.BotCandidateDraft",
        SimpleNamespace(id=_Expr(), conversation_id=_Expr(), query=_DraftQuery()),
    ), patch("admin.bot_routes.sa_inspect", return_value=SimpleNamespace(get_columns=lambda _t: [{"name": "origen_registro"}, {"name": "creado_desde_ruta"}])), patch(
        "admin.bot_routes.or_", return_value=object()
    ):
        resp = client.get("/admin/bot/candidatas-creadas", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Candidata Bot 1" in html
    assert "Candidata Bot 2" in html
    assert "77" in html
    assert "bot_draft:not-an-int" in html
    assert "/admin/bot/conversaciones/901" in html
    assert "/buscar?candidata_id=110" in html


def test_bot_created_candidates_metrics_and_read_only():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    rows = [
        SimpleNamespace(
            fila=210,
            nombre_completo="Cand M1",
            numero_telefono="8095553001",
            telefono_e164=None,
            direccion_completa="SD",
            estado="en_proceso",
            origen_registro="bot_draft",
            creado_desde_ruta="bot_draft:9911",
            marca_temporal="2026-05-09 09:00:00",
        ),
        SimpleNamespace(
            fila=211,
            nombre_completo="Cand M2",
            numero_telefono="8095553002",
            telefono_e164=None,
            direccion_completa="SD",
            estado="lista_para_trabajar",
            origen_registro="bot_draft",
            creado_desde_ruta="bot_draft:9912",
            marca_temporal="2026-05-09 09:01:00",
        ),
        SimpleNamespace(
            fila=212,
            nombre_completo="Cand M3",
            numero_telefono="8095553003",
            telefono_e164=None,
            direccion_completa="SD",
            estado="descalificada",
            origen_registro="bot_draft",
            creado_desde_ruta="bot_draft:9913",
            marca_temporal="2026-05-09 09:02:00",
        ),
    ]

    class _CandidateQuery:
        def filter(self, *_a, **_k):
            return self

        def order_by(self, *_a, **_k):
            return self

        def limit(self, *_a, **_k):
            return self

        def all(self):
            return rows

    class _DraftQuery:
        def with_entities(self, *_a, **_k):
            return self

        def filter_by(self, *_a, **_k):
            return self

        def first(self):
            return None

        def filter(self, *_a, **_k):
            return self

        def all(self):
            return []

    class _Expr:
        def like(self, *_a, **_k):
            return self

        def in_(self, *_a, **_k):
            return self

        def desc(self):
            return self

        def __eq__(self, _other):
            return self

    _login_staff(client)
    with patch(
        "admin.bot_routes.Candidata",
        SimpleNamespace(
            query=_CandidateQuery(),
            origen_registro=_Expr(),
            creado_desde_ruta=_Expr(),
            marca_temporal=_Expr(),
            fila=_Expr(),
        ),
    ), patch(
        "admin.bot_routes.BotCandidateDraft",
        SimpleNamespace(id=_Expr(), conversation_id=_Expr(), query=_DraftQuery()),
    ), patch("admin.bot_routes.sa_inspect", return_value=SimpleNamespace(get_columns=lambda _t: [{"name": "origen_registro"}, {"name": "creado_desde_ruta"}])), patch(
        "admin.bot_routes.or_", return_value=object()
    ):
        resp = client.get("/admin/bot/candidatas-creadas", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Total creadas desde bot" in html
    assert "Revisadas/listas" in html
    assert "Rechazadas/descalificadas" in html
    assert re.search(r"Total creadas desde bot</div>\s*<div class=\"h5 mb-0\">3</div>", html)
    assert re.search(r"en_proceso</div>\s*<div class=\"h5 mb-0\">1</div>", html)
    assert re.search(r"Revisadas/listas</div>\s*<div class=\"h5 mb-0\">1</div>", html)
    assert re.search(r"Rechazadas/descalificadas</div>\s*<div class=\"h5 mb-0\">1</div>", html)


def test_bot_created_candidates_manual_review_full_flow_and_audit_and_manual_only():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    _login_staff(client)

    with flask_app.app_context():
        last = db.session.query(BotCandidateDraft).order_by(BotCandidateDraft.conversation_id.desc()).first()
        next_conversation_id = int(getattr(last, "conversation_id", 5000) or 5000) + 1
        draft = BotCandidateDraft(conversation_id=next_conversation_id, draft_status="created_real", metadata_json={})
        db.session.add(draft)
        db.session.commit()
        draft_id = int(draft.id)
        candidata_id = 1991001 + draft_id
        fake_cand = SimpleNamespace(fila=candidata_id, estado="en_proceso", creado_desde_ruta=f"bot_draft:{draft_id}")

    class _CandidateQuery:
        def filter_by(self, **kwargs):
            assert int(kwargs.get("fila")) == candidata_id
            return self

        def first(self):
            return fake_cand

    with patch("admin.bot_routes.Candidata", SimpleNamespace(query=_CandidateQuery())):
        take = client.post(f"/admin/bot/candidatas-creadas/{candidata_id}/review/take", data={}, follow_redirects=False)
        assert take.status_code in (302, 303)
        approve = client.post(f"/admin/bot/candidatas-creadas/{candidata_id}/review/approve", data={}, follow_redirects=False)
        assert approve.status_code in (302, 303)

    with flask_app.app_context():
        persisted_draft = db.session.get(BotCandidateDraft, draft_id)
        wf = dict((persisted_draft.metadata_json or {}).get("bot_review_workflow") or {})
        assert wf.get("status") == "bot_approved"
        assert wf.get("reviewer_id")
        assert wf.get("review_taken_at")
        assert wf.get("approved_at")
        actions = [x.action_type for x in StaffAuditLog.query.filter(StaffAuditLog.entity_id == str(candidata_id)).all()]
        assert "bot_candidate_review_taken" in actions
        assert "bot_candidate_review_approved" in actions


def test_bot_created_candidates_manual_review_invalid_transitions_and_reject_reason():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    _login_staff(client)

    with flask_app.app_context():
        last = db.session.query(BotCandidateDraft).order_by(BotCandidateDraft.conversation_id.desc()).first()
        next_conversation_id = int(getattr(last, "conversation_id", 7000) or 7000) + 1
        draft = BotCandidateDraft(conversation_id=next_conversation_id, draft_status="created_real", metadata_json={})
        db.session.add(draft)
        db.session.commit()
        draft_id = int(draft.id)
        candidata_id = 1992002 + draft_id
        fake_cand = SimpleNamespace(fila=candidata_id, estado="en_proceso", creado_desde_ruta=f"bot_draft:{draft_id}")
        rejected_logs_before = (
            StaffAuditLog.query.filter_by(
                action_type="bot_candidate_review_rejected",
                entity_type="Candidata",
                entity_id=str(candidata_id),
            ).count()
        )

    class _CandidateQuery:
        def filter_by(self, **kwargs):
            assert int(kwargs.get("fila")) == candidata_id
            return self

        def first(self):
            return fake_cand

    with patch("admin.bot_routes.Candidata", SimpleNamespace(query=_CandidateQuery())):
        bad_approve = client.post(f"/admin/bot/candidatas-creadas/{candidata_id}/review/approve", data={}, follow_redirects=False)
        assert bad_approve.status_code in (302, 303)
        take = client.post(f"/admin/bot/candidatas-creadas/{candidata_id}/review/take", data={}, follow_redirects=False)
        assert take.status_code in (302, 303)
        reject = client.post(
            f"/admin/bot/candidatas-creadas/{candidata_id}/review/reject",
            data={"reason": "Datos inconsistentes"},
            follow_redirects=False,
        )
        assert reject.status_code in (302, 303)
        bad_take_again = client.post(f"/admin/bot/candidatas-creadas/{candidata_id}/review/take", data={}, follow_redirects=False)
        assert bad_take_again.status_code in (302, 303)
        bad_approve_after_reject = client.post(f"/admin/bot/candidatas-creadas/{candidata_id}/review/approve", data={}, follow_redirects=False)
        assert bad_approve_after_reject.status_code in (302, 303)

    with flask_app.app_context():
        persisted_draft = db.session.get(BotCandidateDraft, draft_id)
        wf = dict((persisted_draft.metadata_json or {}).get("bot_review_workflow") or {})
        assert wf.get("status") == "bot_rejected"
        assert wf.get("rejection_reason") == "Datos inconsistentes"
        rejected_logs = StaffAuditLog.query.filter_by(
            action_type="bot_candidate_review_rejected",
            entity_type="Candidata",
            entity_id=str(candidata_id),
        ).all()
        assert len(rejected_logs) == rejected_logs_before + 1
        meta = dict(rejected_logs[-1].metadata_json or {})
        assert meta.get("previous_status") == "bot_reviewing"
        assert meta.get("new_status") == "bot_rejected"
        assert meta.get("reason") == "Datos inconsistentes"


def test_bot_created_candidates_review_routes_require_post_and_enforce_csrf():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = True
    client = flask_app.test_client()
    _login_staff(client)

    with flask_app.app_context():
        last = db.session.query(BotCandidateDraft).order_by(BotCandidateDraft.conversation_id.desc()).first()
        next_conversation_id = int(getattr(last, "conversation_id", 9000) or 9000) + 1
        draft = BotCandidateDraft(conversation_id=next_conversation_id, draft_status="created_real", metadata_json={})
        db.session.add(draft)
        db.session.commit()
        draft_id = int(draft.id)
        candidata_id = 1993003 + draft_id
        fake_cand = SimpleNamespace(fila=candidata_id, estado="en_proceso", creado_desde_ruta=f"bot_draft:{draft_id}")

    class _CandidateQuery:
        def filter_by(self, **kwargs):
            assert int(kwargs.get("fila")) == candidata_id
            return self

        def first(self):
            return fake_cand

    with patch("admin.bot_routes.Candidata", SimpleNamespace(query=_CandidateQuery())):
        get_take = client.get(f"/admin/bot/candidatas-creadas/{candidata_id}/review/take", follow_redirects=False)
        get_approve = client.get(f"/admin/bot/candidatas-creadas/{candidata_id}/review/approve", follow_redirects=False)
        get_reject = client.get(f"/admin/bot/candidatas-creadas/{candidata_id}/review/reject", follow_redirects=False)
        assert get_take.status_code == 405
        assert get_approve.status_code == 405
        assert get_reject.status_code == 405

        no_csrf_take = client.post(f"/admin/bot/candidatas-creadas/{candidata_id}/review/take", data={}, follow_redirects=False)
        assert no_csrf_take.status_code in (302, 303)
        assert f"/admin/bot/candidatas-creadas/{candidata_id}/review/take" in (no_csrf_take.headers.get("Location") or "")

    with flask_app.app_context():
        persisted_draft = db.session.get(BotCandidateDraft, draft_id)
        wf = dict((persisted_draft.metadata_json or {}).get("bot_review_workflow") or {})
        assert wf.get("status") in (None, "bot_pending_review")


def test_bot_created_candidates_second_take_is_blocked_with_audit_and_no_mutation():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    _login_staff(client)

    with flask_app.app_context():
        last = db.session.query(BotCandidateDraft).order_by(BotCandidateDraft.conversation_id.desc()).first()
        next_conversation_id = int(getattr(last, "conversation_id", 10000) or 10000) + 1
        draft = BotCandidateDraft(conversation_id=next_conversation_id, draft_status="created_real", metadata_json={})
        db.session.add(draft)
        db.session.commit()
        draft_id = int(draft.id)
        candidata_id = 1994004 + draft_id
        fake_cand = SimpleNamespace(fila=candidata_id, estado="en_proceso", creado_desde_ruta=f"bot_draft:{draft_id}")
        web_before = _count_candidatas_web()
        blocked_before = (
            StaffAuditLog.query.filter_by(
                action_type="bot_candidate_review_blocked",
                entity_type="Candidata",
                entity_id=str(candidata_id),
            ).count()
        )

    class _CandidateQuery:
        def filter_by(self, **kwargs):
            assert int(kwargs.get("fila")) == candidata_id
            return self

        def first(self):
            return fake_cand

    with patch("admin.bot_routes.Candidata", SimpleNamespace(query=_CandidateQuery())):
        first_take = client.post(f"/admin/bot/candidatas-creadas/{candidata_id}/review/take", data={}, follow_redirects=False)
        second_take = client.post(f"/admin/bot/candidatas-creadas/{candidata_id}/review/take", data={}, follow_redirects=False)
        assert first_take.status_code in (302, 303)
        assert second_take.status_code in (302, 303)

    with flask_app.app_context():
        persisted_draft = db.session.get(BotCandidateDraft, draft_id)
        wf = dict((persisted_draft.metadata_json or {}).get("bot_review_workflow") or {})
        assert wf.get("status") == "bot_reviewing"
        assert wf.get("approved_at") in (None, "")
        assert wf.get("rejected_at") in (None, "")
        blocked = StaffAuditLog.query.filter_by(
            action_type="bot_candidate_review_blocked",
            entity_type="Candidata",
            entity_id=str(candidata_id),
        ).all()
        assert len(blocked) == blocked_before + 1
        blocked_meta = dict(blocked[-1].metadata_json or {})
        assert blocked_meta.get("attempted_action") == "take"
        assert blocked_meta.get("current_status") == "bot_reviewing"
        assert blocked_meta.get("reason") == "state_changed_or_invalid"
        assert fake_cand.estado == "en_proceso"
        web_after = _count_candidatas_web()
        if web_before is not None and web_after is not None:
            assert web_after == web_before


def test_bot_created_candidates_approve_after_approved_is_blocked_with_audit():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    _login_staff(client)

    with flask_app.app_context():
        last = db.session.query(BotCandidateDraft).order_by(BotCandidateDraft.conversation_id.desc()).first()
        next_conversation_id = int(getattr(last, "conversation_id", 11000) or 11000) + 1
        draft = BotCandidateDraft(conversation_id=next_conversation_id, draft_status="created_real", metadata_json={})
        db.session.add(draft)
        db.session.commit()
        draft_id = int(draft.id)
        candidata_id = 1995005 + draft_id
        fake_cand = SimpleNamespace(fila=candidata_id, estado="en_proceso", creado_desde_ruta=f"bot_draft:{draft_id}")
        web_before = _count_candidatas_web()
        blocked_before = (
            StaffAuditLog.query.filter_by(
                action_type="bot_candidate_review_blocked",
                entity_type="Candidata",
                entity_id=str(candidata_id),
            ).count()
        )

    class _CandidateQuery:
        def filter_by(self, **kwargs):
            assert int(kwargs.get("fila")) == candidata_id
            return self

        def first(self):
            return fake_cand

    with patch("admin.bot_routes.Candidata", SimpleNamespace(query=_CandidateQuery())):
        take = client.post(f"/admin/bot/candidatas-creadas/{candidata_id}/review/take", data={}, follow_redirects=False)
        approve = client.post(f"/admin/bot/candidatas-creadas/{candidata_id}/review/approve", data={}, follow_redirects=False)
        approve_again = client.post(f"/admin/bot/candidatas-creadas/{candidata_id}/review/approve", data={}, follow_redirects=False)
        assert take.status_code in (302, 303)
        assert approve.status_code in (302, 303)
        assert approve_again.status_code in (302, 303)

    with flask_app.app_context():
        persisted_draft = db.session.get(BotCandidateDraft, draft_id)
        wf = dict((persisted_draft.metadata_json or {}).get("bot_review_workflow") or {})
        approved_at_before = wf.get("approved_at")
        assert wf.get("status") == "bot_approved"
        blocked = StaffAuditLog.query.filter_by(
            action_type="bot_candidate_review_blocked",
            entity_type="Candidata",
            entity_id=str(candidata_id),
        ).all()
        assert len(blocked) == blocked_before + 1
        blocked_meta = dict(blocked[-1].metadata_json or {})
        assert blocked_meta.get("attempted_action") == "approve"
        assert blocked_meta.get("current_status") == "bot_approved"
        persisted_draft = db.session.get(BotCandidateDraft, draft_id)
        wf_after = dict((persisted_draft.metadata_json or {}).get("bot_review_workflow") or {})
        assert wf_after.get("approved_at") == approved_at_before
        assert fake_cand.estado == "en_proceso"
        web_after = _count_candidatas_web()
        if web_before is not None and web_after is not None:
            assert web_after == web_before


def test_bot_created_candidates_reject_after_rejected_is_blocked_with_audit():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    _login_staff(client)

    with flask_app.app_context():
        last = db.session.query(BotCandidateDraft).order_by(BotCandidateDraft.conversation_id.desc()).first()
        next_conversation_id = int(getattr(last, "conversation_id", 12000) or 12000) + 1
        draft = BotCandidateDraft(conversation_id=next_conversation_id, draft_status="created_real", metadata_json={})
        db.session.add(draft)
        db.session.commit()
        draft_id = int(draft.id)
        candidata_id = 1996006 + draft_id
        fake_cand = SimpleNamespace(fila=candidata_id, estado="en_proceso", creado_desde_ruta=f"bot_draft:{draft_id}")
        web_before = _count_candidatas_web()
        blocked_before = (
            StaffAuditLog.query.filter_by(
                action_type="bot_candidate_review_blocked",
                entity_type="Candidata",
                entity_id=str(candidata_id),
            ).count()
        )

    class _CandidateQuery:
        def filter_by(self, **kwargs):
            assert int(kwargs.get("fila")) == candidata_id
            return self

        def first(self):
            return fake_cand

    with patch("admin.bot_routes.Candidata", SimpleNamespace(query=_CandidateQuery())):
        take = client.post(f"/admin/bot/candidatas-creadas/{candidata_id}/review/take", data={}, follow_redirects=False)
        reject = client.post(f"/admin/bot/candidatas-creadas/{candidata_id}/review/reject", data={"reason": "No cumple"}, follow_redirects=False)
        reject_again = client.post(f"/admin/bot/candidatas-creadas/{candidata_id}/review/reject", data={"reason": "otro"}, follow_redirects=False)
        assert take.status_code in (302, 303)
        assert reject.status_code in (302, 303)
        assert reject_again.status_code in (302, 303)

    with flask_app.app_context():
        persisted_draft = db.session.get(BotCandidateDraft, draft_id)
        wf = dict((persisted_draft.metadata_json or {}).get("bot_review_workflow") or {})
        rejected_at_before = wf.get("rejected_at")
        reason_before = wf.get("rejection_reason")
        assert wf.get("status") == "bot_rejected"
        blocked = StaffAuditLog.query.filter_by(
            action_type="bot_candidate_review_blocked",
            entity_type="Candidata",
            entity_id=str(candidata_id),
        ).all()
        assert len(blocked) == blocked_before + 1
        blocked_meta = dict(blocked[-1].metadata_json or {})
        assert blocked_meta.get("attempted_action") == "reject"
        assert blocked_meta.get("current_status") == "bot_rejected"
        persisted_draft = db.session.get(BotCandidateDraft, draft_id)
        wf_after = dict((persisted_draft.metadata_json or {}).get("bot_review_workflow") or {})
        assert wf_after.get("rejected_at") == rejected_at_before
        assert wf_after.get("rejection_reason") == reason_before
        assert fake_cand.estado == "en_proceso"
        web_after = _count_candidatas_web()
        if web_before is not None and web_after is not None:
            assert web_after == web_before
