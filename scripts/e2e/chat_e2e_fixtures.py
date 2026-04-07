# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass

from werkzeug.security import generate_password_hash

from app import app as flask_app
from config_app import db
from models import ChatConversation, ChatMessage, Cliente, DomainOutbox, Solicitud, StaffUser
from utils.chat_e2e_guard import chat_e2e_run_id, chat_e2e_scope_key, chat_e2e_subject, chat_e2e_tag


@dataclass
class ChatE2EFixture:
    run_id: str
    staff_id: int
    staff_username: str
    staff_password: str
    cliente_id: int
    cliente_username: str
    cliente_password: str
    solicitud_id: int
    conv_general_id: int
    conv_solicitud_id: int


def _require_run_id() -> str:
    run_id = chat_e2e_run_id()
    if not run_id:
        raise RuntimeError("CHAT_E2E_RUN_ID requerido")
    return run_id


def _env(name: str, default: str) -> str:
    return (os.getenv(name) or default).strip()


def create_chat_e2e_fixture() -> ChatE2EFixture:
    run_id = _require_run_id()
    tag = chat_e2e_tag()
    staff_username = _env("CHAT_E2E_STAFF_USERNAME", f"e2e_chat_admin_{run_id}")
    staff_password = _env("CHAT_E2E_STAFF_PASSWORD", "Admin#12345")
    client_username = _env("CHAT_E2E_CLIENT_USERNAME", f"e2e_chat_cliente_{run_id}")
    client_password = _env("CHAT_E2E_CLIENT_PASSWORD", "Cliente#12345")
    client_code = _env("CHAT_E2E_CLIENT_CODE", f"CL-E2ECHAT-{run_id}")
    client_email = _env("CHAT_E2E_CLIENT_EMAIL", f"e2e_chat_cliente_{run_id}@test.local")
    client_phone = _env("CHAT_E2E_CLIENT_PHONE", f"8097{run_id[:6].zfill(6)}")

    with flask_app.app_context():
        staff = StaffUser.query.filter_by(username=staff_username).first()
        if staff is None:
            staff = StaffUser(
                username=staff_username,
                email=f"{staff_username}@test.local",
                password_hash=generate_password_hash(staff_password, method="pbkdf2:sha256"),
                role="admin",
                is_active=True,
                mfa_enabled=False,
            )
            db.session.add(staff)

        cliente = Cliente.query.filter_by(codigo=client_code).first()
        if cliente is None:
            cliente = Cliente(
                codigo=client_code,
                nombre_completo=f"Cliente E2E Chat {run_id}",
                email=client_email,
                telefono=client_phone,
                username=client_username,
                password_hash=generate_password_hash(client_password, method="pbkdf2:sha256"),
                role="cliente",
                is_active=True,
                acepto_politicas=True,
            )
            db.session.add(cliente)

        db.session.flush()

        solicitud = Solicitud.query.filter_by(codigo_solicitud=f"SOL-E2ECHAT-{run_id}").first()
        if solicitud is None:
            solicitud = Solicitud(
                cliente_id=int(cliente.id),
                codigo_solicitud=f"SOL-E2ECHAT-{run_id}",
                estado="proceso",
                experiencia=f"Solicitud E2E {run_id}",
            )
            db.session.add(solicitud)
            db.session.flush()

        scope_general = chat_e2e_scope_key(cliente_id=int(cliente.id), solicitud_id=None)
        conv_general = ChatConversation.query.filter_by(scope_key=scope_general).first()
        if conv_general is None:
            conv_general = ChatConversation(
                scope_key=scope_general,
                conversation_type="general",
                status="open",
                cliente_id=int(cliente.id),
                subject=chat_e2e_subject("Soporte general"),
            )
            db.session.add(conv_general)

        scope_solicitud = chat_e2e_scope_key(cliente_id=int(cliente.id), solicitud_id=int(solicitud.id))
        conv_solicitud = ChatConversation.query.filter_by(scope_key=scope_solicitud).first()
        if conv_solicitud is None:
            conv_solicitud = ChatConversation(
                scope_key=scope_solicitud,
                conversation_type="solicitud",
                status="open",
                cliente_id=int(cliente.id),
                solicitud_id=int(solicitud.id),
                subject=chat_e2e_subject(f"Soporte solicitud {solicitud.codigo_solicitud}"),
            )
            db.session.add(conv_solicitud)

        db.session.commit()

        return ChatE2EFixture(
            run_id=run_id,
            staff_id=int(staff.id),
            staff_username=staff.username,
            staff_password=staff_password,
            cliente_id=int(cliente.id),
            cliente_username=cliente.username or "",
            cliente_password=client_password,
            solicitud_id=int(solicitud.id),
            conv_general_id=int(conv_general.id),
            conv_solicitud_id=int(conv_solicitud.id),
        )


