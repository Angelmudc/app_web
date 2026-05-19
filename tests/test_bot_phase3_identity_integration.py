# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from app import app as flask_app
from config_app import db
import models as models_module
from models import (
    BotContactIdentity,
    BotConversation,
    BotDecisionLog,
    BotEscalation,
    BotMessage,
    BotSetting,
    Candidata,
    Cliente,
)
from services.bot_identity_service import identify_contact_by_phone
from services.bot_identity_service import find_candidate_phone_duplicates
from services.phone_identity_service import normalize_phone_to_e164, sanitize_phone, validate_possible_phone


@pytest.fixture(autouse=True)
def _force_safe_bot_flags(monkeypatch):
    # Aisla tests legacy de Fase 3 ante estado/env residual de Fase 4.
    monkeypatch.setenv("BOT_AI_ENABLED", "false")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")


def _ensure_min_candidate_table() -> None:
    db.session.execute(
        text(
            "CREATE TABLE IF NOT EXISTS candidatas ("
            "fila INTEGER PRIMARY KEY, "
            "numero_telefono VARCHAR(50), "
            "telefono_e164 VARCHAR(20), "
            "nombre_completo VARCHAR(200), "
            "estado VARCHAR(40)"
            ")"
        )
    )
    cols = {r[1] for r in db.session.execute(text("PRAGMA table_info(candidatas)")).fetchall()}
    if "telefono_e164" not in cols:
        db.session.execute(text("ALTER TABLE candidatas ADD COLUMN telefono_e164 VARCHAR(20)"))
    if "nombre_completo" not in cols:
        db.session.execute(text("ALTER TABLE candidatas ADD COLUMN nombre_completo VARCHAR(200)"))
    if "estado" not in cols:
        db.session.execute(text("ALTER TABLE candidatas ADD COLUMN estado VARCHAR(40)"))
    db.session.commit()


def _ensure_bot_tables() -> None:
    BotEscalation.__table__.drop(bind=db.engine, checkfirst=True)
    BotDecisionLog.__table__.drop(bind=db.engine, checkfirst=True)
    BotMessage.__table__.drop(bind=db.engine, checkfirst=True)
    BotConversation.__table__.drop(bind=db.engine, checkfirst=True)
    BotContactIdentity.__table__.drop(bind=db.engine, checkfirst=True)
    BotSetting.__table__.drop(bind=db.engine, checkfirst=True)
    BotContactIdentity.__table__.create(bind=db.engine, checkfirst=True)
    BotConversation.__table__.create(bind=db.engine, checkfirst=True)
    BotMessage.__table__.create(bind=db.engine, checkfirst=True)
    BotDecisionLog.__table__.create(bind=db.engine, checkfirst=True)
    BotSetting.__table__.create(bind=db.engine, checkfirst=True)
    BotEscalation.__table__.create(bind=db.engine, checkfirst=True)


def _reset_bot_tables() -> None:
    db.session.query(BotEscalation).delete()
    db.session.query(BotDecisionLog).delete()
    db.session.query(BotMessage).delete()
    db.session.query(BotConversation).delete()
    db.session.query(BotContactIdentity).delete()
    db.session.query(BotSetting).delete()
    db.session.commit()


def _login_staff(client):
    flask_app.config["WTF_CSRF_ENABLED"] = False
    resp = client.post("/admin/login", data={"usuario": "Owner", "clave": "admin123"}, follow_redirects=False)
    assert resp.status_code in (302, 303)


def test_phone_normalization_rd_and_invalid_cases():
    assert sanitize_phone("(809) 555-1234") == "8095551234"
    assert validate_possible_phone("8095551234") is True
    assert validate_possible_phone("123") is False
    assert normalize_phone_to_e164("8095551234") == "+18095551234"
    assert normalize_phone_to_e164("829-555-1234") == "+18295551234"
    assert normalize_phone_to_e164("+1 (809) 555-1234") == "+18095551234"
    assert normalize_phone_to_e164("849 555 1234") == "+18495551234"
    assert normalize_phone_to_e164("000-000-0000") is None


