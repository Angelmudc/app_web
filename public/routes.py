# app_web/public/routes.py

import os
import json
import urllib.parse
import time
import imghdr
import re
from threading import Lock
from typing import Optional

from flask import (
    render_template,
    abort,
    request,
    redirect,
    flash,
    jsonify,
    make_response,
    session,
    url_for,
    current_app,
    g,
)
from flask_login import current_user
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from . import public_bp
from config_app import db

from utils.audit_logger import log_action
from utils.catalogo_privado_tokens import (
    catalogo_privado_token_hash,
    resolve_catalogo_privado_publico_por_token,
)
from utils.distributed_backplane import bp_get, bp_set
from utils.timezone import iso_utc_z, utc_now_naive

# Límite de paginación pública
PUBLIC_MAX_PAGE = 50
PUBLIC_LIVE_PING_MAX_BODY_BYTES = int(os.getenv("PUBLIC_LIVE_PING_MAX_BODY_BYTES", "4096"))
PUBLIC_LIVE_PING_PATH_MAX_LEN = int(os.getenv("PUBLIC_LIVE_PING_PATH_MAX_LEN", "180"))
PUBLIC_LIVE_PING_RATE_LIMIT_PER_MIN = int(os.getenv("PUBLIC_LIVE_PING_RATE_LIMIT_PER_MIN", "90"))
PUBLIC_LIVE_ALLOWED_EVENT_TYPES = {
    "heartbeat",
    "pageview",
    "cta_click",
    "form_start",
    "form_submit",
}
_PUBLIC_LIVE_RL_LOCK = Lock()
_PUBLIC_LIVE_RL_LOCAL: dict[str, tuple[int, float]] = {}


def _safe_page(value, default=1):
    """
    Convierte a int, fuerza mínimo 1 y máximo PUBLIC_MAX_PAGE.
    """
    try:
        page = int(value)
    except Exception:
        page = default
    if page < 1:
        page = 1
    if page > PUBLIC_MAX_PAGE:
        page = PUBLIC_MAX_PAGE
    return page


# 🔌 SWITCH GENERAL: WEB PÚBLICA HABILITADA / DESHABILITADA
# ❌ DESACTIVADA TEMPORALMENTE (no accesible al público)
PUBLIC_SITE_ENABLED = True
# Para reactivar en el futuro, cambia a True


def _json_no_cache(payload: dict, status: int = 200):
    """JSON response con headers anti-cache para refresco silencioso."""
    resp = make_response(jsonify(payload), status)
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


def _public_client_ip() -> str:
    raw = (
        (request.headers.get("CF-Connecting-IP") or "").strip()
        or (request.headers.get("X-Real-IP") or "").strip()
        or (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        or (request.remote_addr or "").strip()
        or "0.0.0.0"
    )
    return raw[:64]


def _public_live_rate_limited(ip: str) -> bool:
    minute_key = utc_now_naive().strftime("%Y%m%d%H%M")
    key = f"public_live_rl:{minute_key}:{ip}"
    limit = max(10, int(PUBLIC_LIVE_PING_RATE_LIMIT_PER_MIN or 90))
    timeout = 75
    count = None
    try:
        count = int(bp_get(key, default=0, context="public_live_rl_get") or 0) + 1
        bp_set(key, count, timeout=timeout, context="public_live_rl_set")
    except Exception:
        count = None

    if count is None:
        now = time.time()
        with _PUBLIC_LIVE_RL_LOCK:
            val = _PUBLIC_LIVE_RL_LOCAL.get(key)
            if val and val[1] > now:
                c = int(val[0]) + 1
            else:
                c = 1
            _PUBLIC_LIVE_RL_LOCAL[key] = (c, now + float(timeout))
            count = c
    return int(count) > limit


def _public_external_url(endpoint: str, **values) -> str:
    base_raw = (
        (current_app.config.get("PUBLIC_BASE_URL") or "")
        or (os.getenv("PUBLIC_BASE_URL") or "")
        or "https://www.domesticadelcibao.com"
    ).strip()
    if base_raw:
        parsed = urllib.parse.urlparse(base_raw)
        if parsed.scheme and parsed.netloc:
            base = f"{parsed.scheme}://{parsed.netloc}{parsed.path or ''}"
            rel = url_for(endpoint, _external=False, **values).lstrip("/")
            return urllib.parse.urljoin(base.rstrip("/") + "/", rel)
    return url_for(endpoint, _external=True, **values)


def _catalogo_token_hash(token: str) -> str:
    return catalogo_privado_token_hash(token)


def _catalogo_public_alias(candidata, ficha_web=None) -> str:
    raw = (getattr(ficha_web, "nombre_publico", None) or "").strip()
    if raw:
        return raw
    raw = (getattr(candidata, "nombre_completo", None) or "").strip()
    if not raw:
        codigo = (getattr(candidata, "codigo", None) or "").strip()
        return f"Perfil {codigo}" if codigo else f"Perfil #{int(getattr(candidata, 'fila', 0) or 0)}"
    parts = [p for p in raw.split() if p]
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[1][0]}."


def _catalogo_safe_text(value, fallback="No especificado") -> str:
    if value is None:
        return fallback
    text_value = str(value).strip()
    return text_value if text_value else fallback


def _catalogo_public_payload(candidata, ficha_web=None):
    ciudad = _catalogo_safe_text(getattr(ficha_web, "ciudad_publica", None), fallback="").strip() if ficha_web else ""
    sector_general = _catalogo_safe_text(getattr(ficha_web, "sector_publico", None), fallback="").strip() if ficha_web else ""
    if not ciudad:
        ciudad = "Zona disponible bajo coordinacion"
    if not sector_general:
        sector_general = "Informacion general disponible con la agencia"

    modalidad = _catalogo_safe_text(
        getattr(ficha_web, "modalidad_publica", None) if ficha_web else None,
        fallback="",
    ).strip()
    if not modalidad:
        modalidad = _catalogo_safe_text(getattr(candidata, "modalidad_trabajo_preferida", None), fallback="Por definir")

    experiencia = _catalogo_safe_text(
        getattr(ficha_web, "experiencia_resumen", None) if ficha_web else None,
        fallback="",
    ).strip()
    if not experiencia:
        experiencia = _catalogo_safe_text(getattr(candidata, "anos_experiencia", None), fallback="")
    if not experiencia:
        experiencia = _catalogo_safe_text(getattr(candidata, "empleo_anterior", None), fallback="Experiencia no especificada")[:240]

    experiencia_detallada = _catalogo_safe_text(
        getattr(ficha_web, "experiencia_detallada", None) if ficha_web else None,
        fallback="",
    ).strip()
    if not experiencia_detallada:
        experiencia_detallada = "Experiencia detallada no especificada."

    entrevista_publica = _catalogo_safe_text(
        getattr(ficha_web, "entrevista_publica_resumen", None) if ficha_web else None,
        fallback="",
    ).strip()
    if not entrevista_publica:
        entrevista_publica = "Entrevista pública pendiente de preparar por la agencia."

    especialidades = _catalogo_safe_text(
        getattr(ficha_web, "tags_publicos", None) if ficha_web else None,
        fallback="",
    ).strip()
    if not especialidades:
        especialidades = _catalogo_safe_text(getattr(candidata, "areas_experiencia", None), fallback="No especificadas")

    disponibilidad = "Disponible de inmediato" if bool(getattr(ficha_web, "disponible_inmediato", False)) else ""
    if not disponibilidad:
        disponibilidad = _catalogo_safe_text(getattr(candidata, "disponibilidad_inicio", None), fallback="A coordinar")
    sueldo_publico = _catalogo_safe_text(
        getattr(ficha_web, "sueldo_texto_publico", None) if ficha_web else None,
        fallback="",
    ).strip() or None
    estado_publico = _catalogo_safe_text(
        getattr(ficha_web, "estado_publico", None) if ficha_web else None,
        fallback="disponible",
    ).strip().lower()
    foto_publica = _catalogo_safe_text(
        getattr(ficha_web, "foto_publica_url", None) if ficha_web else None,
        fallback="",
    ).strip() or None
    verificacion = "Perfil evaluado por la agencia"
    return {
        "id": int(getattr(candidata, "fila", 0) or 0),
        "codigo": (getattr(candidata, "codigo", None) or "").strip() or None,
        "nombre_publico": _catalogo_public_alias(candidata, ficha_web=ficha_web),
        "edad": _catalogo_safe_text(
            getattr(ficha_web, "edad_publica", None) if ficha_web else getattr(candidata, "edad", None),
            fallback="No especificada",
        ),
        "ciudad": ciudad,
        "sector_general": sector_general,
        "modalidad": modalidad,
        "estado_publico": estado_publico,
        "sueldo_texto_publico": sueldo_publico,
        "experiencia_resumen": experiencia,
        "experiencia_detallada": experiencia_detallada,
        "entrevista_publica_resumen": entrevista_publica,
        "especialidades": especialidades,
        "foto_publica": foto_publica,
        "disponibilidad": disponibilidad,
        "verificacion_general": verificacion,
    }


