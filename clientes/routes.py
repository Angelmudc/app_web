from flask import (
    Blueprint, render_template, redirect,
    url_for, flash, request, abort
)
from flask_login import login_required, current_user, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import datetime, date
from functools import wraps

from .forms import (
    ClienteLoginForm,
    ClienteCancelForm,
    SolicitudForm,
    ClienteSolicitudForm  # si no lo usas aquí, puedes dejarlo importado por compatibilidad
)
from models import Cliente, Solicitud
from config_app import db
from utils import letra_por_indice

# ─────────────────────────────────────────────────────────────
# Helper para validar el parámetro next (evitar open redirect)
# ─────────────────────────────────────────────────────────────
def _is_safe_next(next_url: str) -> bool:
    return bool(next_url) and next_url.startswith('/')

# ─────────────────────────────────────────────────────────────
# Decorador para asegurar que el usuario logueado es un Cliente
# ─────────────────────────────────────────────────────────────
def cliente_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not isinstance(current_user, Cliente):
            return redirect(url_for('clientes.login', next=request.url))
        return f(*args, **kwargs)
    return decorated

# ─────────────────────────────────────────────────────────────
# Blueprint
# ─────────────────────────────────────────────────────────────
clientes_bp = Blueprint(
    'clientes',
    __name__,
    url_prefix='/clientes',
    template_folder='../templates/clientes'
)

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
    # Totales por estado (rápido y útil para KPIs)
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
    # Aquí puedes mostrar datos de la agencia, cobertura, horarios, etc.
    return render_template('clientes/informacion.html')

@clientes_bp.route('/planes')
@login_required
@cliente_required
def planes():
    # Planes/servicios de la agencia
    return render_template('clientes/planes.html')

@clientes_bp.route('/ayuda')
@login_required
@cliente_required
def ayuda():
    # Página de ayuda con FAQ + enlaces a WhatsApp/soporte
    # Puedes usar variables en el template para el número de WhatsApp
    whatsapp = "+1 809 000 0000"  # reemplaza por el real si quieres
    return render_template('clientes/ayuda.html', whatsapp=whatsapp)

# ─────────────────────────────────────────────────────────────
# Configuración de opciones UI (listas y radios)
# ─────────────────────────────────────────────────────────────
AREAS_COMUNES_CHOICES = [
    ('sala', 'Sala'), ('comedor', 'Comedor'),
    ('cocina','Cocina'), ('salon_juegos','Salón de juegos'),
    ('terraza','Terraza'), ('jardin','Jardín'),
    ('estudio','Estudio'), ('patio','Patio'),
    ('piscina','Piscina'), ('marquesina','Marquesina'),
    ('todas_anteriores','Todas las anteriores'),
]

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
                Solicitud.ciudad.ilike(like),
                Solicitud.descripcion.ilike(like)  # si existe el campo
            )
        )

    query = query.order_by(Solicitud.fecha_solicitud.desc())
    paginado = query.paginate(page=page, per_page=per_page, error_out=False)

    # Para filtros en la UI
    estados_disponibles = [e[0] for e in db.session.query(Solicitud.estado).distinct().all() if e[0]]

    return render_template(
        'clientes/solicitudes_list.html',
        solicitudes=paginado.items,
        hoy=date.today(),
        # paginación
        page=page,
        per_page=per_page,
        total=paginado.total,
        pages=paginado.pages,
        has_prev=paginado.has_prev,
        has_next=paginado.has_next,
        prev_num=paginado.prev_num if hasattr(paginado, 'prev_num') else None,
        next_num=paginado.next_num if hasattr(paginado, 'next_num') else None,
        # filtros actuales
        q=q, estado=estado,
        estados_disponibles=estados_disponibles
    )

# ─────────────────────────────────────────────────────────────
# Nueva solicitud
# ─────────────────────────────────────────────────────────────
# clientes/routes_solicitudes.py (fragmento)
# RUTAS: crear y editar solicitud (copia y pega sobre las tuyas)

