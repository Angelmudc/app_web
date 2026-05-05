# -*- coding: utf-8 -*-
from __future__ import annotations

from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import Integer, and_, cast, func, or_
from sqlalchemy.orm import joinedload, load_only
from urllib.parse import urlencode
from datetime import datetime

from config_app import db
from decorators import roles_required
from utils.timezone import format_rd_datetime, rd_today
from utils.envejeciente import format_envejeciente_resumen

from core import legacy_handlers as legacy_h

try:
    from forms import AdminSolicitudForm
except Exception:
    AdminSolicitudForm = None


def _as_list(val):
    if val is None:
        return []
    if isinstance(val, (list, tuple, set)):
        return list(val)
    try:
        return [x.strip() for x in str(val).split(",") if x.strip()]
    except Exception:
        return []


def _fmt_banos(v):
    if v is None or v == "":
        return ""
    return str(v).rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def _norm_area(s):
    txt = (s or "").strip()
    if txt.lower() in {"otro", "otro...", "otro…"}:
        return ""
    return txt


def _normalize_modalidad_publicar(value):
    txt = (value or "").strip()
    if not txt:
        return ""
    low = txt.lower()
    if "viernes a lunes" in low:
        if "dormida" in low or "interna" in low:
            return "Con dormida 💤 fin de semana"
        if "salida diaria" in low:
            return "Salida diaria - fin de semana"
    if low.startswith("con dormida") and "💤" not in txt:
        rest = txt[len("con dormida") :].strip()
        return f"Con dormida 💤 {rest}".strip()
    return txt


def _s(v):
    return "" if v is None else str(v).strip()


def _funciones_choices_map():
    default_choices = {
        "limpieza": "Limpieza general",
        "cocinar": "Cocinar",
        "lavar": "Lavar",
        "planchar": "Planchar",
        "ninos": "Cuidar niños",
        "envejeciente": "Cuidar envejeciente",
        "otro": "Otro",
    }
    funciones_choices = {}
    try:
        form = AdminSolicitudForm() if AdminSolicitudForm else None
        if form and hasattr(form, "funciones") and hasattr(form.funciones, "choices"):
            funciones_choices = dict(form.funciones.choices)
    except Exception:
        funciones_choices = {}
    for code, label in default_choices.items():
        funciones_choices.setdefault(code, label)
    return funciones_choices


def _solicitud_load_only_cols():
    names = (
        "id",
        "fecha_solicitud",
        "codigo_solicitud",
        "ciudad_sector",
        "rutas_cercanas",
        "modalidad_trabajo",
        "modalidad",
        "tipo_modalidad",
        "edad_requerida",
        "experiencia",
        "horario",
        "funciones",
        "funciones_otro",
        "adultos",
        "ninos",
        "edades_ninos",
        "mascota",
        "tipo_lugar",
        "habitaciones",
        "banos",
        "dos_pisos",
        "areas_comunes",
        "area_otro",
        "direccion",
        "sueldo",
        "pasaje_aporte",
        "nota_cliente",
        "last_copiado_at",
        "estado",
    )
    cols = []
    for name in names:
        col = getattr(legacy_h.Solicitud, name, None)
        if col is not None:
            cols.append(col)
    return tuple(cols)