def _domesticas_store_public_payload(candidata, ficha_web=None):
    """Payload público estricto (allowlist) para tienda abierta."""
    nombre_publico = (getattr(ficha_web, "nombre_publico", None) or "").strip()
    if not nombre_publico:
        nombre_publico = _catalogo_public_alias(candidata, ficha_web=ficha_web)

    ciudad_publica = (getattr(ficha_web, "ciudad_publica", None) or "").strip() or None
    sector_publico = (getattr(ficha_web, "sector_publico", None) or "").strip() or None
    modalidad_publica = (getattr(ficha_web, "modalidad_publica", None) or "").strip() or None
    experiencia_resumen = (getattr(ficha_web, "experiencia_resumen", None) or "").strip() or None
    experiencia_detallada = (getattr(ficha_web, "experiencia_detallada", None) or "").strip() or None
    entrevista_publica_resumen = (getattr(ficha_web, "entrevista_publica_resumen", None) or "").strip() or None
    tags_publicos = (getattr(ficha_web, "tags_publicos", None) or "").strip() or None
    sueldo_texto_publico = (getattr(ficha_web, "sueldo_texto_publico", None) or "").strip() or None
    foto_publica_url = (getattr(ficha_web, "foto_publica_url", None) or "").strip() or None

    return {
        "id": int(getattr(candidata, "fila", 0) or 0),
        "codigo": (getattr(candidata, "codigo", None) or "").strip() or None,
        "nombre_publico": nombre_publico,
        "edad_publica": (getattr(ficha_web, "edad_publica", None) or "").strip() or None,
        "ciudad_publica": ciudad_publica,
        "sector_publico": sector_publico,
        "modalidad_publica": modalidad_publica,
        "sueldo_texto_publico": sueldo_texto_publico,
        "experiencia_resumen": experiencia_resumen,
        "experiencia_detallada": experiencia_detallada,
        "tags_publicos": tags_publicos,
        "disponible_inmediato": bool(getattr(ficha_web, "disponible_inmediato", False)),
        "foto_publica_url": foto_publica_url,
        "entrevista_publica_resumen": entrevista_publica_resumen,
        "estado_publico": (getattr(ficha_web, "estado_publico", "disponible") or "disponible").strip().lower(),
    }


def _binary_image_mimetype(blob) -> Optional[str]:
    if isinstance(blob, memoryview):
        blob = blob.tobytes()
    if not isinstance(blob, (bytes, bytearray)) or not blob:
        return None
    kind = imghdr.what(None, h=bytes(blob))
    if not kind:
        return None
    if kind == "jpeg":
        return "image/jpeg"
    if kind == "png":
        return "image/png"
    if kind == "gif":
        return "image/gif"
    if kind == "webp":
        return "image/webp"
    if kind == "bmp":
        return "image/bmp"
    return None


def _private_store_detail_payload(candidata, ficha_web, *, token: str):
    payload = _domesticas_store_public_payload(candidata, ficha_web=ficha_web)
    estado_publico = (payload.get("estado_publico") or "disponible").strip().lower()
    experiencia_resumen = (payload.get("experiencia_resumen") or "").strip() or "Experiencia no especificada."
    experiencia_detallada = (payload.get("experiencia_detallada") or "").strip() or "Experiencia detallada no especificada."
    entrevista_publica = (payload.get("entrevista_publica_resumen") or "").strip() or None
    tags_publicos_raw = (payload.get("tags_publicos") or "").strip()
    tags_publicos = [x.strip() for x in tags_publicos_raw.split(",") if x and x.strip()]
    disponibilidad_texto = "Disponible inmediata" if bool(payload.get("disponible_inmediato")) else "Disponibilidad sujeta a coordinación"
    ciudad_sector = " · ".join([x for x in [(payload.get("ciudad_publica") or "").strip(), (payload.get("sector_publico") or "").strip()] if x]).strip()
    perfil_blob = getattr(candidata, "perfil", None)
    perfil_url = None
    if _binary_image_mimetype(perfil_blob):
        perfil_url = url_for("public.private_store_profile_image", token=token, candidata_id=int(getattr(candidata, "fila", 0) or 0))
    foto_display_url = perfil_url
    payload.update({
        "edad_publica": payload.get("edad_publica") or "No especificada",
        "modalidad_publica": payload.get("modalidad_publica") or "A coordinar",
        "ciudad_sector": ciudad_sector or "Ciudad/sector no especificado",
        "sueldo_texto_publico": payload.get("sueldo_texto_publico") or "Sueldo a coordinar según funciones.",
        "experiencia_resumen": experiencia_resumen,
        "experiencia_detallada": experiencia_detallada,
        "entrevista_publica_resumen": entrevista_publica,
        "entrevista_disponible": bool(entrevista_publica),
        "tags_publicos_lista": tags_publicos,
        "disponibilidad_texto": disponibilidad_texto,
        "foto_publica_url": foto_display_url,
        "foto_origen": "perfil_blob" if perfil_url else "fallback",
        "estado_publico_label": "Disponible" if estado_publico == "disponible" else "Verificado",
        "nota_confianza": "Perfil revisado por Doméstica del Cibao A&D.",
    })
    return payload


def _private_store_card_payload(candidata, ficha_web, *, token: str):
    payload = _domesticas_store_public_payload(candidata, ficha_web=ficha_web)
    perfil_blob = getattr(candidata, "perfil", None)
    perfil_url = None
    if _binary_image_mimetype(perfil_blob):
        perfil_url = url_for(
            "public.private_store_profile_image",
            token=token,
            candidata_id=int(getattr(candidata, "fila", 0) or 0),
        )
    payload["foto_publica_url"] = perfil_url
    return payload


_PROTECTED_INTERVIEW_REDACT = "Información protegida por la agencia"
_SENSITIVE_KEYWORDS = (
    "direccion",
    "dirección",
    "domicilio",
    "residencia",
    "referencia familiar",
    "referencias familiares",
    "referencia laboral",
    "referencias laborales",
)


def _private_store_is_sensitive_label(label: str) -> bool:
    raw = (label or "").strip().lower()
    if not raw:
        return False
    return any(k in raw for k in _SENSITIVE_KEYWORDS)


def _private_store_redact_text(value: str) -> str:
    txt = (value or "").strip()
    if not txt:
        return ""
    return txt


def _private_store_build_protected_interview(candidata):
    from models import Entrevista, EntrevistaRespuesta, EntrevistaPregunta

    sections = []
    has_source = False

    try:
        entrevista = (
            Entrevista.query
            .filter(Entrevista.candidata_id == int(getattr(candidata, "fila", 0) or 0))
            .order_by(Entrevista.id.desc())
            .first()
        )
    except SQLAlchemyError:
        entrevista = None
    if entrevista is not None:
        has_source = True
        respuestas = (
            db.session.query(EntrevistaRespuesta, EntrevistaPregunta)
            .join(EntrevistaPregunta, EntrevistaPregunta.id == EntrevistaRespuesta.pregunta_id)
            .filter(EntrevistaRespuesta.entrevista_id == int(entrevista.id))
            .order_by(EntrevistaPregunta.orden.asc(), EntrevistaPregunta.id.asc())
            .all()
        )
        for resp, pregunta in (respuestas or []):
            label = (getattr(pregunta, "texto", None) or getattr(pregunta, "clave", None) or "Dato").strip()
            answer = (getattr(resp, "respuesta", None) or "").strip()
            if not answer:
                continue
            has_source = True
            if _private_store_is_sensitive_label(label):
                safe_answer = _PROTECTED_INTERVIEW_REDACT
            else:
                safe_answer = _private_store_redact_text(answer)
            if not safe_answer:
                continue
            sections.append({"section": "entrevista", "label": label[:180], "value": safe_answer[:1200]})

    if not sections:
        legacy = (getattr(candidata, "entrevista", None) or "").strip()
        if legacy:
            has_source = True
            for raw in legacy.splitlines():
                line = (raw or "").strip(" -\t")
                if not line:
                    continue
                if ":" in line:
                    q, a = line.split(":", 1)
                    label = (q or "").strip()[:180] or "Pregunta"
                    answer_raw = (a or "").strip()
                else:
                    label = "Observación"
                    answer_raw = line
                if not answer_raw:
                    continue
                safe_answer = (
                    _PROTECTED_INTERVIEW_REDACT
                    if _private_store_is_sensitive_label(label)
                    else _private_store_redact_text(answer_raw)
                )
                if not safe_answer:
                    continue
                sections.append({"section": "entrevista", "label": label, "value": safe_answer[:1200]})

    ref_laborales = (
        getattr(candidata, "referencias_laborales_texto", None)
        or getattr(candidata, "contactos_referencias_laborales", None)
        or getattr(candidata, "referencias_laboral", None)
        or ""
    ).strip()
    ref_familiares = (
        getattr(candidata, "referencias_familiares_texto", None)
        or getattr(candidata, "referencias_familiares_detalle", None)
        or getattr(candidata, "referencias_familiares", None)
        or ""
    ).strip()
    if has_source and (ref_laborales or ref_familiares):
        sections.append({"section": "referencias", "label": "Referencias laborales", "value": _PROTECTED_INTERVIEW_REDACT})
        sections.append({"section": "referencias", "label": "Referencias familiares", "value": _PROTECTED_INTERVIEW_REDACT})

    # Evita duplicados por normalización.
    uniq = []
    seen = set()
    for item in sections:
        key = (item.get("label", "").strip().lower(), item.get("value", "").strip().lower())
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)

    return {"has_source": has_source, "sections": uniq[:80]}


