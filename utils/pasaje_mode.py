from __future__ import annotations

from typing import Any

PASAJES_VALIDOS = {"incluido", "aparte", "otro"}
PASAJE_MARKER_PREFIX = "Pasaje (otro):"


def _to_text(value: Any) -> str:
    return str(value or "").strip()


def _base_mode_and_text_from_pasaje_aporte(pasaje_aporte: Any) -> tuple[str, str]:
    """Interpreta legado booleano y soporta casos donde el valor llegue como texto libre."""
    if isinstance(pasaje_aporte, bool):
        return ("aparte", "") if pasaje_aporte else ("incluido", "")

    raw = _to_text(pasaje_aporte)
    if not raw:
        return "incluido", ""

    low = raw.lower()
    truthy = {"1", "true", "t", "y", "yes", "si", "sí", "on", "aparte"}
    falsy = {"0", "false", "f", "n", "no", "off", "incluido"}

    if low in truthy:
        return "aparte", ""
    if low in falsy:
        return "incluido", ""

    return "otro", raw[:120]


def normalize_pasaje_mode_text(
    mode_raw: Any,
    text_raw: Any,
    *,
    default_mode: str = "incluido",
) -> tuple[str, str]:
    mode = _to_text(mode_raw).lower()
    if mode not in PASAJES_VALIDOS:
        mode = default_mode if default_mode in PASAJES_VALIDOS else "incluido"

    text = _to_text(text_raw)[:120]
    if mode != "otro":
        text = ""

    return mode, text


def extract_pasaje_otro_from_note(note_text: Any) -> str:
    note = _to_text(note_text)
    if not note:
        return ""
    for raw_line in note.splitlines():
        line = raw_line.strip()
        if line.startswith(PASAJE_MARKER_PREFIX):
            return line[len(PASAJE_MARKER_PREFIX):].strip()[:120]
    return ""


def strip_pasaje_marker_from_note(note_text: Any) -> str:
    note = _to_text(note_text)
    if not note:
        return ""

    out: list[str] = []
    for raw_line in note.splitlines():
        line = raw_line.strip()
        if line.startswith(PASAJE_MARKER_PREFIX):
            continue
        if not line:
            if out and out[-1] != "":
                out.append("")
            continue
        out.append(line)

    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


def read_pasaje_mode_text(
    *,
    pasaje_aporte: Any,
    detalles_servicio: Any,
    nota_cliente: Any,
) -> tuple[str, str]:
    mode, text = _base_mode_and_text_from_pasaje_aporte(pasaje_aporte)

    if isinstance(detalles_servicio, dict):
        pasaje_data = detalles_servicio.get("pasaje")
        if isinstance(pasaje_data, dict):
            stored_mode, stored_text = normalize_pasaje_mode_text(
                pasaje_data.get("mode"),
                pasaje_data.get("text"),
                default_mode=mode,
            )
            mode, text = stored_mode, stored_text
        else:
            legacy_text = _to_text(detalles_servicio.get("pasaje_otro_text"))[:120]
            if legacy_text:
                mode, text = "otro", legacy_text

    if mode != "otro" or not text:
        legacy_note_text = extract_pasaje_otro_from_note(nota_cliente)
        if legacy_note_text:
            mode, text = "otro", legacy_note_text

    return mode, text


def apply_pasaje_to_solicitud(
    solicitud: Any,
    *,
    mode_raw: Any,
    text_raw: Any,
    default_mode: str = "incluido",
) -> tuple[str, str]:
    mode, text = normalize_pasaje_mode_text(mode_raw, text_raw, default_mode=default_mode)

    if hasattr(solicitud, "pasaje_aporte"):
        solicitud.pasaje_aporte = (mode == "aparte")

    current = getattr(solicitud, "detalles_servicio", None)
    details = dict(current) if isinstance(current, dict) else {}
    payload = {"mode": mode}
    if mode == "otro" and text:
        payload["text"] = text
    details["pasaje"] = payload
    details.pop("pasaje_otro_text", None)

    if hasattr(solicitud, "detalles_servicio"):
        solicitud.detalles_servicio = details or None

    return mode, text
