# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from datetime import datetime

from flask import current_app, flash, redirect, render_template, request, session, url_for
from sqlalchemy import Date, cast, func, or_

from config_app import cache, db
from decorators import roles_required
from core.services.cache_keys import _cache_key_with_role
from core.services.date_utils import get_date_bounds, get_start_date
from forms import LlamadaCandidataForm
from models import Candidata, LlamadaCandidata
from utils.timezone import rd_today, utc_now_naive


@roles_required("admin", "secretaria")
def listado_llamadas_candidatas():
    q = (request.args.get("q") or "").strip()[:128]
    period = (request.args.get("period") or "all").strip()[:16]
    start_date_str = request.args.get("start_date", None)
    page = max(1, request.args.get("page", 1, type=int))

    start_dt, end_dt = get_date_bounds(period, start_date_str)

    calls_subq = (
        db.session.query(
            LlamadaCandidata.candidata_id.label("cid"),
            func.count(LlamadaCandidata.id).label("num_calls"),
            func.max(LlamadaCandidata.fecha_llamada).label("last_call"),
        )
        .group_by(LlamadaCandidata.candidata_id)
        .subquery()
    )

    base_q = (
        db.session.query(
            Candidata.fila,
            Candidata.nombre_completo,
            Candidata.codigo,
            Candidata.numero_telefono,
            Candidata.marca_temporal,
            calls_subq.c.num_calls,
            calls_subq.c.last_call,
        )
        .outerjoin(calls_subq, Candidata.fila == calls_subq.c.cid)
    )

    if q:
        il = f"%{q}%"
        base_q = base_q.filter(
            or_(
                Candidata.codigo.ilike(il),
                Candidata.nombre_completo.ilike(il),
                Candidata.numero_telefono.ilike(il),
                Candidata.cedula.ilike(il),
            )
        )

    def section(estado: str):
        qsec = base_q.filter(Candidata.estado == estado)
        if start_dt and end_dt:
            qsec = qsec.filter(
                cast(Candidata.marca_temporal, Date) >= start_dt,
                cast(Candidata.marca_temporal, Date) <= end_dt,
            )
        try:
            return (
                qsec.order_by(calls_subq.c.last_call.asc().nullsfirst())
                .paginate(page=page, per_page=10, error_out=False)
            )
        except AttributeError:
            return db.paginate(
                qsec.order_by(calls_subq.c.last_call.asc().nullsfirst()),
                page=page,
                per_page=10,
                error_out=False,
            )

    en_proceso = section("en_proceso")
    en_inscripcion = section("proceso_inscripcion")
    lista_trabajar = section("lista_para_trabajar")

    return render_template(
        "llamadas_candidatas.html",
        q=q,
        period=period,
        start_date=start_date_str,
        en_proceso=en_proceso,
        en_inscripcion=en_inscripcion,
        lista_trabajar=lista_trabajar,
    )


@roles_required("admin", "secretaria")
def registrar_llamada_candidata(fila):
    candidata = Candidata.query.get_or_404(fila)
    form = LlamadaCandidataForm()

    if form.validate_on_submit():
        minutos = form.duracion_minutos.data
        segundos = (minutos * 60) if (minutos is not None) else None

        llamada = LlamadaCandidata(
            candidata_id=candidata.fila,
            fecha_llamada=func.now(),
            agente=session.get("usuario", "desconocido")[:64],
            resultado=(form.resultado.data or "").strip()[:200],
            duracion_segundos=segundos,
            notas=(form.notas.data or "").strip()[:2000],
            created_at=utc_now_naive(),
        )
        db.session.add(llamada)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception("❌ Error guardando llamada de candidata")
            flash("❌ Error al registrar la llamada.", "danger")
            return redirect(url_for("listado_llamadas_candidatas"))

        flash(f"Llamada registrada para {candidata.nombre_completo}.", "success")
        return redirect(url_for("listado_llamadas_candidatas"))

    return render_template(
        "registrar_llamada_candidata.html",
        form=form,
        candidata=candidata,
    )


