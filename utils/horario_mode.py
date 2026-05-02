from __future__ import annotations

from typing import Any


def _txt(v: Any, limit: int = 120) -> str:
    return str(v or "").strip()[:limit]


def infer_horario_tipo(*, modalidad_group: Any, modalidad_trabajo: Any) -> str:
    group = _txt(modalidad_group, 40).lower()
    if group in {"con_dormida", "con_salida_diaria"}:
        return "con_dormida" if group == "con_dormida" else "salida_diaria"

    mod = _txt(modalidad_trabajo, 200).lower()
    if "dormida" in mod:
        return "con_dormida"
    return "salida_diaria"


def build_horario_from_form(
    *,
    modalidad_group: Any,
    modalidad_trabajo: Any,
    dias_trabajo: Any,
    hora_entrada: Any,
    hora_salida: Any,
    dormida_entrada: Any,
    dormida_salida: Any,
    horario_legacy: Any,
) -> tuple[str, dict, list[str]]:
    horario_tipo = infer_horario_tipo(
        modalidad_group=modalidad_group,
        modalidad_trabajo=modalidad_trabajo,
    )
    dias = _txt(dias_trabajo)
    h_in = _txt(hora_entrada, 60)
    h_out = _txt(hora_salida, 60)
    d_in = _txt(dormida_entrada)
    d_out = _txt(dormida_salida)
    legacy = _txt(horario_legacy, 200)
    errors: list[str] = []
    has_structured = any([dias, h_in, h_out, d_in, d_out])

    if not has_structured and legacy:
        payload = {
            "horario_tipo": horario_tipo,
            "dias_trabajo": None,
            "hora_entrada": None,
            "hora_salida": None,
            "dormida_entrada": None,
            "dormida_salida": None,
        }
        return legacy, payload, []

    if horario_tipo == "salida_diaria":
        if not dias:
            errors.append("Indica los días de trabajo.")
        if not h_in:
            errors.append("Indica la hora de entrada.")
        if not h_out:
            errors.append("Indica la hora de salida.")
        horario = f"{dias}, de {h_in} a {h_out}" if (dias and h_in and h_out) else legacy
    else:
        if not d_in:
            errors.append("Indica el día y hora de entrada.")
        if not d_out:
            errors.append("Indica el día y hora de salida.")
        horario = f"Entrada: {d_in} / Salida: {d_out}" if (d_in and d_out) else legacy

    horario = _txt(horario, 200)
    if not horario:
        errors.append("Indica el horario.")

    payload = {
        "horario_tipo": horario_tipo,
        "dias_trabajo": dias or None,
        "hora_entrada": h_in or None,
        "hora_salida": h_out or None,
        "dormida_entrada": d_in or None,
        "dormida_salida": d_out or None,
    }
    return horario, payload, errors


def apply_horario_to_solicitud(
    solicitud: Any,
    *,
    modalidad_group: Any,
    modalidad_trabajo: Any,
    dias_trabajo: Any,
    hora_entrada: Any,
    hora_salida: Any,
    dormida_entrada: Any,
    dormida_salida: Any,
    horario_legacy: Any,
) -> tuple[str, dict, list[str]]:
    horario, payload, errors = build_horario_from_form(
        modalidad_group=modalidad_group,
        modalidad_trabajo=modalidad_trabajo,
        dias_trabajo=dias_trabajo,
        hora_entrada=hora_entrada,
        hora_salida=hora_salida,
        dormida_entrada=dormida_entrada,
        dormida_salida=dormida_salida,
        horario_legacy=horario_legacy,
    )
    if hasattr(solicitud, "horario"):
        solicitud.horario = horario
    current = getattr(solicitud, "detalles_servicio", None)
    details = dict(current) if isinstance(current, dict) else {}
    details.update(payload)
    if hasattr(solicitud, "detalles_servicio"):
        solicitud.detalles_servicio = details or None
    return horario, payload, errors
