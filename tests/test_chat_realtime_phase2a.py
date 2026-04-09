# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from datetime import timedelta
from types import SimpleNamespace

import pytest
from werkzeug.security import generate_password_hash
from werkzeug.exceptions import NotFound

from app import app as flask_app
from config_app import db
from models import ChatConversation, ChatMessage, Cliente, DomainOutbox, RequestIdempotencyKey, StaffUser, StaffPresenceState
from utils.timezone import utc_now_naive, iso_utc_z
import clientes.routes as clientes_routes
import admin.routes as admin_routes
from utils.outbox_relay import OUTBOX_RELAY_ALLOWED_EVENT_TYPES


def _ensure_tables():
    Cliente.__table__.create(bind=db.engine, checkfirst=True)
    StaffUser.__table__.create(bind=db.engine, checkfirst=True)
    StaffPresenceState.__table__.create(bind=db.engine, checkfirst=True)
    DomainOutbox.__table__.create(bind=db.engine, checkfirst=True)
    RequestIdempotencyKey.__table__.create(bind=db.engine, checkfirst=True)
    ChatMessage.__table__.drop(bind=db.engine, checkfirst=True)
    ChatConversation.__table__.drop(bind=db.engine, checkfirst=True)
    ChatConversation.__table__.create(bind=db.engine, checkfirst=True)
    ChatMessage.__table__.create(bind=db.engine, checkfirst=True)


def _reset_tables():
    db.session.query(ChatMessage).delete()
    db.session.query(ChatConversation).delete()
    db.session.query(StaffPresenceState).delete()
    db.session.query(DomainOutbox).delete()
    db.session.query(RequestIdempotencyKey).delete()
    db.session.query(Cliente).delete()
    db.session.query(StaffUser).delete()
    db.session.commit()


def _new_cliente(*, idx: int) -> Cliente:
    row = Cliente(
        codigo=f"CL-CHAT-{idx:03d}",
        nombre_completo=f"Cliente Chat {idx}",
        email=f"cliente_chat_{idx}@test.local",
        telefono=f"809000{idx:04d}",
        username=f"cliente_chat_{idx}",
        password_hash=generate_password_hash("Segura#12345", method="pbkdf2:sha256"),
        role="cliente",
        is_active=True,
    )
    db.session.add(row)
    db.session.flush()
    return row


def _new_staff(*, idx: int) -> StaffUser:
    row = StaffUser(
        username=f"staff_chat_{idx}",
        email=f"staff_chat_{idx}@test.local",
        password_hash=generate_password_hash("Admin#12345", method="pbkdf2:sha256"),
        role="admin",
        is_active=True,
        mfa_enabled=False,
    )
    db.session.add(row)
    db.session.flush()
    return row


def _new_conversation(*, cliente_id: int, solicitud_id: int | None = None) -> ChatConversation:
    conv = ChatConversation(
        scope_key=(f"solicitud:{int(solicitud_id)}" if solicitud_id else f"general:{int(cliente_id)}"),
        conversation_type=("solicitud" if solicitud_id else "general"),
        status="open",
        cliente_id=int(cliente_id),
        solicitud_id=(int(solicitud_id) if solicitud_id else None),
        subject="Soporte general" if not solicitud_id else f"Soporte solicitud {solicitud_id}",
    )
    db.session.add(conv)
    db.session.flush()
    return conv


def _seed_messages(*, conversation_id: int, count: int, sender_type: str = "cliente") -> list[int]:
    out: list[int] = []
    for idx in range(int(count or 0)):
        row = ChatMessage(
            conversation_id=int(conversation_id),
            sender_type=str(sender_type or "cliente"),
            sender_cliente_id=None,
            sender_staff_user_id=None,
            body=f"msg-{int(conversation_id)}-{idx + 1}",
            meta={},
        )
        db.session.add(row)
        db.session.flush()
        out.append(int(row.id))
    return out


def _cliente_user(cliente_id: int):
    return SimpleNamespace(id=int(cliente_id), role="cliente", is_authenticated=True)


def _staff_user(staff_id: int):
    return SimpleNamespace(id=int(staff_id), role="admin", username="staff-test", is_authenticated=True)


def _resp_and_status(resp):
    if isinstance(resp, tuple):
        maybe_resp = resp[0]
        maybe_status = int(resp[1] or 0) if len(resp) > 1 else int(getattr(maybe_resp, "status_code", 0) or 0)
        return maybe_resp, maybe_status
    return resp, int(getattr(resp, "status_code", 0) or 0)