def _private_store_has_real_interview(candidata) -> bool:
    data = _private_store_build_protected_interview(candidata)
    return bool(data.get("has_source")) and bool(data.get("sections"))


_MI_SELECCION_SESSION_KEY = "mi_seleccion_candidatas"
_MI_SELECCION_MAX = 20


def _mi_seleccion_get_ids() -> list[int]:
    raw = session.get(_MI_SELECCION_SESSION_KEY, [])
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for value in raw:
        try:
            num = int(value)
        except Exception:
            continue
        if num > 0 and num not in out:
            out.append(num)
    return out[:_MI_SELECCION_MAX]


def _mi_seleccion_set_ids(ids: list[int]) -> None:
    cleaned: list[int] = []
    for value in ids:
        try:
            num = int(value)
        except Exception:
            continue
        if num > 0 and num not in cleaned:
            cleaned.append(num)
    session[_MI_SELECCION_SESSION_KEY] = cleaned[:_MI_SELECCION_MAX]
    session.modified = True


def _mi_seleccion_return_to() -> str:
    raw = (request.form.get("return_to") or request.referrer or "").strip()
    if not raw:
        return url_for("public.domesticas_store_list")
    try:
        host = (request.host_url or "").rstrip("/")
        if raw.startswith(host):
            raw = raw[len(host):] or "/"
    except Exception:
        pass
    if not raw.startswith("/"):
        return url_for("public.domesticas_store_list")
    if raw.startswith("//"):
        return url_for("public.domesticas_store_list")
    return raw


def _domestica_disponible_para_tienda(candidata_id: int):
    from models import Candidata, CandidataWeb

    row = (
        db.session.query(Candidata, CandidataWeb)
        .join(CandidataWeb, Candidata.fila == CandidataWeb.candidata_id)
        .filter(Candidata.fila == int(candidata_id))
        .filter(CandidataWeb.visible.is_(True))
        .filter(CandidataWeb.estado_publico == "disponible")
        .first()
    )
    return row


def _mi_seleccion_valid_rows(ids: list[int]):
    valid_rows = []
    for candidata_id in ids:
        row = _domestica_disponible_para_tienda(int(candidata_id))
        if row:
            valid_rows.append(row)
    return valid_rows


def _resolver_catalogo_publico_por_token(token: str):
    return resolve_catalogo_privado_publico_por_token(token, touch_last_seen=True)


_RD_CIUDADES_OPCIONES = [
    "Distrito Nacional",
    "Santo Domingo",
    "Santiago",
    "La Vega",
    "Puerto Plata",
    "Espaillat",
    "San Cristóbal",
    "La Romana",
    "San Pedro de Macorís",
    "Higüey / La Altagracia",
    "San Francisco de Macorís / Duarte",
    "Bonao / Monseñor Nouel",
    "Moca",
    "Mao / Valverde",
    "Azua",
    "Barahona",
    "San Juan",
    "Peravia / Baní",
    "Monte Plata",
    "Samaná",
    "María Trinidad Sánchez / Nagua",
    "Hermanas Mirabal",
    "Sánchez Ramírez / Cotuí",
    "Dajabón",
    "Monte Cristi",
    "El Seibo",
    "Hato Mayor",
    "Pedernales",
    "Independencia",
    "Elías Piña",
    "Bahoruco",
]
_TIENDA_MODALIDADES = ["Con dormida", "Salida diaria"]
_TIENDA_FUNCIONES = [
    "Limpieza general",
    "Cocinar",
    "Lavar",
    "Planchar",
    "Cuidar niños",
    "Cuidar envejecientes",
    "Limpieza profunda",
    "Organización del hogar",
]
_TIENDA_FUNCIONES_TERMS = {
    "Limpieza general": ["limpieza general", "limpieza"],
    "Cocinar": ["cocinar", "cocina"],
    "Lavar": ["lavar", "lavado"],
    "Planchar": ["planchar", "planchado"],
    "Cuidar niños": ["cuidar niños", "cuidar ninos", "niños", "ninos"],
    "Cuidar envejecientes": ["cuidar envejecientes", "envejecientes", "adulto mayor"],
    "Limpieza profunda": ["limpieza profunda", "deep cleaning"],
    "Organización del hogar": ["organización del hogar", "organizacion del hogar", "organización", "organizacion"],
}


def _private_store_is_json_request() -> bool:
    xrw = (request.headers.get("X-Requested-With") or "").strip().lower()
    accept = (request.headers.get("Accept") or "").strip().lower()
    return (xrw == "xmlhttprequest") or ("application/json" in accept)


def _private_store_json_error(status: str, code: int):
    message = "Token inválido."
    if status == "expired":
        message = "Este enlace privado expiró."
    return _json_no_cache({"ok": False, "error": status, "message": message}, status=code)


def _private_store_available_and_stats():
    from models import CandidataWeb

    rows = (
        CandidataWeb.query
        .filter(CandidataWeb.visible.is_(True))
        .filter(CandidataWeb.estado_publico == "disponible")
        .all()
    )
    available_ids = [int(getattr(r, "candidata_id", 0) or 0) for r in rows if int(getattr(r, "candidata_id", 0) or 0) > 0]
    con_dormida = 0
    salida_diaria = 0
    inmediatas = 0
    for row in rows:
        modalidad_publica = (getattr(row, "modalidad_publica", None) or "").strip().lower()
        if modalidad_publica == "con dormida":
            con_dormida += 1
        if modalidad_publica == "salida diaria":
            salida_diaria += 1
        if bool(getattr(row, "disponible_inmediato", False)):
            inmediatas += 1
    stats = {
        "total": len(available_ids),
        "con_dormida": int(con_dormida),
        "salida_diaria": int(salida_diaria),
        "inmediatas": int(inmediatas),
    }
    return available_ids, stats


def _private_store_selection_session_key(catalogo_id: int) -> str:
    return f"tienda_sel_{int(catalogo_id)}"


def _private_store_get_ids(catalogo_id: int) -> list[int]:
    key = _private_store_selection_session_key(catalogo_id)
    raw = session.get(key, [])
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for value in raw:
        try:
            num = int(value)
        except Exception:
            continue
        if num > 0 and num not in out:
            out.append(num)
    return out[:_MI_SELECCION_MAX]


def _private_store_set_ids(catalogo_id: int, ids: list[int]) -> None:
    key = _private_store_selection_session_key(catalogo_id)
    cleaned: list[int] = []
    for value in ids:
        try:
            num = int(value)
        except Exception:
            continue
        if num > 0 and num not in cleaned:
            cleaned.append(num)
    session[key] = cleaned[:_MI_SELECCION_MAX]
    session.modified = True


def _private_store_return_to(catalogo_id: int, token: str) -> str:
    raw = (request.form.get("return_to") or request.referrer or "").strip()
    fallback = url_for("public.private_store_list", token=token)
    if not raw:
        return fallback
    try:
        host = (request.host_url or "").rstrip("/")
        if raw.startswith(host):
            raw = raw[len(host):] or "/"
    except Exception:
        pass
    if not raw.startswith("/") or raw.startswith("//"):
        return fallback
    token_prefix = f"/tienda/{token}/"
    if raw == f"/tienda/{token}" or raw.startswith(token_prefix):
        return raw
    # Evita saltar a otra tienda/token.
    return fallback


