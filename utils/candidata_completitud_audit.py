# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Iterable


_REF_PLACEHOLDERS = {
    "none",
    "node",
    "no",
    "n/a",
    "na",
    "null",
    "sin",
    "pendiente",
}


def entrevista_ok(entrevista_legacy: str | None, entrevistas_nuevas_count: int | None) -> bool:
    legacy = (entrevista_legacy or "").strip()
    if legacy:
        return True
    try:
        return int(entrevistas_nuevas_count or 0) > 0
    except Exception:
        return False


def binario_ok(value) -> bool:
    if value is None:
        return False
    if isinstance(value, memoryview):
        try:
            return len(value.tobytes()) > 0
        except Exception:
            return False
    if isinstance(value, (bytes, bytearray)):
        return len(value) > 0
    if isinstance(value, int):
        return value > 0
    try:
        return len(value) > 0  # type: ignore[arg-type]
    except Exception:
        return bool(value)


def referencias_ok(texto: str | None) -> bool:
    clean = (texto or "").strip()
    if not clean:
        return False
    return clean.lower() not in _REF_PLACEHOLDERS


def faltantes_desde_flags(flags: dict[str, bool]) -> list[str]:
    return [key for key, ok in flags.items() if not ok]


def es_incompleta(flags: dict[str, bool]) -> bool:
    return any(not ok for ok in flags.values())


def solo_criticos(faltantes: Iterable[str]) -> bool:
    fs = set(faltantes or [])
    return ("entrevista" in fs) or ("cedula1" in fs) or ("cedula2" in fs)


def solo_sin_documentos(faltantes: Iterable[str]) -> bool:
    fs = set(faltantes or [])
    return bool(fs.intersection({"foto_perfil", "depuracion", "perfil", "cedula1", "cedula2"}))


def solo_sin_referencias(faltantes: Iterable[str]) -> bool:
    fs = set(faltantes or [])
    return bool(fs.intersection({"referencias_laboral", "referencias_familiares"}))
