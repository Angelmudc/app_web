# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from datetime import datetime

from flask import current_app, flash, render_template, request
from sqlalchemy import Date, cast, func
from sqlalchemy.exc import OperationalError

from config_app import cache, db
from decorators import roles_required
from core.services.cache_keys import _cache_key_with_role
from utils.timezone import rd_today

from core import legacy_handlers as legacy_h


@roles_required("admin", "secretaria")
@cache.cached(
    timeout=int(os.getenv("CACHE_DASHBOARD_PROCESOS_SECONDS", "30")),
    key_prefix=lambda: _cache_key_with_role("dashboard_procesos"),
)
def dashboard_procesos():
    estado_filtro = (request.args.get("estado") or "").strip()[:40]
    desde_str = (request.args.get("desde") or "").strip()[:10]
    hasta_str = (request.args.get("hasta") or "").strip()[:10]
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 20, type=int)))

    desde = None
    hasta = None
    try:
        if desde_str:
            desde = datetime.strptime(desde_str, "%Y-%m-%d").date()
        if hasta_str:
            hasta = datetime.strptime(hasta_str, "%Y-%m-%d").date()
    except ValueError:
        desde = None
        hasta = None

    estados = [
        "en_proceso",
        "proceso_inscripcion",
        "inscrita",
        "inscrita_incompleta",
        "lista_para_trabajar",
        "trabajando",
        "descalificada",
    ]

    total = 0
    entradas_hoy = 0
    counts_por_estado = {}
    paginado = None
    pending_page_ids = []
    dashboard_return_url = (request.full_path or request.path or "").strip()
    if dashboard_return_url.endswith("?"):
        dashboard_return_url = dashboard_return_url[:-1]

    try:
        total = legacy_h.Candidata.query.count()
        hoy = rd_today()
        entradas_hoy = legacy_h.Candidata.query.filter(cast(legacy_h.Candidata.fecha_cambio_estado, Date) == hoy).count()
        counts_por_estado = dict(
            db.session.query(legacy_h.Candidata.estado, func.count(legacy_h.Candidata.estado))
            .group_by(legacy_h.Candidata.estado)
            .all()
        )

        q = legacy_h.Candidata.query
        if estado_filtro:
            q = q.filter(legacy_h.Candidata.estado == estado_filtro)
        if desde:
            q = q.filter(cast(legacy_h.Candidata.fecha_cambio_estado, Date) >= desde)
        if hasta:
            q = q.filter(cast(legacy_h.Candidata.fecha_cambio_estado, Date) <= hasta)

        q = q.order_by(legacy_h.Candidata.fecha_cambio_estado.desc())

        try:
            paginado = q.paginate(page=page, per_page=per_page, error_out=False)
        except AttributeError:
            paginado = db.paginate(q, page=page, per_page=per_page, error_out=False)
        pending_states = {"en_proceso", "proceso_inscripcion", "inscrita", "inscrita_incompleta"}
        pending_page_ids = [
            int(getattr(c, "fila", 0) or 0)
            for c in (getattr(paginado, "items", None) or [])
            if int(getattr(c, "fila", 0) or 0) > 0
            and str(getattr(c, "estado", "") or "").strip().lower() in pending_states
        ]

    except OperationalError:
        flash("⚠️ No se pudo conectar a la base de datos. Reintenta en unos segundos.", "warning")

        class _EmptyPagination:
            def __init__(self):
                self.items = []
                self.total = 0
                self.pages = 0
                self.page = page
                self.prev_num = None
                self.next_num = None

            def has_prev(self):
                return False

            def has_next(self):
                return False

            def iter_pages(self, left_edge=1, right_edge=1, left_current=2, right_current=2):  # noqa: ARG002
                return iter([])

        paginado = _EmptyPagination()
    except Exception:
        current_app.logger.exception("❌ Error construyendo dashboard")

        class _EmptyPagination:
            def __init__(self):
                self.items = []
                self.total = 0
                self.pages = 0
                self.page = page
                self.prev_num = None

            def has_prev(self):
                return False

            def has_next(self):
                return False

            def iter_pages(self, *args, **kwargs):  # noqa: ARG002
                return iter([])

        paginado = _EmptyPagination()

    return render_template(
        "dashboard_procesos.html",
        total=total,
        entradas_hoy=entradas_hoy,
        counts_por_estado=counts_por_estado,
        estados=estados,
        estado_filtro=estado_filtro,
        desde_str=desde_str,
        hasta_str=hasta_str,
        candidatas=paginado,
        siguiente_pendiente_id=(pending_page_ids[0] if pending_page_ids else None),
        pendientes_queue=",".join(str(x) for x in pending_page_ids),
        dashboard_return_url=dashboard_return_url,
    )
