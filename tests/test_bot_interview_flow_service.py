from __future__ import annotations

from app import app as flask_app
from config_app import db
from models import BotCandidateDraft, BotContactIdentity, BotConversation, BotDecisionLog, BotEscalation, BotMessage, BotSandboxOutbound, BotSandboxReviewQueue, BotSetting
from services.bot_interview_flow_service import process_interview_inbound
from unittest.mock import patch


def _ensure_tables() -> None:
    db.session.remove()
    with db.engine.begin() as conn:
        BotContactIdentity.__table__.create(bind=conn, checkfirst=True)
        BotConversation.__table__.create(bind=conn, checkfirst=True)
        BotMessage.__table__.create(bind=conn, checkfirst=True)
        BotDecisionLog.__table__.create(bind=conn, checkfirst=True)
        BotSetting.__table__.create(bind=conn, checkfirst=True)
        BotEscalation.__table__.create(bind=conn, checkfirst=True)
        BotSandboxOutbound.__table__.create(bind=conn, checkfirst=True)
        BotSandboxReviewQueue.__table__.create(bind=conn, checkfirst=True)
        BotCandidateDraft.__table__.create(bind=conn, checkfirst=True)
    db.session.query(BotSandboxReviewQueue).delete()
    db.session.query(BotSandboxOutbound).delete()
    db.session.query(BotEscalation).delete()
    db.session.query(BotDecisionLog).delete()
    db.session.query(BotMessage).delete()
    db.session.query(BotConversation).delete()
    db.session.query(BotContactIdentity).delete()
    db.session.query(BotSetting).delete()
    db.session.query(BotCandidateDraft).delete()
    db.session.commit()


def _base_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("BOT_STAGING_MODE", "true")
    monkeypatch.setenv("BOT_SANDBOX_MODE", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "true")
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("BOT_AI_ENABLED", "false")
    monkeypatch.setenv("BOT_INTERVIEW_FLOW_ENABLED", "true")


def _new_conversation() -> BotConversation:
    conv = BotConversation(
        channel="whatsapp",
        phone_e164="+19990000001",
        contact_name="Candidata",
        status="open",
        metadata_json={"sandbox_conversation": True},
    )
    db.session.add(conv)
    db.session.flush()
    return conv