def test_client_chat_send_message_updates_unread_and_emits_outbox():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables()
        _reset_tables()
        cliente = _new_cliente(idx=1)
        conv = _new_conversation(cliente_id=int(cliente.id))
        conv.cliente_unread_count = 2
        conv.status = "closed"
        db.session.commit()

        target = clientes_routes.chat_cliente_send_message
        for _ in range(2):
            target = target.__wrapped__

        with pytest.MonkeyPatch.context() as m:
            m.setattr(clientes_routes, "current_user", _cliente_user(int(cliente.id)))
            m.setattr(clientes_routes, "enforce_business_limit", lambda **_k: (False, 0))
            m.setattr(clientes_routes, "enforce_min_human_interval", lambda **_k: (False, 2))
            with flask_app.test_request_context(
                f"/clientes/chat/conversations/{int(conv.id)}/messages",
                method="POST",
                data={"body": "Hola soporte"},
                headers={
                    "Accept": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                    "Idempotency-Key": f"t-client-send-{int(conv.id)}",
                },
            ):
                resp = target(int(conv.id))

        resp_obj, status_code = _resp_and_status(resp)
        assert status_code == 200
        payload = resp_obj.get_json() or {}
        assert payload.get("ok") is True
        assert (payload.get("message") or {}).get("body") == "Hola soporte"

        conv_db = ChatConversation.query.get(int(conv.id))
        assert conv_db is not None
        assert int(conv_db.staff_unread_count or 0) == 1
        assert int(conv_db.cliente_unread_count or 0) == 2
        assert str(conv_db.last_message_sender_type or "") == "cliente"
        assert str(conv_db.status or "") == "open"

        outbox_row = (
            DomainOutbox.query
            .filter_by(event_type="CHAT_MESSAGE_CREATED", aggregate_type="ChatConversation", aggregate_id=str(conv.id))
            .order_by(DomainOutbox.id.desc())
            .first()
        )
        assert outbox_row is not None
        status_evt = (
            DomainOutbox.query
            .filter_by(event_type="CHAT_CONVERSATION_STATUS_CHANGED", aggregate_type="ChatConversation", aggregate_id=str(conv.id))
            .order_by(DomainOutbox.id.desc())
            .first()
        )
        assert status_evt is not None


def test_client_chat_blocks_cross_cliente_access():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables()
        _reset_tables()
        cliente_1 = _new_cliente(idx=1)
        cliente_2 = _new_cliente(idx=2)
        conv_2 = _new_conversation(cliente_id=int(cliente_2.id))
        db.session.commit()

        target = clientes_routes.chat_cliente_messages_json
        for _ in range(2):
            target = target.__wrapped__

        with pytest.MonkeyPatch.context() as m:
            m.setattr(clientes_routes, "current_user", _cliente_user(int(cliente_1.id)))
            with flask_app.test_request_context(
                f"/clientes/chat/conversations/{int(conv_2.id)}/messages.json",
                method="GET",
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            ):
                with pytest.raises(NotFound):
                    target(int(conv_2.id))


def test_client_chat_messages_pagination_is_incremental_ordered_and_scoped_to_thread():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables()
        _reset_tables()
        cliente = _new_cliente(idx=81)
        conv_general = _new_conversation(cliente_id=int(cliente.id))
        conv_solicitud = _new_conversation(cliente_id=int(cliente.id), solicitud_id=9991)
        general_ids = _seed_messages(conversation_id=int(conv_general.id), count=7, sender_type="cliente")
        _seed_messages(conversation_id=int(conv_solicitud.id), count=3, sender_type="cliente")
        db.session.commit()

        target = clientes_routes.chat_cliente_messages_json
        for _ in range(2):
            target = target.__wrapped__

        with pytest.MonkeyPatch.context() as m:
            m.setattr(clientes_routes, "current_user", _cliente_user(int(cliente.id)))
            with flask_app.test_request_context(
                f"/clientes/chat/conversations/{int(conv_general.id)}/messages.json?limit=3",
                method="GET",
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            ):
                page_1 = target(int(conv_general.id)).get_json() or {}

            with flask_app.test_request_context(
                f"/clientes/chat/conversations/{int(conv_general.id)}/messages.json?limit=3&before_id={int(page_1.get('next_before_id') or 0)}",
                method="GET",
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            ):
                page_2 = target(int(conv_general.id)).get_json() or {}

            with flask_app.test_request_context(
                f"/clientes/chat/conversations/{int(conv_general.id)}/messages.json?limit=3&before_id={int(page_2.get('next_before_id') or 0)}",
                method="GET",
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            ):
                page_3 = target(int(conv_general.id)).get_json() or {}

        ids_1 = [int((r or {}).get("id") or 0) for r in (page_1.get("items") or [])]
        ids_2 = [int((r or {}).get("id") or 0) for r in (page_2.get("items") or [])]
        ids_3 = [int((r or {}).get("id") or 0) for r in (page_3.get("items") or [])]
        merged = ids_1 + ids_2 + ids_3

        assert page_1.get("has_more") is True
        assert page_2.get("has_more") is True
        assert page_3.get("has_more") is False
        assert ids_1 == sorted(ids_1)
        assert ids_2 == sorted(ids_2)
        assert ids_3 == sorted(ids_3)
        assert len(merged) == len(set(merged))
        assert merged == general_ids[-3:] + general_ids[-6:-3] + general_ids[:1]
        assert set(merged).issubset(set(general_ids))


