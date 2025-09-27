# -*- coding: utf-8 -*-
from datetime import datetime, date
from functools import wraps

from flask import (
    render_template, redirect, url_for, flash,
    request, abort, g, session
)
from flask_login import (
    login_required, current_user, login_user, logout_user
)
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy.exc import SQLAlchemyError

from config_app import db
from models import Cliente, Solicitud
from utils import letra_por_indice
from .forms import (
    ClienteLoginForm,
    ClienteCancelForm,
    SolicitudForm,
    ClienteSolicitudForm  # puedes dejarlo por compatibilidad si no se usa aquí
)

# 🔹 Usa SIEMPRE el blueprint único definido en clientes/__init__.py
from . import clientes_bp


# ─────────────────────────────────────────────────────────────
# Helpers básicos
# ─────────────────────────────────────────────────────────────
def _is_safe_next(next_url: str) -> bool:
    return bool(next_url) and next_url.startswith('/')


def cliente_required(f):
    """Asegura que el usuario autenticado es un Cliente (modelo Cliente)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not isinstance(current_user, Cliente):
            return redirect(url_for('clientes.login', next=request.url))
        return f(*args, **kwargs)
    return decorated


def politicas_requeridas(f):
    """Bloquea acceso si el cliente no ha aceptado las políticas."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not getattr(current_user, 'acepto_politicas', False):
            dest = url_for('clientes.politicas', next=request.url)
            return redirect(dest)
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────────────────────────
# Configuración de opciones UI (listas y radios)
# ─────────────────────────────────────────────────────────────
try:
    # Si ya las declaras en admin.routes, se aprovechan para no duplicar
    from admin.routes import AREAS_COMUNES_CHOICES  # type: ignore
except Exception:
    AREAS_COMUNES_CHOICES = [
        ('sala', 'Sala'), ('comedor', 'Comedor'),
        ('cocina', 'Cocina'), ('salon_juegos', 'Salón de juegos'),
        ('terraza', 'Terraza'), ('jardin', 'Jardín'),
        ('estudio', 'Estudio'), ('patio', 'Patio'),
        ('piscina', 'Piscina'), ('marquesina', 'Marquesina'),
        ('todas_anteriores', 'Todas las anteriores'), ('otro', 'Otro'),
    ]


# ─────────────────────────────────────────────────────────────
# Login / Logout
# ─────────────────────────────────────────────────────────────
@clientes_bp.route('/login', methods=['GET', 'POST'])
def login():
    form = ClienteLoginForm()
    next_url = request.args.get('next') or url_for('clientes.dashboard')

    if form.validate_on_submit():
        identificador = (form.username.data or "").strip()
        password = (form.password.data or "")

        # Permite login por username O por email
        user = (
            Cliente.query.filter(Cliente.username == identificador).first()
            or
            Cliente.query.filter(Cliente.email == identificador).first()
        )

        if not user:
            flash('Usuario no encontrado.', 'danger')
            return redirect(url_for('clientes.login', next=next_url))

        # Bloqueo de cuentas migradas sin contraseña real
        if user.password_hash == "DISABLED_RESET_REQUIRED":
            flash('Debes restablecer tu contraseña antes de iniciar sesión.', 'warning')
            return redirect(url_for('clientes.reset_password', codigo=user.codigo))

        # Verificación de contraseña
        if not check_password_hash(user.password_hash, password):
            flash('Usuario o contraseña inválidos.', 'danger')
            return redirect(url_for('clientes.login', next=next_url))

        # Estado
        if not getattr(user, "is_active", True):
            flash('Cuenta inactiva. Contacta soporte.', 'warning')
            return redirect(url_for('clientes.login'))

        # Login OK
        login_user(user)
        flash('Bienvenido.', 'success')
        return redirect(next_url if _is_safe_next(next_url) else url_for('clientes.dashboard'))

    # GET
    return render_template('clientes/login.html', form=form)


@clientes_bp.route('/logout')
@login_required
@cliente_required
def logout():
    logout_user()
    flash('Has cerrado sesión correctamente.', 'success')
    return redirect(url_for('clientes.login'))


