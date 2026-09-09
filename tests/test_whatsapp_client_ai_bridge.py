import pytest

from app import app as flask_app
from config_app import db
from models import BotContactIdentity, BotConversation, BotMessage, ChatConversation, ChatMessage, ClientAIActionProposal, Cliente
from services.client_ai_actions_service import create_or_refresh_action_proposal, create_or_refresh_response_proposal
from services.whatsapp_client_ai_bridge import ingest_inbound_to_client_ai, is_client_ai_whatsapp_allowed, send_approved_client_ai_message, send_approved_client_ai_process_and_plans
from tests.t1_testkit import ensure_sqlite_compat_tables


def _ensure_tables():
    ensure_sqlite_compat_tables(
        [Cliente, BotContactIdentity, BotConversation, BotMessage, ChatConversation, ChatMessage, ClientAIActionProposal],
        reset=False,
    )


def _reset_tables():
    for model in (ClientAIActionProposal, ChatMessage, ChatConversation, BotMessage, BotConversation, BotContactIdentity, Cliente):
        db.session.query(model).delete()
    db.session.commit()


@pytest.fixture(autouse=True)
def _bridge_db(monkeypatch):
    flask_app.config["TESTING"] = True
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CLIENT_AI_WHATSAPP_ENABLED", "true")
    monkeypatch.setenv("WHATSAPP_ENABLED", "true")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_SANDBOX_ENABLED", "true")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_OWNER_ONLY", "true")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_PROVIDER", "meta_sandbox")
    monkeypatch.setenv("BOT_REAL_WHATSAPP_ALLOWED_NUMBERS", "+18095550041")
    with flask_app.app_context():
        _ensure_tables()
        _reset_tables()
        yield
        _reset_tables()


def _make_client():
    client = Cliente(
        codigo="BRIDGE-1",
        nombre_completo="Bridge Client",
        email="bridge@example.com",
        telefono="+18095550041",
        ciudad="Santiago",
        sector="Centro",
    )
    db.session.add(client)
    db.session.flush()
    return client


def _make_bot_conversation(client_id: int):
    identity = BotContactIdentity(
        phone_e164="+18095550041",
        identity_status="client_identified",
        is_client=True,
        client_id=int(client_id),
        is_new_contact=False,
    )
    conversation = BotConversation(channel="whatsapp", phone_e164="+18095550041", identity=identity, status="open")
    db.session.add(conversation)
    db.session.flush()
    return conversation


def test_inbound_creates_one_chat_message_and_one_pending_client_ai_proposal(monkeypatch):
    client = _make_client()
    bot_conversation = _make_bot_conversation(int(client.id))
    inbound = BotMessage(
        conversation_id=int(bot_conversation.id),
        direction="inbound",
        source="whatsapp_user",
        message_type="text",
        wa_message_id="wamid-bridge-1",
        text_body="Quiero continuar",
        status="inbound_stored",
    )
    db.session.add(inbound)
    db.session.flush()

    def fake_ai(*, conversation_id, message_text, client_id):
        proposal = create_or_refresh_response_proposal(
            conversation_id=conversation_id,
            client_id=client_id,
            solicitud_id=None,
            intent="general_response",
            draft_response="Respuesta supervisada",
            target="Cliente Bridge",
            payload={"proposal_kind": "response", "intent": "general_response", "draft_response": "Respuesta supervisada"},
            human_summary="Responder al cliente",
            reason="Prueba bridge",
        )
        return {"ok": True, "proposal_id": int(proposal.id), "proposal": {"id": int(proposal.id)}}

    monkeypatch.setattr("services.whatsapp_client_ai_bridge.get_or_create_identity", lambda _phone: (bot_conversation.identity, {"client_ids": [client.id], "is_client": True}))
    monkeypatch.setattr("services.whatsapp_client_ai_bridge.run_client_ai_shadow_mode", fake_ai)

    first = ingest_inbound_to_client_ai(bot_conversation=bot_conversation, inbound=inbound)
    second = ingest_inbound_to_client_ai(bot_conversation=bot_conversation, inbound=inbound)
    assert first["ok"] is True
    assert second["duplicate"] is True
    assert ChatMessage.query.count() == 1
    assert ClientAIActionProposal.query.count() == 1
    assert ClientAIActionProposal.query.first().status == "pending"


def test_inbound_allowlist_and_supervision_gate_block_other_number(monkeypatch):
    assert is_client_ai_whatsapp_allowed("+18095550041") is True
    assert is_client_ai_whatsapp_allowed("+18095550042") is False
    monkeypatch.setenv("BOT_AUTOREPLY_ENABLED", "true")
    assert is_client_ai_whatsapp_allowed("+18095550041") is True


def test_approved_client_ai_message_uses_graph_sender_once_and_persists_wa_id(monkeypatch):
    client = _make_client()
    bot_conversation = _make_bot_conversation(int(client.id))
    chat_conversation = ChatConversation(scope_key="whatsapp:1", conversation_type="whatsapp", status="open", cliente_id=int(client.id))
    chat_message = ChatMessage(conversation=chat_conversation, sender_type="staff", body="Respuesta aprobada", meta={})
    db.session.add_all([chat_conversation, chat_message])
    db.session.flush()
    send_mock = lambda _phone, _body: {"ok": True, "wa_message_id": "wamid-out-1"}
    monkeypatch.setattr("services.whatsapp_client_ai_bridge.send_text_message", send_mock)

    result = send_approved_client_ai_message(chat_conversation=chat_conversation, chat_message=chat_message, proposal_id=99)
    assert result["ok"] is True
    outbound = BotMessage.query.filter_by(direction="outbound").one()
    assert outbound.wa_message_id == "wamid-out-1"
    assert outbound.status == "sent"
    assert chat_message.meta["whatsapp"]["proposal_id"] == 99