def test_admin_chat_send_message_updates_cliente_unread_and_emits_outbox():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables()
        _reset_tables()
        cliente = _new_cliente(idx=3)
        staff = _new_staff(idx=1)
        conv = _new_conversation(cliente_id=int(cliente.id))
        conv.staff_unread_count = 3
        conv.status = "pending"
        db.session.commit()

        target = admin_routes.chat_staff_send_message
        for _ in range(2):
            target = target.__wrapped__

        with pytest.MonkeyPatch.context() as m:
            m.setattr(admin_routes, "current_user", _staff_user(int(staff.id)))
            m.setattr(admin_routes, "enforce_business_limit", lambda **_k: (False, 0))
            m.setattr(admin_routes, "enforce_min_human_interval", lambda **_k: (False, 2))
            with flask_app.test_request_context(
                f"/admin/chat/conversations/{int(conv.id)}/messages",
                method="POST",
                data={"body": "Recibido. Te ayudamos hoy."},
                headers={
                    "Accept": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                    "Idempotency-Key": f"t-admin-send-{int(conv.id)}",
                },
            ):
                resp = target(int(conv.id))

        resp_obj, status_code = _resp_and_status(resp)
        assert status_code == 200
        payload = resp_obj.get_json() or {}
        assert payload.get("ok") is True
        assert (payload.get("message") or {}).get("sender_type") == "staff"

        conv_db = ChatConversation.query.get(int(conv.id))
        assert conv_db is not None
        assert int(conv_db.cliente_unread_count or 0) == 1
        assert int(conv_db.staff_unread_count or 0) == 3
        assert str(conv_db.status or "") == "open"

        outbox_row = (
            DomainOutbox.query
            .filter_by(event_type="CHAT_MESSAGE_CREATED", aggregate_type="ChatConversation", aggregate_id=str(conv.id))
            .order_by(DomainOutbox.id.desc())
            .first()
        )
        assert outbox_row is not None
        status_evt = (
            DomainOutbox.query
            .filter_by(event_type="CHAT_CONVERSATION_STATUS_CHANGED", aggregate_type="ChatConversation", aggregate_id=str(conv.id))
            .order_by(DomainOutbox.id.desc())
            .first()
        )
        assert status_evt is not None


def test_client_chat_messages_json_includes_staff_presence_in_this_chat():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables()
        _reset_tables()
        cliente = _new_cliente(idx=91)
        staff = _new_staff(idx=91)
        conv = _new_conversation(cliente_id=int(cliente.id))
        now = utc_now_naive()
        state = StaffPresenceState(
            user_id=int(staff.id),
            session_id="tab-chat-presence-1",
            route=f"/admin/chat?conversation_id={int(conv.id)}",
            route_label="Chat soporte",
            entity_type="chat_conversation",
            entity_id=str(int(conv.id)),
            entity_name="",
            entity_code="",
            current_action="chatting",
            action_label="En chat",
            tab_visible=True,
            is_idle=False,
            is_typing=False,
            has_unsaved_changes=False,
            modal_open=False,
            lock_owner="",
            client_status="active",
            page_title="Inbox chat",
            last_interaction_at=now,
            state_hash="presence-test",
            started_at=now,
            last_seen_at=now,
            updated_at=now,
        )
        db.session.add(state)
        db.session.commit()

        target = clientes_routes.chat_cliente_messages_json
        for _ in range(2):
            target = target.__wrapped__

        with pytest.MonkeyPatch.context() as m:
            m.setattr(clientes_routes, "current_user", _cliente_user(int(cliente.id)))
            with flask_app.test_request_context(
                f"/clientes/chat/conversations/{int(conv.id)}/messages.json",
                method="GET",
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            ):
                payload = target(int(conv.id)).get_json() or {}

        conversation = payload.get("conversation") or {}
        assert conversation.get("staff_presence_state") == "in_this_chat"
        assert bool(conversation.get("staff_in_this_chat")) is True


def test_admin_chat_messages_json_includes_cliente_presence_in_this_chat():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables()
        _reset_tables()
        cliente = _new_cliente(idx=92)
        staff = _new_staff(idx=92)
        conv = _new_conversation(cliente_id=int(cliente.id))
        db.session.commit()

        target = admin_routes.chat_staff_messages_json
        for _ in range(2):
            target = target.__wrapped__

        with pytest.MonkeyPatch.context() as m:
            m.setattr(admin_routes, "current_user", _staff_user(int(staff.id)))
            m.setattr(
                admin_routes,
                "bp_get",
                lambda key, default=None, context=None: {
                    "cliente_id": int(cliente.id),
                    "current_path": f"/clientes/chat?conversation_id={int(conv.id)}",
                    "conversation_id": int(conv.id),
                    "last_seen_at": iso_utc_z(),
                },
            )
            with flask_app.test_request_context(
                f"/admin/chat/conversations/{int(conv.id)}/messages.json",
                method="GET",
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            ):
                payload = target(int(conv.id)).get_json() or {}

        conversation = payload.get("conversation") or {}
        assert conversation.get("cliente_presence_state") == "in_this_chat"
        assert bool(conversation.get("cliente_in_this_chat")) is True