# ─────────────────────────────────────────────────────────────
# Reset de contraseña (por código del cliente)
# ─────────────────────────────────────────────────────────────
@clientes_bp.route('/reset/<codigo>', methods=['GET', 'POST'])
def reset_password(codigo):
    user = Cliente.query.filter_by(codigo=codigo).first()
    if not user:
        flash('Cliente no encontrado.', 'danger')
        return redirect(url_for('clientes.login'))

    if request.method == 'POST':
        pwd1 = (request.form.get('password1') or '').strip()
        pwd2 = (request.form.get('password2') or '').strip()

        if not pwd1 or len(pwd1) < 6:
            flash('La contraseña debe tener al menos 6 caracteres.', 'danger')
            return redirect(url_for('clientes.reset_password', codigo=codigo))

        if pwd1 != pwd2:
            flash('Las contraseñas no coinciden.', 'danger')
            return redirect(url_for('clientes.reset_password', codigo=codigo))

        user.password_hash = generate_password_hash(pwd1)
        db.session.commit()
        flash('Contraseña actualizada. Ya puedes iniciar sesión.', 'success')
        return redirect(url_for('clientes.login'))

    return render_template('clientes/reset_password.html', user=user)


# ─────────────────────────────────────────────────────────────
# Dashboard del cliente
# ─────────────────────────────────────────────────────────────
@clientes_bp.route('/')
@login_required
@cliente_required
def dashboard():
    total = Solicitud.query.filter_by(cliente_id=current_user.id).count()
    por_estado = (
        db.session.query(Solicitud.estado, db.func.count(Solicitud.id))
        .filter(Solicitud.cliente_id == current_user.id)
        .group_by(Solicitud.estado)
        .all()
    )
    por_estado_dict = {estado or 'sin_definir': cnt for estado, cnt in por_estado}

    recientes = (
        Solicitud.query
        .filter_by(cliente_id=current_user.id)
        .order_by(Solicitud.fecha_solicitud.desc())
        .limit(5)
        .all()
    )

    return render_template(
        'clientes/dashboard.html',
        total_solicitudes=total,
        por_estado=por_estado_dict,
        recientes=recientes,
        hoy=date.today()
    )


# ─────────────────────────────────────────────────────────────
# Páginas informativas
# ─────────────────────────────────────────────────────────────
@clientes_bp.route('/informacion')
@login_required
@cliente_required
def informacion():
    return render_template('clientes/informacion.html')


@clientes_bp.route('/planes')
@login_required
@cliente_required
def planes():
    return render_template('clientes/planes.html')


@clientes_bp.route('/ayuda')
@login_required
@cliente_required
def ayuda():
    whatsapp = "+1 809 429 6892"  # reemplaza por el real
    return render_template('clientes/ayuda.html', whatsapp=whatsapp)


# ─────────────────────────────────────────────────────────────
# Listado de solicitudes (búsqueda + filtro + paginación)
# ─────────────────────────────────────────────────────────────
@clientes_bp.route('/solicitudes')
@login_required
@cliente_required
def listar_solicitudes():
    q        = request.args.get('q', '').strip()
    estado   = request.args.get('estado', '').strip()
    page     = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)

    query = Solicitud.query.filter(Solicitud.cliente_id == current_user.id)

    if estado:
        query = query.filter(Solicitud.estado == estado)

    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                Solicitud.codigo_solicitud.ilike(like),
                getattr(Solicitud, 'ciudad', db.literal('')).ilike(like),
                getattr(Solicitud, 'descripcion', db.literal('')).ilike(like)
            )
        )

    query = query.order_by(Solicitud.fecha_solicitud.desc())
    paginado = query.paginate(page=page, per_page=per_page, error_out=False)

    estados_disponibles = [e[0] for e in db.session.query(Solicitud.estado).distinct().all() if e[0]]

    return render_template(
        'clientes/solicitudes_list.html',
        solicitudes=paginado.items,
        hoy=date.today(),
        page=page, per_page=per_page, total=paginado.total, pages=paginado.pages,
        has_prev=paginado.has_prev, has_next=paginado.has_next,
        prev_num=getattr(paginado, 'prev_num', None),
        next_num=getattr(paginado, 'next_num', None),
        q=q, estado=estado, estados_disponibles=estados_disponibles
    )