def _private_store_filters():
    q = (request.args.get("q") or "").strip()[:120]
    ciudad = (request.args.get("ciudad") or "").strip()[:120]
    modalidad = (request.args.get("modalidad") or "").strip()[:120]
    raw_funciones = request.args.getlist("funciones")
    if not raw_funciones:
        legacy_tag = (request.args.get("tag") or "").strip()[:120]
        raw_funciones = [legacy_tag] if legacy_tag else []
    funciones = []
    valid_map = {x.lower(): x for x in _TIENDA_FUNCIONES}
    for raw in raw_funciones:
        clean = (raw or "").strip()[:120]
        if not clean:
            continue
        canonical = valid_map.get(clean.lower(), clean)
        if canonical not in funciones:
            funciones.append(canonical)
    disponible_inmediato = (request.args.get("disponible_inmediato") or "").strip().lower()
    return q, ciudad, modalidad, funciones[:8], disponible_inmediato


def _resolve_share_state(code: str):
    from clientes import routes as clientes_routes

    alias = clientes_routes.resolve_public_share_alias(code)
    if alias is None:
        return None, "invalid", "invalid", None

    link_type = (getattr(alias, "link_type", "") or "").strip().lower()
    token = str(getattr(alias, "token", "") or "")
    token_hash = clientes_routes._public_link_token_hash_storage(token)

    if link_type == "existente":
        if clientes_routes._public_link_usage_by_hash(token_hash) is not None:
            return alias, "used", link_type, token
        cliente, fail_reason, _meta = clientes_routes._resolve_public_link_token(token)
        if cliente is not None:
            return alias, "ready", link_type, token
        if fail_reason == "expired":
            return alias, "expired", link_type, token
        return alias, "invalid", link_type, token

    if link_type == "nuevo":
        if clientes_routes._public_new_link_usage_by_hash(token_hash) is not None:
            return alias, "used", link_type, token
        ok, fail_reason, _meta = clientes_routes._resolve_public_new_link_token(token)
        if ok:
            return alias, "ready", link_type, token
        if fail_reason == "expired":
            return alias, "expired", link_type, token
        return alias, "invalid", link_type, token

    return alias, "invalid", "invalid", token


@public_bp.route("/solicitud/<code>", methods=["GET"])
def solicitud_share_landing(code: str):
    alias, share_state, link_type, _token = _resolve_share_state(code)
    code_clean = (code or "").strip().upper()
    og_url = _public_external_url("public.solicitud_share_landing", code=code_clean)
    continue_url = _public_external_url("public.solicitud_share_continue", code=code_clean)

    if alias is None:
        return render_template(
            "clientes/public_link_invalid.html",
            reason_key="invalid",
            status_code=404,
            og_url=og_url,
            canonical_url=og_url,
        ), 404

    return render_template(
        "clientes/public_share_landing.html",
        share_code=code_clean,
        share_state=share_state,
        link_type=link_type,
        continue_url=continue_url,
        og_url=og_url,
        canonical_url=og_url,
    ), 200


@public_bp.route("/solicitud/<code>/continuar", methods=["GET", "POST"])
def solicitud_share_continue(code: str):
    alias, share_state, link_type, token = _resolve_share_state(code)
    code_clean = (code or "").strip().upper()
    og_url = _public_external_url("public.solicitud_share_landing", code=code_clean)
    sent_state = (request.args.get("estado") or "").strip().lower()

    if alias is None:
        return render_template(
            "clientes/public_link_invalid.html",
            reason_key="invalid",
            status_code=404,
            og_url=og_url,
            canonical_url=og_url,
        ), 404

    if share_state == "used":
        # UX: tras envío exitoso por alias, mostrar primero confirmación de éxito
        # (si existe estado de sesión válido), y "used" solo en reingresos posteriores.
        if sent_state == "enviado" and token:
            g.public_share_code = code_clean
            g.public_share_url_override = og_url
            from clientes import routes as clientes_routes
            if link_type == "existente":
                return clientes_routes.solicitud_publica(token)
            if link_type == "nuevo":
                return clientes_routes.solicitud_publica_nueva_token(token)
        return render_template(
            "clientes/public_link_used.html",
            status_code=410,
            og_url=og_url,
            canonical_url=og_url,
        ), 410

    if share_state == "expired":
        return render_template(
            "clientes/public_link_invalid.html",
            reason_key="expired",
            status_code=410,
            og_url=og_url,
            canonical_url=og_url,
        ), 410

    if share_state != "ready":
        return render_template(
            "clientes/public_link_invalid.html",
            reason_key="invalid",
            status_code=404,
            og_url=og_url,
            canonical_url=og_url,
        ), 404

    g.public_share_code = code_clean
    g.public_share_url_override = og_url

    from clientes import routes as clientes_routes

    if link_type == "existente":
        return clientes_routes.solicitud_publica(token)
    if link_type == "nuevo":
        return clientes_routes.solicitud_publica_nueva_token(token)

    return render_template(
        "clientes/public_link_invalid.html",
        reason_key="invalid",
        status_code=404,
        og_url=og_url,
        canonical_url=og_url,
    ), 404


@public_bp.route('/ping', methods=['GET'])
def public_ping():
    """Endpoint liviano para saber si el servidor está vivo (público)."""
    return _json_no_cache({
        'ok': True,
        'public_enabled': bool(PUBLIC_SITE_ENABLED),
        'server_time': iso_utc_z(),
    })


@public_bp.route('/live/ping', methods=['POST'])
def public_live_ping():
    content_length = int(request.content_length or 0)
    if content_length > int(PUBLIC_LIVE_PING_MAX_BODY_BYTES):
        return _json_no_cache({"ok": False, "error": "payload_too_large"}, status=413)

    ip = _public_client_ip()
    if _public_live_rate_limited(ip):
        return _json_no_cache({"ok": False, "error": "rate_limited"}, status=429)

    if not request.is_json:
        return _json_no_cache({"ok": False, "error": "invalid_json"}, status=400)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _json_no_cache({"ok": False, "error": "invalid_json"}, status=400)

    event_type = str(payload.get('event_type') or '').strip().lower()[:32]
    if event_type not in PUBLIC_LIVE_ALLOWED_EVENT_TYPES:
        return _json_no_cache({"ok": False, "error": "invalid_event_type"}, status=400)

    current_path = str(payload.get('current_path') or "").strip()
    if (not current_path) or (not current_path.startswith("/")):
        return _json_no_cache({"ok": False, "error": "invalid_current_path"}, status=400)
    current_path = current_path[:int(PUBLIC_LIVE_PING_PATH_MAX_LEN)]

    page_title = (payload.get('page_title') or '').strip()[:160]
    ua = (request.headers.get("User-Agent") or "").strip().lower()
    is_bot = any(tok in ua for tok in ("bot", "crawler", "spider", "curl", "python-requests"))

    # Muestreo liviano para no generar ruido ni costo de escritura innecesario.
    if (event_type == "heartbeat") and is_bot:
        return _json_no_cache({'ok': True, 'sampled': False})

    minute_key = utc_now_naive().strftime("%Y%m%d%H%M")
    route_key = f"public_live:{minute_key}:{current_path}"
    count = 0
    try:
        count = int(bp_get(route_key, default=0, context="public_live_route_count_get") or 0) + 1
        bp_set(route_key, count, timeout=180, context="public_live_route_count_set")
    except Exception:
        count = 0

    # Persistencia en auditoria con dedupe por minuto/ruta/evento.
    dedupe_key = f"public_live_log:{minute_key}:{event_type}:{current_path}"
    should_log = (event_type != "heartbeat")
    try:
        if should_log or not bp_get(dedupe_key, default=0, context="public_live_dedupe_get"):
            bp_set(dedupe_key, 1, timeout=180, context="public_live_dedupe_set")
            should_log = True
    except Exception:
        pass

    if should_log:
        log_action(
            action_type="PUBLIC_LIVE_EVENT",
            entity_type="public_route",
            entity_id=current_path[:64] or "home",
            summary=f"Visita publica {current_path}"[:255],
            metadata={
                "scope": "public",
                "event_type": event_type,
                "page_title": page_title,
                "route_hits_minute": count,
            },
            success=True,
        )
    return _json_no_cache({'ok': True, 'sampled': True, 'route_hits_minute': count})


@public_bp.route("/")
def index():
    """
    Raíz del sitio.

    - Si la web pública está deshabilitada:
        👉 redirige al login interno (/login).
    - Si la web pública está habilitada:
        👉 muestra el landing público normal.
    """
    if not PUBLIC_SITE_ENABLED:
        # 🔴 Cambia "/login" por "/home" o la ruta que uses como inicio de sesión
        return redirect("/login")

    return render_template("public/index.html")


@public_bp.route("/servicios")
def servicios():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("public/servicios.html")


