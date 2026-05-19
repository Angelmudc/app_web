"""Motor deterministico de identidad para contactos del bot (Fase 3)."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import OperationalError, ProgrammingError

from config_app import db
from models import BotContactIdentity, Candidata, Cliente
from services.bot_constants import (
    IDENTITY_STATUS_AMBIGUOUS,
    IDENTITY_STATUS_CANDIDATE_IDENTIFIED,
    IDENTITY_STATUS_CLIENT_AND_CANDIDATE,
    IDENTITY_STATUS_CLIENT_IDENTIFIED,
    IDENTITY_STATUS_NEW_CONTACT,
)
from services.phone_identity_service import normalize_phone_to_e164, sanitize_phone
from utils.timezone import utc_now_naive


def _phone_match_keys(phone_e164: str) -> set[str]:
    keys = set()
    e164 = normalize_phone_to_e164(phone_e164)
    if not e164:
        return keys
    digits = sanitize_phone(e164)
    if digits:
        keys.add(digits)
    if len(digits) == 11 and digits.startswith("1"):
        keys.add(digits[1:])
    keys.add(e164.lstrip("+"))
    return {k for k in keys if k}


def detect_ambiguous_matches(*, client_ids: list[int], candidate_ids: list[int]) -> bool:
    return len(client_ids) > 1 or len(candidate_ids) > 1


def identify_contact_by_phone(phone_e164: str) -> dict:
    normalized = normalize_phone_to_e164(phone_e164)
    if not normalized:
        return {
            "phone_e164": None,
            "identity_status": IDENTITY_STATUS_NEW_CONTACT,
            "client_ids": [],
            "candidate_ids": [],
            "is_client": False,
            "is_candidate": False,
            "is_new_contact": True,
            "is_ambiguous": False,
            "confidence_score": Decimal("0"),
            "rule_code": "PHONE_INVALID",
            "reason_human": "Telefono invalido o no normalizable",
        }

    keys = _phone_match_keys(normalized)
    try:
        clients = Cliente.query.filter(Cliente.telefono_norm.in_(sorted(keys))).all() if keys else []
    except (OperationalError, ProgrammingError):
        clients = []

    try:
        candidates = Candidata.query.filter(Candidata.telefono_e164 == normalized).all()
    except (OperationalError, ProgrammingError):
        candidates = []
        try:
            rows = db.session.execute(text("SELECT fila, numero_telefono FROM candidatas")).fetchall()
            for fila, numero_telefono in rows:
                if normalize_phone_to_e164(numero_telefono, default_country="DO") == normalized:
                    candidates.append(type("CandidateLite", (), {"fila": int(fila)})())
        except Exception:
            candidates = []
    client_ids = [int(c.id) for c in clients]
    candidate_ids = [int(c.fila) for c in candidates]

    if detect_ambiguous_matches(client_ids=client_ids, candidate_ids=candidate_ids):
        return {
            "phone_e164": normalized,
            "identity_status": IDENTITY_STATUS_AMBIGUOUS,
            "client_ids": client_ids,
            "candidate_ids": candidate_ids,
            "is_client": bool(client_ids),
            "is_candidate": bool(candidate_ids),
            "is_new_contact": False,
            "is_ambiguous": True,
            "confidence_score": Decimal("0"),
            "rule_code": "AMBIGUOUS_MULTIPLE_MATCHES",
            "reason_human": "Multiples coincidencias por telefono",
        }

    if len(client_ids) == 1 and len(candidate_ids) == 1:
        return {
            "phone_e164": normalized,
            "identity_status": IDENTITY_STATUS_CLIENT_AND_CANDIDATE,
            "client_ids": client_ids,
            "candidate_ids": candidate_ids,
            "is_client": True,
            "is_candidate": True,
            "is_new_contact": False,
            "is_ambiguous": False,
            "confidence_score": Decimal("100"),
            "rule_code": "UNIQUE_CLIENT_AND_CANDIDATE",
            "reason_human": "Telefono coincide de forma unica con cliente y candidata",
        }

    if len(client_ids) == 1:
        return {
            "phone_e164": normalized,
            "identity_status": IDENTITY_STATUS_CLIENT_IDENTIFIED,
            "client_ids": client_ids,
            "candidate_ids": [],
            "is_client": True,
            "is_candidate": False,
            "is_new_contact": False,
            "is_ambiguous": False,
            "confidence_score": Decimal("100"),
            "rule_code": "UNIQUE_CLIENT_MATCH",
            "reason_human": "Telefono coincide de forma unica con cliente",
        }

    if len(candidate_ids) == 1:
        return {
            "phone_e164": normalized,
            "identity_status": IDENTITY_STATUS_CANDIDATE_IDENTIFIED,
            "client_ids": [],
            "candidate_ids": candidate_ids,
            "is_client": False,
            "is_candidate": True,
            "is_new_contact": False,
            "is_ambiguous": False,
            "confidence_score": Decimal("100"),
            "rule_code": "UNIQUE_CANDIDATE_MATCH",
            "reason_human": "Telefono coincide de forma unica con candidata",
        }

    return {
        "phone_e164": normalized,
        "identity_status": IDENTITY_STATUS_NEW_CONTACT,
        "client_ids": [],
        "candidate_ids": [],
        "is_client": False,
        "is_candidate": False,
        "is_new_contact": True,
        "is_ambiguous": False,
        "confidence_score": Decimal("50"),
        "rule_code": "NO_MATCH",
        "reason_human": "Telefono sin coincidencias en cliente/candidata",
    }


def resolve_identity(phone_e164: str) -> dict:
    return identify_contact_by_phone(phone_e164)


def get_or_create_identity(phone_e164: str) -> tuple[BotContactIdentity, dict]:
    resolution = resolve_identity(phone_e164)
    normalized = resolution.get("phone_e164")
    if not normalized:
        normalized = normalize_phone_to_e164(phone_e164) or ""
    if not normalized:
        raise ValueError("phone_e164 invalido para identidad")

    identity = BotContactIdentity.query.filter_by(phone_e164=normalized).first()
    if identity is None:
        identity = BotContactIdentity(phone_e164=normalized)
        db.session.add(identity)

    client_ids = resolution.get("client_ids") or []
    candidate_ids = resolution.get("candidate_ids") or []

    identity.identity_status = resolution.get("identity_status") or IDENTITY_STATUS_NEW_CONTACT
    identity.is_client = len(client_ids) == 1
    identity.client_id = int(client_ids[0]) if len(client_ids) == 1 else None
    identity.is_candidate = len(candidate_ids) == 1
    identity.candidate_id = int(candidate_ids[0]) if len(candidate_ids) == 1 else None
    identity.is_new_contact = bool(resolution.get("is_new_contact"))
    identity.confidence_score = resolution.get("confidence_score") or Decimal("0")
    identity.last_identity_check_at = utc_now_naive()
    identity.notes = (resolution.get("reason_human") or "")[:255] or None
    db.session.flush()
    return identity, resolution


def find_candidate_phone_duplicates() -> list[dict]:
    """Retorna duplicados de candidatas.telefono_e164 para diagnóstico admin."""
    try:
        cols = {c.get("name") for c in sa_inspect(db.engine).get_columns("candidatas")}
    except Exception:
        return []

    if "telefono_e164" not in cols:
        return []

    try:
        dup_rows = db.session.execute(
            text(
                "SELECT telefono_e164, COUNT(*) AS total "
                "FROM candidatas "
                "WHERE telefono_e164 IS NOT NULL AND TRIM(telefono_e164) <> '' "
                "GROUP BY telefono_e164 "
                "HAVING COUNT(*) > 1 "
                "ORDER BY total DESC, telefono_e164 ASC"
            )
        ).fetchall()
    except (OperationalError, ProgrammingError, Exception):
        return []

    if not dup_rows:
        return []

    select_cols = ["fila", "telefono_e164"]
    if "nombre_completo" in cols:
        select_cols.append("nombre_completo")
    if "numero_telefono" in cols:
        select_cols.append("numero_telefono")
    if "estado" in cols:
        select_cols.append("estado")
    select_cols_sql = ", ".join(select_cols)

    out: list[dict] = []
    for row in dup_rows:
        phone = str(row[0] or "").strip()
        count = int(row[1] or 0)
        candidates: list[dict] = []
        try:
            details = db.session.execute(
                text(
                    f"SELECT {select_cols_sql} FROM candidatas "
                    "WHERE telefono_e164 = :phone "
                    "ORDER BY fila ASC"
                ),
                {"phone": phone},
            ).fetchall()
        except (OperationalError, ProgrammingError, Exception):
            details = []

        for d in details:
            data = dict(d._mapping) if hasattr(d, "_mapping") else dict(zip(select_cols, d))
            candidates.append(
                {
                    "fila": data.get("fila"),
                    "nombre_completo": data.get("nombre_completo"),
                    "numero_telefono": data.get("numero_telefono"),
                    "estado": data.get("estado"),
                }
            )

        out.append(
            {
                "phone_e164": phone,
                "count": count,
                "candidates": candidates,
            }
        )
    return out