@clientes_bp.route('/solicitudes/nueva', methods=['GET', 'POST'])
@login_required
@cliente_required
def nueva_solicitud():
    from sqlalchemy.exc import SQLAlchemyError

    form = SolicitudForm()
    # Mantén sincronizadas las opciones de áreas comunes con las del admin
    form.areas_comunes.choices = AREAS_COMUNES_CHOICES

    # Helpers internos ---------------------------------------------------------
    def _clean_list(seq):
        """Convierte a lista, quita None, espacios y duplicados respetando orden."""
        seen = set()
        out = []
        for v in (seq or []):
            if v is None:
                continue
            v = str(v).strip()
            if not v:
                continue
            if v not in seen:
                seen.add(v)
                out.append(v)
        return out

    def _map_edad(vals):
        """Normaliza edad: '25+' -> '25 en adelante'. Maneja 'otro'."""
        vals = _clean_list(vals)
        mapped = [('25 en adelante' if v == '25+' else v) for v in vals]
        # Si eligieron "otro", sustituir por lo escrito
        if 'otro' in mapped:
            mapped = [v for v in mapped if v != 'otro']  # quita marcador
            extra = (form.edad_otro.data or '').strip() if hasattr(form, 'edad_otro') else ''
            if extra:
                mapped.append(extra)
        return _clean_list(mapped)

    def _map_funciones(vals):
        """Normaliza funciones: reemplaza 'otro' por lo escrito (una o varias, separadas por coma)."""
        vals = _clean_list(vals)
        if 'otro' in vals:
            vals = [v for v in vals if v != 'otro']
            extra = (form.funciones_otro.data or '').strip() if hasattr(form, 'funciones_otro') else ''
            if extra:
                # Permite varias separadas por coma
                extras = [x.strip() for x in extra.split(',') if x.strip()]
                vals.extend(extras or [extra])
        return _clean_list(vals)

    def _map_tipo_lugar(value):
        """Si seleccionaron 'otro', usa el texto proporcionado."""
        value = (value or '').strip()
        if value == 'otro':
            extra = (form.tipo_lugar_otro.data or '').strip() if hasattr(form, 'tipo_lugar_otro') else ''
            return extra or value  # si no escriben nada, se queda en 'otro'
        return value

    def _money_sanitize(raw):
        """Remueve RD$, $, puntos y comas; deja sólo dígitos (o string original si queda vacío)."""
        if raw is None:
            return None
        s = str(raw)
        limpio = s.replace('RD$', '').replace('$', '').replace('.', '').replace(',', '').strip()
        return limpio or s.strip()

    # GET ----------------------------------------------------------------------
    if request.method == 'GET':
        # Inicializa listas/booleanos para que el template no truene
        form.funciones.data = form.funciones.data or []
        form.areas_comunes.data = form.areas_comunes.data or []
        form.edad_requerida.data = form.edad_requerida.data or []
        if form.dos_pisos.data is None:
            form.dos_pisos.data = False
        if form.pasaje_aporte.data is None:
            form.pasaje_aporte.data = False

    # POST ---------------------------------------------------------------------
    if form.validate_on_submit():
        try:
            # Código consecutivo por cliente (CLI-001-A, CLI-001-B, ...)
            count = Solicitud.query.filter_by(cliente_id=current_user.id).count()
            codigo = f"{current_user.codigo}-{letra_por_indice(count)}"

            s = Solicitud(
                cliente_id=current_user.id,
                fecha_solicitud=datetime.utcnow(),
                codigo_solicitud=codigo
            )

            # Vuelca campos simples
            form.populate_obj(s)

            # Normaliza listas
            s.funciones      = _map_funciones(form.funciones.data)
            s.areas_comunes  = _clean_list(form.areas_comunes.data)
            s.edad_requerida = _map_edad(form.edad_requerida.data)

            # Tipo de lugar (maneja 'otro')
            s.tipo_lugar = _map_tipo_lugar(getattr(s, 'tipo_lugar', ''))

            # Mascota (si existe en el form y en el modelo)
            if hasattr(s, 'mascota') and hasattr(form, 'mascota'):
                s.mascota = (form.mascota.data or '').strip() or None

            # Area "otro" y nota
            if hasattr(s, 'area_otro') and hasattr(form, 'area_otro'):
                s.area_otro = (form.area_otro.data or '').strip()
            if hasattr(s, 'nota_cliente') and hasattr(form, 'nota_cliente'):
                s.nota_cliente = (form.nota_cliente.data or '').strip()

            # Sueldo
            if hasattr(s, 'sueldo'):
                s.sueldo = _money_sanitize(form.sueldo.data)

            # Marca de última modificación si existe el campo
            if hasattr(s, 'fecha_ultima_modificacion'):
                s.fecha_ultima_modificacion = datetime.utcnow()

            db.session.add(s)

            # Métricas rápidas del cliente (si existen)
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

    # Render por defecto
    return render_template('clientes/solicitud_form.html', form=form, nuevo=True)