def _build_copy_order_item(s, funciones_choices):
    funcs = []
    try:
        seleccion = set(_as_list(getattr(s, "funciones", None)))
    except Exception:
        seleccion = set()
    for code in seleccion:
        if code == "otro":
            continue
        label = funciones_choices.get(code)
        if label:
            funcs.append(label)
    custom_otro = (getattr(s, "funciones_otro", None) or "").strip()
    if custom_otro:
        funcs.append(custom_otro)

    adultos = s.adultos or ""
    ninos_line = ""
    if getattr(s, "ninos", None):
        ninos_line = f"Niños: {s.ninos}"
        if getattr(s, "edades_ninos", None):
            ninos_line += f" ({s.edades_ninos})"
    mascota_val = (getattr(s, "mascota", None) or "").strip()
    mascota_line = f"Mascota: {mascota_val}" if mascota_val else ""

    modalidad_val = (
        getattr(s, "modalidad_trabajo", None)
        or getattr(s, "modalidad", None)
        or getattr(s, "tipo_modalidad", None)
        or ""
    )
    modalidad_val = _normalize_modalidad_publicar(modalidad_val)

    hogar_partes = []
    if getattr(s, "habitaciones", None):
        hogar_partes.append(f"{s.habitaciones} habitaciones")
    banos_txt = _fmt_banos(getattr(s, "banos", None))
    if banos_txt:
        hogar_partes.append(f"{banos_txt} baños")
    if bool(getattr(s, "dos_pisos", False)):
        hogar_partes.append("2 pisos")

    areas = []
    if getattr(s, "areas_comunes", None):
        try:
            for a in s.areas_comunes:
                a = str(a).strip()
                if a:
                    area_norm = _norm_area(a)
                    if area_norm:
                        areas.append(area_norm)
        except Exception:
            pass
    area_otro = (getattr(s, "area_otro", None) or "").strip()
    if area_otro:
        area_norm = _norm_area(area_otro)
        if area_norm:
            areas.append(area_norm)
    if areas:
        hogar_partes.append(", ".join(areas))

    tipo_lugar = (getattr(s, "tipo_lugar", "") or "").strip()
    if tipo_lugar and hogar_partes:
        hogar_descr = f"{tipo_lugar} - {', '.join(hogar_partes)}"
    elif tipo_lugar:
        hogar_descr = tipo_lugar
    else:
        hogar_descr = ", ".join(hogar_partes)
    hogar_val = hogar_descr.strip() if hogar_descr else ""

    if isinstance(s.edad_requerida, (list, tuple, set)):
        edad_req = ", ".join([str(x).strip() for x in s.edad_requerida if str(x).strip()])
    else:
        edad_req = s.edad_requerida or ""

    nota_cli = (s.nota_cliente or "").strip()
    nota_line = f"Nota: {nota_cli}" if nota_cli else ""
    envejeciente_lines = format_envejeciente_resumen(
        tipo_cuidado=getattr(s, "envejeciente_tipo_cuidado", None),
        responsabilidades=getattr(s, "envejeciente_responsabilidades", None),
        solo_acompanamiento=getattr(s, "envejeciente_solo_acompanamiento", False),
        nota=getattr(s, "envejeciente_nota", None),
    )
    sueldo_txt = (
        f"Sueldo: ${_s(s.sueldo)} mensual"
        f"{', más ayuda del pasaje' if bool(getattr(s, 'pasaje_aporte', False)) else ', pasaje incluido'}"
    )

    lines = [
        f"Disponible ( {s.codigo_solicitud or ''} )",
        f"📍 {s.ciudad_sector or ''}",
        f"Ruta más cercana: {s.rutas_cercanas or ''}",
        "",
    ]
    if modalidad_val:
        lines += [modalidad_val, ""]

    lines += [
        f"Edad: {edad_req}",
        "Dominicana",
        "Que sepa leer y escribir",
        f"Experiencia en: {s.experiencia or ''}",
        f"Horario: {s.horario or ''}",
        "",
        f"Funciones: {', '.join(funcs)}" if funcs else "Funciones: ",
    ]
    if hogar_val:
        lines += ["", hogar_val]
    if envejeciente_lines:
        lines += [""] + envejeciente_lines

    lines += ["", f"Adultos: {adultos}"]
    if ninos_line:
        lines.append(ninos_line)
    if mascota_line:
        lines.append(mascota_line)
    lines += ["", sueldo_txt]
    if nota_line:
        lines += ["", nota_line]

    order_text = "\n".join(lines).strip()[:4000]
    return {
        "codigo_solicitud": _s(s.codigo_solicitud),
        "ciudad_sector": _s(s.ciudad_sector),
        "modalidad": modalidad_val,
        "funciones": ", ".join(funcs),
        "sueldo": _s(s.sueldo),
        "pasaje": "Sí" if bool(getattr(s, "pasaje_aporte", False)) else "No",
        "order_text": order_text,
    }