def test_identity_resolver_all_statuses():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_min_candidate_table()
        db.session.execute(text("DELETE FROM candidatas"))
        db.session.query(Cliente).filter(Cliente.codigo.like("P3-ID-%")).delete(synchronize_session=False)
        db.session.commit()

        base = "P3-ID-"
        client_phones = ["8095551201", "8095551203", "8095551204", "8095551205", "8095551206"]
        for idx, phone in enumerate(client_phones, start=1):
            cli = Cliente(
                codigo=f"{base}C{idx}",
                nombre_completo=f"Cliente {idx}",
                email=f"p3_id_cliente_{idx}@example.com",
                telefono=phone,
            )
            db.session.add(cli)
        db.session.commit()
        db.session.execute(text("INSERT INTO candidatas (fila, numero_telefono) VALUES (9001, '8095551201')"))
        db.session.execute(text("INSERT INTO candidatas (fila, numero_telefono) VALUES (9002, '8095551202')"))
        db.session.execute(text("INSERT INTO candidatas (fila, numero_telefono) VALUES (9003, '8095551203')"))
        db.session.execute(text("INSERT INTO candidatas (fila, numero_telefono) VALUES (9004, '8095551203')"))
        db.session.commit()

        client_only = identify_contact_by_phone("+18095551204")
        assert client_only["identity_status"] == "client_identified"

        candidate_only = identify_contact_by_phone("+18095551202")
        assert candidate_only["identity_status"] == "candidate_identified"

        both = identify_contact_by_phone("+18095551201")
        assert both["identity_status"] == "client_and_candidate"

        new_contact = identify_contact_by_phone("+18095559999")
        assert new_contact["identity_status"] == "new_contact"

        ambiguous = identify_contact_by_phone("+18095551203")
        assert ambiguous["identity_status"] == "ambiguous"


def test_identity_resolver_marks_ambiguous_with_duplicate_clients():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        db.session.query(Cliente).filter(Cliente.codigo.like("P3-DUP-%")).delete(synchronize_session=False)
        db.session.commit()

        c1 = Cliente(
            codigo="P3-DUP-C1",
            nombre_completo="Dup 1",
            email="p3_dup_1@example.com",
            telefono="8095557777",
        )
        c2 = Cliente(
            codigo="P3-DUP-C2",
            nombre_completo="Dup 2",
            email="p3_dup_2@example.com",
            telefono="8095557777",
        )
        db.session.add_all([c1, c2])
        db.session.commit()

        result = identify_contact_by_phone("+18095557777")
        assert result["identity_status"] == "ambiguous"
        assert len(result["client_ids"]) == 2