@public_bp.route("/sobre-nosotros")
def sobre_nosotros():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("public/sobre_nosotros.html")


@public_bp.route("/contacto")
def contacto():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("public/contacto.html")


@public_bp.route("/faq")
def faq():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("public/faq.html")


@public_bp.route("/como-funciona")
def como_funciona():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("public/como_funciona.html")


@public_bp.route("/beneficios")
def beneficios():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("public/beneficios.html")


@public_bp.route("/candidatas")
def candidatas_publico():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    # Compatibilidad de coexistencia:
    # Si hay sesión interna de staff, usar el flujo legacy de banco/listado.
    try:
        role = (str(session.get("role") or "").strip().lower())
        if role in ("owner", "admin", "secretaria"):
            from core import legacy_handlers as legacy_h
            return legacy_h.list_candidatas()
    except Exception:
        pass
    try:
        if bool(getattr(current_user, "is_authenticated", False)):
            role = (getattr(current_user, "role", None) or getattr(current_user, "rol", None) or "").strip().lower()
            if role in ("owner", "admin", "secretaria"):
                from core import legacy_handlers as legacy_h
                return legacy_h.list_candidatas()
    except Exception:
        pass
    return render_template("public/candidatas.html")


@public_bp.route("/politicas")
def politicas_publicas():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("politicas.html")


@public_bp.route("/privacidad")
def privacidad_publica():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("privacidad.html")


@public_bp.route("/gracias")
def gracias():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("public/gracias.html")


@public_bp.route("/tienda-domesticas", methods=["GET"])
def domesticas_store_alias():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return redirect(url_for("public.domesticas_store_list"), code=302)


@public_bp.route("/domesticas", methods=["GET"])
def domesticas_store_list():
    if not PUBLIC_SITE_ENABLED:
        abort(404)

    from models import Candidata, CandidataWeb

    q = (request.args.get("q") or "").strip()[:120]
    ciudad = (request.args.get("ciudad") or "").strip()[:120]
    modalidad = (request.args.get("modalidad") or "").strip()[:120]
    tag = (request.args.get("tag") or "").strip()[:120]
    disponible_inmediato = (request.args.get("disponible_inmediato") or "").strip().lower()
    page = _safe_page(request.args.get("page"), default=1)
    per_page = request.args.get("per_page", 12, type=int)
    per_page = per_page if per_page in (12, 24, 36) else 12

    query = (
        db.session.query(Candidata, CandidataWeb)
        .join(CandidataWeb, Candidata.fila == CandidataWeb.candidata_id)
        .filter(CandidataWeb.visible.is_(True))
        .filter(CandidataWeb.estado_publico == "disponible")
        .order_by(
            db.case((CandidataWeb.orden_lista.is_(None), 1), else_=0).asc(),
            CandidataWeb.orden_lista.asc(),
            CandidataWeb.fecha_ultima_actualizacion.desc(),
            Candidata.fila.desc(),
        )
    )

    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                CandidataWeb.nombre_publico.ilike(like),
                CandidataWeb.tags_publicos.ilike(like),
                CandidataWeb.ciudad_publica.ilike(like),
                CandidataWeb.sector_publico.ilike(like),
                CandidataWeb.modalidad_publica.ilike(like),
            )
        )
    if ciudad:
        query = query.filter(CandidataWeb.ciudad_publica.ilike(f"%{ciudad}%"))
    if modalidad:
        query = query.filter(CandidataWeb.modalidad_publica.ilike(f"%{modalidad}%"))
    if tag:
        query = query.filter(CandidataWeb.tags_publicos.ilike(f"%{tag}%"))
    if disponible_inmediato in {"1", "true", "si", "sí", "yes"}:
        query = query.filter(CandidataWeb.disponible_inmediato.is_(True))

    total = query.count()
    items = (
        query
        .limit(per_page)
        .offset((page - 1) * per_page)
        .all()
    )

    pages = (total + per_page - 1) // per_page if per_page else 1
    pages = max(1, pages)
    has_prev = page > 1
    has_next = page < pages

    selected_ids = _mi_seleccion_get_ids()
    selected_set = set(selected_ids)
    cards = []
    for cand, ficha in (items or []):
        payload = _private_store_card_payload(cand, ficha_web=ficha, token=token)
        payload["is_selected"] = int(payload["id"]) in selected_set
        cards.append(payload)

    base_options_q = (
        db.session.query(CandidataWeb)
        .filter(CandidataWeb.visible.is_(True))
        .filter(CandidataWeb.estado_publico == "disponible")
    )
    ciudades = sorted({(row.ciudad_publica or "").strip() for row in base_options_q if (row.ciudad_publica or "").strip()})
    modalidades = sorted({(row.modalidad_publica or "").strip() for row in base_options_q if (row.modalidad_publica or "").strip()})

    return render_template(
        "public/domesticas_store_list.html",
        cards=cards,
        selection_count=len(selected_ids),
        total=total,
        page=page,
        pages=pages,
        per_page=per_page,
        has_prev=has_prev,
        has_next=has_next,
        prev_num=page - 1 if has_prev else 1,
        next_num=page + 1 if has_next else pages,
        q=q,
        ciudad=ciudad,
        modalidad=modalidad,
        tag=tag,
        disponible_inmediato=disponible_inmediato,
        ciudades=ciudades,
        modalidades=modalidades,
    )


@public_bp.route("/domesticas/<codigo_o_id>", methods=["GET"])
def domesticas_store_detail(codigo_o_id: str):
    if not PUBLIC_SITE_ENABLED:
        abort(404)

    from models import Candidata, CandidataWeb

    raw = (codigo_o_id or "").strip()
    if not raw:
        abort(404)
    if raw.isdigit():
        cand = Candidata.query.filter(Candidata.fila == int(raw)).first()
    else:
        cand = Candidata.query.filter(func.lower(Candidata.codigo) == raw.lower()).first()
    if not cand:
        abort(404)

    ficha = CandidataWeb.query.filter_by(candidata_id=int(cand.fila)).first()
    if not ficha or (not bool(getattr(ficha, "visible", False))) or (str(getattr(ficha, "estado_publico", "") or "").strip().lower() != "disponible"):
        abort(404)

    selected_ids = _mi_seleccion_get_ids()
    selected_set = set(selected_ids)
    candidata = _domesticas_store_public_payload(cand, ficha_web=ficha)
    candidata["is_selected"] = int(candidata["id"]) in selected_set
    return render_template(
        "public/domesticas_store_detail.html",
        candidata=candidata,
        selection_count=len(selected_ids),
    )


@public_bp.route("/mi-seleccion", methods=["GET"])
def mi_seleccion_list():
    if not PUBLIC_SITE_ENABLED:
        abort(404)

    selected_ids = _mi_seleccion_get_ids()
    rows = _mi_seleccion_valid_rows(selected_ids)
    valid_ids = [int(getattr(cand, "fila", 0) or 0) for cand, _ficha in rows]
    if valid_ids != selected_ids:
        _mi_seleccion_set_ids(valid_ids)
    cards = [_private_store_card_payload(cand, ficha_web=ficha, token=token) for cand, ficha in rows]
    return render_template(
        "public/mi_seleccion.html",
        cards=cards,
        selection_count=len(cards),
    )


@public_bp.route("/mi-seleccion/agregar", methods=["POST"])
def mi_seleccion_agregar():
    if not PUBLIC_SITE_ENABLED:
        abort(404)

    candidata_id = int(request.form.get("candidata_id") or 0)
    return_to = _mi_seleccion_return_to()
    if candidata_id <= 0:
        return redirect(return_to, code=303)
    if not _domestica_disponible_para_tienda(candidata_id):
        return redirect(return_to, code=303)

    ids = _mi_seleccion_get_ids()
    if candidata_id not in ids:
        ids.append(candidata_id)
    _mi_seleccion_set_ids(ids[:_MI_SELECCION_MAX])
    return redirect(return_to, code=303)


@public_bp.route("/mi-seleccion/quitar", methods=["POST"])
def mi_seleccion_quitar():
    if not PUBLIC_SITE_ENABLED:
        abort(404)

    candidata_id = int(request.form.get("candidata_id") or 0)
    return_to = _mi_seleccion_return_to()
    ids = _mi_seleccion_get_ids()
    if candidata_id > 0 and candidata_id in ids:
        ids = [x for x in ids if int(x) != int(candidata_id)]
        _mi_seleccion_set_ids(ids)
    return redirect(return_to, code=303)


@public_bp.route("/mi-seleccion/limpiar", methods=["POST"])
def mi_seleccion_limpiar():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return_to = _mi_seleccion_return_to()
    _mi_seleccion_set_ids([])
    return redirect(return_to, code=303)