@roles_required("admin", "secretaria")
def secretarias_copiar_solicitudes():
    """
    Lista solicitudes copiables. En el texto:
    - NO imprime 'Modalidad:' ni 'Hogar:' como etiqueta.
    - Si hay modalidad, imprime SOLO el valor en una línea.
    - Si hay descripción de hogar, imprime SOLO la descripción (sin prefijo).
    """
    hoy = rd_today()

    base_q = (
        legacy_h.Solicitud.query.options(
            joinedload(legacy_h.Solicitud.reemplazos).joinedload(legacy_h.Reemplazo.candidata_new)
        )
        .filter(legacy_h.Solicitud.estado.in_(("activa", "reemplazo")))
        .filter(
            or_(
                legacy_h.Solicitud.last_copiado_at.is_(None),
                func.date(legacy_h.Solicitud.last_copiado_at) < hoy,
            )
        )
        .order_by(legacy_h.Solicitud.fecha_solicitud.desc())
    )

    try:
        raw_sols = base_q.limit(500).all()
    except Exception:
        current_app.logger.exception("❌ Error listando solicitudes copiables")
        raw_sols = []

    funciones_choices = _funciones_choices_map()

    solicitudes = []
    for s in raw_sols:
        item_base = _build_copy_order_item(s, funciones_choices)

        solicitudes.append(
            {
                "id": s.id,
                "codigo_solicitud": item_base["codigo_solicitud"],
                "ciudad_sector": item_base["ciudad_sector"],
                "modalidad": item_base["modalidad"],
                "copiada_hoy": False,
                "order_text": item_base["order_text"],
            }
        )

    return render_template(
        "secretarias_solicitudes_copiar.html",
        solicitudes=solicitudes,
        q="",
        q_enabled=False,
        endpoint="secretarias_copiar_solicitudes",
    )


@roles_required("admin", "secretaria")
def secretarias_copiar_solicitud(id):
    s = legacy_h.Solicitud.query.get_or_404(id)
    try:
        s.last_copiado_at = func.now()
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("❌ Error marcando solicitud copiada")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": False, "error": "No se pudo marcar como copiada"}), 500
        flash("❌ No se pudo marcar la solicitud como copiada.", "danger")
        return redirect(url_for("secretarias_copiar_solicitudes"))

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True, "id": id, "codigo": _s(s.codigo_solicitud)}), 200

    flash(f"Solicitud {_s(s.codigo_solicitud)} copiada. Ya no se mostrará hasta mañana.", "success")
    return redirect(url_for("secretarias_copiar_solicitudes"))