# ─────────────────────────────────────────────────────────────
# Helpers para normalización de formularios de solicitud
# ─────────────────────────────────────────────────────────────
def _clean_list(seq):
    bad = {"-", "–", "—"}
    out, seen = [], set()
    for v in (seq or []):
        s = str(v).strip()
        if not s or s in bad:
            continue
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _choices_maps(choices):
    code_to_label, label_to_code = {}, {}
    for code, label in (choices or []):
        c = str(code).strip()
        l = str(label).strip()
        if not c or not l:
            continue
        code_to_label[c] = l
        label_to_code[l] = c
    return code_to_label, label_to_code


def _map_edad_choices(codes_selected, edad_choices, otro_text):
    codes_selected = _clean_list([str(x) for x in (codes_selected or [])])
    code_to_label, _ = _choices_maps(edad_choices)

    result = []
    for code in codes_selected:
        if code == "otro":
            continue
        label = code_to_label.get(code)
        if label:
            result.append(label)

    if "otro" in codes_selected:
        extra = (otro_text or "").strip()
        if extra:
            result.append(extra)

    return _clean_list(result)


def _split_edad_for_form(stored_list, edad_choices):
    stored_list = _clean_list(stored_list)
    _, label_to_code = _choices_maps(edad_choices)

    selected_codes, otros = [], []
    for label in stored_list:
        code = label_to_code.get(label)
        if code:
            selected_codes.append(code)
        else:
            otros.append(label)

    otro_text = ", ".join(otros) if otros else ""
    if otros:
        selected_codes = _clean_list(selected_codes + ["otro"])
    return selected_codes, otro_text


def _map_funciones(vals, extra_text):
    vals = _clean_list(vals)
    if 'otro' in vals:
        vals = [v for v in vals if v != 'otro']
        extra = (extra_text or '').strip()
        if extra:
            vals.extend([x.strip() for x in extra.split(',') if x.strip()])
    return _clean_list(vals)


def _map_tipo_lugar(value, extra):
    value = (value or '').strip()
    if value == 'otro':
        return (extra or '').strip() or value
    return value


def _money_sanitize(raw):
    if raw is None:
        return None
    s = str(raw)
    limpio = s.replace('RD$', '').replace('$', '').replace('.', '').replace(',', '').strip()
    return limpio or s.strip()


# ─────────────────────────────────────────────────────────────
# NUEVA SOLICITUD (CLIENTE) — requiere aceptar políticas
# ─────────────────────────────────────────────────────────────
@clientes_bp.route('/solicitudes/nueva', methods=['GET', 'POST'])
@login_required
@cliente_required
@politicas_requeridas
def nueva_solicitud():
    form = SolicitudForm()
    form.areas_comunes.choices = AREAS_COMUNES_CHOICES

    if request.method == 'GET':
        form.funciones.data       = form.funciones.data or []
        form.areas_comunes.data   = form.areas_comunes.data or []
        form.edad_requerida.data  = form.edad_requerida.data or []
        if form.dos_pisos.data is None:
            form.dos_pisos.data = False
        if form.pasaje_aporte.data is None:
            form.pasaje_aporte.data = False

    if form.validate_on_submit():
        try:
            count  = Solicitud.query.filter_by(cliente_id=current_user.id).count()
            codigo = f"{current_user.codigo}-{letra_por_indice(count)}"

            s = Solicitud(
                cliente_id=current_user.id,
                fecha_solicitud=datetime.utcnow(),
                codigo_solicitud=codigo
            )
            form.populate_obj(s)

            # Normalizaciones
            s.funciones      = _map_funciones(form.funciones.data, getattr(form, 'funciones_otro', None).data if hasattr(form, 'funciones_otro') else '')
            s.areas_comunes  = _clean_list(form.areas_comunes.data)
            s.edad_requerida = _map_edad_choices(form.edad_requerida.data, form.edad_requerida.choices, getattr(form, 'edad_otro', None).data if hasattr(form, 'edad_otro') else '')
            s.tipo_lugar     = _map_tipo_lugar(getattr(s, 'tipo_lugar', ''), getattr(getattr(form, 'tipo_lugar_otro', None), 'data', '') if hasattr(form, 'tipo_lugar_otro') else '')

            if hasattr(s, 'mascota') and hasattr(form, 'mascota'):
                s.mascota = (form.mascota.data or '').strip() or None
            if hasattr(s, 'area_otro') and hasattr(form, 'area_otro'):
                s.area_otro = (form.area_otro.data or '').strip()
            if hasattr(s, 'nota_cliente') and hasattr(form, 'nota_cliente'):
                s.nota_cliente = (form.nota_cliente.data or '').strip()
            if hasattr(s, 'sueldo'):
                s.sueldo = _money_sanitize(form.sueldo.data)
            if hasattr(s, 'fecha_ultima_modificacion'):
                s.fecha_ultima_modificacion = datetime.utcnow()

            db.session.add(s)
            try:
                current_user.total_solicitudes = (current_user.total_solicitudes or 0) + 1
                current_user.fecha_ultima_solicitud = datetime.utcnow()
                current_user.fecha_ultima_actividad = datetime.utcnow()
            except Exception:
                pass

            db.session.commit()
            flash(f'Solicitud {codigo} creada correctamente.', 'success')
            return redirect(url_for('clientes.listar_solicitudes'))

        except SQLAlchemyError:
            db.session.rollback()
            flash('No se pudo crear la solicitud. Intenta de nuevo.', 'danger')

    return render_template('clientes/solicitud_form.html', form=form, nuevo=True)