# ─────────────────────────────────────────────────────────────
# Editar solicitud
# ─────────────────────────────────────────────────────────────
@clientes_bp.route('/solicitudes/<int:id>/editar', methods=['GET','POST'])
@login_required
@cliente_required
def editar_solicitud(id):
    from sqlalchemy.exc import SQLAlchemyError

    s = Solicitud.query.filter_by(id=id, cliente_id=current_user.id).first_or_404()
    form = SolicitudForm(obj=s)
    form.areas_comunes.choices = AREAS_COMUNES_CHOICES

    # Helpers (mismos que arriba) ----------------------------------------------
    def _clean_list(seq):
        seen, out = set(), []
        for v in (seq or []):
            if v is None:
                continue
            v = str(v).strip()
            if not v:
                continue
            if v not in seen:
                seen.add(v)
                out.append(v)
        return out

    def _map_edad(vals):
        vals = _clean_list(vals)
        mapped = [('25 en adelante' if v == '25+' else v) for v in vals]
        if 'otro' in mapped:
            mapped = [v for v in mapped if v != 'otro']
            extra = (form.edad_otro.data or '').strip() if hasattr(form, 'edad_otro') else ''
            if extra:
                mapped.append(extra)
        return _clean_list(mapped)

    def _map_funciones(vals):
        vals = _clean_list(vals)
        if 'otro' in vals:
            vals = [v for v in vals if v != 'otro']
            extra = (form.funciones_otro.data or '').strip() if hasattr(form, 'funciones_otro') else ''
            if extra:
                extras = [x.strip() for x in extra.split(',') if x.strip()]
                vals.extend(extras or [extra])
        return _clean_list(vals)

    def _map_tipo_lugar(value):
        value = (value or '').strip()
        if value == 'otro':
            extra = (form.tipo_lugar_otro.data or '').strip() if hasattr(form, 'tipo_lugar_otro') else ''
            return extra or value
        return value

    def _money_sanitize(raw):
        if raw is None:
            return None
        s = str(raw)
        limpio = s.replace('RD$', '').replace('$', '').replace('.', '').replace(',', '').strip()
        return limpio or s.strip()

    # GET ----------------------------------------------------------------------
    if request.method == 'GET':
        # Precargar listas
        form.funciones.data      = _clean_list(s.funciones)
        form.areas_comunes.data  = _clean_list(s.areas_comunes)
        form.edad_requerida.data = _clean_list(s.edad_requerida)

        # Si hay valores personalizados, marca “otro” y precarga el input
        try:
            allowed_edad = {v for v, _ in form.edad_requerida.choices}
            custom_edad = [v for v in (s.edad_requerida or []) if v and v not in allowed_edad]
            if custom_edad:
                data = set(form.edad_requerida.data or [])
                data.add('otro')
                form.edad_requerida.data = list(data)
                form.edad_otro.data = ', '.join(custom_edad)
        except Exception:
            pass

        try:
            allowed_fun = {v for v, _ in form.funciones.choices}
            custom_fun = [v for v in (s.funciones or []) if v and v not in allowed_fun]
            if custom_fun:
                data = set(form.funciones.data or [])
                data.add('otro')
                form.funciones.data = list(data)
                form.funciones_otro.data = ', '.join(custom_fun)
        except Exception:
            pass

        try:
            allowed_tl = {v for v, _ in form.tipo_lugar.choices}
            if s.tipo_lugar and s.tipo_lugar not in allowed_tl:
                form.tipo_lugar.data = 'otro'
                form.tipo_lugar_otro.data = s.tipo_lugar
        except Exception:
            pass

        if form.dos_pisos.data is None:
            form.dos_pisos.data = bool(getattr(s, 'dos_pisos', False))
        if form.pasaje_aporte.data is None:
            form.pasaje_aporte.data = bool(getattr(s, 'pasaje_aporte', False))

        # Mascota: ya viene precargada por WTForms (obj=s). Nada extra.

    # POST ---------------------------------------------------------------------
    if form.validate_on_submit():
        try:
            form.populate_obj(s)

            s.funciones      = _map_funciones(form.funciones.data)
            s.areas_comunes  = _clean_list(form.areas_comunes.data)
            s.edad_requerida = _map_edad(form.edad_requerida.data)

            s.tipo_lugar = _map_tipo_lugar(getattr(s, 'tipo_lugar', ''))

            # Mascota (si existe en form/modelo)
            if hasattr(s, 'mascota') and hasattr(form, 'mascota'):
                s.mascota = (form.mascota.data or '').strip() or None

            if hasattr(s, 'area_otro'):
                s.area_otro = (form.area_otro.data or '').strip()
            if hasattr(s, 'nota_cliente'):
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
# Detalle de solicitud (con timeline de envíos y cancelaciones)
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
# Seguimiento (vista centrada en la línea de tiempo)
# ─────────────────────────────────────────────────────────────
@clientes_bp.route('/solicitudes/<int:id>/seguimiento')
@login_required
@cliente_required
def seguimiento_solicitud(id):
    s = Solicitud.query.filter_by(id=id, cliente_id=current_user.id).first_or_404()

    timeline = []

    # Creación
    timeline.append({
        'titulo': 'Solicitud creada',
        'detalle': f'Código {s.codigo_solicitud}',
        'fecha': s.fecha_solicitud
    })

    # Envíos (inicial + reemplazos)
    if getattr(s, 'candidata', None):
        timeline.append({
            'titulo': 'Candidata enviada',
            'detalle': s.candidata.nombre_completo,
            'fecha': s.fecha_solicitud
        })

    # Reemplazos (usa "or" en vez de "o" y asegura created_at si falta)
    for idx, r in enumerate(getattr(s, 'reemplazos', []) or [], start=1):
        if getattr(r, 'candidata_new', None):
            timeline.append({
                'titulo': f'Reemplazo #{idx}',
                'detalle': r.candidata_new.nombre_completo,
                'fecha': (getattr(r, 'fecha_inicio_reemplazo', None) or getattr(r, 'created_at', None))
            })

    # Cancelación (si aplica)
    if s.estado == 'cancelada' and getattr(s, 'fecha_cancelacion', None):
        timeline.append({
            'titulo': 'Solicitud cancelada',
            'detalle': getattr(s, 'motivo_cancelacion', ''),
            'fecha': s.fecha_cancelacion
        })

    # Última modificación
    if getattr(s, 'fecha_ultima_modificacion', None):
        timeline.append({
            'titulo': 'Actualizada',
            'detalle': 'Se registraron cambios en la solicitud.',
            'fecha': s.fecha_ultima_modificacion
        })

    # Ordenar por fecha ascendente (si alguna fecha es None, cae a datetime.min)
    timeline.sort(key=lambda x: x.get('fecha') or datetime.min)

    return render_template(
        'clientes/solicitud_seguimiento.html',
        s=s,
        timeline=timeline
    )

# ─────────────────────────────────────────────────────────────
# Cancelar solicitud (queda “pendiente de aprobación”)
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

    return render_template(
        'clientes/solicitud_cancel.html',
        s=s,
        form=form
    )
