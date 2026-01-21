from datetime import datetime

from flask import abort, current_app, flash, redirect, render_template, request, url_for, session
from sqlalchemy import or_
from flask_login import current_user
from functools import wraps

def _get_user_role():
    """
    Devuelve el rol del usuario autenticado.
    Soporta ambos nombres de campo comunes: role / rol.
    """
    return getattr(current_user, "role", None) or getattr(current_user, "rol", None)

def roles_required(*roles):
    """
    Requiere que el usuario esté autenticado y tenga uno de los roles permitidos.
    - Si no está autenticado: redirige a admin.login con next
    - Si no tiene rol permitido: 404
    """
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("admin.login", next=request.full_path))
            user_role = _get_user_role()
            if user_role not in roles:
                abort(404)
            return f(*args, **kwargs)
        return wrapped
    return decorator
from . import webadmin_bp
from models import Candidata, CandidataWeb, db

# Roles permitidos para entrar a WEBADMIN
WEBADMIN_ALLOWED_ROLES = ("admin", "secretaria")


@webadmin_bp.before_request
def _webadmin_guard():
    """
    Protección global del blueprint webadmin:
    - Si no hay sesión: redirige a admin.login (con next)
    - Si el rol no está permitido: 404 (mejor que 403 para no revelar existencia)
    """
    # Permite que flask sirva los endpoints internos sin romper
    if request.endpoint is None:
        return None

    # Solo aplica a este blueprint (defensivo)
    if not request.endpoint.startswith("webadmin."):
        return None

    # Requiere autenticación
    if not current_user.is_authenticated:
        # Guardamos destino para volver luego
        return redirect(url_for("admin.login", next=request.full_path))

    # Requiere rol permitido
    role = _get_user_role()
    if role not in WEBADMIN_ALLOWED_ROLES:
        # 404 para no filtrar que existe un panel interno
        abort(404)

    return None

# ─────────────────────────────────────────────────────────────
# ADMIN / SECRETARÍA – GESTIÓN DE CANDIDATAS PARA LA WEB
# ─────────────────────────────────────────────────────────────

@webadmin_bp.route('/candidatas_web', methods=['GET', 'POST'])
@roles_required('admin')
def listar_candidatas_web():

    # Paginación (evita cargar miles de filas de golpe)
    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        page = 1
    page = max(page, 1)

    try:
        per_page = int(request.args.get("per_page", "30"))
    except ValueError:
        per_page = 30
    per_page = min(max(per_page, 10), 100)  # entre 10 y 100

    q = (request.form.get('q') or request.args.get('q') or '').strip()[:120]

    # BASE: TODAS las candidatas
    query = (
        db.session.query(Candidata, CandidataWeb)
        .outerjoin(CandidataWeb, Candidata.fila == CandidataWeb.candidata_id)
    )

    # 🔍 BÚSQUEDA SOLO EN CANDIDATA (donde están los datos reales)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Candidata.nombre_completo.ilike(like),
                Candidata.cedula.ilike(like),
                Candidata.codigo.ilike(like),
                Candidata.numero_telefono.ilike(like),
            )
        )

    # Orden limpio y lógico
    query = query.order_by(
        Candidata.nombre_completo.asc()
    )

    # Total (para controles de paginación). Ojo: count en join puede ser pesado,
    # pero aquí es aceptable porque ya no traemos todo el dataset.
    try:
        total = query.count()
    except Exception:
        total = 0

    resultados = (
        query
        .limit(per_page)
        .offset((page - 1) * per_page)
        .all()
    )  # [(candidata, ficha_web), ...]

    has_prev = page > 1
    has_next = (page * per_page) < total if total else (len(resultados) == per_page)

    return render_template(
        'webadmin/candidatas_web_list.html',
        resultados=resultados,
        q=q,
        page=page,
        per_page=per_page,
        total=total,
        has_prev=has_prev,
        has_next=has_next,
    )


