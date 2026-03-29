# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy import inspect

from config_app import db
from models import StaffNotificacion


def create_staff_notification(
    *,
    tipo: str,
    entity_type: str,
    entity_id: int,
    titulo: str,
    mensaje: str | None = None,
    payload: dict[str, Any] | None = None,
    session_commit: bool = True,
) -> bool:
    """Crea una notificación interna sin afectar el flujo principal."""
    tipo_val = (tipo or "").strip()[:50]
    etype_val = (entity_type or "").strip()[:30]
    title_val = (titulo or "").strip()[:180]
    if not tipo_val or not etype_val or not title_val:
        return False
    try:
        eid = int(entity_id or 0)
    except Exception:
        return False
    if eid <= 0:
        return False

    row = StaffNotificacion(
        tipo=tipo_val,
        entity_type=etype_val,
        entity_id=eid,
        titulo=title_val,
        mensaje=(mensaje or "").strip()[:300] or None,
        payload=payload or None,
    )
    try:
        insp = inspect(db.engine)
        if not insp.has_table("staff_notificaciones"):
            return False
        db.session.add(row)
        if session_commit:
            db.session.commit()
        else:
            db.session.flush()
        return True
    except IntegrityError:
        db.session.rollback()
        return False
    except SQLAlchemyError:
        db.session.rollback()
        return False
