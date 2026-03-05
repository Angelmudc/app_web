# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Iterable, Optional

from flask import abort, flash, redirect, url_for
from sqlalchemy import or_


_DESCALIFICADA_ESTADO = "descalificada"
_DEFAULT_ALLOWED_ACTIONS = {
    "editar_candidata",
    "subir_archivos",
    "ver_archivos",
    "ver_detalle",
}


def candidata_esta_descalificada(candidata) -> bool:
    if not candidata:
        return False

    estado = (getattr(candidata, "estado", None) or "").strip().lower()
    if estado == _DESCALIFICADA_ESTADO:
        return True

    # Compatibilidad defensiva por si aparece un booleano en otro entorno.
    return bool(getattr(candidata, "is_descalificada", False))


def is_candidata_descalificada(candidata) -> bool:
    """Alias canónico para reglas de negocio."""
    return candidata_esta_descalificada(candidata)


def candidatas_activas_filter(model_cls):
    """Filtro SQL reutilizable: excluye descalificadas sin asumir NOT NULL."""
    return or_(model_cls.estado.is_(None), model_cls.estado != _DESCALIFICADA_ESTADO)


def assert_candidata_no_descalificada(
    candidata,
    action: str = "",
    *,
    allowed_actions: Optional[Iterable[str]] = None,
    redirect_endpoint: Optional[str] = None,
    redirect_kwargs: Optional[dict] = None,
):
    """Bloquea acción operativa para candidatas descalificadas.

    - Devuelve `None` si la acción puede continuar.
    - Si está descalificada:
      - con `redirect_endpoint`: hace flash + redirect.
      - sin `redirect_endpoint`: aborta con 403.
    """
    if not candidata_esta_descalificada(candidata):
        return None

    allowed = set(allowed_actions or _DEFAULT_ALLOWED_ACTIONS)
    action_norm = (action or "").strip().lower()
    if action_norm in allowed:
        return None

    nombre = (getattr(candidata, "nombre_completo", None) or "").strip() or f"#{getattr(candidata, 'fila', '?')}"
    mensaje = f"La candidata {nombre} está descalificada y no puede usarse para {action_norm or 'esta acción'}."

    if redirect_endpoint:
        flash(mensaje, "warning")
        return redirect(url_for(redirect_endpoint, **(redirect_kwargs or {})))

    abort(403, description=mensaje)


def require_not_descalificada(candidata, action_name: str = "", **kwargs):
    """Alias canónico para bloqueo operativo."""
    return assert_candidata_no_descalificada(candidata, action=action_name, **kwargs)
