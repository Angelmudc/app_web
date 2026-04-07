from flask import abort, current_app, flash, redirect, render_template, request, url_for, session, send_file
from sqlalchemy import or_, and_, func
import re
import unicodedata
from flask_login import current_user
from functools import wraps
from utils.timezone import utc_now_naive


def _get_user_role():
    """Devuelve el rol del usuario autenticado.
    Soporta ambos nombres de campo comunes: role / rol.
    """
    role = getattr(current_user, "role", None) or getattr(current_user, "rol", None) or ""
    return str(role).strip().lower()


def roles_required(*roles):
    """Requiere que el usuario esté autenticado y tenga uno de los roles permitidos.
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
from utils.candidata_readiness import candidata_is_ready_to_send
from utils.guards import candidata_esta_descalificada, candidatas_activas_filter

# Roles permitidos para entrar a WEBADMIN
WEBADMIN_ALLOWED_ROLES = ("owner", "admin", "secretaria")

# ─────────────────────────────────────────────────────────────
# Helpers de búsqueda (igual filosofía que app.py)
# ─────────────────────────────────────────────────────────────
CODIGO_PATTERN = re.compile(r'^[A-Z]{3}-\d{6}$')

# Palabras comunes que NO ayudan a filtrar (evita falsos negativos)
STOPWORDS_NOMBRES = {"de", "del", "la", "las", "los", "y"}


def _strip_accents_py(s: str) -> str:
    if not s:
        return ''
    nfkd = unicodedata.normalize('NFKD', s)
    return ''.join(c for c in nfkd if unicodedata.category(c) != 'Mn')


def normalize_query_text(raw: str) -> str:
    s = (raw or '').strip()
    if not s:
        return ''
    s = s.replace(',', ' ').replace('.', ' ').replace(';', ' ').replace(':', ' ')
    s = s.replace('\n', ' ').replace('\t', ' ')
    s = _strip_accents_py(s).lower()
    s = re.sub(r"[^a-z0-9\s\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_digits(raw: str) -> str:
    return re.sub(r'\D', '', raw or '').strip()


def normalize_code(raw: str) -> str:
    return re.sub(r"\s+", "", (raw or '').strip().upper())


def _sql_name_norm(col):
    lowered = func.lower(col)
    translated = func.translate(
        lowered,
        'áàäâãéèëêíìïîóòöôõúùüûñ',
        'aaaaaeeeeiiiiooooouuuun'
    )
    cleaned = func.regexp_replace(translated, r"[^a-z0-9\s\-]", " ", "g")
    cleaned = func.regexp_replace(cleaned, r"[\s]+", " ", "g")
    return func.trim(cleaned)


def _sql_digits(col):
    return func.regexp_replace(col, r"\D", "", "g")


class SimplePagination:
    def __init__(self, page: int, per_page: int, total: int):
        self.page = max(int(page or 1), 1)
        self.per_page = int(per_page or 20)
        self.total = int(total or 0)

        self.pages = (self.total + self.per_page - 1) // self.per_page if self.per_page else 0
        self.has_prev = self.page > 1
        self.has_next = self.page < self.pages
        self.prev_num = self.page - 1 if self.has_prev else 1
        self.next_num = self.page + 1 if self.has_next else self.pages


@webadmin_bp.before_request
def _webadmin_guard():
    """Protección global del blueprint webadmin:
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


@webadmin_bp.route('/candidatas_web/<int:fila>/foto_perfil')
@roles_required(*WEBADMIN_ALLOWED_ROLES)
def candidata_foto_perfil(fila: int):
    """Devuelve la foto_perfil (LargeBinary) como imagen."""
    from io import BytesIO
    import imghdr

    cand = Candidata.query.filter_by(fila=fila).first_or_404()
    if candidata_esta_descalificada(cand):
        abort(404)
    blob = cand.foto_perfil

    if not blob:
        abort(404)

    kind = imghdr.what(None, h=blob)
    if kind == 'jpeg':
        mimetype = 'image/jpeg'
        ext = 'jpg'
    elif kind == 'png':
        mimetype = 'image/png'
        ext = 'png'
    elif kind == 'gif':
        mimetype = 'image/gif'
        ext = 'gif'
    elif kind == 'webp':
        mimetype = 'image/webp'
        ext = 'webp'
    else:
        mimetype = 'application/octet-stream'
        ext = 'bin'

    return send_file(
        BytesIO(blob),
        mimetype=mimetype,
        as_attachment=False,
        download_name=f"candidata_{fila}_perfil.{ext}",
        max_age=3600,
    )