# ─────────────────────────────────────────────────────────────
# EDITAR SOLICITUD (CLIENTE) — requiere aceptar políticas
# ─────────────────────────────────────────────────────────────
@clientes_bp.route('/solicitudes/<int:id>/editar', methods=['GET','POST'])
@login_required
@cliente_required
@politicas_requeridas
def editar_solicitud(id):
    s = Solicitud.query.filter_by(id=id, cliente_id=current_user.id).first_or_404()
    form = SolicitudForm(obj=s)
    form.areas_comunes.choices = AREAS_COMUNES_CHOICES

    if request.method == 'GET':
        # Precargar listas
        form.funciones.data      = _clean_list(s.funciones)
        form.areas_comunes.data  = _clean_list(s.areas_comunes)

        # Edad: LABELS (BD) → códigos (form) + texto otro
        selected_codes, otro_text = _split_edad_for_form(
            stored_list=s.edad_requerida,
            edad_choices=form.edad_requerida.choices
        )
        form.edad_requerida.data = selected_codes
        if hasattr(form, 'edad_otro'):
            form.edad_otro.data = otro_text

        # Funciones personalizadas → “otro”
        try:
            allowed_fun = {str(v) for v, _ in form.funciones.choices}
            custom_fun = [v for v in (s.funciones or []) if v and v not in allowed_fun]
            if custom_fun and hasattr(form, 'funciones_otro'):
                data = set(form.funciones.data or [])
                data.add('otro')
                form.funciones.data = list(data)
                form.funciones_otro.data = ', '.join(custom_fun)
        except Exception:
            pass

        # Tipo de lugar
        try:
            allowed_tl = {str(v) for v, _ in form.tipo_lugar.choices}
            if s.tipo_lugar and s.tipo_lugar not in allowed_tl and hasattr(form, 'tipo_lugar_otro'):
                form.tipo_lugar.data = 'otro'
                form.tipo_lugar_otro.data = s.tipo_lugar
        except Exception:
            pass

        if form.dos_pisos.data is None:
            form.dos_pisos.data = bool(getattr(s, 'dos_pisos', False))
        if form.pasaje_aporte.data is None:
            form.pasaje_aporte.data = bool(getattr(s, 'pasaje_aporte', False))

    if form.validate_on_submit():
        try:
            form.populate_obj(s)

            s.funciones      = _map_funciones(form.funciones.data, getattr(form, 'funciones_otro', None).data if hasattr(form, 'funciones_otro') else '')
            s.areas_comunes  = _clean_list(form.areas_comunes.data)
            s.edad_requerida = _map_edad_choices(form.edad_requerida.data, form.edad_requerida.choices, getattr(form, 'edad_otro', None).data if hasattr(form, 'edad_otro') else '')
            s.tipo_lugar     = _map_tipo_lugar(getattr(s, 'tipo_lugar', ''), getattr(getattr(form, 'tipo_lugar_otro', None), 'data', '') if hasattr(form, 'tipo_lugar_otro') else '')

            if hasattr(s, 'mascota') and hasattr(form, 'mascota'):
                s.mascota = (form.mascota.data or '').strip() or None
            if hasattr(s, 'area_otro') and hasattr(form, 'area_otro'):
                s.area_otro = (form.area_otro.data or '').strip()
            if hasattr(s, 'nota_cliente') and hasattr(form, 'nota_cliente'):
                s.nota_cliente = (form.nota_cliente.data or '').strip()
            if hasattr(s, 'sueldo'):
                s.sueldo = _money_sanitize(form.sueldo.data)
            if hasattr(s, 'fecha_ultima_modificacion'):
                s.fecha_ultima_modificacion = datetime.utcnow()

            db.session.commit()
            flash('Solicitud actualizada.', 'success')
            return redirect(url_for('clientes.detalle_solicitud', id=id))

        except SQLAlchemyError:
            db.session.rollback()
            flash('No se pudo actualizar la solicitud. Intenta de nuevo.', 'danger')

    return render_template('clientes/solicitud_form.html', form=form, editar=True, solicitud=s)