@roles_required("admin", "secretaria")
def secretarias_buscar_solicitudes():
    q = (request.args.get("q") or "").strip()[:128]
    estado = (request.args.get("estado") or "").strip()[:20]
    desde_str = (request.args.get("desde") or "").strip()[:10]
    hasta_str = (request.args.get("hasta") or "").strip()[:10]
    modalidad = (request.args.get("modalidad") or "").strip()[:60]
    mascota = (request.args.get("mascota") or "").strip()[:3]
    con_ninos = (request.args.get("con_ninos") or "").strip()[:3]
    page = max(1, request.args.get("page", type=int, default=1))
    per_page = min(100, max(10, request.args.get("per_page", type=int, default=20)))

    cols = _solicitud_load_only_cols()

    qy = db.session.query(legacy_h.Solicitud).options(load_only(*cols)).execution_options(stream_results=True)

    if q:
        like = f"%{q}%"
        qy = qy.filter(
            or_(
                legacy_h.Solicitud.codigo_solicitud.ilike(like),
                legacy_h.Solicitud.ciudad_sector.ilike(like),
            )
        )

    if estado:
        qy = qy.filter(legacy_h.Solicitud.estado == estado)
    if modalidad:
        qy = qy.filter(
            or_(
                legacy_h.Solicitud.modalidad_trabajo.ilike(f"%{modalidad}%"),
                getattr(legacy_h.Solicitud, "modalidad", legacy_h.Solicitud.modalidad_trabajo).ilike(f"%{modalidad}%"),
                getattr(legacy_h.Solicitud, "tipo_modalidad", legacy_h.Solicitud.modalidad_trabajo).ilike(f"%{modalidad}%"),
            )
        )

    if mascota == "si":
        qy = qy.filter(
            legacy_h.Solicitud.mascota.isnot(None),
            func.length(func.trim(legacy_h.Solicitud.mascota)) > 0,
        )
    elif mascota == "no":
        qy = qy.filter(
            or_(
                legacy_h.Solicitud.mascota.is_(None),
                func.length(func.trim(legacy_h.Solicitud.mascota)) == 0,
            )
        )

    if con_ninos == "si":
        qy = qy.filter(legacy_h.Solicitud.ninos.isnot(None), legacy_h.Solicitud.ninos > 0)
    elif con_ninos == "no":
        qy = qy.filter(or_(legacy_h.Solicitud.ninos.is_(None), legacy_h.Solicitud.ninos == 0))

    def _parse_date(s):
        try:
            return datetime.strptime(s, "%Y-%m-%d")
        except Exception:
            return None

    desde_dt = _parse_date(desde_str)
    hasta_dt = _parse_date(hasta_str)
    if desde_dt and hasta_dt:
        hasta_end = hasta_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
        qy = qy.filter(
            and_(
                legacy_h.Solicitud.fecha_solicitud >= desde_dt,
                legacy_h.Solicitud.fecha_solicitud <= hasta_end,
            )
        )
    elif desde_dt:
        qy = qy.filter(legacy_h.Solicitud.fecha_solicitud >= desde_dt)
    elif hasta_dt:
        hasta_end = hasta_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
        qy = qy.filter(legacy_h.Solicitud.fecha_solicitud <= hasta_end)

    order_col = getattr(legacy_h.Solicitud, "fecha_solicitud", None) or legacy_h.Solicitud.id
    qy = qy.order_by(order_col.desc())

    try:
        paginado = qy.paginate(page=page, per_page=per_page, error_out=False)
    except AttributeError:
        paginado = db.paginate(qy, page=page, per_page=per_page, error_out=False)

    funciones_choices = {}
    try:
        form = AdminSolicitudForm() if AdminSolicitudForm else None
        if form and hasattr(form, "funciones") and hasattr(form.funciones, "choices"):
            funciones_choices = dict(form.funciones.choices)
    except Exception:
        funciones_choices = {}

    items = []
    for s in paginado.items:
        modalidad_val = _normalize_modalidad_publicar((s.modalidad_trabajo or s.modalidad or s.tipo_modalidad or ""))

        funcs = []
        try:
            seleccion = set(_as_list(getattr(s, "funciones", None)))
        except Exception:
            seleccion = set()
        for code in seleccion:
            if code == "otro":
                continue
            label = funciones_choices.get(code)
            if label:
                funcs.append(label)
        custom_otro = (getattr(s, "funciones_otro", None) or "").strip()
        if custom_otro:
            funcs.append(custom_otro)

        adultos = s.adultos or ""
        ninos_line = ""
        if getattr(s, "ninos", None):
            ninos_line = f"Niños: {s.ninos}"
            if getattr(s, "edades_ninos", None):
                ninos_line += f" ({s.edades_ninos})"
        mascota_val = (getattr(s, "mascota", None) or "").strip()
        mascota_line = f"Mascota: {mascota_val}" if mascota_val else ""

        hogar_partes = []
        if getattr(s, "habitaciones", None):
            hogar_partes.append(f"{s.habitaciones} habitaciones")
        banos_txt = _fmt_banos(getattr(s, "banos", None))
        if banos_txt:
            hogar_partes.append(f"{banos_txt} baños")
        if bool(getattr(s, "dos_pisos", False)):
            hogar_partes.append("2 pisos")
        areas = []
        if getattr(s, "areas_comunes", None):
            try:
                for a in s.areas_comunes:
                    a = str(a).strip()
                    if a:
                        area_norm = _norm_area(a)
                        if area_norm:
                            areas.append(area_norm)
            except Exception:
                pass
        area_otro = (getattr(s, "area_otro", None) or "").strip()
        if area_otro:
            area_norm = _norm_area(area_otro)
            if area_norm:
                areas.append(area_norm)
        if areas:
            hogar_partes.append(", ".join(areas))
        tipo_lugar = (getattr(s, "tipo_lugar", "") or "").strip()
        if tipo_lugar and hogar_partes:
            hogar_descr = f"{tipo_lugar} - {', '.join(hogar_partes)}"
        elif tipo_lugar:
            hogar_descr = tipo_lugar
        else:
            hogar_descr = ", ".join(hogar_partes)
        hogar_val = hogar_descr.strip() if hogar_descr else ""

        if isinstance(s.edad_requerida, (list, tuple, set)):
            edad_req = ", ".join([str(x).strip() for x in s.edad_requerida if str(x).strip()])
        else:
            edad_req = s.edad_requerida or ""

        nota_cli = (s.nota_cliente or "").strip()
        nota_line = f"Nota: {nota_cli}" if nota_cli else ""
        sueldo_txt = (
            f"Sueldo: ${_s(s.sueldo)} mensual"
            f"{', más ayuda del pasaje' if bool(getattr(s, 'pasaje_aporte', False)) else ', pasaje incluido'}"
        )

        lines = [
            f"Disponible ( {s.codigo_solicitud or ''} )",
            f"📍 {s.ciudad_sector or ''}",
            f"Ruta más cercana: {s.rutas_cercanas or ''}",
            "",
        ]
        if modalidad_val:
            lines += [modalidad_val, ""]

        lines += [
            f"Edad: {edad_req}",
            "Dominicana",
            "Que sepa leer y escribir",
            f"Experiencia en: {s.experiencia or ''}",
            f"Horario: {s.horario or ''}",
            "",
            f"Funciones: {', '.join(funcs)}" if funcs else "Funciones: ",
        ]
        if hogar_val:
            lines += ["", hogar_val]

        lines += ["", f"Adultos: {adultos}"]
        if ninos_line:
            lines.append(ninos_line)
        if mascota_line:
            lines.append(mascota_line)
        lines += ["", sueldo_txt]
        if nota_line:
            lines += ["", nota_line]

        order_text = "\n".join(lines).strip()[:4000]

        items.append(
            {
                "id": s.id,
                "codigo_solicitud": _s(s.codigo_solicitud),
                "ciudad_sector": _s(s.ciudad_sector),
                "modalidad": modalidad_val,
                "estado": _s(s.estado),
                "fecha_solicitud": (
                    format_rd_datetime(s.fecha_solicitud, "%Y-%m-%d %H:%M", "") if s.fecha_solicitud else ""
                ),
                "copiada_ciclo": (s.last_copiado_at is not None),
                "order_text": order_text,
            }
        )

    current_params = request.args.to_dict(flat=True)

    def page_url(p):
        d = current_params.copy()
        d["page"] = p
        return url_for("secretarias_buscar_solicitudes") + ("?" + urlencode(d) if d else "")

    total_pages = paginado.pages or 1
    page_links = [{"n": p, "url": page_url(p), "active": (p == paginado.page)} for p in range(1, total_pages + 1)]
    prev_url = page_url(paginado.page - 1) if paginado.page > 1 else None
    next_url = page_url(paginado.page + 1) if paginado.page < total_pages else None

    return render_template(
        "secretarias_solicitudes_buscar.html",
        items=items,
        page=paginado.page,
        pages=total_pages,
        total=paginado.total,
        per_page=per_page,
        q=q,
        estado=estado,
        estados_opts=["proceso", "activa", "pagada", "cancelada", "reemplazo"],
        desde=desde_str,
        hasta=hasta_str,
        modalidad=modalidad,
        mascota=mascota,
        con_ninos=con_ninos,
        page_links=page_links,
        prev_url=prev_url,
        next_url=next_url,
    )