def test_webhook_inbound_creates_identity_conversation_and_decision(monkeypatch):
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    monkeypatch.setenv("WHATSAPP_VALIDATE_SIGNATURE", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        _ensure_min_candidate_table()
        db.session.execute(text("DELETE FROM candidatas"))
        db.session.query(Cliente).filter(Cliente.codigo == "P3-WEBHOOK-C1").delete(synchronize_session=False)
        db.session.commit()
        cli = Cliente(
            codigo="P3-WEBHOOK-C1",
            nombre_completo="Cliente Webhook",
            email="p3_webhook_cliente@example.com",
            telefono="8095556600",
        )
        db.session.add(cli)
        db.session.commit()

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": "18095556600", "profile": {"name": "Webhook Cliente"}}],
                            "messages": [
                                {
                                    "id": "wamid-p3-1",
                                    "from": "18095556600",
                                    "timestamp": "1715000000",
                                    "type": "text",
                                    "text": {"body": "hola identidad"},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }
    resp = client.post("/bot/whatsapp/webhook", json=payload)
    assert resp.status_code == 200

    with flask_app.app_context():
        conv = BotConversation.query.filter_by(phone_e164="+18095556600").first()
        assert conv is not None
        assert conv.identity_id is not None
        ident = BotContactIdentity.query.get(conv.identity_id)
        assert ident is not None
        assert ident.identity_status == "client_identified"
        dec = (
            BotDecisionLog.query.filter_by(conversation_id=conv.id, decision_type="identify_contact")
            .order_by(BotDecisionLog.id.desc())
            .first()
        )
        assert dec is not None
        assert dec.decision_type == "identify_contact"
        assert dec.decision_result == "allow"


def test_webhook_inbound_with_invalid_phone_does_not_break(monkeypatch):
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    monkeypatch.setenv("WHATSAPP_VALIDATE_SIGNATURE", "false")

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": "abc", "profile": {"name": "Invalido"}}],
                            "messages": [
                                {
                                    "id": "wamid-p3-invalid-1",
                                    "from": "abc",
                                    "timestamp": "1715000010",
                                    "type": "text",
                                    "text": {"body": "hola invalido"},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }
    resp = client.post("/bot/whatsapp/webhook", json=payload)
    assert resp.status_code == 200
    with flask_app.app_context():
        assert BotConversation.query.count() == 0
        assert BotMessage.query.count() == 0
        assert BotDecisionLog.query.count() == 0


def test_webhook_inbound_stores_message_when_identity_resolution_fails(monkeypatch):
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    monkeypatch.setenv("WHATSAPP_VALIDATE_SIGNATURE", "false")
    monkeypatch.setattr("bot.whatsapp_routes.get_or_create_identity", lambda _phone: (_ for _ in ()).throw(RuntimeError("boom")))

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": "18095559901", "profile": {"name": "Fallback"}}],
                            "messages": [
                                {
                                    "id": "wamid-p3-fallback-1",
                                    "from": "18095559901",
                                    "timestamp": "1715000020",
                                    "type": "text",
                                    "text": {"body": "hola fallback"},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }
    resp = client.post("/bot/whatsapp/webhook", json=payload)
    assert resp.status_code == 200
    with flask_app.app_context():
        conv = BotConversation.query.filter_by(phone_e164="+18095559901").first()
        assert conv is not None
        assert conv.status == "pending_human"
        assert conv.identity_id is None
        msg = BotMessage.query.filter_by(conversation_id=conv.id).first()
        assert msg is not None
        dec = (
            BotDecisionLog.query.filter_by(conversation_id=conv.id, decision_type="identify_contact")
            .order_by(BotDecisionLog.id.desc())
            .first()
        )
        assert dec is not None
        assert dec.decision_result == "escalate"
        assert dec.rule_code == "IDENTITY_RESOLUTION_FAILED"


def test_admin_conversation_detail_renders_when_identity_id_is_null():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        conv = BotConversation(channel="whatsapp", phone_e164="+18095558800", contact_name="Sin Identity", status="open")
        db.session.add(conv)
        db.session.commit()
        conv_id = int(conv.id)

    _login_staff(client)
    resp = client.get(f"/admin/bot/conversaciones/{conv_id}", follow_redirects=False)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Conversación" in body


def test_admin_conversation_identity_badges_and_ambiguous_warning():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        _ensure_bot_tables()
        _reset_bot_tables()
        identity = BotContactIdentity(
            phone_e164="+18095557700",
            identity_status="ambiguous",
            is_client=False,
            is_candidate=False,
            is_new_contact=False,
            confidence_score=0,
        )
        db.session.add(identity)
        db.session.flush()
        conv = BotConversation(
            channel="whatsapp",
            phone_e164="+18095557700",
            contact_name="Ambiguous",
            status="pending_human",
            identity_id=identity.id,
        )
        db.session.add(conv)
        db.session.commit()
        conv_id = int(conv.id)

    _login_staff(client)

    lst = client.get("/admin/bot/conversaciones", follow_redirects=False)
    assert lst.status_code == 200
    list_html = lst.get_data(as_text=True)
    assert "ambiguous" in list_html

    detail = client.get(f"/admin/bot/conversaciones/{conv_id}", follow_redirects=False)
    assert detail.status_code == 200
    detail_html = detail.get_data(as_text=True)
    assert "Revisión manual requerida por coincidencias múltiples." in detail_html
    assert "Ambiguo / requiere revisión" in detail_html
    assert "cliente_id=" not in detail_html
    assert "candidata_id=" not in detail_html


def _load_phase3_migration_module():
    migration_path = Path("migrations/versions/20260508_1600_add_candidata_telefono_e164_backfill.py").resolve()
    spec = importlib.util.spec_from_file_location("bot_migration_20260508_1600", str(migration_path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_phase3_migration_adds_column_backfills_and_downgrades():
    tmp_db = Path(tempfile.gettempdir()) / "app_web_bot_phase3_migration.sqlite"
    if tmp_db.exists():
        tmp_db.unlink()
    engine = create_engine(f"sqlite:///{tmp_db}")
    migration = _load_phase3_migration_module()

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE candidatas (fila INTEGER PRIMARY KEY, numero_telefono VARCHAR(50))"))
        conn.execute(text("INSERT INTO candidatas (fila, numero_telefono) VALUES (1, '8095551234')"))
        conn.execute(text("INSERT INTO candidatas (fila, numero_telefono) VALUES (2, 'tel-basura')"))

        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration.upgrade()

        insp = inspect(conn)
        cols = {c["name"] for c in insp.get_columns("candidatas")}
        assert "telefono_e164" in cols
        idx = {i["name"] for i in insp.get_indexes("candidatas")}
        assert "ix_candidatas_telefono_e164" in idx

        v1 = conn.execute(text("SELECT telefono_e164 FROM candidatas WHERE fila = 1")).scalar_one()
        v2 = conn.execute(text("SELECT telefono_e164 FROM candidatas WHERE fila = 2")).scalar_one()
        assert v1 == "+18095551234"
        assert v2 is None

        with Operations.context(ctx):
            migration.downgrade()

        insp2 = inspect(conn)
        cols_after = {c["name"] for c in insp2.get_columns("candidatas")}
        assert "telefono_e164" not in cols_after


def test_candidata_phone_e164_sync_on_create_and_edit():
    cand = Candidata(
        nombre_completo="Candidata Sync",
        cedula="123-4567890-1",
        numero_telefono="(809) 555-1000",
    )
    models_module._candidata_before_insert(None, None, cand)
    assert cand.telefono_e164 == "+18095551000"

    cand.numero_telefono = "849-555-2000"
    models_module._candidata_before_update(None, None, cand)
    assert cand.telefono_e164 == "+18495552000"


def test_find_candidate_phone_duplicates_detects_and_ignores_null_empty():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_min_candidate_table()
        db.session.execute(text("DELETE FROM candidatas"))
        db.session.execute(
            text(
                "INSERT INTO candidatas (fila, numero_telefono, telefono_e164, nombre_completo, estado) VALUES "
                "(9101, '809-555-0001', '+18095550001', 'Cand A', 'en_proceso'),"
                "(9102, '809-555-0002', '+18095550001', 'Cand B', 'lista_para_trabajar'),"
                "(9103, '809-555-0003', NULL, 'Cand C', 'en_proceso'),"
                "(9104, '809-555-0004', '', 'Cand D', 'en_proceso')"
            )
        )
        db.session.commit()

        groups = find_candidate_phone_duplicates()
        assert len(groups) == 1
        assert groups[0]["phone_e164"] == "+18095550001"
        assert groups[0]["count"] == 2
        filas = [int(c["fila"]) for c in groups[0]["candidates"]]
        assert filas == [9101, 9102]


def test_bot_identity_duplicates_route_requires_staff_and_renders(monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    anon = client.get("/admin/bot/identidades/duplicados", follow_redirects=False)
    assert anon.status_code in (302, 303)
    assert "/admin/login" in (anon.headers.get("Location") or "")

    with flask_app.app_context():
        _ensure_min_candidate_table()
        db.session.execute(text("DELETE FROM candidatas"))
        db.session.execute(
            text(
                "INSERT INTO candidatas (fila, numero_telefono, telefono_e164, nombre_completo, estado) VALUES "
                "(9201, '809-555-0101', '+18095550101', 'Dup Uno', 'en_proceso'),"
                "(9202, '809-555-0102', '+18095550101', 'Dup Dos', 'inscrita')"
            )
        )
        db.session.commit()

    _login_staff(client)
    resp = client.get("/admin/bot/identidades/duplicados", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Duplicados legacy por teléfono E.164" in html
    assert "+18095550101" in html
    assert "Dup Uno" in html
    assert "Dup Dos" in html


def test_bot_identity_duplicates_route_empty_state():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_min_candidate_table()
        db.session.execute(text("DELETE FROM candidatas"))
        db.session.execute(
            text(
                "INSERT INTO candidatas (fila, numero_telefono, telefono_e164, nombre_completo, estado) VALUES "
                "(9301, '809-555-0201', '+18095550201', 'Solo Uno', 'en_proceso'),"
                "(9302, '809-555-0202', '+18095550202', 'Solo Dos', 'en_proceso')"
            )
        )
        db.session.commit()

    _login_staff(client)
    resp = client.get("/admin/bot/identidades/duplicados", follow_redirects=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "No se detectaron duplicados en candidatas.telefono_e164." in html
