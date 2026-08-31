# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Optional, Tuple

from sqlalchemy import func

from config_app import db
from models import Candidata
from utils.cedula_normalizer import normalize_cedula_for_compare


def _with_exclude_fila(query, exclude_fila: Optional[int]):
    if exclude_fila is None:
        return query
    try:
        return query.filter(Candidata.fila != int(exclude_fila))
    except Exception:
        return query


def _find_by_digits_11(digits: str, exclude_fila: Optional[int] = None):
    query = _with_exclude_fila(Candidata.query, exclude_fila)

    with db.session.no_autoflush:
        dup = query.filter(Candidata.cedula_norm_digits == digits).first()
        if dup:
            return dup

        # Fallback defensivo por filas históricas sin cedula_norm_digits.
        try:
            clean_expr = func.regexp_replace(Candidata.cedula, r"[^0-9]", "", "g")
            return query.filter(clean_expr == digits).first()
        except Exception:
            try:
                clean_expr = Candidata.cedula
                for token in ("-", " ", "/", ".", ",", ":", ";", "_", "\\"):
                    clean_expr = func.replace(clean_expr, token, "")
                return query.filter(clean_expr == digits).first()
            except Exception:
                return None


def _find_by_exact_text(raw: str, exclude_fila: Optional[int] = None):
    query = _with_exclude_fila(Candidata.query, exclude_fila)
    with db.session.no_autoflush:
        return query.filter(Candidata.cedula == (raw or "").strip()).first()


def _candidate_descriptor(existing: Candidata) -> str | None:
    nombre = (getattr(existing, "nombre_completo", None) or "").strip()
    fila = getattr(existing, "fila", None)
    if nombre and fila is not None:
        return f"{nombre} (ficha #{fila})"
    if nombre:
        return nombre
    if fila is not None:
        return f"ficha #{fila}"
    return None


def find_duplicate_candidata_by_cedula(
    raw_cedula: str,
    *,
    exclude_fila: Optional[int] = None,
) -> Tuple[Optional[Candidata], str]:
    """Busca duplicados de cédula con estrategia segura para datos existentes.

    Retorna: (candidata_duplicada|None, digits_input)
    """
    raw = (raw_cedula or "").strip()
    digits = normalize_cedula_for_compare(raw)
    if len(digits) == 11:
        return _find_by_digits_11(digits, exclude_fila=exclude_fila), digits
    return _find_by_exact_text(raw, exclude_fila=exclude_fila), digits


def duplicate_cedula_message(existing: Candidata) -> str:
    descriptor = _candidate_descriptor(existing)
    if descriptor:
        return f"Esta cédula ya está registrada en {descriptor}. Verifique cuál expediente debe conservarse."
    return "Esta cédula ya está registrada en otra candidata. Verifique el expediente duplicado antes de continuar."
