# -*- coding: utf-8 -*-
"""E2E guardrails for chat fixtures.

Fail-closed when CHAT_E2E_RUN_ID is set and ids are not allowlisted.
"""

from __future__ import annotations

import os


class E2EChatGuardError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = str(reason or "e2e_guard_blocked")


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def chat_e2e_enabled() -> bool:
    return bool(_env("CHAT_E2E_RUN_ID"))


def chat_e2e_run_id() -> str:
    return _env("CHAT_E2E_RUN_ID")


def chat_e2e_tag() -> str:
    rid = chat_e2e_run_id()
    return f"E2E-{rid}" if rid else ""


def chat_e2e_scope_prefix() -> str:
    rid = chat_e2e_run_id()
    return f"e2e:{rid}:" if rid else ""


def chat_e2e_scope_key(*, cliente_id: int, solicitud_id: int | None) -> str:
    if int(solicitud_id or 0) > 0:
        return f"{chat_e2e_scope_prefix()}solicitud:{int(solicitud_id)}"
    return f"{chat_e2e_scope_prefix()}general:{int(cliente_id)}"


def chat_e2e_subject(base_subject: str) -> str:
    tag = chat_e2e_tag()
    if not tag:
        return str(base_subject or "").strip()
    base = str(base_subject or "").strip() or "Soporte"
    if tag in base:
        return base
    return f"[{tag}] {base}"


def _parse_allowlist(raw: str) -> set[int]:
    out: set[int] = set()
    for item in (raw or "").split(","):
        val = item.strip()
        if not val:
            continue
        try:
            out.add(int(val))
        except Exception:
            continue
    return out


def _allowlist_or_fail(env_name: str) -> set[int]:
    allow = _parse_allowlist(_env(env_name))
    if not allow:
        raise E2EChatGuardError(f"allowlist_empty:{env_name}")
    return allow


def enforce_e2e_cliente_id(cliente_id: int) -> None:
    if not chat_e2e_enabled():
        return
    cid = int(cliente_id or 0)
    if cid <= 0:
        raise E2EChatGuardError("invalid_cliente_id")
    allow = _allowlist_or_fail("CHAT_E2E_ALLOWLIST_CLIENTE_IDS")
    if cid not in allow:
        raise E2EChatGuardError("cliente_id_not_allowlisted")


def enforce_e2e_solicitud_id(solicitud_id: int | None) -> None:
    if not chat_e2e_enabled():
        return
    sid = int(solicitud_id or 0)
    if sid <= 0:
        return
    allow = _allowlist_or_fail("CHAT_E2E_ALLOWLIST_SOLICITUD_IDS")
    if sid not in allow:
        raise E2EChatGuardError("solicitud_id_not_allowlisted")


def enforce_e2e_conversation_id(conversation_id: int) -> None:
    if not chat_e2e_enabled():
        return
    cid = int(conversation_id or 0)
    if cid <= 0:
        raise E2EChatGuardError("invalid_conversation_id")
    allow = _allowlist_or_fail("CHAT_E2E_ALLOWLIST_CONVERSATION_IDS")
    if cid not in allow:
        raise E2EChatGuardError("conversation_id_not_allowlisted")


def is_e2e_conversation(conversation) -> bool:
    if not chat_e2e_enabled():
        return False
    prefix = chat_e2e_scope_prefix()
    tag = chat_e2e_tag()
    scope_key = str(getattr(conversation, "scope_key", "") or "")
    subject = str(getattr(conversation, "subject", "") or "")
    return (prefix and scope_key.startswith(prefix)) or (tag and tag in subject)


def enforce_e2e_conversation(conversation) -> str:
    if not chat_e2e_enabled():
        return ""
    if conversation is None:
        raise E2EChatGuardError("conversation_missing")
    if not is_e2e_conversation(conversation):
        raise E2EChatGuardError("conversation_not_e2e")
    enforce_e2e_cliente_id(int(getattr(conversation, "cliente_id", 0) or 0))
    enforce_e2e_solicitud_id(int(getattr(conversation, "solicitud_id", 0) or 0))
    return chat_e2e_tag()


def e2e_message_meta(existing: dict | None = None) -> dict:
    meta = dict(existing or {})
    tag = chat_e2e_tag()
    if tag:
        meta.setdefault("e2e_tag", tag)
        meta.setdefault("e2e_run_id", chat_e2e_run_id())
    return meta