@public_bp.route("/catalogo/<token>", methods=["GET"])
def catalogo_privado_listado(token: str):
    from models import CatalogoPrivadoItem, CandidataWeb

    catalogo, status = _resolver_catalogo_publico_por_token(token)
    if status == "invalid":
        return render_template("catalogo_privado/catalogo_invalido.html"), 404
    if status == "expired":
        return render_template("catalogo_privado/catalogo_expirado.html"), 410

    items = (
        CatalogoPrivadoItem.query.filter_by(catalogo_id=catalogo.id, is_visible=True)
        .order_by(CatalogoPrivadoItem.orden.asc().nullslast(), CatalogoPrivadoItem.id.asc())
        .all()
    )
    candidata_ids = [int(item.candidata_id) for item in items if item.candidata_id]
    fichas = {}
    if candidata_ids:
        rows = CandidataWeb.query.filter(CandidataWeb.candidata_id.in_(candidata_ids)).all()
        fichas = {int(r.candidata_id): r for r in rows}
    candidatas = [
        _catalogo_public_payload(item.candidata, ficha_web=fichas.get(int(item.candidata_id)))
        for item in items
        if item.candidata
    ]
    return render_template(
        "catalogo_privado/catalogo_listado.html",
        catalogo=catalogo,
        candidatas=candidatas,
        token=token,
    )


@public_bp.route("/catalogo/<token>/candidata/<codigo_o_id>", methods=["GET"])
def catalogo_privado_candidata_detalle(token: str, codigo_o_id: str):
    from models import Candidata, CatalogoPrivadoItem, CandidataWeb

    catalogo, status = _resolver_catalogo_publico_por_token(token)
    if status == "invalid":
        return render_template("catalogo_privado/catalogo_invalido.html"), 404
    if status == "expired":
        return render_template("catalogo_privado/catalogo_expirado.html"), 410

    candidata_q = Candidata.query
    raw = (codigo_o_id or "").strip()
    if raw.isdigit():
        candidata = candidata_q.filter(Candidata.fila == int(raw)).first()
    else:
        candidata = candidata_q.filter(func.lower(Candidata.codigo) == raw.lower()).first()
    if not candidata:
        return render_template("catalogo_privado/catalogo_invalido.html"), 404

    exists_item = CatalogoPrivadoItem.query.filter_by(
        catalogo_id=catalogo.id,
        candidata_id=int(candidata.fila),
        is_visible=True,
    ).first()
    if not exists_item:
        return render_template("catalogo_privado/catalogo_invalido.html"), 404
    ficha_web = CandidataWeb.query.filter_by(candidata_id=int(candidata.fila)).first()

    return render_template(
        "catalogo_privado/catalogo_candidata_detalle.html",
        catalogo=catalogo,
        candidata=_catalogo_public_payload(candidata, ficha_web=ficha_web),
        token=token,
    )


@public_bp.route("/tienda/<token>", methods=["GET"])
def private_store_list(token: str):
    from models import Candidata, CandidataWeb

    catalogo, status = _resolver_catalogo_publico_por_token(token)
    if status == "invalid":
        return render_template("private_store/token_invalid.html"), 404
    if status == "expired":
        return render_template("private_store/token_expired.html"), 410

    scope_mode = str(getattr(catalogo, "scope_mode", "manual_shortlist") or "manual_shortlist").strip().lower()
    if scope_mode == "manual_shortlist":
        return redirect(url_for("public.catalogo_privado_listado", token=token), code=302)

    q, ciudad, modalidad, funciones, disponible_inmediato = _private_store_filters()
    page = _safe_page(request.args.get("page"), default=1)
    per_page = request.args.get("per_page", 12, type=int)
    per_page = per_page if per_page in (12, 24, 36) else 12

    query = (
        db.session.query(Candidata, CandidataWeb)
        .join(CandidataWeb, Candidata.fila == CandidataWeb.candidata_id)
        .filter(CandidataWeb.visible.is_(True))
        .filter(CandidataWeb.estado_publico == "disponible")
        .order_by(
            db.case((CandidataWeb.orden_lista.is_(None), 1), else_=0).asc(),
            CandidataWeb.orden_lista.asc(),
            CandidataWeb.fecha_ultima_actualizacion.desc(),
            Candidata.fila.desc(),
        )
    )

    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                CandidataWeb.nombre_publico.ilike(like),
                CandidataWeb.tags_publicos.ilike(like),
                CandidataWeb.ciudad_publica.ilike(like),
                CandidataWeb.sector_publico.ilike(like),
                CandidataWeb.modalidad_publica.ilike(like),
            )
        )
    if ciudad:
        query = query.filter(CandidataWeb.ciudad_publica.ilike(f"%{ciudad}%"))
    if modalidad:
        query = query.filter(CandidataWeb.modalidad_publica.ilike(f"%{modalidad}%"))
    if funciones:
        for funcion in funciones:
            terms = _TIENDA_FUNCIONES_TERMS.get(funcion, [funcion])
            query = query.filter(
                db.or_(
                    *[
                        db.or_(
                            CandidataWeb.tags_publicos.ilike(f"%{term}%"),
                            CandidataWeb.experiencia_resumen.ilike(f"%{term}%"),
                            CandidataWeb.experiencia_detallada.ilike(f"%{term}%"),
                        )
                        for term in terms
                    ]
                )
            )
    if disponible_inmediato in {"1", "true", "si", "sí", "yes"}:
        query = query.filter(CandidataWeb.disponible_inmediato.is_(True))

    total = query.count()
    items = query.limit(per_page).offset((page - 1) * per_page).all()
    pages = (total + per_page - 1) // per_page if per_page else 1
    pages = max(1, pages)
    has_prev = page > 1
    has_next = page < pages

    selected_ids = _private_store_get_ids(int(catalogo.id))
    selected_set = set(selected_ids)
    cards = []
    for cand, ficha in (items or []):
        payload = _private_store_card_payload(cand, ficha_web=ficha, token=token)
        payload["is_selected"] = int(payload["id"]) in selected_set
        cards.append(payload)

    return render_template(
        "private_store/store_list.html",
        catalogo=catalogo,
        token=token,
        cards=cards,
        total=total,
        page=page,
        pages=pages,
        per_page=per_page,
        has_prev=has_prev,
        has_next=has_next,
        prev_num=page - 1 if has_prev else 1,
        next_num=page + 1 if has_next else pages,
        q=q,
        ciudad=ciudad,
        modalidad=modalidad,
        funciones=funciones,
        disponible_inmediato=disponible_inmediato,
        ciudades=_RD_CIUDADES_OPCIONES,
        modalidades=_TIENDA_MODALIDADES,
        funciones_disponibles=_TIENDA_FUNCIONES,
        scope_mode=scope_mode,
        selection_count=len(selected_ids),
        selected_ids=selected_ids,
    )


@public_bp.route("/tienda/<token>/domesticas/<codigo_o_id>", methods=["GET"])
def private_store_detail(token: str, codigo_o_id: str):
    from models import Candidata, CandidataWeb

    catalogo, status = _resolver_catalogo_publico_por_token(token)
    if status == "invalid":
        return render_template("private_store/token_invalid.html"), 404
    if status == "expired":
        return render_template("private_store/token_expired.html"), 410

    scope_mode = str(getattr(catalogo, "scope_mode", "manual_shortlist") or "manual_shortlist").strip().lower()
    if scope_mode == "manual_shortlist":
        return redirect(url_for("public.catalogo_privado_candidata_detalle", token=token, codigo_o_id=codigo_o_id), code=302)

    raw = (codigo_o_id or "").strip()
    if not raw:
        abort(404)
    if raw.isdigit():
        cand = Candidata.query.filter(Candidata.fila == int(raw)).first()
    else:
        cand = Candidata.query.filter(func.lower(Candidata.codigo) == raw.lower()).first()
    if not cand:
        abort(404)

    ficha = CandidataWeb.query.filter_by(candidata_id=int(cand.fila)).first()
    if not ficha or (not bool(getattr(ficha, "visible", False))) or (str(getattr(ficha, "estado_publico", "") or "").strip().lower() != "disponible"):
        abort(404)

    selected_ids = _private_store_get_ids(int(catalogo.id))
    interview_data = _private_store_build_protected_interview(cand)
    has_protected_interview = bool(interview_data.get("has_source")) and bool(interview_data.get("sections"))
    return render_template(
        "private_store/store_detail.html",
        catalogo=catalogo,
        token=token,
        candidata={
            **_private_store_detail_payload(cand, ficha, token=token),
            "is_selected": int(cand.fila) in set(selected_ids),
            "has_protected_interview": has_protected_interview,
            "entrevista_protegida_url": (
                url_for("public.private_store_interview_protected", token=token, candidata_id=int(cand.fila))
                if has_protected_interview else None
            ),
        },
        selection_count=len(selected_ids),
    )