def test_admin_chat_messages_pagination_is_incremental_ordered_and_scoped_to_thread():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables()
        _reset_tables()
        cliente = _new_cliente(idx=82)
        staff = _new_staff(idx=82)
        conv_general = _new_conversation(cliente_id=int(cliente.id))
        conv_solicitud = _new_conversation(cliente_id=int(cliente.id), solicitud_id=9992)
        general_ids = _seed_messages(conversation_id=int(conv_general.id), count=6, sender_type="staff")
        _seed_messages(conversation_id=int(conv_solicitud.id), count=2, sender_type="staff")
        db.session.commit()

        target = admin_routes.chat_staff_messages_json
        for _ in range(2):
            target = target.__wrapped__

        with pytest.MonkeyPatch.context() as m:
            m.setattr(admin_routes, "current_user", _staff_user(int(staff.id)))
            with flask_app.test_request_context(
                f"/admin/chat/conversations/{int(conv_general.id)}/messages.json?limit=2",
                method="GET",
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            ):
                page_1 = target(int(conv_general.id)).get_json() or {}

            with flask_app.test_request_context(
                f"/admin/chat/conversations/{int(conv_general.id)}/messages.json?limit=2&before_id={int(page_1.get('next_before_id') or 0)}",
                method="GET",
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            ):
                page_2 = target(int(conv_general.id)).get_json() or {}

            with flask_app.test_request_context(
                f"/admin/chat/conversations/{int(conv_general.id)}/messages.json?limit=2&before_id={int(page_2.get('next_before_id') or 0)}",
                method="GET",
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            ):
                page_3 = target(int(conv_general.id)).get_json() or {}

        ids_1 = [int((r or {}).get("id") or 0) for r in (page_1.get("items") or [])]
        ids_2 = [int((r or {}).get("id") or 0) for r in (page_2.get("items") or [])]
        ids_3 = [int((r or {}).get("id") or 0) for r in (page_3.get("items") or [])]
        merged = ids_1 + ids_2 + ids_3

        assert page_1.get("has_more") is True
        assert page_2.get("has_more") is True
        assert page_3.get("has_more") is False
        assert ids_1 == sorted(ids_1)
        assert ids_2 == sorted(ids_2)
        assert ids_3 == sorted(ids_3)
        assert len(merged) == len(set(merged))
        assert merged == general_ids[-2:] + general_ids[-4:-2] + general_ids[:2]
        assert set(merged).issubset(set(general_ids))


def test_admin_chat_mark_pending_and_closed_change_status_and_emit_outbox():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables()
        _reset_tables()
        cliente = _new_cliente(idx=31)
        staff = _new_staff(idx=31)
        conv = _new_conversation(cliente_id=int(cliente.id))
        db.session.commit()

        mark_pending_target = admin_routes.chat_staff_mark_pending
        mark_closed_target = admin_routes.chat_staff_mark_closed
        for _ in range(2):
            mark_pending_target = mark_pending_target.__wrapped__
            mark_closed_target = mark_closed_target.__wrapped__

        with pytest.MonkeyPatch.context() as m:
            m.setattr(admin_routes, "current_user", _staff_user(int(staff.id)))
            with flask_app.test_request_context(
                f"/admin/chat/{int(conv.id)}/mark_pending",
                method="POST",
                data={},
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            ):
                pending_resp = mark_pending_target(int(conv.id))

            with flask_app.test_request_context(
                f"/admin/chat/{int(conv.id)}/mark_closed",
                method="POST",
                data={},
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            ):
                closed_resp = mark_closed_target(int(conv.id))

        assert pending_resp.status_code == 200
        assert closed_resp.status_code == 200

        conv_db = ChatConversation.query.get(int(conv.id))
        assert conv_db is not None
        assert str(conv_db.status or "") == "closed"

        events = (
            DomainOutbox.query
            .filter_by(event_type="CHAT_CONVERSATION_STATUS_CHANGED", aggregate_type="ChatConversation", aggregate_id=str(conv.id))
            .all()
        )
        assert len(events or []) >= 2


def test_admin_chat_open_filter_excludes_closed_conversations():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables()
        _reset_tables()
        cliente = _new_cliente(idx=41)
        cliente_2 = _new_cliente(idx=42)
        staff = _new_staff(idx=41)
        open_conv = _new_conversation(cliente_id=int(cliente.id))
        closed_conv = ChatConversation(
            scope_key=f"general:{int(cliente_2.id)}",
            conversation_type="general",
            status="closed",
            cliente_id=int(cliente_2.id),
            solicitud_id=None,
            subject="Soporte cerrado",
        )
        db.session.add(closed_conv)
        db.session.commit()

        target = admin_routes.chat_staff_conversations_json
        for _ in range(2):
            target = target.__wrapped__

        with pytest.MonkeyPatch.context() as m:
            m.setattr(admin_routes, "current_user", _staff_user(int(staff.id)))
            with flask_app.test_request_context(
                "/admin/chat/conversations.json?status=open",
                method="GET",
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            ):
                resp = target()

        assert resp.status_code == 200
        payload = resp.get_json() or {}
        ids = {int((row or {}).get("id") or 0) for row in (payload.get("items") or [])}
        assert int(open_conv.id) in ids
        assert int(closed_conv.id) not in ids


def test_client_chat_mark_read_clears_unread_and_emits_outbox():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables()
        _reset_tables()
        cliente = _new_cliente(idx=4)
        conv = _new_conversation(cliente_id=int(cliente.id))
        conv.cliente_unread_count = 2
        db.session.commit()

        target = clientes_routes.chat_cliente_mark_read
        for _ in range(2):
            target = target.__wrapped__

        with pytest.MonkeyPatch.context() as m:
            m.setattr(clientes_routes, "current_user", _cliente_user(int(cliente.id)))
            with flask_app.test_request_context(
                f"/clientes/chat/conversations/{int(conv.id)}/read",
                method="POST",
                data={},
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            ):
                resp = target(int(conv.id))

        assert resp.status_code == 200
        payload = resp.get_json() or {}
        assert payload.get("ok") is True

        conv_db = ChatConversation.query.get(int(conv.id))
        assert conv_db is not None
        assert int(conv_db.cliente_unread_count or 0) == 0

        outbox_row = (
            DomainOutbox.query
            .filter_by(event_type="CHAT_CONVERSATION_READ", aggregate_type="ChatConversation", aggregate_id=str(conv.id))
            .order_by(DomainOutbox.id.desc())
            .first()
        )
        assert outbox_row is not None


