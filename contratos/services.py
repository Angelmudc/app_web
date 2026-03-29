# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import os
import secrets
import re
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Optional
from xml.sax.saxutils import escape

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import HRFlowable, Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from config_app import db
from models import ContratoDigital, ContratoEvento, Solicitud
from utils.funciones_formatter import format_funciones
from utils.timezone import to_rd, utc_now_naive


CONTRACT_STATES = {"borrador", "enviado", "visto", "firmado", "expirado", "anulado"}
EDITABLE_STATES = {"borrador", "enviado", "visto", "expirado"}
SIGNABLE_STATES = {"enviado", "visto"}
REENVIABLE_STATES = {"enviado", "visto", "expirado"}


class ContractValidationError(ValueError):
    pass


@dataclass
class TokenResolution:
    ok: bool
    contrato: Optional[ContratoDigital]
    reason: str = ""


def contrato_ttl_seconds() -> int:
    raw = (os.getenv("CONTRATO_LINK_TTL_HOURS") or "72").strip()
    try:
        hours = int(raw)
    except Exception:
        hours = 72
    hours = max(1, min(168, hours))
    return int(timedelta(hours=hours).total_seconds())


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        current_app.config["SECRET_KEY"],
        salt="contratos-digitales-link-publico-v1",
    )


def hash_token_storage(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def generar_token_publico(*, contrato_id: int, token_version: int) -> str:
    payload = {
        "v": 1,
        "purpose": "contrato_digital",
        "cid": int(contrato_id),
        "tv": int(token_version),
        "nonce": secrets.token_urlsafe(24),
    }
    return _serializer().dumps(payload)


def emitir_nuevo_link(contrato: ContratoDigital, *, ttl_seconds: Optional[int] = None) -> str:
    ttl = int(ttl_seconds or contrato_ttl_seconds())
    if ttl <= 0:
        raise ContractValidationError("invalid_ttl")
    contrato.token_version = int(contrato.token_version or 0) + 1
    token = generar_token_publico(contrato_id=int(contrato.id), token_version=int(contrato.token_version))
    new_hash = hash_token_storage(token)
    now = utc_now_naive()
    if contrato.token_hash and not hmac.compare_digest(str(contrato.token_hash), new_hash):
        contrato.token_revocado_at = now
    contrato.token_hash = new_hash
    contrato.token_generado_at = now
    contrato.token_expira_at = now + timedelta(seconds=ttl)
    return token


def resolver_token_publico(token: str) -> TokenResolution:
    token = (token or "").strip()
    if not token:
        return TokenResolution(False, None, "missing_token")
    try:
        payload = _serializer().loads(token, max_age=contrato_ttl_seconds())
    except SignatureExpired:
        return TokenResolution(False, None, "expired_signature")
    except BadSignature:
        return TokenResolution(False, None, "invalid_signature")
    except Exception:
        return TokenResolution(False, None, "invalid_payload")

    if not isinstance(payload, dict):
        return TokenResolution(False, None, "invalid_payload")
    if str(payload.get("purpose") or "").strip().lower() != "contrato_digital":
        return TokenResolution(False, None, "invalid_purpose")

    try:
        contrato_id = int(payload.get("cid") or 0)
        token_version = int(payload.get("tv") or 0)
    except Exception:
        return TokenResolution(False, None, "invalid_payload")

    if contrato_id <= 0 or token_version <= 0:
        return TokenResolution(False, None, "invalid_payload")

    contrato = ContratoDigital.query.filter_by(id=contrato_id).first()
    if contrato is None:
        return TokenResolution(False, None, "contract_not_found")

    if int(contrato.token_version or 0) != token_version:
        return TokenResolution(False, None, "token_revoked")

    expected_hash = str(contrato.token_hash or "")
    incoming_hash = hash_token_storage(token)
    if (not expected_hash) or (not hmac.compare_digest(expected_hash, incoming_hash)):
        return TokenResolution(False, None, "token_revoked")

    if contrato.anulado_at is not None or str(contrato.estado or "") == "anulado":
        return TokenResolution(False, contrato, "contract_annulled")

    now = utc_now_naive()
    if contrato.firmado_at is None and contrato.token_expira_at and now > contrato.token_expira_at:
        prev_state = str(contrato.estado or "")
        if prev_state in {"enviado", "visto"}:
            contrato.estado = "expirado"
            contrato.updated_at = now
            db.session.add(
                ContratoEvento(
                    contrato_id=contrato.id,
                    evento_tipo="LINK_EXPIRADO",
                    estado_anterior=prev_state,
                    estado_nuevo="expirado",
                    actor_tipo="sistema",
                    metadata_json={"reason": "ttl_exceeded"},
                    success=True,
                )
            )
            db.session.commit()
            return TokenResolution(False, contrato, "expired")
        return TokenResolution(False, contrato, "expired")

    if contrato.firmado_at is None and str(contrato.estado or "") not in {"enviado", "visto", "expirado"}:
        return TokenResolution(False, contrato, "invalid_state")

    return TokenResolution(True, contrato, "")


def evento_contrato(
    contrato: ContratoDigital,
    *,
    evento_tipo: str,
    actor_tipo: str,
    estado_anterior: Optional[str] = None,
    estado_nuevo: Optional[str] = None,
    actor_staff_id: Optional[int] = None,
    ip: str = "",
    user_agent: str = "",
    metadata: Optional[dict[str, Any]] = None,
    success: bool = True,
    error_code: Optional[str] = None,
) -> ContratoEvento:
    ev = ContratoEvento(
        contrato_id=int(contrato.id),
        evento_tipo=str(evento_tipo or "").strip()[:48],
        actor_tipo=(str(actor_tipo or "sistema").strip()[:16] or "sistema"),
        actor_staff_id=actor_staff_id,
        estado_anterior=(str(estado_anterior or "").strip()[:16] or None),
        estado_nuevo=(str(estado_nuevo or "").strip()[:16] or None),
        ip=(str(ip or "").strip()[:64] or None),
        user_agent=(str(user_agent or "").strip()[:512] or None),
        metadata_json=metadata or {},
        success=bool(success),
        error_code=(str(error_code or "").strip()[:80] or None),
        created_at=utc_now_naive(),
    )
    db.session.add(ev)
    return ev


def registrar_vista_publica(contrato: ContratoDigital, *, ip: str = "", user_agent: str = "") -> None:
    now = utc_now_naive()
    old_state = str(contrato.estado or "")
    if contrato.primer_visto_at is None:
        contrato.primer_visto_at = now
        contrato.primera_ip = (ip or "")[:64] or None
        contrato.primer_user_agent = (user_agent or "")[:512] or None
    contrato.ultimo_visto_at = now
    if old_state == "enviado":
        contrato.estado = "visto"

    evento_contrato(
        contrato,
        evento_tipo="LINK_ABIERTO" if old_state in {"enviado", "visto"} else "ACCESO_SOLO_LECTURA",
        actor_tipo="cliente_publico",
        estado_anterior=old_state,
        estado_nuevo=contrato.estado,
        ip=ip,
        user_agent=user_agent,
        metadata={"readonly": bool(contrato.firmado_at is not None)},
    )
    db.session.commit()


def snapshot_desde_solicitud(solicitud: Solicitud) -> dict[str, Any]:
    cliente = getattr(solicitud, "cliente", None)
    return {
        "solicitud_id": int(solicitud.id),
        "codigo_solicitud": (solicitud.codigo_solicitud or "").strip(),
        "cliente_id": int(solicitud.cliente_id),
        "cliente_nombre": ((getattr(cliente, "nombre_completo", None) or "").strip() or None),
        "cliente_telefono": ((getattr(cliente, "telefono", None) or "").strip() or None),
        "cliente_email": ((getattr(cliente, "email", None) or "").strip() or None),
        "cliente_ciudad": ((getattr(cliente, "ciudad", None) or "").strip() or None),
        "cliente_sector": ((getattr(cliente, "sector", None) or "").strip() or None),
        "fecha_solicitud": solicitud.fecha_solicitud.isoformat() if solicitud.fecha_solicitud else None,
        "tipo_plan": (solicitud.tipo_plan or "").strip() or None,
        "tipo_servicio": (getattr(solicitud, "tipo_servicio", None) or "").strip() or None,
        "ciudad_sector": (getattr(solicitud, "ciudad_sector", None) or "").strip() or None,
        "rutas_cercanas": (getattr(solicitud, "rutas_cercanas", None) or "").strip() or None,
        "modalidad_trabajo": (solicitud.modalidad_trabajo or "").strip() or None,
        "horario": (solicitud.horario or "").strip() or None,
        "funciones": list(getattr(solicitud, "funciones", []) or []),
        "funciones_otro": (getattr(solicitud, "funciones_otro", None) or "").strip() or None,
        "experiencia": (getattr(solicitud, "experiencia", None) or "").strip() or None,
        "detalles_servicio": getattr(solicitud, "detalles_servicio", None),
        "tipo_lugar": (getattr(solicitud, "tipo_lugar", None) or "").strip() or None,
        "habitaciones": getattr(solicitud, "habitaciones", None),
        "banos": getattr(solicitud, "banos", None),
        "adultos": getattr(solicitud, "adultos", None),
        "ninos": getattr(solicitud, "ninos", None),
        "edades_ninos": (getattr(solicitud, "edades_ninos", None) or "").strip() or None,
        "mascota": (getattr(solicitud, "mascota", None) or "").strip() or None,
        "sueldo": (getattr(solicitud, "sueldo", None) or "").strip() or None,
        "pasaje_aporte": getattr(solicitud, "pasaje_aporte", None),
        "nota_cliente": (solicitud.nota_cliente or "").strip() or None,
        "monto_pagado": (solicitud.monto_pagado or "").strip() or None,
        "captured_at": utc_now_naive().isoformat(),
    }


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _fallback(*values: Any) -> str:
    for value in values:
        txt = _text(value)
        if txt:
            return txt
    return ""


def _value_or_none(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                return value.strip()
            continue
        return value
    return None


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        chunks = [part.strip() for part in value.split(",")]
        return [part for part in chunks if part]
    try:
        iterable = list(value)
    except Exception:
        iterable = [value]
    out: list[str] = []
    for item in iterable:
        txt = _text(item)
        if txt:
            out.append(txt)
    return out


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", _text(text)).strip()


def _safe_int_from_text(raw: str) -> int | None:
    txt = _normalize_spaces(raw)
    if not txt:
        return None
    digits = re.sub(r"[^\d]", "", txt)
    if not digits:
        return None
    try:
        return int(digits)
    except Exception:
        return None


def _format_rd_currency(value: Any) -> str:
    txt = _normalize_spaces(_text(value))
    if not txt:
        return ""

    raw = txt.upper().replace("RD$", "").replace("DOP", "")
    raw = raw.replace(" ", "")
    if "," in raw and "." in raw:
        raw = raw.replace(",", "")
    elif "," in raw and "." not in raw:
        # Interpretar "20000,50" como decimal
        raw = raw.replace(",", ".")

    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        digits = _safe_int_from_text(txt)
        if digits is None:
            return txt
        amount = Decimal(digits)

    quantized = amount.quantize(Decimal("0.01"))
    if quantized == quantized.to_integral():
        return f"RD${int(quantized):,}"
    return f"RD${quantized:,.2f}"


def _format_yes_no(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Sí" if value else "No"
    txt = _normalize_spaces(_text(value)).lower()
    if not txt:
        return ""
    if txt in {"1", "true", "si", "sí", "yes", "con", "incluye"}:
        return "Sí"
    if txt in {"0", "false", "no", "sin"}:
        return "No"
    return ""


def _humanize_slug(value: Any, *, map_values: dict[str, str] | None = None) -> str:
    txt = _normalize_spaces(_text(value))
    if not txt:
        return ""
    if map_values:
        direct = map_values.get(txt)
        if direct:
            return direct
        lower = map_values.get(txt.lower())
        if lower:
            return lower
        upper = map_values.get(txt.upper())
        if upper:
            return upper

    if "_" not in txt and "-" not in txt and not txt.isupper():
        return txt[:1].upper() + txt[1:]

    normalized = txt.replace("_", " ").replace("-", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return ""

    if normalized.isupper():
        normalized = normalized.lower()
    words = [w.capitalize() for w in normalized.split(" ")]
    return " ".join(words)


_TIPO_SERVICIO_LABELS = {
    "DOMESTICA_LIMPIEZA": "Doméstica de limpieza",
    "NINERA": "Niñera",
    "ENFERMERA": "Enfermera / Cuidadora",
    "CHOFER": "Chofer",
    "domestica_limpieza": "Doméstica de limpieza",
    "ninera": "Niñera",
    "enfermera": "Enfermera / Cuidadora",
    "chofer": "Chofer",
}

_TIPO_LUGAR_LABELS = {
    "apto": "Apartamento",
    "apartamento": "Apartamento",
    "casa": "Casa",
    "residencia": "Residencia",
    "villa": "Villa",
    "penthouse": "Penthouse",
}

_MASCOTA_LABELS = {
    "si": "Sí",
    "sí": "Sí",
    "no": "No",
    "perro": "Perro",
    "gato": "Gato",
    "perro_gato": "Perro y gato",
    "perros": "Perros",
    "gatos": "Gatos",
}

_MODALIDAD_LABELS = {
    "con_dormida": "Con dormida",
    "sin_dormida": "Sin dormida",
    "salida_diaria": "Salida diaria",
}


def _humanize_modalidad(value: Any) -> str:
    return _humanize_slug(value, map_values=_MODALIDAD_LABELS)


def _humanize_tipo_servicio(value: Any, solicitud: Any) -> str:
    if solicitud is not None:
        label = _text(getattr(solicitud, "tipo_servicio_label", None))
        if label:
            return label
    return _humanize_slug(value, map_values=_TIPO_SERVICIO_LABELS)


def _humanize_tipo_lugar(value: Any) -> str:
    return _humanize_slug(value, map_values=_TIPO_LUGAR_LABELS)


def _humanize_mascota(value: Any) -> str:
    yn = _format_yes_no(value)
    if yn:
        return yn
    return _humanize_slug(value, map_values=_MASCOTA_LABELS)


def _display_or_default(value: Any, default: str = "No especificado") -> str:
    txt = _normalize_spaces(_text(value))
    return txt or default


def format_signed_at_rd_human(dt) -> str:
    if dt is None:
        return "-"
    try:
        rd = to_rd(dt)
    except Exception:
        # Fallback defensivo para objetos dobles/mocks usados en tests.
        iso = ""
        try:
            iso = str(dt.isoformat() or "").strip()
        except Exception:
            iso = ""
        return iso or "-"
    if rd is None:
        return "-"
    months = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
    ]
    hour = rd.strftime("%I").lstrip("0") or "12"
    am_pm = "AM" if rd.hour < 12 else "PM"
    return f"{rd.day} de {months[rd.month - 1]} de {rd.year}, {hour}:{rd.strftime('%M')} {am_pm}"


def _pasaje_text(snapshot: dict[str, Any], solicitud: Any) -> str:
    details = snapshot.get("detalles_servicio")
    if not isinstance(details, dict):
        details = getattr(solicitud, "detalles_servicio", None)
    if isinstance(details, dict):
        pasaje = details.get("pasaje")
        if isinstance(pasaje, dict):
            mode = _text(pasaje.get("mode")).lower()
            if mode == "otro":
                txt = _text(pasaje.get("text"))
                if txt:
                    return txt
            if mode in {"si", "sí"}:
                return "Sí"
            if mode == "no":
                return "No"
            if mode:
                return _humanize_slug(mode)
    raw_flag = _value_or_none(snapshot.get("pasaje_aporte"), getattr(solicitud, "pasaje_aporte", None))
    yn = _format_yes_no(raw_flag)
    if yn:
        return yn
    return _humanize_slug(raw_flag)


def _clausulas_estandar() -> list[dict[str, str]]:
    return [
        {
            "titulo": "Objeto del contrato",
            "texto": (
                "La agencia prestará al cliente un servicio de intermediación y colocación laboral para facilitar la "
                "evaluación y selección de personal doméstico conforme a la solicitud registrada."
            ),
        },
        {
            "titulo": "Detalles de la solicitud",
            "texto": (
                "La búsqueda se ejecutará con base en los datos de modalidad, horario, funciones, condiciones del "
                "hogar y demás especificaciones consignadas en este documento."
            ),
        },
        {
            "titulo": "Honorarios de la agencia",
            "texto": (
                "El cliente se compromete a pagar a la trabajadora doméstica el salario acordado. De dicho monto, "
                "el cliente descontará un 25% y lo entregará a la agencia como pago por el servicio de colocación "
                "laboral. El restante será entregado a la trabajadora. Este descuento se realiza una sola vez y no "
                "es recurrente."
            ),
        },
        {
            "titulo": "Condiciones posteriores a la presentación de candidata",
            "texto": (
                "Una vez la candidata haya sido enviada o presentada al cliente, no se podrán modificar las "
                "condiciones de la solicitud (funciones, horario, salario u otros detalles) sin previa consulta y "
                "aprobación de la agencia."
            ),
        },
        {
            "titulo": "Responsabilidades del cliente",
            "texto": (
                "El cliente se compromete a suministrar información veraz sobre el puesto, condiciones de trabajo y "
                "compensación, así como a comunicar cualquier cambio relevante durante el proceso."
            ),
        },
        {
            "titulo": "Aclaraciones importantes",
            "texto": (
                "La agencia actúa exclusivamente como intermediaria de colocación laboral y no como empleadora "
                "directa del personal contratado por el cliente."
            ),
        },
        {
            "titulo": "Aceptación",
            "texto": (
                "Al firmar este documento, el cliente confirma que leyó íntegramente el contrato y acepta su "
                "contenido, alcance y condiciones."
            ),
        },
    ]


def build_contract_public_context(contrato: ContratoDigital) -> dict[str, Any]:
    snapshot = dict(getattr(contrato, "contenido_snapshot_json", None) or {})
    solicitud = getattr(contrato, "solicitud", None)
    cliente = getattr(contrato, "cliente", None)
    if cliente is None and solicitud is not None:
        cliente = getattr(solicitud, "cliente", None)

    funciones = _as_list(_value_or_none(snapshot.get("funciones"), getattr(solicitud, "funciones", None)))
    funciones_otro = _fallback(snapshot.get("funciones_otro"), getattr(solicitud, "funciones_otro", None))
    funciones_fmt = format_funciones(funciones, funciones_otro)
    ciudad = _fallback(snapshot.get("cliente_ciudad"), getattr(cliente, "ciudad", None))
    sector = _fallback(snapshot.get("cliente_sector"), getattr(cliente, "sector", None))
    ciudad_sector_cliente = " / ".join([part for part in [ciudad, sector] if part])
    if not ciudad_sector_cliente:
        ciudad_sector_cliente = _fallback(snapshot.get("ciudad_sector"), getattr(solicitud, "ciudad_sector", None))

    tipo_servicio = _humanize_tipo_servicio(
        _value_or_none(snapshot.get("tipo_servicio"), getattr(solicitud, "tipo_servicio", None)),
        solicitud,
    )
    modalidad = _humanize_modalidad(_value_or_none(snapshot.get("modalidad_trabajo"), getattr(solicitud, "modalidad_trabajo", None)))
    tipo_lugar = _humanize_tipo_lugar(_value_or_none(snapshot.get("tipo_lugar"), getattr(solicitud, "tipo_lugar", None)))
    pasaje = _pasaje_text(snapshot, solicitud)
    sueldo = _format_rd_currency(_value_or_none(snapshot.get("sueldo"), getattr(solicitud, "sueldo", None)))
    mascota = _humanize_mascota(_value_or_none(snapshot.get("mascota"), getattr(solicitud, "mascota", None)))
    rutas_cercanas = _humanize_slug(_value_or_none(snapshot.get("rutas_cercanas"), getattr(solicitud, "rutas_cercanas", None)))
    edades_ninos = _normalize_spaces(_text(_value_or_none(snapshot.get("edades_ninos"), getattr(solicitud, "edades_ninos", None))))

    codigo_solicitud = _fallback(
        snapshot.get("codigo_solicitud"),
        getattr(solicitud, "codigo_solicitud", None),
        f"SOL-{getattr(contrato, 'solicitud_id', '')}",
    )
    horario = _fallback(snapshot.get("horario"), getattr(solicitud, "horario", None))
    habitaciones = _text(_value_or_none(snapshot.get("habitaciones"), getattr(solicitud, "habitaciones", None)))
    banos = _text(_value_or_none(snapshot.get("banos"), getattr(solicitud, "banos", None)))
    adultos = _text(_value_or_none(snapshot.get("adultos"), getattr(solicitud, "adultos", None)))
    ninos = _text(_value_or_none(snapshot.get("ninos"), getattr(solicitud, "ninos", None)))
    notas = _fallback(snapshot.get("nota_cliente"), getattr(solicitud, "nota_cliente", None))
    experiencia = _fallback(snapshot.get("experiencia"), getattr(solicitud, "experiencia", None))

    funciones_principales = format_funciones(funciones, "")
    funciones_adicionales = _normalize_spaces(funciones_otro)

    sections = [
        {
            "title": "Datos del cliente",
            "fields": [
                {"label": "Nombre completo", "value": _display_or_default(_fallback(snapshot.get("cliente_nombre"), getattr(cliente, "nombre_completo", None), getattr(contrato, "firma_nombre", None)))},
                {"label": "Teléfono", "value": _display_or_default(_fallback(snapshot.get("cliente_telefono"), getattr(cliente, "telefono", None)))},
                {"label": "Correo", "value": _display_or_default(_fallback(snapshot.get("cliente_email"), getattr(cliente, "email", None)))},
                {"label": "Ciudad / Sector", "value": _display_or_default(ciudad_sector_cliente)},
            ],
        },
        {
            "title": "Datos generales de la solicitud",
            "fields": [
                {"label": "N. de solicitud", "value": _display_or_default(codigo_solicitud)},
                {"label": "Tipo de servicio", "value": _display_or_default(tipo_servicio)},
                {"label": "Modalidad", "value": _display_or_default(modalidad)},
                {"label": "Horario", "value": _display_or_default(horario)},
                {"label": "Sueldo acordado", "value": _display_or_default(sueldo)},
                {"label": "Pasaje", "value": _display_or_default(pasaje)},
                {"label": "Rutas cercanas", "value": _display_or_default(rutas_cercanas)},
            ],
        },
        {
            "title": "Funciones del servicio",
            "fields": [
                {"label": "Funciones", "value": _display_or_default(funciones_principales)},
                {"label": "Funciones adicionales", "value": funciones_adicionales},
                {"label": "Observaciones operativas", "value": experiencia},
            ],
        },
        {
            "title": "Detalles del hogar",
            "fields": [
                {"label": "Tipo de lugar", "value": _display_or_default(tipo_lugar)},
                {"label": "Habitaciones", "value": _display_or_default(habitaciones)},
                {"label": "Baños", "value": _display_or_default(banos)},
                {"label": "Adultos", "value": _display_or_default(adultos)},
            ],
        },
        {
            "title": "Niños y condiciones especiales",
            "fields": [
                {"label": "Niños", "value": _display_or_default(ninos)},
                {"label": "Edades de niños", "value": _display_or_default(edades_ninos)},
                {"label": "Mascota", "value": _display_or_default(mascota)},
                {"label": "Notas de la solicitud", "value": notas},
            ],
        },
    ]
    for section in sections:
        section["fields"] = [
            field for field in section.get("fields", [])
            if _normalize_spaces(_text(field.get("value")))
        ]

    introduction = (
        "Entre la agencia Doméstica del Cibao A&D y el cliente identificado en este documento, se formaliza el "
        "presente contrato de servicio de colocación laboral para gestionar la solicitud indicada a continuación."
    )
    return {
        "document_title": "Contrato de Servicio de Colocación Laboral",
        "intro_text": introduction,
        "sections": [section for section in sections if section.get("fields")],
        "clausulas": _clausulas_estandar(),
        "signer_name": _fallback(getattr(contrato, "firma_nombre", None), snapshot.get("cliente_nombre"), getattr(cliente, "nombre_completo", None)),
    }


def _firma_max_bytes() -> int:
    raw = (os.getenv("CONTRATO_FIRMA_MAX_KB") or "400").strip()
    try:
        kb = int(raw)
    except Exception:
        kb = 400
    kb = max(16, min(1024, kb))
    return kb * 1024


def parse_signature_data_url(data_url: str) -> bytes:
    raw = (data_url or "").strip()
    prefix = "data:image/png;base64,"
    if not raw.startswith(prefix):
        raise ContractValidationError("signature_invalid_format")

    b64 = raw[len(prefix):].strip()
    if not b64:
        raise ContractValidationError("signature_empty")

    try:
        blob = base64.b64decode(b64, validate=True)
    except Exception as exc:
        raise ContractValidationError("signature_invalid_base64") from exc

    if len(blob) < 64:
        raise ContractValidationError("signature_too_small")
    if len(blob) > _firma_max_bytes():
        raise ContractValidationError("signature_too_large")

    png_magic = b"\x89PNG\r\n\x1a\n"
    if not blob.startswith(png_magic):
        raise ContractValidationError("signature_invalid_png")

    return blob


def _render_contract_pdf(contrato: ContratoDigital, *, signer_name: str, signature_png: bytes, signed_at) -> bytes:
    def _section_title_by_index(idx: int, raw_title: str) -> str:
        names = [
            "A. DATOS DEL CLIENTE",
            "B. DATOS GENERALES DEL SERVICIO",
            "C. FUNCIONES DEL SERVICIO",
            "D. DETALLES DEL HOGAR",
            "E. CONDICIONES ESPECIALES",
        ]
        if 0 <= idx < len(names):
            return names[idx]
        return _text(raw_title).upper()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=46,
        rightMargin=46,
        topMargin=44,
        bottomMargin=44,
        title="Contrato de Servicio de Colocacion Laboral",
        author="Domestica del Cibao A&D",
    )
    story = []

    styles = {
        "company": ParagraphStyle(
            "company",
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=14,
            textColor=colors.HexColor("#0f172a"),
            alignment=TA_LEFT,
        ),
        "doc_title": ParagraphStyle(
            "doc_title",
            fontName="Helvetica-Bold",
            fontSize=16,
            leading=19,
            textColor=colors.HexColor("#111827"),
            alignment=TA_LEFT,
        ),
        "meta": ParagraphStyle(
            "meta",
            fontName="Helvetica",
            fontSize=9.5,
            leading=12,
            textColor=colors.HexColor("#334155"),
            alignment=TA_LEFT,
        ),
        "block_title": ParagraphStyle(
            "block_title",
            fontName="Helvetica-Bold",
            fontSize=11.5,
            leading=14,
            textColor=colors.HexColor("#0f172a"),
            alignment=TA_LEFT,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "body",
            fontName="Times-Roman",
            fontSize=10.5,
            leading=15,
            textColor=colors.HexColor("#111827"),
            alignment=TA_LEFT,
        ),
        "clause_title": ParagraphStyle(
            "clause_title",
            fontName="Helvetica-Bold",
            fontSize=10.8,
            leading=14,
            textColor=colors.HexColor("#111827"),
            alignment=TA_LEFT,
            spaceAfter=3,
        ),
        "note": ParagraphStyle(
            "note",
            fontName="Helvetica-Oblique",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#475569"),
            alignment=TA_CENTER,
        ),
    }

    # Header legal-corporativo
    logo_path = os.path.join(current_app.root_path, "static", "logo_nuevo.png")
    logo_flow = ""
    if os.path.exists(logo_path):
        try:
            logo_info = ImageReader(logo_path)
            w_px, h_px = logo_info.getSize()
            target_w = 3.2 * cm
            ratio = float(h_px or 1) / float(w_px or 1)
            target_h = target_w * ratio
            logo_flow = Image(logo_path, width=target_w, height=target_h)
        except Exception:
            logo_flow = ""

    header_table = Table(
        [[
            logo_flow,
            Paragraph("Agencia Domestica del Cibao A&amp;D", styles["company"]),
        ]],
        colWidths=[3.7 * cm, doc.width - (3.7 * cm)],
    )
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(header_table)
    story.append(Paragraph("CONTRATO DE SERVICIO DE COLOCACION LABORAL", styles["doc_title"]))
    story.append(
        Paragraph(
            f"Codigo de contrato: C-{int(contrato.id)}-V{int(contrato.version)} | Solicitud No. {int(contrato.solicitud_id)}",
            styles["meta"],
        )
    )
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#cbd5e1")))
    story.append(Spacer(1, 10))

    contract_data = build_contract_public_context(contrato)
    cliente_nombre = _text(signer_name) or _text(contract_data.get("signer_name")) or "Cliente"

    # Identificacion de partes
    story.append(Paragraph("ENTRE:", styles["block_title"]))
    story.append(
        Paragraph(
            (
                "De una parte, <b>Agencia Domestica del Cibao A&amp;D</b>; "
                f"y de la otra, <b>{escape(cliente_nombre)}</b>, en lo adelante denominado EL CLIENTE."
            ),
            styles["body"],
        )
    )
    story.append(Spacer(1, 8))
    story.append(Paragraph(escape(_text(contract_data.get("intro_text"))), styles["body"]))
    story.append(Spacer(1, 12))

    # Secciones en tablas profesionales (Campo / Detalle)
    for idx, section in enumerate(contract_data.get("sections", [])):
        title = _section_title_by_index(idx, _text(section.get("title")))
        story.append(Paragraph(title, styles["block_title"]))
        rows = [[
            Paragraph("<b>Campo</b>", styles["meta"]),
            Paragraph("<b>Detalle</b>", styles["meta"]),
        ]]
        for field in section.get("fields", []):
            label = escape(_text(field.get("label")))
            value = escape(_text(field.get("value")))
            if not value:
                continue
            rows.append([
                Paragraph(label, styles["body"]),
                Paragraph(value, styles["body"]),
            ])
        if len(rows) == 1:
            rows.append([Paragraph("-", styles["body"]), Paragraph("-", styles["body"])])
        table = Table(rows, colWidths=[doc.width * 0.36, doc.width * 0.64], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
            ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#cbd5e1")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ]))
        story.append(table)
        story.append(Spacer(1, 10))

    # Clausulas
    story.append(Paragraph("CLAUSULAS Y ACUERDOS", styles["block_title"]))
    for idx, clause in enumerate(contract_data.get("clausulas", []), start=1):
        title = escape(_text(clause.get("titulo")))
        body = escape(_text(clause.get("texto")))
        story.append(Paragraph(f"<b>{idx}. {title}</b>", styles["clause_title"]))
        story.append(Paragraph(body, styles["body"]))
        story.append(Spacer(1, 6))

    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#cbd5e1")))
    story.append(Spacer(1, 10))

    # Aceptacion y firma
    story.append(Paragraph("ACEPTACION Y FIRMA", styles["block_title"]))
    story.append(
        Paragraph(
            "Declaro que he leido y aceptado el presente contrato en todos sus terminos y condiciones.",
            styles["body"],
        )
    )
    story.append(Spacer(1, 8))

    signed_label = format_signed_at_rd_human(signed_at)
    signer_data = Table(
        [
            [Paragraph("<b>Nombre completo</b>", styles["meta"]), Paragraph(escape(cliente_nombre), styles["body"])],
            [Paragraph("<b>Fecha de firma</b>", styles["meta"]), Paragraph(escape(signed_label), styles["body"])],
        ],
        colWidths=[doc.width * 0.28, doc.width * 0.72],
    )
    signer_data.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.55, colors.HexColor("#cbd5e1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#e2e8f0")),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(signer_data)
    story.append(Spacer(1, 8))

    sig_image = Image(io.BytesIO(signature_png), width=7.2 * cm, height=2.6 * cm)
    signature_box = Table(
        [[
            Paragraph("<b>Firma del cliente</b>", styles["meta"]),
            sig_image,
        ]],
        colWidths=[doc.width * 0.28, doc.width * 0.72],
    )
    signature_box.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#94a3b8")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(signature_box)
    story.append(Spacer(1, 8))
    story.append(Paragraph("Documento firmado digitalmente", styles["note"]))

    doc.build(story)
    return buffer.getvalue()


def firmar_contrato_atomico(
    contrato: ContratoDigital,
    *,
    signature_data_url: str,
    signer_name: str,
    ip: str = "",
    user_agent: str = "",
) -> None:
    if contrato is None:
        raise ContractValidationError("contract_not_found")

    estado_actual = str(contrato.estado or "")
    if estado_actual == "firmado" or contrato.firmado_at is not None:
        raise ContractValidationError("already_signed")
    if estado_actual not in SIGNABLE_STATES:
        raise ContractValidationError("invalid_state_for_sign")
    if contrato.anulado_at is not None:
        raise ContractValidationError("contract_annulled")

    signer = (signer_name or "").strip()
    if len(signer) < 3:
        raise ContractValidationError("signer_name_required")
    signer = signer[:180]

    signature_png = parse_signature_data_url(signature_data_url)
    firma_sha = hashlib.sha256(signature_png).hexdigest()
    signed_at = utc_now_naive()

    try:
        pdf_bytes = _render_contract_pdf(
            contrato,
            signer_name=signer,
            signature_png=signature_png,
            signed_at=signed_at,
        )
    except Exception as exc:
        raise ContractValidationError("pdf_generation_failed") from exc

    if not pdf_bytes or len(pdf_bytes) < 256:
        raise ContractValidationError("pdf_invalid")

    pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()

    old_state = estado_actual
    contrato.firma_png = signature_png
    contrato.firma_png_sha256 = firma_sha
    contrato.firma_nombre = signer
    contrato.firmado_at = signed_at
    contrato.firmado_ip = (ip or "")[:64] or None
    contrato.firmado_user_agent = (user_agent or "")[:512] or None
    contrato.pdf_final_bytea = pdf_bytes
    contrato.pdf_final_sha256 = pdf_sha
    contrato.pdf_final_size_bytes = len(pdf_bytes)
    contrato.pdf_generado_at = signed_at
    contrato.estado = "firmado"
    contrato.updated_at = signed_at

    evento_contrato(
        contrato,
        evento_tipo="CONTRATO_FIRMADO",
        actor_tipo="cliente_publico",
        estado_anterior=old_state,
        estado_nuevo="firmado",
        ip=ip,
        user_agent=user_agent,
        metadata={
            "firma_sha256": firma_sha,
            "pdf_sha256": pdf_sha,
            "pdf_size": len(pdf_bytes),
        },
        success=True,
    )

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