# ─────────────────────────────────────────────────────────────
# ADMIN / SECRETARÍA – GESTIÓN DE CANDIDATAS PARA LA WEB
# ─────────────────────────────────────────────────────────────


@webadmin_bp.route('/candidatas_web', methods=['GET'])
@roles_required(*WEBADMIN_ALLOWED_ROLES)
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
    per_page = per_page if per_page in (10, 20, 50, 100) else 20

    q = (request.args.get('q') or '').strip()[:120]

    # BASE: TODAS las candidatas
    query = (
        db.session.query(Candidata, CandidataWeb)
        .outerjoin(CandidataWeb, Candidata.fila == CandidataWeb.candidata_id)
        .filter(
            candidatas_activas_filter(Candidata),
            Candidata.estado != "trabajando",
        )
    )

    # Si no hay búsqueda, NO cargamos candidatas (solo mostramos la barra de búsqueda)
    if not q:
        pagination = None
        return render_template(
            'webadmin/candidatas_web_list.html',
            resultados=[],
            q=q,
            page=page,
            per_page=per_page,
            total=0,
            has_prev=False,
            has_next=False,
            pagination=pagination,
        )

    # 🔍 BÚSQUEDA SOLO EN CANDIDATA (donde están los datos reales)
    if q:
        q_code = normalize_code(q)
        q_digits = normalize_digits(q)
        q_text = normalize_query_text(q)

        # 1) Código estricto (AAA-000000): si el usuario escribió un código válido,
        #    buscamos SOLO por ese código exacto.
        if CODIGO_PATTERN.fullmatch(q_code):
            query = query.filter(
                Candidata.codigo.isnot(None),
                func.trim(func.upper(Candidata.codigo)) == q_code
            )
        else:
            or_filters = []

            # 2) Nombre inteligente (AND por tokens) — sin acentos / con coma / etc.
            if q_text:
                tokens = [t for t in q_text.split(' ') if t and t not in STOPWORDS_NOMBRES]
                if tokens:
                    name_norm = _sql_name_norm(Candidata.nombre_completo)
                    name_and = and_(*[name_norm.ilike(f"%{t}%") for t in tokens])
                    or_filters.append(name_and)

            # 3) Cédula / Teléfono flexible por dígitos
            if q_digits:
                ced_digits = _sql_digits(Candidata.cedula).ilike(f"%{q_digits}%")
                tel_digits = _sql_digits(Candidata.numero_telefono).ilike(f"%{q_digits}%")
                or_filters.append(or_(ced_digits, tel_digits))

            # Fallback mínimo si no se pudo tokenizar
            if not or_filters:
                like = f"%{q}%"
                or_filters.extend([
                    Candidata.nombre_completo.ilike(like),
                    Candidata.cedula.ilike(like),
                    Candidata.numero_telefono.ilike(like),
                ])

            query = query.filter(or_(*or_filters))

    # Orden limpio y lógico
    query = query.order_by(
        Candidata.nombre_completo.asc()
    )

    # Total
    try:
        total = query.count()
    except Exception:
        total = 0

    pagination = SimplePagination(page=page, per_page=per_page, total=total)

    resultados = (
        query
        .limit(pagination.per_page)
        .offset((pagination.page - 1) * pagination.per_page)
        .all()
    )

    # Si la página está fuera de rango
    if pagination.pages and pagination.page > pagination.pages:
        pagination = SimplePagination(page=pagination.pages, per_page=pagination.per_page, total=total)
        resultados = (
            query
            .limit(pagination.per_page)
            .offset((pagination.page - 1) * pagination.per_page)
            .all()
        )

    return render_template(
        'webadmin/candidatas_web_list.html',
        resultados=resultados,
        q=q,
        page=pagination.page,
        per_page=pagination.per_page,
        total=pagination.total,
        has_prev=pagination.has_prev,
        has_next=pagination.has_next,
        pagination=pagination,
    )


