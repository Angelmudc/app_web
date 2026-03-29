from __future__ import annotations

from config_app import db
from models import Candidata


def get_candidata_by_id(raw_id):
    """Obtiene una candidata por ID de forma segura; retorna None si no es válido."""
    cid = str(raw_id or "").strip()
    if not cid.isdigit():
        return None
    return db.session.get(Candidata, int(cid))