def test_admin_chat_mark_read_clears_unread_and_emits_outbox():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables()
        _reset_tables()
        cliente = _new_cliente(idx=5)
        staff = _new_staff(idx=2)
        conv = _new_conversation(cliente_id=int(cliente.id))
        conv.staff_unread_count = 2
        db.session.commit()

        target = admin_routes.chat_staff_mark_read
        for _ in range(2):
            target = target.__wrapped__

        with pytest.MonkeyPatch.context() as m:
            m.setattr(admin_routes, "current_user", _staff_user(int(staff.id)))
            with flask_app.test_request_context(
                f"/admin/chat/conversations/{int(conv.id)}/read",
                method="POST",
                data={},
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            ):
                resp = target(int(conv.id))

        assert resp.status_code == 200
        payload = resp.get_json() or {}
        assert payload.get("ok") is True

        conv_db = ChatConversation.query.get(int(conv.id))
        assert conv_db is not None
        assert int(conv_db.staff_unread_count or 0) == 0

        outbox_row = (
            DomainOutbox.query
            .filter_by(event_type="CHAT_CONVERSATION_READ", aggregate_type="ChatConversation", aggregate_id=str(conv.id))
            .order_by(DomainOutbox.id.desc())
            .first()
        )
        assert outbox_row is not None


def test_admin_chat_take_and_release_assignment_roundtrip():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables()
        _reset_tables()
        cliente = _new_cliente(idx=61)
        staff = _new_staff(idx=61)
        conv = _new_conversation(cliente_id=int(cliente.id))
        db.session.commit()

        take_target = admin_routes.chat_staff_take_conversation
        release_target = admin_routes.chat_staff_release_conversation
        for _ in range(2):
            take_target = take_target.__wrapped__
            release_target = release_target.__wrapped__

        with pytest.MonkeyPatch.context() as m:
            m.setattr(admin_routes, "current_user", _staff_user(int(staff.id)))
            with flask_app.test_request_context(
                f"/admin/chat/conversations/{int(conv.id)}/take",
                method="POST",
                data={},
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            ):
                take_resp = take_target(int(conv.id))

            with flask_app.test_request_context(
                f"/admin/chat/conversations/{int(conv.id)}/release",
                method="POST",
                data={},
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            ):
                release_resp = release_target(int(conv.id))

        assert take_resp.status_code == 200
        assert release_resp.status_code == 200

        conv_db = ChatConversation.query.get(int(conv.id))
        assert conv_db is not None
        assert conv_db.assigned_staff_user_id is None
        assert conv_db.assigned_at is None

        assignment_events = (
            DomainOutbox.query
            .filter_by(event_type="CHAT_CONVERSATION_ASSIGNED", aggregate_type="ChatConversation", aggregate_id=str(conv.id))
            .order_by(DomainOutbox.id.asc())
            .all()
        )
        assert len(assignment_events or []) >= 2


def test_admin_chat_send_message_auto_assigns_when_unassigned():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables()
        _reset_tables()
        cliente = _new_cliente(idx=62)
        staff = _new_staff(idx=62)
        conv = _new_conversation(cliente_id=int(cliente.id))
        conv.assigned_staff_user_id = None
        conv.assigned_at = None
        db.session.commit()

        target = admin_routes.chat_staff_send_message
        for _ in range(2):
            target = target.__wrapped__

        with pytest.MonkeyPatch.context() as m:
            m.setattr(admin_routes, "current_user", _staff_user(int(staff.id)))
            m.setattr(admin_routes, "enforce_business_limit", lambda **_k: (False, 0))
            m.setattr(admin_routes, "enforce_min_human_interval", lambda **_k: (False, 2))
            with flask_app.test_request_context(
                f"/admin/chat/conversations/{int(conv.id)}/messages",
                method="POST",
                data={"body": "Tomado para atender."},
                headers={
                    "Accept": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                    "Idempotency-Key": f"t-admin-send-autoassign-{int(conv.id)}",
                },
            ):
                resp = target(int(conv.id))

        assert resp.status_code == 200
        payload = resp.get_json() or {}
        assert payload.get("ok") is True

        conv_db = ChatConversation.query.get(int(conv.id))
        assert conv_db is not None
        assert int(conv_db.assigned_staff_user_id or 0) == int(staff.id)
        assert conv_db.assigned_at is not None

        assignment_evt = (
            DomainOutbox.query
            .filter_by(event_type="CHAT_CONVERSATION_ASSIGNED", aggregate_type="ChatConversation", aggregate_id=str(conv.id))
            .order_by(DomainOutbox.id.desc())
            .first()
        )
        assert assignment_evt is not None