# ─────────────────────────────────────────────────────────────
# Detalle de solicitud
# ─────────────────────────────────────────────────────────────
@clientes_bp.route('/solicitudes/<int:id>')
@login_required
@cliente_required
def detalle_solicitud(id):
    s = Solicitud.query.filter_by(id=id, cliente_id=current_user.id).first_or_404()

    # Historial de envíos (inicial + reemplazos)
    envios = []
    if getattr(s, 'candidata', None):
        envios.append({
            'tipo': 'Envío inicial',
            'candidata': s.candidata.nombre_completo,
            'fecha': s.fecha_solicitud
        })
    for idx, r in enumerate(getattr(s, 'reemplazos', []) or [], start=1):
        if getattr(r, 'candidata_new', None):
            envios.append({
                'tipo': f'Reemplazo #{idx}',
                'candidata': r.candidata_new.nombre_completo,
                'fecha': r.fecha_inicio_reemplazo or r.created_at
            })

    # Historial de cancelaciones
    cancelaciones = []
    if s.estado == 'cancelada' and getattr(s, 'fecha_cancelacion', None):
        cancelaciones.append({
            'fecha': s.fecha_cancelacion,
            'motivo': getattr(s, 'motivo_cancelacion', '')
        })

    return render_template(
        'clientes/solicitud_detail.html',
        s=s,
        envios=envios,
        cancelaciones=cancelaciones,
        hoy=date.today()
    )


# ─────────────────────────────────────────────────────────────
# Seguimiento (línea de tiempo)
# ─────────────────────────────────────────────────────────────
@clientes_bp.route('/solicitudes/<int:id>/seguimiento')
@login_required
@cliente_required
def seguimiento_solicitud(id):
    s = Solicitud.query.filter_by(id=id, cliente_id=current_user.id).first_or_404()

    timeline = []
    timeline.append({
        'titulo': 'Solicitud creada',
        'detalle': f'Código {s.codigo_solicitud}',
        'fecha': s.fecha_solicitud
    })

    if getattr(s, 'candidata', None):
        timeline.append({
            'titulo': 'Candidata enviada',
            'detalle': s.candidata.nombre_completo,
            'fecha': s.fecha_solicitud
        })

    for idx, r in enumerate(getattr(s, 'reemplazos', []) or [], start=1):
        if getattr(r, 'candidata_new', None):
            timeline.append({
                'titulo': f'Reemplazo #{idx}',
                'detalle': r.candidata_new.nombre_completo,
                'fecha': (getattr(r, 'fecha_inicio_reemplazo', None) or getattr(r, 'created_at', None))
            })

    if s.estado == 'cancelada' and getattr(s, 'fecha_cancelacion', None):
        timeline.append({
            'titulo': 'Solicitud cancelada',
            'detalle': getattr(s, 'motivo_cancelacion', ''),
            'fecha': s.fecha_cancelacion
        })

    if getattr(s, 'fecha_ultima_modificacion', None):
        timeline.append({
            'titulo': 'Actualizada',
            'detalle': 'Se registraron cambios en la solicitud.',
            'fecha': s.fecha_ultima_modificacion
        })

    timeline.sort(key=lambda x: x.get('fecha') or datetime.min)

    return render_template('clientes/solicitud_seguimiento.html', s=s, timeline=timeline)