def test_approved_client_ai_message_failure_does_not_report_success(monkeypatch):
    client = _make_client()
    bot_conversation = _make_bot_conversation(int(client.id))
    chat_conversation = ChatConversation(scope_key="whatsapp:2", conversation_type="whatsapp", status="open", cliente_id=int(client.id))
    chat_message = ChatMessage(conversation=chat_conversation, sender_type="staff", body="No enviar", meta={})
    db.session.add_all([chat_conversation, chat_message])
    db.session.flush()
    monkeypatch.setattr(
        "services.whatsapp_client_ai_bridge.send_text_message",
        lambda _phone, _body: {"ok": False, "error_code": "meta_delivery_failed", "error_message": "mock failure"},
    )

    result = send_approved_client_ai_message(chat_conversation=chat_conversation, chat_message=chat_message, proposal_id=100)
    assert result["ok"] is False
    assert BotMessage.query.filter_by(direction="outbound").one().status == "failed"


def test_process_and_plans_sends_text_image_followup_and_marks_outbound_side(monkeypatch):
    client = _make_client()
    bot_conversation = _make_bot_conversation(int(client.id))
    chat_conversation = ChatConversation(scope_key="whatsapp:3", conversation_type="whatsapp", status="open", cliente_id=int(client.id))
    db.session.add(chat_conversation)
    db.session.flush()
    proposal = create_or_refresh_action_proposal(
        conversation_id=int(chat_conversation.id),
        client_id=int(client.id),
        action_type="send_process_and_plans",
        target="Cliente actual",
        payload={"action": "send_process_and_plans"},
        human_summary="Enviar proceso y planes",
    )
    calls = []

    def send_text(phone, body):
        calls.append(("text", body))
        return {"ok": True, "wa_message_id": f"wamid-text-{len(calls)}"}

    def send_image(phone, image_url):
        calls.append(("image", image_url))
        return {"ok": True, "wa_message_id": "wamid-image-1"}

    monkeypatch.setattr("services.whatsapp_client_ai_bridge.send_text_message", send_text)
    monkeypatch.setattr("services.whatsapp_client_ai_bridge.send_image_message", send_image)

    result = send_approved_client_ai_process_and_plans(chat_conversation=chat_conversation, proposal=proposal)

    assert result["ok"] is True
    assert [kind for kind, _value in calls] == ["text", "image", "text"]
    assert proposal.execution_result_json["process_text_sent"] is True
    assert proposal.execution_result_json["plans_image_sent"] is True
    assert proposal.execution_result_json["followup_sent"] is True
    messages = ChatMessage.query.filter_by(conversation_id=int(chat_conversation.id)).order_by(ChatMessage.id.asc()).all()
    assert [message.sender_type for message in messages] == ["staff", "staff", "staff"]
    assert messages[1].meta["whatsapp"]["media"]["url"].endswith("public-assets/client-ai/planes-domestica")
    assert messages[1].meta["whatsapp"]["wa_message_id"] == "wamid-image-1"


def test_process_and_plans_image_failure_is_retryable_without_followup_or_duplicate_text(monkeypatch):
    client = _make_client()
    bot_conversation = _make_bot_conversation(int(client.id))
    chat_conversation = ChatConversation(scope_key="whatsapp:4", conversation_type="whatsapp", status="open", cliente_id=int(client.id))
    db.session.add(chat_conversation)
    db.session.flush()
    proposal = create_or_refresh_action_proposal(
        conversation_id=int(chat_conversation.id),
        client_id=int(client.id),
        action_type="send_process_and_plans",
        target="Cliente actual",
        payload={"action": "send_process_and_plans"},
        human_summary="Enviar proceso y planes",
    )
    first_calls = []
    monkeypatch.setattr("services.whatsapp_client_ai_bridge.send_text_message", lambda _phone, _body: first_calls.append("text") or {"ok": True, "wa_message_id": "wamid-process-1"})
    monkeypatch.setattr("services.whatsapp_client_ai_bridge.send_image_message", lambda _phone, _url: first_calls.append("image") or {"ok": False, "error_code": "provider_error"})

    failed = send_approved_client_ai_process_and_plans(chat_conversation=chat_conversation, proposal=proposal)

    assert failed["ok"] is False
    assert first_calls == ["text", "image"]
    assert proposal.execution_result_json["process_text_sent"] is True
    assert proposal.execution_result_json.get("plans_image_sent") is not True
    assert proposal.execution_result_json.get("followup_sent") is not True
    assert ChatMessage.query.filter_by(conversation_id=int(chat_conversation.id)).count() == 1

    retry_calls = []
    monkeypatch.setattr("services.whatsapp_client_ai_bridge.send_text_message", lambda _phone, _body: retry_calls.append("text") or {"ok": True, "wa_message_id": "wamid-followup-1"})
    monkeypatch.setattr("services.whatsapp_client_ai_bridge.send_image_message", lambda _phone, _url: retry_calls.append("image") or {"ok": True, "wa_message_id": "wamid-image-2"})
    retried = send_approved_client_ai_process_and_plans(chat_conversation=chat_conversation, proposal=proposal)

    assert retried["ok"] is True
    assert retry_calls == ["image", "text"]
    assert ChatMessage.query.filter_by(conversation_id=int(chat_conversation.id)).count() == 3