def test_admin_chat_filter_mine_only_returns_current_staff_assigned_conversations():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables()
        _reset_tables()
        cliente_1 = _new_cliente(idx=63)
        cliente_2 = _new_cliente(idx=64)
        cliente_3 = _new_cliente(idx=65)
        staff_1 = _new_staff(idx=63)
        staff_2 = _new_staff(idx=64)
        conv_mine = _new_conversation(cliente_id=int(cliente_1.id))
        conv_other = _new_conversation(cliente_id=int(cliente_2.id))
        conv_unassigned = _new_conversation(cliente_id=int(cliente_3.id))
        conv_mine.assigned_staff_user_id = int(staff_1.id)
        conv_other.assigned_staff_user_id = int(staff_2.id)
        conv_unassigned.assigned_staff_user_id = None
        db.session.commit()

        target = admin_routes.chat_staff_conversations_json
        for _ in range(2):
            target = target.__wrapped__

        with pytest.MonkeyPatch.context() as m:
            m.setattr(admin_routes, "current_user", _staff_user(int(staff_1.id)))
            with flask_app.test_request_context(
                "/admin/chat/conversations.json?status=all&assignment=mine",
                method="GET",
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            ):
                resp = target()

        assert resp.status_code == 200
        payload = resp.get_json() or {}
        ids = {int((row or {}).get("id") or 0) for row in (payload.get("items") or [])}
        assert int(conv_mine.id) in ids
        assert int(conv_other.id) not in ids
        assert int(conv_unassigned.id) not in ids


def test_admin_chat_sla_snapshot_and_priority_ordering_for_conversations_json():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables()
        _reset_tables()
        staff = _new_staff(idx=120)
        now = admin_routes.utc_now_naive()

        cliente_overdue = _new_cliente(idx=1201)
        cliente_warning = _new_cliente(idx=1202)
        cliente_recent = _new_cliente(idx=1203)
        cliente_pending = _new_cliente(idx=1204)
        cliente_closed = _new_cliente(idx=1205)

        conv_overdue = _new_conversation(cliente_id=int(cliente_overdue.id))
        conv_overdue.status = "open"
        conv_overdue.last_message_sender_type = "cliente"
        conv_overdue.last_message_at = now - timedelta(minutes=180)
        conv_overdue.staff_unread_count = 1

        conv_warning = _new_conversation(cliente_id=int(cliente_warning.id))
        conv_warning.status = "open"
        conv_warning.last_message_sender_type = "cliente"
        conv_warning.last_message_at = now - timedelta(minutes=40)
        conv_warning.staff_unread_count = 1

        conv_recent = _new_conversation(cliente_id=int(cliente_recent.id))
        conv_recent.status = "open"
        conv_recent.last_message_sender_type = "cliente"
        conv_recent.last_message_at = now - timedelta(minutes=5)
        conv_recent.staff_unread_count = 1

        conv_pending = _new_conversation(cliente_id=int(cliente_pending.id))
        conv_pending.status = "pending"
        conv_pending.last_message_sender_type = "cliente"
        conv_pending.last_message_at = now - timedelta(minutes=360)
        conv_pending.staff_unread_count = 10

        conv_closed = _new_conversation(cliente_id=int(cliente_closed.id))
        conv_closed.status = "closed"
        conv_closed.last_message_sender_type = "cliente"
        conv_closed.last_message_at = now - timedelta(minutes=720)
        conv_closed.staff_unread_count = 20
        db.session.commit()

        target = admin_routes.chat_staff_conversations_json
        for _ in range(2):
            target = target.__wrapped__

        with pytest.MonkeyPatch.context() as m:
            m.setattr(admin_routes, "current_user", _staff_user(int(staff.id)))
            with flask_app.test_request_context(
                "/admin/chat/conversations.json?status=all",
                method="GET",
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            ):
                resp = target()

        assert resp.status_code == 200
        payload = resp.get_json() or {}
        items = payload.get("items") or []

        assert len(items) >= 5
        by_id = {int((row or {}).get("id") or 0): (row or {}) for row in items}

        assert by_id[int(conv_overdue.id)].get("sla_level") == "overdue"
        assert by_id[int(conv_overdue.id)].get("sla_label") == "Atrasada"
        assert by_id[int(conv_warning.id)].get("sla_level") == "warning"
        assert by_id[int(conv_warning.id)].get("sla_label") == "Atención"
        assert by_id[int(conv_recent.id)].get("sla_level") == "normal"
        assert by_id[int(conv_recent.id)].get("sla_label") == "Reciente"
        assert by_id[int(conv_pending.id)].get("status") == "pending"
        assert by_id[int(conv_pending.id)].get("sla_priority_rank") == 30
        assert by_id[int(conv_closed.id)].get("status") == "closed"
        assert by_id[int(conv_closed.id)].get("sla_level") in (None, "")

        ids_ordered = [int((row or {}).get("id") or 0) for row in items]
        assert ids_ordered.index(int(conv_overdue.id)) < ids_ordered.index(int(conv_warning.id))
        assert ids_ordered.index(int(conv_warning.id)) < ids_ordered.index(int(conv_recent.id))
        assert ids_ordered.index(int(conv_recent.id)) < ids_ordered.index(int(conv_pending.id))
        assert ids_ordered.index(int(conv_pending.id)) < ids_ordered.index(int(conv_closed.id))


