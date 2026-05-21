# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib

from config_app import db
from utils.timezone import utc_now_naive


def catalogo_privado_token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def resolve_catalogo_privado_publico_por_token(token: str, *, touch_last_seen: bool = True):
    from models import CatalogoPrivado

    token_hash = catalogo_privado_token_hash(token)
    catalogo = CatalogoPrivado.query.filter_by(token_hash=token_hash).first()
    if not catalogo:
        return None, "invalid"

    now = utc_now_naive()
    if not bool(catalogo.is_active):
        return catalogo, "expired"
    if catalogo.expires_at and catalogo.expires_at <= now:
        return catalogo, "expired"

    if touch_last_seen:
        catalogo.last_seen_at = now
        db.session.commit()
    return catalogo, "ok"
