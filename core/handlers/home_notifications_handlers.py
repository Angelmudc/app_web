from typing import Optional

from flask import jsonify, request, session, url_for
from flask_login import current_user
from sqlalchemy import and_, func, inspect
from sqlalchemy.exc import IntegrityError

from config_app import db
from decorators import roles_required
from models import StaffNotificacion, StaffNotificacionLectura
from utils.timezone import iso_utc_z


def _staff_reader_key() -> Optional[str]:
    try:
        if bool(getattr(current_user, "is_authenticated", False)):
            raw_id = str(getattr(current_user, "get_id", lambda: "")() or "").strip()
            if raw_id.startswith("staff:"):
                return raw_id[:120]
            if hasattr(current_user, "id"):
                return f"staff:{int(getattr(current_user, 'id'))}"
    except Exception:
        pass
    usuario = (session.get("usuario") or "").strip().lower()
    if not usuario:
        return None
    import hashlib

    digest = hashlib.sha256(usuario.encode("utf-8")).hexdigest()[:40]
    return f"legacy:{digest}"


def _notif_review_url(notif: StaffNotificacion) -> str:
    entity_type = (getattr(notif, "entity_type", "") or "").strip().lower()
    entity_id = int(getattr(notif, "entity_id", 0) or 0)
    if entity_id <= 0:
        return url_for("home")
    if entity_type == "candidata":
        return url_for("buscar_candidata", candidata_id=entity_id)
    if entity_type == "recluta_perfil":
        return url_for("reclutas.detalle", recluta_id=entity_id)
    return url_for("home")


def _staff_notifications_unread_count(reader_key: str) -> int:
    if not reader_key:
        return 0
    unread_count = (
        db.session.query(func.count(StaffNotificacion.id))
        .outerjoin(
            StaffNotificacionLectura,
            and_(
                StaffNotificacionLectura.notificacion_id == StaffNotificacion.id,
                StaffNotificacionLectura.reader_key == reader_key,
            ),
        )
        .filter(StaffNotificacionLectura.id.is_(None))
        .scalar()
    )
    return int(unread_count or 0)


def _staff_notification_to_item(notif: StaffNotificacion, is_read: bool) -> dict:
    return {
        "id": int(notif.id),
        "tipo": (notif.tipo or "").strip(),
        "titulo": (notif.titulo or "").strip(),
        "mensaje": (notif.mensaje or "").strip() or None,
        "entity_type": (notif.entity_type or "").strip(),
        "entity_id": int(notif.entity_id or 0),
        "created_at": iso_utc_z(notif.created_at) if notif.created_at else None,
        "review_url": _notif_review_url(notif),
        "is_read": bool(is_read),
    }


def _staff_notifications_ready() -> bool:
    try:
        insp = inspect(db.engine)
        return bool(
            insp.has_table("staff_notificaciones")
            and insp.has_table("staff_notificaciones_lecturas")
        )
    except Exception:
        return False


@roles_required("admin", "secretaria")
def home_public_notifications_count():
    if not _staff_notifications_ready():
        return jsonify({"unread": 0})
    reader_key = _staff_reader_key()
    unread = _staff_notifications_unread_count(reader_key or "")
    return jsonify({"unread": int(unread)})


@roles_required("admin", "secretaria")
def home_public_notifications_list():
    if not _staff_notifications_ready():
        return jsonify(
            {
                "unread": 0,
                "items": [],
                "pending_items": [],
                "reviewed_items": [],
                "has_more_pending": False,
                "has_more_reviewed": False,
            }
        )
    reader_key = _staff_reader_key()
    per_bucket_limit = min(10, max(1, int(request.args.get("limit", 10) or 10)))
    if not reader_key:
        return jsonify(
            {
                "unread": 0,
                "items": [],
                "pending_items": [],
                "reviewed_items": [],
                "has_more_pending": False,
                "has_more_reviewed": False,
            }
        )

    pending_rows_plus_one = (
        db.session.query(StaffNotificacion)
        .outerjoin(
            StaffNotificacionLectura,
            and_(
                StaffNotificacionLectura.notificacion_id == StaffNotificacion.id,
                StaffNotificacionLectura.reader_key == reader_key,
            ),
        )
        .filter(StaffNotificacionLectura.id.is_(None))
        .order_by(StaffNotificacion.created_at.desc(), StaffNotificacion.id.desc())
        .limit(per_bucket_limit + 1)
        .all()
    )
    reviewed_rows_plus_one = (
        db.session.query(StaffNotificacion)
        .join(
            StaffNotificacionLectura,
            and_(
                StaffNotificacionLectura.notificacion_id == StaffNotificacion.id,
                StaffNotificacionLectura.reader_key == reader_key,
            ),
        )
        .order_by(StaffNotificacion.created_at.desc(), StaffNotificacion.id.desc())
        .limit(per_bucket_limit + 1)
        .all()
    )

    has_more_pending = len(pending_rows_plus_one) > per_bucket_limit
    has_more_reviewed = len(reviewed_rows_plus_one) > per_bucket_limit
    pending_rows = pending_rows_plus_one[:per_bucket_limit]
    reviewed_rows = reviewed_rows_plus_one[:per_bucket_limit]
    pending_items = [_staff_notification_to_item(n, is_read=False) for n in pending_rows]
    reviewed_items = [_staff_notification_to_item(n, is_read=True) for n in reviewed_rows]
    items = pending_items + reviewed_items

    unread = _staff_notifications_unread_count(reader_key)
    return jsonify(
        {
            "unread": unread,
            "items": items,
            "pending_items": pending_items,
            "reviewed_items": reviewed_items,
            "has_more_pending": has_more_pending,
            "has_more_reviewed": has_more_reviewed,
        }
    )


@roles_required("admin", "secretaria")
def home_public_notifications_mark_read(notificacion_id: int):
    if not _staff_notifications_ready():
        return jsonify({"ok": False, "error": "notifications_not_ready"}), 503
    reader_key = _staff_reader_key()
    if not reader_key:
        return jsonify({"ok": False, "error": "reader_not_available"}), 400
    notif = StaffNotificacion.query.get_or_404(notificacion_id)
    already = (
        StaffNotificacionLectura.query
        .filter_by(notificacion_id=int(notif.id), reader_key=reader_key)
        .first()
    )
    if already is None:
        try:
            db.session.add(StaffNotificacionLectura(notificacion_id=int(notif.id), reader_key=reader_key))
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            pass
        except Exception:
            db.session.rollback()
            return jsonify({"ok": False, "error": "read_mark_failed"}), 500
    return jsonify({"ok": True, "unread": _staff_notifications_unread_count(reader_key)})
