from __future__ import annotations

from sqlalchemy.exc import DBAPIError, OperationalError

from config_app import db


def _retry_query(callable_fn, retries: int = 2, swallow: bool = False):
    """
    Ejecuta una función que hace queries a la BD con reintentos básicos.
    - retries: número de reintentos adicionales.
    - swallow: si True, retorna None en vez de levantar excepción tras agotar reintentos.
    """
    last_err = None
    for _ in range(retries + 1):
        try:
            return callable_fn()
        except (OperationalError, DBAPIError) as e:
            # Limpia la sesión para no dejarla en estado inválido
            try:
                db.session.rollback()
            except Exception:
                pass
            last_err = e
            continue
    if swallow:
        return None
    raise last_err