@public_bp.route("/tienda/<token>/domesticas/<int:candidata_id>/perfil", methods=["GET"])
def private_store_profile_image(token: str, candidata_id: int):
    from models import Candidata, CandidataWeb

    catalogo, status = _resolver_catalogo_publico_por_token(token)
    if status == "invalid":
        abort(404)
    if status == "expired":
        abort(410)

    _scope_mode = str(getattr(catalogo, "scope_mode", "manual_shortlist") or "manual_shortlist").strip().lower()
    row = (
        db.session.query(Candidata, CandidataWeb)
        .join(CandidataWeb, Candidata.fila == CandidataWeb.candidata_id)
        .filter(Candidata.fila == int(candidata_id))
        .filter(CandidataWeb.visible.is_(True))
        .filter(CandidataWeb.estado_publico == "disponible")
        .first()
    )
    if not row:
        abort(404)
    candidata, _ficha = row
    blob = getattr(candidata, "perfil", None)
    mimetype = _binary_image_mimetype(blob)
    if not mimetype:
        abort(404)

    resp = make_response(bytes(blob))
    resp.headers["Content-Type"] = mimetype
    resp.headers["Cache-Control"] = "private, max-age=300"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@public_bp.route("/tienda/<token>/domesticas/<int:candidata_id>/entrevista", methods=["GET"])
def private_store_interview_protected(token: str, candidata_id: int):
    from models import Candidata, CandidataWeb

    catalogo, status = _resolver_catalogo_publico_por_token(token)
    if status == "invalid":
        abort(404)
    if status == "expired":
        abort(410)

    row = (
        db.session.query(Candidata, CandidataWeb)
        .join(CandidataWeb, Candidata.fila == CandidataWeb.candidata_id)
        .filter(Candidata.fila == int(candidata_id))
        .filter(CandidataWeb.visible.is_(True))
        .filter(CandidataWeb.estado_publico == "disponible")
        .first()
    )
    if not row:
        abort(404)
    candidata, ficha = row
    interview_data = _private_store_build_protected_interview(candidata)
    if not interview_data.get("sections"):
        abort(404)

    selected_ids = _private_store_get_ids(int(catalogo.id))
    return render_template(
        "private_store/store_interview_protected.html",
        catalogo=catalogo,
        token=token,
        candidata=_private_store_detail_payload(candidata, ficha, token=token),
        entrevista_items=interview_data.get("sections") or [],
        selection_count=len(selected_ids),
    )


@public_bp.route("/tienda/<token>/mi-seleccion", methods=["GET"])
def private_store_selection_list(token: str):
    catalogo, status = _resolver_catalogo_publico_por_token(token)
    if status == "invalid":
        return render_template("private_store/token_invalid.html"), 404
    if status == "expired":
        return render_template("private_store/token_expired.html"), 410

    scope_mode = str(getattr(catalogo, "scope_mode", "manual_shortlist") or "manual_shortlist").strip().lower()
    if scope_mode != "all_available_store":
        return redirect(url_for("public.catalogo_privado_listado", token=token), code=302)

    selected_ids = _private_store_get_ids(int(catalogo.id))
    rows = _mi_seleccion_valid_rows(selected_ids)
    valid_ids = [int(getattr(cand, "fila", 0) or 0) for cand, _ficha in rows]
    if valid_ids != selected_ids:
        _private_store_set_ids(int(catalogo.id), valid_ids)
    cards = [_private_store_card_payload(cand, ficha_web=ficha, token=token) for cand, ficha in rows]
    return render_template(
        "private_store/store_selection.html",
        catalogo=catalogo,
        token=token,
        cards=cards,
        selection_count=len(cards),
    )


@public_bp.route("/tienda/<token>/seleccion/agregar", methods=["POST"])
def private_store_selection_add(token: str):
    catalogo, status = _resolver_catalogo_publico_por_token(token)
    wants_json = _private_store_is_json_request()
    if status == "invalid":
        if wants_json:
            return _private_store_json_error("invalid", 404)
        return render_template("private_store/token_invalid.html"), 404
    if status == "expired":
        if wants_json:
            return _private_store_json_error("expired", 410)
        return render_template("private_store/token_expired.html"), 410

    candidata_id = int(request.form.get("candidata_id") or 0)
    return_to = _private_store_return_to(int(catalogo.id), token)
    removed_unavailable_ids = []
    selected_ids = _private_store_get_ids(int(catalogo.id))
    valid_rows = _mi_seleccion_valid_rows(selected_ids)
    valid_ids = [int(getattr(cand, "fila", 0) or 0) for cand, _ficha in valid_rows]
    removed_unavailable_ids = [x for x in selected_ids if x not in set(valid_ids)]
    if valid_ids != selected_ids:
        _private_store_set_ids(int(catalogo.id), valid_ids)
        selected_ids = valid_ids
    if candidata_id <= 0:
        if wants_json:
            return _json_no_cache({
                "ok": False,
                "error": "invalid_id",
                "message": "Candidata inválida.",
                "selection_count": len(selected_ids),
                "selected_ids": selected_ids,
                "removed_unavailable_ids": removed_unavailable_ids,
            }, status=400)
        return redirect(return_to, code=303)
    if not _domestica_disponible_para_tienda(candidata_id):
        if wants_json:
            return _json_no_cache({
                "ok": False,
                "error": "not_available",
                "message": "Esta candidata ya no está disponible.",
                "selection_count": len(selected_ids),
                "selected_ids": selected_ids,
                "removed_unavailable_ids": removed_unavailable_ids,
            }, status=409)
        return redirect(return_to, code=303)
    ids = _private_store_get_ids(int(catalogo.id))
    already_selected = candidata_id in ids
    if candidata_id not in ids:
        ids.append(candidata_id)
    ids = ids[:_MI_SELECCION_MAX]
    _private_store_set_ids(int(catalogo.id), ids)
    if wants_json:
        return _json_no_cache({
            "ok": True,
            "selection_count": len(ids),
            "selected_ids": ids,
            "message": "Ya estaba en tu selección" if already_selected else "Agregada a tu selección",
            "removed_unavailable_ids": removed_unavailable_ids,
        })
    return redirect(return_to, code=303)


@public_bp.route("/tienda/<token>/seleccion/quitar", methods=["POST"])
def private_store_selection_remove(token: str):
    catalogo, status = _resolver_catalogo_publico_por_token(token)
    wants_json = _private_store_is_json_request()
    if status == "invalid":
        if wants_json:
            return _private_store_json_error("invalid", 404)
        return render_template("private_store/token_invalid.html"), 404
    if status == "expired":
        if wants_json:
            return _private_store_json_error("expired", 410)
        return render_template("private_store/token_expired.html"), 410

    candidata_id = int(request.form.get("candidata_id") or 0)
    return_to = _private_store_return_to(int(catalogo.id), token)
    ids = _private_store_get_ids(int(catalogo.id))
    removed_unavailable_ids = []
    if candidata_id > 0 and candidata_id in ids:
        ids = [x for x in ids if int(x) != int(candidata_id)]
        _private_store_set_ids(int(catalogo.id), ids)
    if wants_json:
        return _json_no_cache({
            "ok": True,
            "selection_count": len(ids),
            "selected_ids": ids,
            "message": "Candidata removida de tu selección",
            "removed_unavailable_ids": removed_unavailable_ids,
        })
    return redirect(return_to, code=303)


@public_bp.route("/tienda/<token>/seleccion/limpiar", methods=["POST"])
def private_store_selection_clear(token: str):
    catalogo, status = _resolver_catalogo_publico_por_token(token)
    wants_json = _private_store_is_json_request()
    if status == "invalid":
        if wants_json:
            return _private_store_json_error("invalid", 404)
        return render_template("private_store/token_invalid.html"), 404
    if status == "expired":
        if wants_json:
            return _private_store_json_error("expired", 410)
        return render_template("private_store/token_expired.html"), 410
    return_to = _private_store_return_to(int(catalogo.id), token)
    _private_store_set_ids(int(catalogo.id), [])
    if wants_json:
        return _json_no_cache({
            "ok": True,
            "selection_count": 0,
            "selected_ids": [],
            "message": "Selección limpiada",
            "removed_unavailable_ids": [],
        })
    return redirect(return_to, code=303)


