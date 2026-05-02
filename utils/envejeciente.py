from __future__ import annotations

from typing import Any

RESP_LABELS = {
    "pampers": "cambiar pampers",
    "higiene": "higiene personal",
    "comida": "darle de comer",
    "medicamentos": "darle medicamentos",
    "movilidad": "movilizarlo o ayudarlo a levantarse",
    "otro": "otro cuidado especial",
}


def clean_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        src = raw
    else:
        src = str(raw).split(",")
    out: list[str] = []
    for item in src:
        val = str(item or "").strip().lower()
        if val and val not in out:
            out.append(val)
    return out


def cuidado_envejeciente_aplica(funciones: Any) -> bool:
    return "envejeciente" in clean_list(funciones)


def format_envejeciente_resumen(
    *,
    tipo_cuidado: str | None,
    responsabilidades: Any,
    solo_acompanamiento: Any,
    nota: str | None,
) -> list[str]:
    tipo = (tipo_cuidado or "").strip().lower()
    if tipo not in {"independiente", "encamado"}:
        return []

    lines: list[str] = []
    if tipo == "independiente":
        lines.append("Envejeciente: independiente. Requiere acompañamiento, supervisión o apoyo ligero.")
    else:
        if bool(solo_acompanamiento):
            lines.append(
                "Envejeciente: encamado. La doméstica no realizará higiene, pañales, alimentación ni medicamentos; solo acompañamiento o supervisión."
            )
        else:
            clean = [r for r in clean_list(responsabilidades) if r in RESP_LABELS]
            if clean:
                labels = [RESP_LABELS[r] for r in clean]
                lines.append(f"Envejeciente: encamado. Requiere: {', '.join(labels)}.")
            else:
                lines.append("Envejeciente: encamado.")

    note = (nota or "").strip()
    if note:
        lines.append(f"Nota sobre el envejeciente: {note}")
    return lines
