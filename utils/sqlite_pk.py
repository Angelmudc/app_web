from __future__ import annotations

import threading

from sqlalchemy import func


_PK_LOCK = threading.Lock()
_NEXT_PK_BY_TABLE: dict[str, int] = {}


def maybe_assign_sqlite_pk(*, session, model_obj, model_cls) -> None:
    try:
        bind = session.get_bind()
        dialect = str(getattr(getattr(bind, "dialect", None), "name", "")).strip().lower()
        if dialect != "sqlite":
            return
        if getattr(model_obj, "id", None):
            return
        table_key = str(getattr(model_cls, "__tablename__", "") or model_cls.__name__)
        with _PK_LOCK:
            next_id = int(_NEXT_PK_BY_TABLE.get(table_key, 0) or 0)
            if next_id <= 0:
                max_id = session.query(func.max(model_cls.id)).scalar() or 0
                next_id = int(max_id) + 1
            model_obj.id = int(next_id)
            _NEXT_PK_BY_TABLE[table_key] = int(next_id) + 1
    except Exception:
        return