@webadmin_bp.route('/candidatas_web/<int:fila>/editar', methods=['GET', 'POST'])
@roles_required('admin')
def editar_candidata_web(fila):
    """
    Editar la ficha pública de una candidata:
    - Lee los datos internos desde Candidata (nombre_completo, etc.).
    - Guarda todo lo que es de la web en CandidataWeb.
    """
    # 'fila' = identificador interno de la candidata
    cand = Candidata.query.filter_by(fila=fila).first_or_404()

    # Buscar ficha web; si no existe, crearla en memoria
    ficha = CandidataWeb.query.filter_by(candidata_id=cand.fila).first()
    if not ficha:
        ficha = CandidataWeb(
            candidata_id=cand.fila,
            visible=True,
            estado_publico='disponible',
        )
        db.session.add(ficha)
        db.session.flush()  # la deja lista sin hacer commit todavía

    if request.method == 'POST':
        # Checkboxes
        ficha.visible = bool(request.form.get('visible'))
        ficha.es_destacada = bool(request.form.get('es_destacada'))
        ficha.disponible_inmediato = bool(request.form.get('disponible_inmediato'))

        # Estado público (select con opciones válidas)
        estado = (request.form.get('estado_publico') or '').strip()
        if estado in ['disponible', 'reservada', 'no_disponible']:
            ficha.estado_publico = estado

        # Orden manual
        orden_raw = (request.form.get('orden_lista') or '').strip()
        if orden_raw:
            try:
                ficha.orden_lista = int(orden_raw)
            except ValueError:
                flash("⚠️ El orden debe ser un número entero.", "warning")
        else:
            ficha.orden_lista = None

        # Textos públicos
        ficha.nombre_publico = (request.form.get('nombre_publico') or '').strip()[:200] or None
        ficha.edad_publica = (request.form.get('edad_publica') or '').strip()[:50] or None
        ficha.ciudad_publica = (request.form.get('ciudad_publica') or '').strip()[:120] or None
        ficha.sector_publico = (request.form.get('sector_publico') or '').strip()[:120] or None
        ficha.modalidad_publica = (request.form.get('modalidad_publica') or '').strip()[:120] or None
        ficha.tipo_servicio_publico = (request.form.get('tipo_servicio_publico') or '').strip()[:50] or None
        ficha.anos_experiencia_publicos = (request.form.get('anos_experiencia_publicos') or '').strip()[:50] or None

        ficha.experiencia_resumen = (request.form.get('experiencia_resumen') or '').strip() or None
        ficha.experiencia_detallada = (request.form.get('experiencia_detallada') or '').strip() or None
        ficha.tags_publicos = (request.form.get('tags_publicos') or '').strip()[:255] or None
        ficha.frase_destacada = (request.form.get('frase_destacada') or '').strip()[:200] or None

        # Sueldo y foto
        sueldo_desde_raw = (request.form.get('sueldo_desde') or '').strip()
        sueldo_hasta_raw = (request.form.get('sueldo_hasta') or '').strip()

        ficha.sueldo_desde = int(sueldo_desde_raw) if sueldo_desde_raw.isdigit() else None
        ficha.sueldo_hasta = int(sueldo_hasta_raw) if sueldo_hasta_raw.isdigit() else None

        ficha.sueldo_texto_publico = (request.form.get('sueldo_texto_publico') or '').strip()[:120] or None
        ficha.foto_publica_url = (request.form.get('foto_publica_url') or '').strip()[:255] or None

        # Fecha de publicación la primera vez que se marca visible
        if ficha.visible and ficha.fecha_publicacion is None:
            ficha.fecha_publicacion = datetime.utcnow()

        try:
            db.session.commit()
            flash("✅ Ficha para la web actualizada correctamente.", "success")
            # Para que no se ponga lento, volvemos a la misma ficha en vez de cargar el listado completo
            return redirect(url_for('webadmin.editar_candidata_web', fila=cand.fila))
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Error guardando ficha web de candidata")
            flash("❌ Ocurrió un error guardando los cambios.", "danger")

    return render_template(
        'webadmin/candidata_web_form.html',
        cand=cand,
        ficha=ficha,
    )