@public_bp.route("/tienda/<token>/estado.json", methods=["GET"])
def private_store_state_json(token: str):
    catalogo, status = _resolver_catalogo_publico_por_token(token)
    if status == "invalid":
        return _private_store_json_error("invalid", 404)
    if status == "expired":
        return _private_store_json_error("expired", 410)

    scope_mode = str(getattr(catalogo, "scope_mode", "manual_shortlist") or "manual_shortlist").strip().lower()
    if scope_mode != "all_available_store":
        return _json_no_cache({"ok": False, "error": "unsupported_scope", "message": "Estado no disponible para este catálogo."}, status=400)

    selected_ids = _private_store_get_ids(int(catalogo.id))
    available_ids, stats = _private_store_available_and_stats()
    available_set = set(available_ids)
    sanitized_selected_ids = [x for x in selected_ids if x in available_set]
    removed_unavailable_ids = [x for x in selected_ids if x not in available_set]
    if sanitized_selected_ids != selected_ids:
        _private_store_set_ids(int(catalogo.id), sanitized_selected_ids)

    return _json_no_cache({
        "ok": True,
        "catalogo_id": int(catalogo.id),
        "selection_count": len(sanitized_selected_ids),
        "selected_ids": sanitized_selected_ids,
        "available_ids": available_ids,
        "updated_at": iso_utc_z(),
        "removed_unavailable_ids": removed_unavailable_ids,
        "stats": stats,
    })


@public_bp.route("/tienda/<token>/solicitar-entrevistas", methods=["GET", "POST"])
def private_store_request_interviews(token: str):
    from models import TiendaInteres, TiendaInteresItem

    catalogo, status = _resolver_catalogo_publico_por_token(token)
    if status == "invalid":
        return render_template("private_store/token_invalid.html"), 404
    if status == "expired":
        return render_template("private_store/token_expired.html"), 410
    scope_mode = str(getattr(catalogo, "scope_mode", "manual_shortlist") or "manual_shortlist").strip().lower()
    if scope_mode != "all_available_store":
        return redirect(url_for("public.catalogo_privado_listado", token=token), code=302)

    ids = _private_store_get_ids(int(catalogo.id))
    rows = _mi_seleccion_valid_rows(ids)
    cards = [_domesticas_store_public_payload(cand, ficha_web=ficha) for cand, ficha in rows]
    valid_ids = [int((c or {}).get("id") or 0) for c in cards if int((c or {}).get("id") or 0) > 0]
    if valid_ids != ids:
        _private_store_set_ids(int(catalogo.id), valid_ids)
        ids = valid_ids

    cliente = getattr(catalogo, "cliente", None)
    cliente_nombre = (getattr(cliente, "nombre_completo", None) or "").strip()
    cliente_telefono = (getattr(cliente, "telefono", None) or "").strip()
    has_linked_cliente = bool(cliente and cliente_nombre and cliente_telefono)
    default_nombre = cliente_nombre if has_linked_cliente else (request.form.get("nombre_contacto") or "").strip()
    default_telefono = cliente_telefono if has_linked_cliente else (request.form.get("telefono_contacto") or "").strip()
    comentario = (request.form.get("comentario") or "").strip()

    if request.method == "POST":
        posted_ids = sorted({int(x) for x in request.form.getlist("candidata_ids") if str(x).isdigit() and int(x) > 0})
        if (not ids) and posted_ids:
            valid_posted = []
            for candidata_id in posted_ids:
                if _domestica_disponible_para_tienda(int(candidata_id)):
                    valid_posted.append(int(candidata_id))
            if valid_posted:
                ids = valid_posted[:_MI_SELECCION_MAX]
                _private_store_set_ids(int(catalogo.id), ids)
        if not ids:
            flash("Debes seleccionar al menos una candidata antes de solicitar entrevistas.", "danger")
            return redirect(url_for("public.private_store_selection_list", token=token))
        if not default_nombre or not default_telefono:
            flash("Nombre y teléfono/WhatsApp son obligatorios.", "danger")
            return render_template(
                "private_store/store_request_interviews.html",
                catalogo=catalogo,
                token=token,
                cards=cards,
                selection_count=len(cards),
                has_linked_cliente=has_linked_cliente,
                nombre_contacto=default_nombre,
                telefono_contacto=default_telefono,
                comentario=comentario,
            ), 400
        payload = {
            "token": str(token),
            "catalogo_id": int(catalogo.id),
            "cliente_id": int(catalogo.cliente_id) if getattr(catalogo, "cliente_id", None) else None,
            "solicitud_id": int(catalogo.solicitud_id) if getattr(catalogo, "solicitud_id", None) else None,
            "selection_ids": [int(x) for x in ids],
            "selection_count": len(ids),
            "posted_ids": [int(x) for x in posted_ids],
            "nombre_contacto": default_nombre[:200],
            "telefono_contacto": default_telefono[:50],
            "token_hint_usado": (getattr(catalogo, "token_hint", None) or None),
        }
        current_app.logger.warning("private_store.checkout.persist.start %s", payload)
        try:
            if not all(int(x or 0) > 0 for x in ids):
                current_app.logger.warning("private_store.checkout.persist.invalid_ids %s", payload)
                flash("La selección contiene candidatas inválidas. Vuelve a seleccionar.", "danger")
                return redirect(url_for("public.private_store_selection_list", token=token))
            interes = TiendaInteres(
                catalogo_id=int(catalogo.id),
                cliente_id=int(catalogo.cliente_id) if getattr(catalogo, "cliente_id", None) else None,
                solicitud_id=int(catalogo.solicitud_id) if getattr(catalogo, "solicitud_id", None) else None,
                nombre_contacto=default_nombre[:200],
                telefono_contacto=default_telefono[:50],
                comentario=comentario or None,
                estado="nuevo",
                token_hint_usado=(getattr(catalogo, "token_hint", None) or None),
            )
            db.session.add(interes)
            db.session.flush()
            for idx, candidata_id in enumerate(ids, start=1):
                db.session.add(TiendaInteresItem(interes_id=int(interes.id), candidata_id=int(candidata_id), orden=idx))
            current_app.logger.warning(
                "private_store.checkout.persist.created interes_id=%s item_count=%s",
                int(interes.id),
                len(ids),
            )
            db.session.commit()
            current_app.logger.warning(
                "private_store.checkout.persist.committed interes_id=%s catalogo_id=%s cliente_id=%s estado=%s",
                int(interes.id),
                int(interes.catalogo_id),
                (int(interes.cliente_id) if interes.cliente_id else None),
                str(interes.estado or ""),
            )
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("private_store.checkout.persist.rollback.sqlalchemy %s", payload)
            flash("No pudimos enviar tu solicitud. Intenta nuevamente o contáctanos por WhatsApp.", "danger")
            return render_template(
                "private_store/store_request_interviews.html",
                catalogo=catalogo,
                token=token,
                cards=cards,
                selection_count=len(cards),
                has_linked_cliente=has_linked_cliente,
                nombre_contacto=default_nombre,
                telefono_contacto=default_telefono,
                comentario=comentario,
            ), 500
        except Exception:
            db.session.rollback()
            current_app.logger.exception("private_store.checkout.persist.rollback.unexpected %s", payload)
            flash("No pudimos enviar tu solicitud. Intenta nuevamente o contáctanos por WhatsApp.", "danger")
            return render_template(
                "private_store/store_request_interviews.html",
                catalogo=catalogo,
                token=token,
                cards=cards,
                selection_count=len(cards),
                has_linked_cliente=has_linked_cliente,
                nombre_contacto=default_nombre,
                telefono_contacto=default_telefono,
                comentario=comentario,
            ), 500
        _private_store_set_ids(int(catalogo.id), [])
        return redirect(url_for("public.private_store_request_interviews_success", token=token, interes_id=int(interes.id)), code=303)

    return render_template(
        "private_store/store_request_interviews.html",
        catalogo=catalogo,
        token=token,
        cards=cards,
        selection_count=len(cards),
        has_linked_cliente=has_linked_cliente,
        nombre_contacto=default_nombre,
        telefono_contacto=default_telefono,
        comentario=comentario,
    )


@public_bp.route("/tienda/<token>/solicitar-entrevistas/success/<int:interes_id>", methods=["GET"])
def private_store_request_interviews_success(token: str, interes_id: int):
    from models import TiendaInteres

    catalogo, status = _resolver_catalogo_publico_por_token(token)
    if status == "invalid":
        return render_template("private_store/token_invalid.html"), 404
    if status == "expired":
        return render_template("private_store/token_expired.html"), 410
    row = TiendaInteres.query.get_or_404(int(interes_id))
    if int(getattr(row, "catalogo_id", 0) or 0) != int(catalogo.id):
        abort(404)
    return render_template(
        "private_store/store_request_success.html",
        catalogo=catalogo,
        token=token,
        interes=row,
        selection_count=0,
    )
