"""Adaptador live local para la matriz Round 9 de entrada.

El runner usa el Flask real y PostgreSQL local, pero nunca selecciona una DB
por defecto. Solo ejecuta con ``APP_ENV=development`` y
``domestica_cibao_local``. Cada caso crea y elimina sus propios fixtures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import text

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

LIVE_PREFIX = "QA-R9C-LIVE-"
SAFE_BODY_KEYS = {
    "ok",
    "success",
    "error",
    "error_code",
    "message",
    "route_hits_minute",
    "sampled",
}


@dataclass(frozen=True)
class RequestCase:
    name: str
    content_type: str | None
    payload: Any
    expected_mutation: bool
    category: str


@dataclass(frozen=True)
class LiveObservation:
    endpoint: str
    case: str
    status: int | None
    controlled: bool
    mutated: bool
    internal_error: bool
    body: dict[str, Any]


def live_prefix() -> str:
    return f"{LIVE_PREFIX}{uuid4().hex[:12].upper()}-"


def fixture_value(prefix: str, label: str) -> str:
    if not str(prefix or "").startswith(LIVE_PREFIX):
        raise ValueError("fixture prefix must start with QA-R9C-LIVE-")
    clean_label = "-".join(str(label or "fixture").split())[:80]
    return f"{prefix}{clean_label}"


def _assert_live_db(app) -> None:
    env = (os.getenv("APP_ENV") or "").strip().lower()
    if env != "development":
        raise RuntimeError("Adaptador live bloqueado: APP_ENV debe ser development")
    uri = str(app.config.get("SQLALCHEMY_DATABASE_URI") or "")
    if "domestica_cibao_local" not in uri or "localhost" not in uri and "127.0.0.1" not in uri:
        raise RuntimeError("Adaptador live bloqueado: URI no es PostgreSQL local domestica_cibao_local")
    if app.extensions["sqlalchemy"].engine.dialect.name != "postgresql":
        raise RuntimeError("Adaptador live bloqueado: se requiere PostgreSQL")
    with app.extensions["sqlalchemy"].engine.connect() as conn:
        database = conn.execute(text("SELECT current_database()")).scalar()
        host = conn.execute(text("SELECT inet_server_addr()::text")).scalar()
    if database != "domestica_cibao_local" or host not in {"127.0.0.1/32", "::1/128", "127.0.0.1"}:
        raise RuntimeError(f"Adaptador live bloqueado: DB/host inesperados: {database!r}/{host!r}")


def _safe_body(response) -> dict[str, Any]:
    if 300 <= response.status_code < 400:
        return {"location": (response.headers.get("Location") or "")[:160]}
    try:
        payload = response.get_json(silent=True)
    except Exception:
        payload = None
    if not isinstance(payload, dict):
        return {"content_type": response.content_type or "", "body_length": len(response.get_data())}
    return {key: payload[key] for key in SAFE_BODY_KEYS if key in payload}


def content_type_cases(endpoint: str, *, valid_payload: dict[str, Any]) -> tuple[RequestCase, ...]:
    """Construye la matriz live; JSON/form/multipart son los formatos útiles."""
    return (
        RequestCase("json_valido", "application/json", valid_payload, False, "valid-but-unsupported"),
        RequestCase("json_invalido", "application/json", b"{invalid", False, "invalid"),
        RequestCase("form_urlencoded", "application/x-www-form-urlencoded", valid_payload, True, "valid"),
        RequestCase("multipart", "multipart/form-data", valid_payload, True, "valid"),
        RequestCase("text_plain", "text/plain", json.dumps(valid_payload), False, "unsupported"),
        RequestCase("sin_content_type", None, json.dumps(valid_payload).encode("utf-8"), False, "unsupported"),
    )


def _csrf_token(app, client, path: str) -> str:
    """Create the same signed token Flask-WTF validates for the client."""
    from itsdangerous import URLSafeTimedSerializer

    raw_token = hashlib.sha1(os.urandom(64)).hexdigest()
    serializer = URLSafeTimedSerializer(app.secret_key, salt="wtf-csrf-token")
    token = serializer.dumps(raw_token)
    with client.session_transaction() as client_session:
        client_session["csrf_token"] = raw_token
    return token


def client_plan_input_cases() -> tuple[RequestCase, ...]:
    base = {"tipo_plan": "premium"}
    return (
        RequestCase("required_ausente", "application/x-www-form-urlencoded", {}, False, "invalid"),
        RequestCase("null", "application/x-www-form-urlencoded", {"tipo_plan": ""}, False, "invalid"),
        RequestCase("tipo_incorrecto", "application/x-www-form-urlencoded", {"tipo_plan": "[]"}, False, "invalid"),
        RequestCase("enum_invalido", "application/x-www-form-urlencoded", {"tipo_plan": "ultra"}, False, "invalid"),
        RequestCase("string_largo", "application/x-www-form-urlencoded", {"tipo_plan": "x" * 500}, False, "invalid"),
        RequestCase("unicode", "application/x-www-form-urlencoded", {"tipo_plan": "plan-ñ"}, False, "invalid"),
        RequestCase("campo_extra", "application/x-www-form-urlencoded", {**base, "campo_extra": "QA-R9C-LIVE-extra"}, True, "valid"),
        RequestCase("array_en_escalar", "application/json", {"tipo_plan": ["premium"]}, False, "unsupported"),
        RequestCase("objeto_en_escalar", "application/json", {"tipo_plan": {"value": "premium"}}, False, "unsupported"),
    )


def public_live_input_cases(path: str) -> tuple[RequestCase, ...]:
    valid = {"event_type": "pageview", "current_path": path, "page_title": "QA Round9"}
    return (
        RequestCase("required_ausente", "application/json", {}, False, "invalid"),
        RequestCase("null", "application/json", None, False, "invalid"),
        RequestCase("tipo_incorrecto", "application/json", {**valid, "event_type": 12}, False, "invalid"),
        RequestCase("enum_invalido", "application/json", {**valid, "event_type": "not-allowed"}, False, "invalid"),
        RequestCase("string_largo", "application/json", {**valid, "event_type": "x" * 500}, False, "invalid"),
        RequestCase("unicode", "application/json", {**valid, "page_title": "QA Ñá"}, True, "valid"),
        RequestCase("campo_extra", "application/json", {**valid, "extra": "QA-R9C-LIVE-extra"}, True, "valid"),
        RequestCase("array_en_escalar", "application/json", {**valid, "current_path": [path]}, False, "invalid"),
        RequestCase("objeto_en_escalar", "application/json", {**valid, "current_path": {"path": path}}, False, "invalid"),
    )


def _request(client, path: str, case: RequestCase, *, csrf: str | None = None):
    headers = {"Accept": "application/json"}
    if csrf:
        headers["X-CSRFToken"] = csrf
    if case.content_type == "application/json":
        data = case.payload if isinstance(case.payload, (bytes, str)) else json.dumps(case.payload)
        return client.post(path, data=data, content_type=case.content_type, headers=headers)
    if case.content_type == "multipart/form-data":
        return client.post(path, data=case.payload, content_type=case.content_type, headers=headers)
    if case.content_type == "application/x-www-form-urlencoded":
        data = dict(case.payload)
        if csrf:
            data["csrf_token"] = csrf
        return client.post(path, data=data, content_type=case.content_type, headers=headers)
    if case.content_type == "text/plain":
        return client.post(path, data=case.payload, content_type=case.content_type, headers=headers)
    return client.post(path, data=case.payload, headers=headers)


def _run_case(*, app, endpoint: str, case: RequestCase, fixture: dict[str, Any], login: Callable, snapshot: Callable, cleanup: Callable) -> LiveObservation:
    client = app.test_client()
    login(client, fixture)
    before = snapshot(fixture)
    with client:
        csrf = _csrf_token(app, client, fixture["csrf_path"])
        response = _request(client, fixture["path"], case, csrf=csrf)
    after = snapshot(fixture)
    cleanup(fixture)
    body = _safe_body(response)
    return LiveObservation(
        endpoint=endpoint,
        case=case.name,
        status=response.status_code,
        controlled=response.status_code < 500 and "Traceback" not in response.get_data(as_text=True),
        mutated=before != after,
        internal_error=response.status_code >= 500,
        body=body,
    )


def run_live_matrix(app, endpoint_specs: tuple[dict[str, Any], ...]) -> list[LiveObservation]:
    """Execute each case with an isolated fixture and exact cleanup."""
    _assert_live_db(app)
    observations: list[LiveObservation] = []
    for spec in endpoint_specs:
        for case in spec["cases"]():
            fixture = spec["prepare"]()
            try:
                observations.append(
                    _run_case(
                        app=app,
                        endpoint=spec["name"],
                        case=case,
                        fixture=fixture,
                        login=spec["login"],
                        snapshot=spec["snapshot"],
                        cleanup=spec["cleanup"],
                    )
                )
            finally:
                spec["cleanup"](fixture)
    return observations


def _make_specs(app):
    from models import Cliente, Solicitud, StaffAuditLog
    from app import db
    from utils.timezone import utc_now_naive

    prefix = live_prefix()
    short_code = prefix[:20]

    def prepare_client_plan():
        client = Cliente(
            codigo=short_code,
            nombre_completo="QA Round9 Live Client",
            email=f"{prefix.lower()}@example.invalid",
            telefono="8095550101",
            username=fixture_value(prefix, "LOGIN"),
            acepto_politicas=True,
        )
        db.session.add(client)
        db.session.flush()
        solicitud = Solicitud(
            cliente_id=client.id,
            codigo_solicitud=fixture_value(prefix, "SOL"),
            fecha_solicitud=utc_now_naive(),
            estado="proceso",
            tipo_plan=None,
        )
        db.session.add(solicitud)
        db.session.commit()
        return {"client_id": client.id, "solicitud_id": solicitud.id, "path": f"/clientes/solicitudes/{solicitud.id}/plan", "csrf_path": f"/clientes/solicitudes/{solicitud.id}/plan"}

    def client_login(client, fixture):
        from flask_login import login_user

        user = db.session.get(Cliente, fixture["client_id"])
        with client:
            client.get("/")
            login_user(user)
            from flask import session
            session_values = dict(session)
        with client.session_transaction() as sess:
            sess.update(session_values)

    def client_snapshot(fixture):
        row = db.session.get(Solicitud, fixture["solicitud_id"])
        return (row.tipo_plan if row else None, row.row_version if row else None)

    def client_cleanup(fixture):
        db.session.rollback()
        solicitud = db.session.get(Solicitud, fixture.get("solicitud_id"))
        client = db.session.get(Cliente, fixture.get("client_id"))
        if solicitud:
            db.session.delete(solicitud)
        if client:
            db.session.delete(client)
        db.session.commit()

    def prepare_public_live():
        path = f"/{prefix.lower()}"
        return {"path": "/live/ping", "csrf_path": "/", "audit_entity": path[:64]}

    def public_login(client, fixture):
        return None

    def public_snapshot(fixture):
        return db.session.query(StaffAuditLog.id).filter_by(
            action_type="PUBLIC_LIVE_EVENT", entity_id=fixture["audit_entity"]
        ).count()

    def public_cleanup(fixture):
        db.session.rollback()
        db.session.execute(db.delete(StaffAuditLog).where(
            StaffAuditLog.action_type == "PUBLIC_LIVE_EVENT",
            StaffAuditLog.entity_id == fixture["audit_entity"],
        ))
        db.session.commit()

    return (
        {"name": "cliente_plan", "cases": lambda: content_type_cases("cliente_plan", valid_payload={"tipo_plan": "premium"}) + client_plan_input_cases(), "prepare": prepare_client_plan, "login": client_login, "snapshot": client_snapshot, "cleanup": client_cleanup},
        {"name": "public_live_ping", "cases": lambda: content_type_cases("public_live_ping", valid_payload={"event_type": "pageview", "current_path": f"/{prefix.lower()}", "page_title": "QA Round9"}) + public_live_input_cases(f"/{prefix.lower()}"), "prepare": prepare_public_live, "login": public_login, "snapshot": public_snapshot, "cleanup": public_cleanup},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="ejecuta contra PostgreSQL local; sin esto solo muestra el comando")
    args = parser.parse_args(argv)
    if not args.execute:
        print("Dry-run: APP_ENV=development venv/bin/python scripts/local/round9_live_input_adapter.py --execute")
        return 0
    if (os.getenv("APP_ENV") or "").strip().lower() != "development":
        raise SystemExit("Bloqueado: exporta APP_ENV=development")
    from app import app

    with app.app_context():
        observations = run_live_matrix(app, _make_specs(app))
    print(json.dumps([observation.__dict__ for observation in observations], ensure_ascii=False, indent=2))
    return 1 if any(item.internal_error for item in observations) else 0


if __name__ == "__main__":
    sys.exit(main())
