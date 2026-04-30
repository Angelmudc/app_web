from __future__ import annotations

import re
import unicodedata
from time import perf_counter
from typing import Optional

from flask import current_app
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import load_only

from models import Candidata


CODIGO_PATTERN = re.compile(r"^[A-Z]{3}-\d{6}$")


def _strip_accents_py(s: str) -> str:
    """Quita acentos en Python (para normalizar el texto de busqueda)."""
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")


def normalize_query_text(raw: str) -> str:
    """Normaliza texto para busquedas flexibles (nombre, etc.)."""
    s = (raw or "").strip()
    if not s:
        return ""
    s = s.replace(",", " ").replace(".", " ").replace(";", " ").replace(":", " ")
    s = s.replace("\n", " ").replace("\t", " ")
    s = _strip_accents_py(s).lower()
    s = re.sub(r"[^a-z0-9\s\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_digits(raw: str) -> str:
    """Deja solo digitos (para cedula/telefono)."""
    return re.sub(r"\D", "", raw or "").strip()


def normalize_code(raw: str) -> str:
    """Normaliza codigo: MAYUSCULAS y sin espacios."""
    return re.sub(r"\s+", "", (raw or "").strip().upper())


def _sql_name_norm(col):
    """Normaliza nombre en SQL (PostgreSQL) sin depender de unaccent."""
    lowered = func.lower(col)
    translated = func.translate(
        lowered,
        "\u00e1\u00e0\u00e4\u00e2\u00e3\u00e9\u00e8\u00eb\u00ea\u00ed\u00ec\u00ef\u00ee\u00f3\u00f2\u00f6\u00f4\u00f5\u00fa\u00f9\u00fc\u00fb\u00f1",
        "aaaaaeeeeiiiiooooouuuun",
    )
    cleaned = func.regexp_replace(translated, r"[^a-z0-9\s\-]", " ", "g")
    cleaned = func.regexp_replace(cleaned, r"[\s]+", " ", "g")
    return func.trim(cleaned)


def _sql_digits(col):
    """Extrae solo digitos desde una columna (PostgreSQL)."""
    return func.regexp_replace(col, r"\D", "", "g")


def build_flexible_search_filters(q: str):
    """Construye filtros flexibles para nombre/cedula/telefono."""
    q = (q or "").strip()
    if not q:
        return None, []

    q_code = normalize_code(q)
    q_digits = normalize_digits(q)
    q_text = normalize_query_text(q)

    strict_code = None
    if CODIGO_PATTERN.fullmatch(q_code):
        strict_code = func.trim(func.upper(Candidata.codigo)) == q_code

    filters = []

    if q_text:
        tokens = [t for t in q_text.split(" ") if t]
        name_norm = _sql_name_norm(Candidata.nombre_completo)

        if tokens:
            name_and = and_(*[name_norm.ilike(f"%{t}%") for t in tokens])
            filters.append(name_and)

    if q_digits:
        ced_digits = _sql_digits(Candidata.cedula).ilike(f"%{q_digits}%")
        tel_digits = _sql_digits(Candidata.numero_telefono).ilike(f"%{q_digits}%")
        filters.append(or_(ced_digits, tel_digits))

    if not filters:
        like = f"%{q}%"
        filters.extend(
            [
                Candidata.nombre_completo.ilike(like),
                Candidata.cedula.ilike(like),
                Candidata.numero_telefono.ilike(like),
            ]
        )

    return strict_code, filters


def apply_search_to_candidata_query(base_query, q: str):
    """Aplica la logica de busqueda estandar a una query de Candidata."""
    strict_code, filters = build_flexible_search_filters(q)

    if strict_code is not None:
        return base_query.filter(Candidata.codigo.isnot(None)).filter(strict_code)

    if filters:
        return base_query.filter(or_(*filters))

    return base_query


def search_candidatas_limited(
    q: str,
    *,
    limit: int = 300,
    base_query=None,
    minimal_fields: bool = False,
    fields=None,
    order_mode: str = "nombre_asc",
    log_label: str = "default",
):
    """Ejecuta la busqueda estandar de candidatas con limite y orden consistentes."""
    q = (q or "").strip()[:128]
    if not q:
        return []

    query = base_query if base_query is not None else Candidata.query
    if fields:
        query = query.options(load_only(*fields))
    elif minimal_fields:
        query = query.options(
            load_only(
                Candidata.fila,
                Candidata.nombre_completo,
                Candidata.cedula,
                Candidata.numero_telefono,
                Candidata.codigo,
            )
        )
    safe_limit = max(1, min(int(limit or 300), 500))
    t0 = perf_counter()
    filtered = apply_search_to_candidata_query(query, q)
    if order_mode == "id_desc":
        filtered = filtered.order_by(Candidata.fila.desc())
    else:
        filtered = filtered.order_by(Candidata.nombre_completo.asc())
    rows = filtered.limit(safe_limit).all()
    dt_ms = round((perf_counter() - t0) * 1000, 2)
    current_app.logger.info(
        "search_candidatas_limited[%s] q=%r rows=%s dt_ms=%s",
        log_label,
        q,
        len(rows),
        dt_ms,
    )
    return rows


def _prioritize_candidata_result(rows: list, prioritized_fila: Optional[int]) -> list:
    """Mueve al inicio la fila indicada, si esta en la lista de resultados."""
    if not rows:
        return rows
    try:
        target = int(prioritized_fila or 0)
    except Exception:
        return rows
    if target <= 0:
        return rows
    idx = next((i for i, row in enumerate(rows) if int(getattr(row, "fila", 0) or 0) == target), None)
    if idx is None or idx <= 0:
        return rows
    ordered = list(rows)
    ordered.insert(0, ordered.pop(idx))
    return ordered