@webadmin_bp.route('/candidatas_web/<int:fila>/editar', methods=['GET', 'POST'])
@roles_required(*WEBADMIN_ALLOWED_ROLES)
def editar_candidata_web(fila):
    """Editar la ficha pública de una candidata."""
    cand = Candidata.query.filter_by(fila=fila).first_or_404()
    if candidata_esta_descalificada(cand):
        flash("Esta candidata está descalificada y no puede mostrarse en la web pública.", "warning")
        return redirect(url_for("webadmin.listar_candidatas_web", q=(cand.nombre_completo or "")))

    ficha = CandidataWeb.query.filter_by(candidata_id=cand.fila).first()
    if not ficha:
        ficha = CandidataWeb(
            candidata_id=cand.fila,
            visible=True,
            estado_publico='disponible',
        )
        db.session.add(ficha)
        db.session.flush()

    if request.method == 'POST':
        ficha.visible = bool(request.form.get('visible'))
        ficha.es_destacada = bool(request.form.get('es_destacada'))
        ficha.disponible_inmediato = bool(request.form.get('disponible_inmediato'))

        estado = (request.form.get('estado_publico') or '').strip()
        if estado in ['disponible', 'reservada', 'no_disponible']:
            ficha.estado_publico = estado

        orden_raw = (request.form.get('orden_lista') or '').strip()
        if orden_raw:
            try:
                ficha.orden_lista = int(orden_raw)
            except ValueError:
                flash("⚠️ El orden debe ser un número entero.", "warning")
        else:
            ficha.orden_lista = None

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

        sueldo_desde_raw = (request.form.get('sueldo_desde') or '').strip()
        sueldo_hasta_raw = (request.form.get('sueldo_hasta') or '').strip()

        ficha.sueldo_desde = int(sueldo_desde_raw) if sueldo_desde_raw.isdigit() else None
        ficha.sueldo_hasta = int(sueldo_hasta_raw) if sueldo_hasta_raw.isdigit() else None

        ficha.sueldo_texto_publico = (request.form.get('sueldo_texto_publico') or '').strip()[:120] or None
        # foto_publica_url ya no se usa en el admin (se gestiona por “Subir fotos”).

        if ficha.visible and ficha.fecha_publicacion is None:
            ficha.fecha_publicacion = utc_now_naive()

        try:
            db.session.commit()
            flash("✅ Ficha para la web actualizada correctamente.", "success")
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


@webadmin_bp.route('/candidatas_web/<int:fila>/marcar_lista_para_trabajar', methods=['POST'])
@roles_required(*WEBADMIN_ALLOWED_ROLES)
def marcar_lista_para_trabajar_web(fila: int):
    cand = Candidata.query.filter_by(fila=fila).first_or_404()
    q = (request.form.get("q") or request.args.get("q") or "").strip()
    next_url = url_for("webadmin.listar_candidatas_web", q=q) if q else url_for("webadmin.listar_candidatas_web")

    if candidata_esta_descalificada(cand):
        flash("No se puede pasar a lista: candidata descalificada.", "warning")
        return redirect(next_url)

    ready_ok, reasons = candidata_is_ready_to_send(cand)
    blocking = [r for r in (reasons or []) if not str(r).lower().startswith("advertencia:")]
    if not ready_ok or blocking:
        flash("No se puede pasar a lista para trabajar. Falta: " + "; ".join(blocking[:4]), "warning")
        return redirect(next_url)

    cand.estado = "lista_para_trabajar"
    if hasattr(cand, "fecha_cambio_estado"):
        cand.fecha_cambio_estado = utc_now_naive()
    if hasattr(cand, "usuario_cambio_estado"):
        actor = (
            getattr(current_user, "username", None)
            or getattr(current_user, "id", None)
            or session.get("usuario")
            or "sistema"
        )
        cand.usuario_cambio_estado = str(actor)[:100]

    try:
        db.session.commit()
        flash("Candidata marcada como lista para trabajar.", "success")
    except Exception:
        db.session.rollback()
        flash("No se pudo actualizar el estado.", "danger")
    return redirect(next_url)