def test_01_primer_hola_genera_pregunta_nombre(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        out = process_interview_inbound(conversation=conv, inbound_text="hola")
        db.session.commit()

        assert out["active"] is True
        assert "nombre completo" in str(out["reply"]).lower()
        assert "agencia doméstica" in str(out["reply"]).lower()


def test_02_nombre_guarda_y_pregunta_edad(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        process_interview_inbound(conversation=conv, inbound_text="hola")
        out = process_interview_inbound(conversation=conv, inbound_text="Ana Perez")
        db.session.commit()

        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert (flow.get("collected_data") or {}).get("full_name") == "Ana Perez"
        assert flow.get("current_step") == "ask_age"
        assert "edad" in str(out["reply"]).lower()
        assert "agencia doméstica" not in str(out["reply"]).lower()


def test_03_edad_guarda_y_pregunta_ciudad_sector(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        process_interview_inbound(conversation=conv, inbound_text="hola")
        process_interview_inbound(conversation=conv, inbound_text="Ana Perez")
        out = process_interview_inbound(conversation=conv, inbound_text="34")
        db.session.commit()

        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert (flow.get("collected_data") or {}).get("age") == 34
        assert flow.get("current_step") == "ask_city_sector"
        assert "ciudad" in str(out["reply"]).lower()


def test_04_incompleta_no_avanza_y_pide_aclaracion(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        process_interview_inbound(conversation=conv, inbound_text="hola")
        out = process_interview_inbound(conversation=conv, inbound_text="Ana")
        db.session.commit()

        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert flow.get("current_step") == "ask_name"
        assert "nombre completo" in str(out["reply"]).lower()


def test_05_completa_todos_los_pasos_genera_resumen(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        inputs = [
            "hola",
            "Ana Perez",
            "34",
            "Santiago, Cienfuegos",
            "2 años trabajando en casas de familia",
            "Limpieza, cocinar y cuidar niños",
            "ambos",
            "Maria 8095550101",
        ]
        out = {}
        for text in inputs:
            out = process_interview_inbound(conversation=conv, inbound_text=text)
        db.session.commit()

        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert flow.get("completed") is True
        assert flow.get("current_step") == "completed"
        assert "Resumen para revisión humana" in str(flow.get("summary") or "")
        assert "revisión humana" in str(out.get("reply") or "").lower()


def test_06_no_envia_sin_aprobacion_manual(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    resp = client.post(
        "/admin/bot/sandbox/webhook/inbound",
        json={
            "from": "+19990000002",
            "name": "Candidata Sandbox",
            "message": "hola",
            "message_id": "int-flow-06",
            "timestamp": "2026-05-15T10:00:00Z",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 200
    with flask_app.app_context():
        assert BotSandboxReviewQueue.query.count() == 1
        assert BotSandboxOutbound.query.count() == 0


def test_07_no_duplica_review_ni_outbox(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    payload = {
        "from": "+19990000003",
        "name": "Candidata Sandbox",
        "message": "hola",
        "message_id": "int-flow-07",
        "timestamp": "2026-05-15T10:00:00Z",
    }
    r1 = client.post("/admin/bot/sandbox/webhook/inbound", json=payload, follow_redirects=False)
    r2 = client.post("/admin/bot/sandbox/webhook/inbound", json=payload, follow_redirects=False)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert (r2.get_json() or {}).get("idempotent") is True

    with flask_app.app_context():
        assert BotSandboxReviewQueue.query.count() == 1
        assert BotSandboxOutbound.query.count() == 0


def test_08_webhook_flow_persiste_current_step_y_no_repite_saludo(monkeypatch):
    _base_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()

    base = {
        "from": "+19990000004",
        "name": "Candidata Sandbox",
        "timestamp": "2026-05-15T10:00:00Z",
    }
    r1 = client.post("/admin/bot/sandbox/webhook/inbound", json={**base, "message": "hola", "message_id": "int-flow-08-1"}, follow_redirects=False)
    r2 = client.post("/admin/bot/sandbox/webhook/inbound", json={**base, "message": "María Pérez", "message_id": "int-flow-08-2"}, follow_redirects=False)
    r3 = client.post("/admin/bot/sandbox/webhook/inbound", json={**base, "message": "25", "message_id": "int-flow-08-3"}, follow_redirects=False)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 200

    with flask_app.app_context():
        conv = BotConversation.query.filter_by(phone_e164="+19990000004").order_by(BotConversation.id.desc()).first()
        assert conv is not None
        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert flow.get("current_step") == "ask_city_sector"
        assert int((flow.get("collected_data") or {}).get("age") or 0) == 25

        reviews = (
            BotSandboxReviewQueue.query.filter_by(conversation_id=int(conv.id))
            .order_by(BotSandboxReviewQueue.id.asc())
            .all()
        )
        assert len(reviews) == 3
        first = str(reviews[0].final_suggested_reply or "").lower()
        second = str(reviews[1].final_suggested_reply or "").lower()
        third = str(reviews[2].final_suggested_reply or "").lower()
        assert "nombre completo" in first
        assert "agencia doméstica" in first
        assert "edad" in second
        assert "agencia doméstica" not in second
        assert "ciudad" in third


def test_09_nombre_hola_no_avanza(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        process_interview_inbound(conversation=conv, inbound_text="hola")
        out = process_interview_inbound(conversation=conv, inbound_text="hola")
        db.session.commit()
        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert flow.get("current_step") == "ask_name"
        assert "nombre completo" in str(out.get("reply") or "").lower()
        assert str(flow.get("last_invalid_answer") or "") == "hola"
        assert "nombre completo" in str(flow.get("validation_error") or "").lower()


def test_10_nombre_valido_avanza_edad(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        process_interview_inbound(conversation=conv, inbound_text="hola")
        out = process_interview_inbound(conversation=conv, inbound_text="María Pérez")
        db.session.commit()
        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert flow.get("current_step") == "ask_age"
        assert "edad" in str(out.get("reply") or "").lower()


def test_11_edad_treinta_no_avanza(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        process_interview_inbound(conversation=conv, inbound_text="hola")
        process_interview_inbound(conversation=conv, inbound_text="María Pérez")
        out = process_interview_inbound(conversation=conv, inbound_text="treinta")
        db.session.commit()
        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert flow.get("current_step") == "ask_age"
        assert "edad" in str(out.get("reply") or "").lower()


def test_12_edad_30_anos_avanza(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        process_interview_inbound(conversation=conv, inbound_text="hola")
        process_interview_inbound(conversation=conv, inbound_text="María Pérez")
        out = process_interview_inbound(conversation=conv, inbound_text="30 años")
        db.session.commit()
        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert flow.get("current_step") == "ask_city_sector"


def test_41_confusion_repetida_no_avanza_y_guarda_contador_help(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        process_interview_inbound(conversation=conv, inbound_text="hola")
        process_interview_inbound(conversation=conv, inbound_text="Ana Perez")

        out1 = process_interview_inbound(conversation=conv, inbound_text="que?")
        out2 = process_interview_inbound(conversation=conv, inbound_text="no entiendo")
        db.session.commit()

        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert flow.get("current_step") == "ask_age"
        assert out1.get("advanced") is False
        assert out2.get("advanced") is False
        help_counts = dict(flow.get("help_repeat_count_by_step") or {})
        assert int(help_counts.get("ask_age") or 0) >= 2


def test_42_help_no_repite_texto_exacto_en_mismo_step(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        process_interview_inbound(conversation=conv, inbound_text="hola")
        process_interview_inbound(conversation=conv, inbound_text="Ana Perez")

        out1 = process_interview_inbound(conversation=conv, inbound_text="hola")
        out2 = process_interview_inbound(conversation=conv, inbound_text="hola")
        db.session.commit()

        assert out1.get("advanced") is False
        assert out2.get("advanced") is False
        assert str(out1.get("reply") or "").strip() != str(out2.get("reply") or "").strip()


def test_43_reduce_examples_al_primer_fallo(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        process_interview_inbound(conversation=conv, inbound_text="hola")
        process_interview_inbound(conversation=conv, inbound_text="Ana Perez")
        out = process_interview_inbound(conversation=conv, inbound_text="treinta")
        db.session.commit()

        reply = str(out.get("reply") or "").lower()
        assert "ejemplo" not in reply
        assert "edad" in reply


def test_44_muestra_ejemplo_solo_tras_varios_fallos(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        process_interview_inbound(conversation=conv, inbound_text="hola")
        process_interview_inbound(conversation=conv, inbound_text="Ana Perez")
        process_interview_inbound(conversation=conv, inbound_text="treinta")
        process_interview_inbound(conversation=conv, inbound_text="que?")
        out = process_interview_inbound(conversation=conv, inbound_text="no entiendo")
        db.session.commit()

        reply = str(out.get("reply") or "").lower()
        assert "edad" in reply
        assert "ejemplo" in reply


def test_45_tono_natural_orientador_en_city_sector(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        process_interview_inbound(conversation=conv, inbound_text="hola")
        process_interview_inbound(conversation=conv, inbound_text="Ana Perez")
        process_interview_inbound(conversation=conv, inbound_text="30")
        out = process_interview_inbound(conversation=conv, inbound_text="ok")
        db.session.commit()

        reply = str(out.get("reply") or "").lower()
        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert out.get("advanced") is False
        assert flow.get("current_step") == "ask_city_sector"
        assert "ciudad" in reply or "zona" in reply
        assert "ciudad" in str(out.get("reply") or "").lower()


def test_13_ciudad_no_tengo_no_avanza(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        process_interview_inbound(conversation=conv, inbound_text="hola")
        process_interview_inbound(conversation=conv, inbound_text="María Pérez")
        process_interview_inbound(conversation=conv, inbound_text="30")
        out = process_interview_inbound(conversation=conv, inbound_text="no tengo")
        db.session.commit()
        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert flow.get("current_step") == "ask_city_sector"
        assert "ciudad y el sector" in str(out.get("reply") or "").lower()


def test_14_ciudad_santiago_gurabo_avanza(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        process_interview_inbound(conversation=conv, inbound_text="hola")
        process_interview_inbound(conversation=conv, inbound_text="María Pérez")
        process_interview_inbound(conversation=conv, inbound_text="30")
        out = process_interview_inbound(conversation=conv, inbound_text="Santiago, Gurabo")
        db.session.commit()
        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert flow.get("current_step") == "ask_experience"
        assert "experiencia" in str(out.get("reply") or "").lower()


def test_15_funciones_no_se_no_avanza(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        process_interview_inbound(conversation=conv, inbound_text="hola")
        process_interview_inbound(conversation=conv, inbound_text="María Pérez")
        process_interview_inbound(conversation=conv, inbound_text="30")
        process_interview_inbound(conversation=conv, inbound_text="Santiago, Gurabo")
        process_interview_inbound(conversation=conv, inbound_text="No tengo experiencia, pero quiero aprender")
        out = process_interview_inbound(conversation=conv, inbound_text="no sé")
        db.session.commit()
        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert flow.get("current_step") == "ask_skills"
        assert "necesito saber qué funciones" in str(out.get("reply") or "").lower()


def test_16_funciones_cocinar_limpiar_avanza(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        process_interview_inbound(conversation=conv, inbound_text="hola")
        process_interview_inbound(conversation=conv, inbound_text="María Pérez")
        process_interview_inbound(conversation=conv, inbound_text="30")
        process_interview_inbound(conversation=conv, inbound_text="Santiago, Gurabo")
        process_interview_inbound(conversation=conv, inbound_text="No tengo experiencia, pero quiero aprender")
        out = process_interview_inbound(conversation=conv, inbound_text="sé cocinar y limpiar")
        db.session.commit()
        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert flow.get("current_step") == "ask_availability"
        assert "disponibilidad" in str(out.get("reply") or "").lower()


def test_17_disponibilidad_salida_diaria_avanza(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        process_interview_inbound(conversation=conv, inbound_text="hola")
        process_interview_inbound(conversation=conv, inbound_text="María Pérez")
        process_interview_inbound(conversation=conv, inbound_text="30")
        process_interview_inbound(conversation=conv, inbound_text="Santiago, Gurabo")
        process_interview_inbound(conversation=conv, inbound_text="No tengo experiencia, pero quiero aprender")
        process_interview_inbound(conversation=conv, inbound_text="sé cocinar y limpiar")
        out = process_interview_inbound(conversation=conv, inbound_text="salida diaria")
        db.session.commit()
        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert flow.get("current_step") == "ask_references"
        assert "referencias" in str(out.get("reply") or "").lower()


def test_18_referencias_no_no_avanza(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        process_interview_inbound(conversation=conv, inbound_text="hola")
        process_interview_inbound(conversation=conv, inbound_text="María Pérez")
        process_interview_inbound(conversation=conv, inbound_text="30")
        process_interview_inbound(conversation=conv, inbound_text="Santiago, Gurabo")
        process_interview_inbound(conversation=conv, inbound_text="No tengo experiencia, pero quiero aprender")
        process_interview_inbound(conversation=conv, inbound_text="sé cocinar y limpiar")
        process_interview_inbound(conversation=conv, inbound_text="salida diaria")
        out = process_interview_inbound(conversation=conv, inbound_text="no")
        db.session.commit()
        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert flow.get("current_step") == "ask_references"
        assert "referencia laboral o familiar" in str(out.get("reply") or "").lower()


def test_19_referencias_puedo_enviarla_luego_avanza_y_resumen(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        inputs = [
            "hola",
            "María Pérez",
            "30 años",
            "Santiago, Gurabo",
            "No tengo experiencia, pero quiero aprender",
            "sé cocinar y limpiar",
            "salida diaria",
            "puedo enviarla luego",
        ]
        for text in inputs:
            process_interview_inbound(conversation=conv, inbound_text=text)
        db.session.commit()
        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert flow.get("completed") is True
        assert flow.get("current_step") == "completed"
        summary = str(flow.get("summary") or "")
        assert "nombre" in summary.lower()
        assert "edad" in summary.lower()
        assert "ciudad y sector" in summary.lower()
        assert "experiencia laboral" in summary.lower()
        assert "funciones" in summary.lower()
        assert "disponibilidad" in summary.lower()
        assert "referencias" in summary.lower()


def test_19b_referencias_necesito_esto_no_completa(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        inputs = [
            "hola",
            "María Pérez",
            "30 años",
            "Santiago, Gurabo",
            "No tengo experiencia, pero quiero aprender",
            "sé cocinar y limpiar",
            "salida diaria",
        ]
        for text in inputs:
            process_interview_inbound(conversation=conv, inbound_text=text)
        out = process_interview_inbound(conversation=conv, inbound_text="necesito esto ?")
        db.session.commit()
        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert flow.get("current_step") == "ask_references"
        assert flow.get("completed") is False
        assert str(flow.get("last_invalid_answer") or "") == "necesito esto ?"
        assert "cerrar el registro necesito tus referencias" in str(out.get("reply") or "").lower()


def test_19c_disponibilidad_como_estas_no_avanza(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        inputs = [
            "hola",
            "María Pérez",
            "30 años",
            "Santiago, Gurabo",
            "No tengo experiencia, pero quiero aprender",
            "sé cocinar y limpiar",
        ]
        for text in inputs:
            process_interview_inbound(conversation=conv, inbound_text=text)
        out = process_interview_inbound(conversation=conv, inbound_text="como estas")
        db.session.commit()
        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert flow.get("current_step") == "ask_availability"
        assert "necesito confirmar tu disponibilidad" in str(out.get("reply") or "").lower()


def test_19d_hola_mitad_de_flujo_no_avanza(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        process_interview_inbound(conversation=conv, inbound_text="hola")
        process_interview_inbound(conversation=conv, inbound_text="María Pérez")
        out = process_interview_inbound(conversation=conv, inbound_text="hola")
        db.session.commit()
        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert flow.get("current_step") == "ask_age"
        assert "necesito confirmar tu edad" in str(out.get("reply") or "").lower()


def test_19e_referencias_validas_si_avanza(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        inputs = [
            "hola",
            "María Pérez",
            "30 años",
            "Santiago, Gurabo",
            "No tengo experiencia, pero quiero aprender",
            "sé cocinar y limpiar",
            "salida diaria",
            "Ana Perez 8095551212 vecina",
        ]
        for text in inputs:
            process_interview_inbound(conversation=conv, inbound_text=text)
        db.session.commit()
        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert flow.get("current_step") == "completed"
        assert flow.get("completed") is True


def _auto_reply_env(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_STAGING_MODE", "false")
    monkeypatch.setenv("BOT_SANDBOX_MODE", "false")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_SANDBOX_ENABLED", "true")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_OWNER_ONLY", "true")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_PROVIDER", "meta_sandbox")
    monkeypatch.setenv("WHATSAPP_ENABLED", "true")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_SIMULATE", "false")
    monkeypatch.setenv("BOT_DRY_RUN", "false")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_ALLOWED_NUMBERS", "+19990009999")
    monkeypatch.setenv("BOT_SANDBOX_AUTO_REPLY_ENABLED", "true")


def test_20_auto_reply_on_envia_exactamente_una_por_inbound(monkeypatch):
    _auto_reply_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    with patch("services.whatsapp_cloud_service.send_text_message", return_value={"ok": True, "wa_message_id": "wamid.auto.1", "http_status": 200, "raw_response": {"messages": [{"id": "wamid.auto.1"}]}}):
        r = client.post(
            "/admin/bot/sandbox/webhook/inbound",
            json={"from": "+19990009999", "name": "Auto", "message": "hola", "message_id": "int-flow-auto-1", "timestamp": "2026-05-15T10:00:00Z"},
            follow_redirects=False,
        )
    assert r.status_code == 200
    with flask_app.app_context():
        assert BotSandboxReviewQueue.query.count() == 1
        assert BotSandboxOutbound.query.count() == 1
        assert BotSandboxOutbound.query.filter_by(state="simulated_sent").count() == 1


def test_21_auto_reply_reintento_webhook_no_duplica_envio(monkeypatch):
    _auto_reply_env(monkeypatch)
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    with flask_app.app_context():
        _ensure_tables()
    with patch("services.whatsapp_cloud_service.send_text_message", return_value={"ok": True, "wa_message_id": "wamid.auto.2", "http_status": 200, "raw_response": {"messages": [{"id": "wamid.auto.2"}]}}):
        payload = {"from": "+19990009999", "name": "Auto", "message": "hola", "message_id": "int-flow-auto-dup", "timestamp": "2026-05-15T10:00:00Z"}
        r1 = client.post("/admin/bot/sandbox/webhook/inbound", json=payload, follow_redirects=False)
        r2 = client.post("/admin/bot/sandbox/webhook/inbound", json=payload, follow_redirects=False)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert (r2.get_json() or {}).get("idempotent") is True
    with flask_app.app_context():
        assert BotSandboxReviewQueue.query.count() == 1
        assert BotSandboxOutbound.query.count() == 1


def test_22_ai_classifier_off_mantiene_comportamiento_actual(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.delenv("BOT_INTERVIEW_AI_CLASSIFIER_ENABLED", raising=False)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        process_interview_inbound(conversation=conv, inbound_text="hola")
        out = process_interview_inbound(conversation=conv, inbound_text="Ana")
        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert out["advanced"] is False
        assert flow.get("current_step") == "ask_name"


def test_23_ai_classifier_confidence_baja_no_avanza(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_INTERVIEW_AI_CLASSIFIER_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_API_KEY", "x")
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        process_interview_inbound(conversation=conv, inbound_text="hola")
        with patch(
            "services.bot_interview_flow_service._openai_chat_json",
            return_value={"data": {"is_valid_for_step": True, "normalized_value": "Ana Perez", "confidence": 0.60, "reason": "baja"}, "raw": "{}"},
        ):
            out = process_interview_inbound(conversation=conv, inbound_text="Ana")
        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert out["advanced"] is False
        assert flow.get("current_step") == "ask_name"


def test_24_ai_classifier_confidence_alta_acepta_typo(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_INTERVIEW_AI_CLASSIFIER_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_API_KEY", "x")
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        process_interview_inbound(conversation=conv, inbound_text="hola")
        with patch(
            "services.bot_interview_flow_service._openai_chat_json",
            return_value={"data": {"is_valid_for_step": True, "normalized_value": "Maria Perez", "confidence": 0.95, "reason": "normalizado"}, "raw": "{\"ok\":1}"},
        ):
            out = process_interview_inbound(conversation=conv, inbound_text="mria")
        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert out["advanced"] is True
        assert flow.get("current_step") == "ask_age"
        assert flow.get("ai_classifier_used") is True
        assert float(flow.get("ai_classifier_confidence") or 0) >= 0.95


def test_25_ai_classifier_no_acepta_que_como_nombre(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_INTERVIEW_AI_CLASSIFIER_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_API_KEY", "x")
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        process_interview_inbound(conversation=conv, inbound_text="hola")
        with patch(
            "services.bot_interview_flow_service._openai_chat_json",
            return_value={"data": {"is_valid_for_step": True, "normalized_value": "que?", "confidence": 0.99, "reason": "segun ai"}, "raw": "{}"},
        ):
            out = process_interview_inbound(conversation=conv, inbound_text="q")
        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert out["advanced"] is False
        assert flow.get("current_step") == "ask_name"


def test_26_ai_classifier_no_puede_saltar_pasos(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_INTERVIEW_AI_CLASSIFIER_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_API_KEY", "x")
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        process_interview_inbound(conversation=conv, inbound_text="hola")
        process_interview_inbound(conversation=conv, inbound_text="Maria Perez")
        with patch(
            "services.bot_interview_flow_service._openai_chat_json",
            return_value={"data": {"is_valid_for_step": True, "normalized_value": "30 y vivo en santiago", "confidence": 0.98, "reason": "mezclado"}, "raw": "{}"},
        ):
            out = process_interview_inbound(conversation=conv, inbound_text="treinta y santiago")
        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert out["advanced"] is True
        assert flow.get("current_step") == "ask_city_sector"
        assert (flow.get("collected_data") or {}).get("city_sector") is None


def test_27_ai_copy_mejora_mensaje_sin_cambiar_objetivo(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_INTERVIEW_AI_COPY_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_API_KEY", "x")
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        process_interview_inbound(conversation=conv, inbound_text="hola")
        with patch("services.bot_interview_flow_service._openai_chat_json", return_value={"data": {"text": "Para seguir, compárteme tu nombre completo y apellido por favor."}, "raw": "{}"}):
            out = process_interview_inbound(conversation=conv, inbound_text="q")
        assert "nombre" in str(out.get("reply") or "").lower()
        assert len(str(out.get("reply") or "")) <= 280


def test_28_ai_copy_fallback_si_texto_largo(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_INTERVIEW_AI_COPY_ENABLED", "true")
    monkeypatch.setenv("BOT_AI_API_KEY", "x")
    long_text = "x" * 400
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        process_interview_inbound(conversation=conv, inbound_text="hola")
        with patch("services.bot_interview_flow_service._openai_chat_json", return_value={"data": {"text": long_text}, "raw": "{}"}):
            out = process_interview_inbound(conversation=conv, inbound_text="q")
        assert len(str(out.get("reply") or "")) < 280
        assert "nombre completo" in str(out.get("reply") or "").lower()


def test_29_sin_api_key_no_rompe(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("BOT_INTERVIEW_AI_CLASSIFIER_ENABLED", "true")
    monkeypatch.setenv("BOT_INTERVIEW_AI_COPY_ENABLED", "true")
    monkeypatch.delenv("BOT_AI_API_KEY", raising=False)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        out1 = process_interview_inbound(conversation=conv, inbound_text="hola")
        out2 = process_interview_inbound(conversation=conv, inbound_text="Ana Perez")
        flow = dict((conv.metadata_json or {}).get("interview_flow") or {})
        assert out1["active"] is True
        assert out2["advanced"] is True
        assert flow.get("current_step") == "ask_age"


def test_30_completed_crea_borrador_pendiente_y_no_duplica(monkeypatch):
    _base_env(monkeypatch)
    with flask_app.app_context():
        _ensure_tables()
        conv = _new_conversation()
        inputs = [
            "hola",
            "Ana Perez",
            "30",
            "Santiago Gurabo",
            "Tengo experiencia en casas de familia",
            "limpio cocino y lavo",
            "salida diaria",
            "Ana 8095550001",
        ]
        last = {}
        for txt in inputs:
            last = process_interview_inbound(conversation=conv, inbound_text=txt)
        assert last.get("advanced") is True
        d1 = BotCandidateDraft.query.filter_by(conversation_id=int(conv.id)).all()
        assert len(d1) == 1
        assert str((d1[0].metadata_json or {}).get("summary", {}).get("status") or "") == "pendiente_revision_bot"
        assert str((d1[0].metadata_json or {}).get("summary", {}).get("source") or "") == "whatsapp_bot"
        again = process_interview_inbound(conversation=conv, inbound_text="gracias")
        assert again.get("active") is True
        d2 = BotCandidateDraft.query.filter_by(conversation_id=int(conv.id)).all()
        assert len(d2) == 1