@roles_required("admin", "secretaria")
def secretarias_filtrar_solicitudes():
    ciudad_sector = (request.args.get("ciudad_sector") or "").strip()[:200]
    ruta = (request.args.get("ruta") or "").strip()[:200]
    raw_funciones = (request.args.getlist("funciones") or []) + (request.args.getlist("funciones[]") or [])
    funciones = [str(v or "").strip().lower() for v in raw_funciones if str(v or "").strip()]
    # Evita duplicados manteniendo orden para filtros OR previsibles.
    funciones = list(dict.fromkeys(funciones))
    experiencia = (request.args.get("experiencia") or "").strip()[:500]
    pasaje = (request.args.get("pasaje") or "").strip().lower()
    modalidad = (request.args.get("modalidad") or "").strip().lower()
    pisos = (request.args.get("pisos") or "").strip()
    tipo_casa = (request.args.get("tipo_casa") or "").strip().lower()
    page = max(1, request.args.get("page", type=int, default=1))
    per_page = 20

    sueldo_min = request.args.get("sueldo_min", type=int)
    sueldo_max = request.args.get("sueldo_max", type=int)
    if sueldo_min is not None and sueldo_min < 0:
        sueldo_min = None
    if sueldo_max is not None and sueldo_max < 0:
        sueldo_max = None

    has_useful_filters = any([
        bool(ciudad_sector),
        bool(ruta),
        bool(funciones),
        bool(experiencia),
        sueldo_min is not None,
        sueldo_max is not None,
        pasaje in {"si", "no"},
        modalidad in {"salida_diaria", "con_dormida"},
        pisos in {"1", "2"},
        tipo_casa in {"pequena", "normal", "grande"},
    ])

    funciones_opts = []
    try:
        funciones_opts = sorted(list((_funciones_choices_map() or {}).items()), key=lambda x: str(x[1] or x[0]))
    except Exception:
        funciones_opts = []

    if not has_useful_filters:
        return render_template(
            "secretarias_solicitudes_buscar.html",
            items=[],
            page=1,
            pages=1,
            total=0,
            per_page=per_page,
            q="",
            estado="",
            estados_opts=["proceso", "activa", "pagada", "cancelada", "reemplazo"],
            desde="",
            hasta="",
            modalidad="",
            mascota="",
            con_ninos="",
            page_links=[{"n": 1, "url": url_for("secretarias_filtrar_solicitudes"), "active": True}],
            prev_url=None,
            next_url=None,
            endpoint="secretarias_filtrar_solicitudes",
            empty_state_message="Aplica filtros para ver resultados",
            filtros_aplicados=False,
            filtro_vals={
                "ciudad_sector": ciudad_sector,
                "ruta": ruta,
                "funciones": funciones,
                "experiencia": experiencia,
                "sueldo_min": sueldo_min if sueldo_min is not None else "",
                "sueldo_max": sueldo_max if sueldo_max is not None else "",
                "pasaje": pasaje,
                "modalidad": modalidad,
                "pisos": pisos,
                "tipo_casa": tipo_casa,
            },
            funciones_opts=funciones_opts,
        )

    cols = _solicitud_load_only_cols()
    qy = db.session.query(legacy_h.Solicitud).options(load_only(*cols)).execution_options(stream_results=True)
    # Filtro base obligatorio: solo solicitudes activas.
    qy = qy.filter(legacy_h.Solicitud.estado == "activa")

    if ciudad_sector:
        qy = qy.filter(legacy_h.Solicitud.ciudad_sector.ilike(f"%{ciudad_sector}%"))
    if ruta:
        qy = qy.filter(legacy_h.Solicitud.rutas_cercanas.ilike(f"%{ruta}%"))
    if funciones:
        funciones_predicates = [legacy_h.Solicitud.funciones.any(fcode) for fcode in funciones]
        if "otro" in funciones:
            funciones_predicates.append(legacy_h.Solicitud.funciones_otro.isnot(None))
            funciones_predicates.append(func.length(func.trim(legacy_h.Solicitud.funciones_otro)) > 0)
        qy = qy.filter(or_(*funciones_predicates))
    if experiencia:
        qy = qy.filter(legacy_h.Solicitud.experiencia.ilike(f"%{experiencia}%"))
    if sueldo_min is not None:
        qy = qy.filter(cast(func.nullif(legacy_h.Solicitud.sueldo, ""), Integer) >= sueldo_min)
    if sueldo_max is not None:
        qy = qy.filter(cast(func.nullif(legacy_h.Solicitud.sueldo, ""), Integer) <= sueldo_max)
    if pasaje == "si":
        qy = qy.filter(legacy_h.Solicitud.pasaje_aporte.is_(True))
    elif pasaje == "no":
        qy = qy.filter(or_(legacy_h.Solicitud.pasaje_aporte.is_(False), legacy_h.Solicitud.pasaje_aporte.is_(None)))
    if modalidad == "salida_diaria":
        qy = qy.filter(legacy_h.Solicitud.modalidad_trabajo.ilike("%salida diaria%"))
    elif modalidad == "con_dormida":
        qy = qy.filter(
            or_(
                legacy_h.Solicitud.modalidad_trabajo.ilike("%con dormida%"),
                legacy_h.Solicitud.modalidad_trabajo.ilike("%dormida%"),
                legacy_h.Solicitud.modalidad_trabajo.ilike("%interna%"),
            )
        )
    if pisos == "1":
        qy = qy.filter(legacy_h.Solicitud.dos_pisos.is_(False))
    elif pisos == "2":
        qy = qy.filter(legacy_h.Solicitud.dos_pisos.is_(True))
    if tipo_casa in {"pequena", "normal", "grande"}:
        house_filter = None
        if tipo_casa == "pequena":
            house_filter = and_(legacy_h.Solicitud.habitaciones <= 2, legacy_h.Solicitud.banos <= 2)
        elif tipo_casa == "normal":
            house_filter = and_(legacy_h.Solicitud.habitaciones == 3, legacy_h.Solicitud.banos <= 3)
        elif tipo_casa == "grande":
            house_filter = or_(legacy_h.Solicitud.habitaciones >= 4, legacy_h.Solicitud.banos >= 4)
        qy = qy.filter(legacy_h.Solicitud.funciones.any("limpieza"), house_filter)

    order_col = getattr(legacy_h.Solicitud, "fecha_solicitud", None) or legacy_h.Solicitud.id
    qy = qy.order_by(order_col.desc())

    try:
        paginado = qy.paginate(page=page, per_page=per_page, error_out=False)
    except AttributeError:
        paginado = db.paginate(qy, page=page, per_page=per_page, error_out=False)

    funciones_choices = _funciones_choices_map()
    items = []
    for s in paginado.items:
        base = _build_copy_order_item(s, funciones_choices)
        items.append(
            {
                "id": s.id,
                "codigo_solicitud": base["codigo_solicitud"],
                "ciudad_sector": base["ciudad_sector"],
                "modalidad": base["modalidad"],
                "estado": _s(s.estado),
                "fecha_solicitud": (
                    format_rd_datetime(s.fecha_solicitud, "%Y-%m-%d %H:%M", "") if s.fecha_solicitud else ""
                ),
                "copiada_ciclo": (s.last_copiado_at is not None),
                "order_text": base["order_text"],
                "funciones_principales": base["funciones"],
                "sueldo_valor": base["sueldo"],
                "pasaje_label": base["pasaje"],
                "ruta": _s(getattr(s, "rutas_cercanas", "")),
                "experiencia": _s(getattr(s, "experiencia", "")),
                "tipo_lugar": _s(getattr(s, "tipo_lugar", "")),
                "habitaciones": _s(getattr(s, "habitaciones", "")),
                "banos": _fmt_banos(getattr(s, "banos", None)),
                "pisos_label": ("2 niveles" if bool(getattr(s, "dos_pisos", False)) else "1 nivel"),
                "adultos": _s(getattr(s, "adultos", "")),
                "ninos": _s(getattr(s, "ninos", "")),
                "copy_action_endpoint": "secretarias_copiar_solicitud",
            }
        )

    current_params = request.args.to_dict(flat=True)

    def page_url(p):
        d = current_params.copy()
        d["page"] = p
        return url_for("secretarias_filtrar_solicitudes") + ("?" + urlencode(d) if d else "")

    total_pages = paginado.pages or 1
    page_links = [{"n": p, "url": page_url(p), "active": (p == paginado.page)} for p in range(1, total_pages + 1)]
    prev_url = page_url(paginado.page - 1) if paginado.page > 1 else None
    next_url = page_url(paginado.page + 1) if paginado.page < total_pages else None

    return render_template(
        "secretarias_solicitudes_buscar.html",
        items=items,
        page=paginado.page,
        pages=total_pages,
        total=paginado.total,
        per_page=per_page,
        q="",
        estado="",
        estados_opts=["proceso", "activa", "pagada", "cancelada", "reemplazo"],
        desde="",
        hasta="",
        modalidad="",
        mascota="",
        con_ninos="",
        page_links=page_links,
        prev_url=prev_url,
        next_url=next_url,
        endpoint="secretarias_filtrar_solicitudes",
        empty_state_message="",
        filtros_aplicados=True,
        filtro_vals={
            "ciudad_sector": ciudad_sector,
            "ruta": ruta,
            "funciones": funciones,
            "experiencia": experiencia,
            "sueldo_min": sueldo_min if sueldo_min is not None else "",
            "sueldo_max": sueldo_max if sueldo_max is not None else "",
            "pasaje": pasaje,
            "modalidad": modalidad,
            "pisos": pisos,
            "tipo_casa": tipo_casa,
        },
        funciones_opts=funciones_opts,
    )