@roles_required("admin")
@cache.cached(
    timeout=int(os.getenv("CACHE_REPORTE_LLAMADAS_SECONDS", "30")),
    key_prefix=lambda: _cache_key_with_role("reporte_llamadas"),
)
def reporte_llamadas_candidatas():
    period = (request.args.get("period") or "week").strip()[:16]
    start_date_str = request.args.get("start_date", None)
    start_dt = get_start_date(period, start_date_str)
    hoy = rd_today()
    page = max(1, request.args.get("page", 1, type=int))

    stats_subq = (
        db.session.query(
            LlamadaCandidata.candidata_id.label("cid"),
            func.count(LlamadaCandidata.id).label("num_calls"),
            func.max(LlamadaCandidata.fecha_llamada).label("last_call"),
        )
        .group_by(LlamadaCandidata.candidata_id)
        .subquery()
    )

    base_q = (
        db.session.query(
            Candidata.fila,
            Candidata.nombre_completo,
            Candidata.codigo,
            Candidata.numero_telefono,
            Candidata.marca_temporal,
            stats_subq.c.num_calls,
            stats_subq.c.last_call,
        )
        .outerjoin(stats_subq, Candidata.fila == stats_subq.c.cid)
    )

    def paginate_estado(estado: str):
        qy = base_q.filter(Candidata.estado == estado)
        if start_dt:
            qy = qy.filter(
                or_(
                    stats_subq.c.last_call == None,  # noqa: E711
                    cast(stats_subq.c.last_call, Date) < start_dt,
                )
            )
        try:
            return (
                qy.order_by(cast(stats_subq.c.last_call, Date).desc().nullsfirst())
                .paginate(page=page, per_page=10, error_out=False)
            )
        except AttributeError:
            return db.paginate(
                qy.order_by(cast(stats_subq.c.last_call, Date).desc().nullsfirst()),
                page=page,
                per_page=10,
                error_out=False,
            )

    estancadas_en_proceso = paginate_estado("en_proceso")
    estancadas_inscripcion = paginate_estado("proceso_inscripcion")
    estancadas_lista = paginate_estado("lista_para_trabajar")

    calls_query = (
        db.session.query(
            LlamadaCandidata.candidata_id,
            func.count().label("cnt"),
        )
        .group_by(LlamadaCandidata.candidata_id)
        .all()
    )
    total_calls = sum(c.cnt for c in calls_query)
    num_with_calls = len(calls_query)
    promedio = round(total_calls / num_with_calls, 1) if num_with_calls else 0

    calls_q = db.session.query(LlamadaCandidata).order_by(LlamadaCandidata.fecha_llamada.desc())
    if start_dt:
        start_dt_dt = datetime.combine(start_dt, datetime.min.time())
        calls_q = calls_q.filter(LlamadaCandidata.fecha_llamada >= start_dt_dt)
    max_calls_period = min(
        10000,
        max(100, int(os.getenv("MAX_REPORT_CALLS_PERIOD_ROWS", "2500"))),
    )
    calls_period = calls_q.limit(max_calls_period).all()

    filtros = []
    if start_dt:
        filtros.append(LlamadaCandidata.fecha_llamada >= start_dt_dt)

    calls_by_day = (
        db.session.query(
            func.date_trunc("day", LlamadaCandidata.fecha_llamada).label("periodo"),
            func.count().label("cnt"),
        )
        .filter(*filtros)
        .group_by("periodo")
        .order_by("periodo")
        .all()
    )
    calls_by_week = (
        db.session.query(
            func.date_trunc("week", LlamadaCandidata.fecha_llamada).label("periodo"),
            func.count().label("cnt"),
        )
        .filter(*filtros)
        .group_by("periodo")
        .order_by("periodo")
        .all()
    )
    calls_by_month = (
        db.session.query(
            func.date_trunc("month", LlamadaCandidata.fecha_llamada).label("periodo"),
            func.count().label("cnt"),
        )
        .filter(*filtros)
        .group_by("periodo")
        .order_by("periodo")
        .all()
    )

    return render_template(
        "reporte_llamadas.html",
        period=period,
        start_date=start_date_str,
        hoy=hoy,
        estancadas_en_proceso=estancadas_en_proceso,
        estancadas_inscripcion=estancadas_inscripcion,
        estancadas_lista=estancadas_lista,
        promedio=promedio,
        calls_period=calls_period,
        calls_by_day=calls_by_day,
        calls_by_week=calls_by_week,
        calls_by_month=calls_by_month,
    )