def test_admin_chat_sla_recalculates_after_new_client_message():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables()
        _reset_tables()
        cliente = _new_cliente(idx=1301)
        staff = _new_staff(idx=1301)
        conv = _new_conversation(cliente_id=int(cliente.id))
        conv.status = "pending"
        conv.last_message_sender_type = "staff"
        conv.last_message_at = admin_routes.utc_now_naive() - timedelta(minutes=200)
        conv.staff_unread_count = 0
        db.session.commit()

        send_target = clientes_routes.chat_cliente_send_message
        for _ in range(2):
            send_target = send_target.__wrapped__

        with pytest.MonkeyPatch.context() as m:
            m.setattr(clientes_routes, "current_user", _cliente_user(int(cliente.id)))
            m.setattr(clientes_routes, "enforce_business_limit", lambda **_k: (False, 0))
            m.setattr(clientes_routes, "enforce_min_human_interval", lambda **_k: (False, 2))
            with flask_app.test_request_context(
                f"/clientes/chat/conversations/{int(conv.id)}/messages",
                method="POST",
                data={"body": "Necesito ayuda por favor"},
                headers={
                    "Accept": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                    "Idempotency-Key": f"t-client-sla-refresh-{int(conv.id)}",
                },
            ):
                send_resp = send_target(int(conv.id))
        assert _resp_and_status(send_resp)[1] == 200

        list_target = admin_routes.chat_staff_conversations_json
        for _ in range(2):
            list_target = list_target.__wrapped__
        with pytest.MonkeyPatch.context() as m:
            m.setattr(admin_routes, "current_user", _staff_user(int(staff.id)))
            with flask_app.test_request_context(
                "/admin/chat/conversations.json?status=all",
                method="GET",
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            ):
                list_resp = list_target()

        assert list_resp.status_code == 200
        items = (list_resp.get_json() or {}).get("items") or []
        row = next((r for r in items if int((r or {}).get("id") or 0) == int(conv.id)), None)
        assert row is not None
        assert str(row.get("status") or "") == "open"
        assert str(row.get("last_message_sender_type") or "") == "cliente"
        assert bool(row.get("sla_waiting_on_staff")) is True
        assert str(row.get("sla_label") or "") == "Reciente"


def test_client_routes_do_not_expose_chat_assignment_mutations():
    rules = [str(rule.rule or "") for rule in flask_app.url_map.iter_rules() if str(rule.endpoint or "").startswith("clientes.")]
    lowered = [r.lower() for r in rules]
    assert all("/assignment" not in r for r in lowered)
    assert all("/take" not in r for r in lowered)
    assert all("/release" not in r for r in lowered)
    assert all("/assign" not in r for r in lowered)


def test_live_normalizers_accept_chat_events_and_route_to_chat_view():
    row = DomainOutbox(
        id=991001,
        event_id="evt_chat_normalizer_1",
        event_type="CHAT_MESSAGE_CREATED",
        aggregate_type="ChatConversation",
        aggregate_id="42",
        occurred_at=clientes_routes.utc_now_naive(),
        created_at=clientes_routes.utc_now_naive(),
        payload={"cliente_id": 7, "conversation_id": 42, "message_id": 100, "sender_type": "staff"},
    )

    with pytest.MonkeyPatch.context() as m:
        m.setattr(clientes_routes, "_cliente_live_target_matches_solicitud", lambda *_a, **_k: True)
        evt_cliente = clientes_routes._normalize_cliente_live_event_from_outbox(row, current_cliente_id=7)

    assert evt_cliente is not None
    assert evt_cliente.get("event_type") == "cliente.chat.message_created"
    assert "chat" in set(((evt_cliente.get("invalidate") or {}).get("views") or []))

    row_status = DomainOutbox(
        id=991002,
        event_id="evt_chat_status_normalizer_1",
        event_type="CHAT_CONVERSATION_STATUS_CHANGED",
        aggregate_type="ChatConversation",
        aggregate_id="42",
        occurred_at=clientes_routes.utc_now_naive(),
        created_at=clientes_routes.utc_now_naive(),
        payload={"cliente_id": 7, "conversation_id": 42, "from": "pending", "to": "closed"},
    )
    with pytest.MonkeyPatch.context() as m:
        m.setattr(clientes_routes, "_cliente_live_target_matches_solicitud", lambda *_a, **_k: True)
        evt_cliente_status = clientes_routes._normalize_cliente_live_event_from_outbox(row_status, current_cliente_id=7)
    assert evt_cliente_status is not None
    assert evt_cliente_status.get("event_type") == "cliente.chat.status_changed"
    assert "chat" in set(((evt_cliente_status.get("invalidate") or {}).get("views") or []))

    evt_admin = admin_routes._normalize_live_invalidation_event(
        {
            "event_id": "evt_chat_normalizer_admin",
            "event_type": "CHAT_CONVERSATION_STATUS_CHANGED",
            "aggregate": {"type": "ChatConversation", "id": "42", "version": None},
            "payload": {"conversation_id": 42, "cliente_id": 7},
        }
    )
    assert evt_admin is not None
    assert ((evt_admin.get("target") or {}).get("entity_type") or "") == "chat_conversation"
    assert int(((evt_admin.get("target") or {}).get("conversation_id") or 0)) == 42

    evt_admin_assignment = admin_routes._normalize_live_invalidation_event(
        {
            "event_id": "evt_chat_assignment_admin",
            "event_type": "CHAT_CONVERSATION_ASSIGNED",
            "aggregate": {"type": "ChatConversation", "id": "77", "version": None},
            "payload": {"conversation_id": 77, "cliente_id": 9, "assigned_staff_user_id": 11},
        }
    )
    assert evt_admin_assignment is not None
    assert int(((evt_admin_assignment.get("target") or {}).get("conversation_id") or 0)) == 77