def cleanup_chat_e2e_fixture(run_id: str | None = None) -> int:
    rid = (run_id or chat_e2e_run_id()).strip()
    if not rid:
        raise RuntimeError("CHAT_E2E_RUN_ID requerido para cleanup")
    tag = f"E2E-{rid}"
    scope_prefix = f"e2e:{rid}:"

    deleted = 0
    with flask_app.app_context():
        conv_ids = [
            int(r.id)
            for r in ChatConversation.query
            .filter(
                (ChatConversation.scope_key.like(f"{scope_prefix}%"))
                | (ChatConversation.subject.ilike(f"%{tag}%"))
            )
            .all()
        ]
        if conv_ids:
            deleted += (
                db.session.query(ChatMessage)
                .filter(ChatMessage.conversation_id.in_(conv_ids))
                .delete(synchronize_session=False)
            )
            deleted += (
                db.session.query(ChatConversation)
                .filter(ChatConversation.id.in_(conv_ids))
                .delete(synchronize_session=False)
            )
            deleted += (
                db.session.query(DomainOutbox)
                .filter(
                    DomainOutbox.aggregate_type == "ChatConversation",
                    DomainOutbox.aggregate_id.in_([str(x) for x in conv_ids]),
                )
                .delete(synchronize_session=False)
            )

        deleted += (
            db.session.query(Solicitud)
            .filter(Solicitud.codigo_solicitud == f"SOL-E2ECHAT-{rid}")
            .delete(synchronize_session=False)
        )
        deleted += (
            db.session.query(Cliente)
            .filter(Cliente.codigo == f"CL-E2ECHAT-{rid}")
            .delete(synchronize_session=False)
        )
        try:
            deleted += (
                db.session.query(StaffUser)
                .filter(StaffUser.username == f"e2e_chat_admin_{rid}")
                .delete(synchronize_session=False)
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
    return int(deleted)


def _print_exports(fx: ChatE2EFixture) -> None:
    print("export CHAT_E2E_RUN_ID=%s" % fx.run_id)
    print("export CHAT_E2E_ALLOWLIST_CLIENTE_IDS=%s" % fx.cliente_id)
    print("export CHAT_E2E_ALLOWLIST_SOLICITUD_IDS=%s" % fx.solicitud_id)
    print(
        "export CHAT_E2E_ALLOWLIST_CONVERSATION_IDS=%s"
        % (f"{fx.conv_general_id},{fx.conv_solicitud_id}")
    )


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Uso: chat_e2e_fixtures.py create|cleanup [run_id]", file=sys.stderr)
        return 2
    action = (argv[1] or "").strip().lower()
    if action == "create":
        fx = create_chat_e2e_fixture()
        _print_exports(fx)
        return 0
    if action == "cleanup":
        rid = argv[2].strip() if len(argv) > 2 else None
        deleted = cleanup_chat_e2e_fixture(rid)
        print("deleted=%s" % deleted)
        return 0
    print("Accion no valida", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