# ─────────────────────────────────────────────────────────────
# Cancelar solicitud
# ─────────────────────────────────────────────────────────────
@clientes_bp.route('/solicitudes/<int:id>/cancelar', methods=['GET','POST'])
@login_required
@cliente_required
def cancelar_solicitud(id):
    s = Solicitud.query.filter_by(id=id, cliente_id=current_user.id).first_or_404()
    form = ClienteCancelForm()
    if form.validate_on_submit():
        s.estado = 'cancelada'
        s.fecha_cancelacion = datetime.utcnow()
        s.motivo_cancelacion = form.motivo.data
        db.session.commit()
        flash('Solicitud marcada como cancelada (pendiente aprobación).', 'warning')
        return redirect(url_for('clientes.listar_solicitudes'))

    return render_template('clientes/solicitud_cancel.html', s=s, form=form)


# ─────────────────────────────────────────────────────────────
# MIDDLEWARE: mostrar modal si no ha aceptado políticas
# ─────────────────────────────────────────────────────────────
@clientes_bp.before_app_request
def _show_policies_modal_once():
    """
    Si el usuario (cliente) NO ha aceptado todavía:
    - Mostrar un modal solo 1 vez por sesión (flag en session).
    - Bloquear POSTs a cualquier ruta excepto aceptar_politicas.
    - Permitir acceder a la página de políticas.
    """
    WHITELIST = {
        'clientes.politicas',
        'clientes.aceptar_politicas',
        'clientes.rechazar_politicas',
        'clientes.login',
        'clientes.logout',
        'static'
    }

    if not current_user.is_authenticated:
        return None

    # Solo aplica a ROLE cliente
    if getattr(current_user, 'role', 'cliente') != 'cliente':
        return None

    # Ya aceptó
    if getattr(current_user, 'acepto_politicas', False):
        return None

    # Mostrar modal una sola vez en la sesión
    g.show_policies_modal = False
    if not session.get('policies_modal_shown', False):
        g.show_policies_modal = True
        session['policies_modal_shown'] = True

    # Evitar POSTs a otras rutas sin aceptar antes
    if request.method == 'POST' and request.endpoint not in WHITELIST:
        return redirect(url_for('clientes.politicas', next=request.url))

    return None


# ─────────────────────────────────────────────────────────────
# PÁGINA: Políticas (acceso manual desde menú)
# ─────────────────────────────────────────────────────────────
@clientes_bp.route('/politicas', methods=['GET'])
@login_required
def politicas():
    # Solo clientes
    if getattr(current_user, 'role', 'cliente') != 'cliente':
        flash('Acceso no permitido.', 'warning')
        return redirect(url_for('clientes.dashboard'))
    return render_template('clientes/politicas.html')


# ─────────────────────────────────────────────────────────────
# ACCIONES: aceptar / rechazar políticas
# ─────────────────────────────────────────────────────────────
@clientes_bp.route('/politicas/aceptar', methods=['POST'])
@login_required
def aceptar_politicas():
    next_url = request.args.get('next') or url_for('clientes.dashboard')
    current_user.acepto_politicas = True
    current_user.fecha_acepto_politicas = datetime.utcnow()
    db.session.commit()
    flash('Gracias por aceptar nuestras políticas.', 'success')
    return redirect(next_url if _is_safe_next(next_url) else url_for('clientes.dashboard'))


@clientes_bp.route('/politicas/rechazar', methods=['GET'])
@login_required
def rechazar_politicas():
    logout_user()
    flash('Debes aceptar las políticas para usar el portal.', 'warning')
    return redirect(url_for('clientes.login'))