def test_outbox_relay_allowlist_includes_chat_event_types():
    assert "CHAT_MESSAGE_CREATED" in OUTBOX_RELAY_ALLOWED_EVENT_TYPES
    assert "CHAT_CONVERSATION_READ" in OUTBOX_RELAY_ALLOWED_EVENT_TYPES
    assert "CHAT_CONVERSATION_STATUS_CHANGED" in OUTBOX_RELAY_ALLOWED_EVENT_TYPES
    assert "CHAT_CONVERSATION_ASSIGNED" in OUTBOX_RELAY_ALLOWED_EVENT_TYPES


def test_admin_chat_badge_json_counts_unread_conversations():
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables()
        _reset_tables()
        cliente = _new_cliente(idx=92)
        staff = _new_staff(idx=92)
        conv_1 = _new_conversation(cliente_id=int(cliente.id))
        conv_2 = _new_conversation(cliente_id=int(cliente.id), solicitud_id=902)
        conv_3 = _new_conversation(cliente_id=int(cliente.id), solicitud_id=903)
        conv_1.staff_unread_count = 4
        conv_2.staff_unread_count = 1
        conv_3.staff_unread_count = 0
        db.session.commit()

        target = admin_routes.chat_staff_badge_json
        for _ in range(2):
            target = target.__wrapped__

        with pytest.MonkeyPatch.context() as m:
            m.setattr(admin_routes, "current_user", _staff_user(int(staff.id)))
            with flask_app.test_request_context(
                "/admin/chat/badge.json",
                method="GET",
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            ):
                resp = target()

        payload = (resp.get_json() or {})
        assert payload.get("ok") is True
        assert int(payload.get("unread_conversations") or 0) == 2
        assert int(payload.get("unread_messages") or 0) == 5


def test_chat_templates_and_assets_are_wired():
    base_cli = os.path.join(os.getcwd(), "templates", "clientes", "base.html")
    chat_cli = os.path.join(os.getcwd(), "templates", "clientes", "chat.html")
    base_admin = os.path.join(os.getcwd(), "templates", "base.html")
    chat_admin = os.path.join(os.getcwd(), "templates", "admin", "chat_inbox.html")
    js_cli = os.path.join(os.getcwd(), "static", "js", "chat", "client_chat.js")
    js_admin = os.path.join(os.getcwd(), "static", "js", "chat", "admin_chat.js")
    js_global_badge = os.path.join(os.getcwd(), "static", "js", "chat", "chat_global_badge.js")

    with open(base_cli, "r", encoding="utf-8") as f:
        base_cli_txt = f.read()
    with open(chat_cli, "r", encoding="utf-8") as f:
        chat_cli_txt = f.read()
    with open(base_admin, "r", encoding="utf-8") as f:
        base_admin_txt = f.read()
    with open(chat_admin, "r", encoding="utf-8") as f:
        chat_admin_txt = f.read()
    with open(js_cli, "r", encoding="utf-8") as f:
        js_cli_txt = f.read()
    with open(js_admin, "r", encoding="utf-8") as f:
        js_admin_txt = f.read()
    with open(js_global_badge, "r", encoding="utf-8") as f:
        js_global_badge_txt = f.read()

    assert "url_for('clientes.chat_cliente')" in base_cli_txt
    assert 'data-client-live-view="chat"' in chat_cli_txt
    assert "client_chat.js" in chat_cli_txt
    assert "url_for('admin.chat_staff_inbox')" in base_admin_txt
    assert "adminChatGlobalBadge" in base_admin_txt
    assert "url_for('admin.chat_staff_badge_json')" in base_admin_txt
    assert "chat_global_badge.js" in base_admin_txt
    assert "admin_chat.js" in chat_admin_txt
    assert "window.ClientChat" in js_cli_txt
    assert "clientChatLoadOlderBtn" in chat_cli_txt
    assert "adminChatLoadOlderBtn" in chat_admin_txt
    assert "loadOlderMessages" in js_cli_txt
    assert "loadOlderMessages" in js_admin_txt
    assert "chat_inbox" in js_admin_txt
    assert "adminChatQuickReplies" in chat_admin_txt
    assert "admin-chat-quick-reply-btn" in chat_admin_txt
    assert "data-quick-reply-body" in chat_admin_txt
    assert "adminChatGoClienteLink" in chat_admin_txt
    assert "adminChatGoSolicitudLink" in chat_admin_txt
    assert "insertIntoComposer" in js_admin_txt
    assert "parseQuickReplyBody" in js_admin_txt
    assert "clienteUrlForConversation" in js_admin_txt
    assert "solicitudUrlForConversation" in js_admin_txt
    assert "adminChatQuickReplies" not in chat_cli_txt
    assert "data-chat-global-badge-enabled" in base_admin_txt
    assert "unread_conversations" in js_global_badge_txt
