import re

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, abort
from flask_login import login_user, logout_user, login_required, UserMixin, current_user
from werkzeug.security import check_password_hash
from datetime import datetime, date, timedelta
from sqlalchemy import or_, func, cast, desc
from sqlalchemy.types import Numeric
from functools import wraps

from config_app import db, USUARIOS
from models import Cliente, Solicitud, Candidata, Reemplazo
from admin.forms import (
    AdminClienteForm,
    AdminSolicitudForm,
    AdminPagoForm,
    AdminReemplazoForm,
    AdminGestionPlanForm
)
from utils import letra_por_indice

from . import admin_bp
from .decorators import admin_required

admin_bp = Blueprint(
    'admin',
    __name__,
    template_folder='../templates/admin'
)


# ——— Decorador para restringir sólo a admins ———
from functools import wraps
from flask import abort
from flask_login import current_user

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            abort(403)
        return f(*args, **kwargs)
    return decorated
# ———————————————————————————————


class AdminUser(UserMixin):
    def __init__(self, username):
        self.id = username
        self.role = USUARIOS[username]['role']


@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        usuario = request.form.get('usuario', '').strip()
        clave   = request.form.get('clave', '').strip()
        user_data = USUARIOS.get(usuario)
        if user_data and check_password_hash(user_data['pwd_hash'], clave):
            user = AdminUser(usuario)
            login_user(user)
            return redirect(url_for('admin.listar_clientes'))
        error = 'Credenciales inválidas.'
    return render_template('admin/login.html', error=error)


@admin_bp.route('/logout')
@login_required
@admin_required
def logout():
    logout_user()
    return redirect(url_for('admin.login'))


@admin_bp.route('/clientes')
@login_required
@admin_required
def listar_clientes():
    q = request.args.get('q', '').strip()
    query = Cliente.query
    if q:
        filtros = [
            Cliente.nombre_completo.ilike(f'%{q}%'),
            Cliente.telefono.ilike(f'%{q}%'),
            Cliente.codigo.ilike(f'%{q}%'),
        ]
        if q.isdigit():
            filtros.append(Cliente.id == int(q))
        query = query.filter(or_(*filtros))
    clientes = query.order_by(Cliente.fecha_registro.desc()).all()
    return render_template('admin/clientes_list.html', clientes=clientes, q=q)


@admin_bp.route('/clientes/nuevo', methods=['GET', 'POST'])
@login_required
@admin_required
def nuevo_cliente():
    form = AdminClienteForm()

    if form.validate_on_submit():
        # 1) Validar unicidad de código
        existe_codigo = Cliente.query.filter_by(codigo=form.codigo.data).first()
        if existe_codigo:
            flash(f"El código «{form.codigo.data}» ya está en uso.", "danger")
        else:
            # 2) Crear instancia y poblar campos
            c = Cliente()
            form.populate_obj(c)
            # 3) Fecha de registro y guardado
            c.fecha_registro = datetime.utcnow()
            db.session.add(c)
            db.session.commit()
            flash('Cliente creado correctamente.', 'success')
            return redirect(url_for('admin.listar_clientes'))

    return render_template(
        'admin/cliente_form.html',
        cliente_form=form,
        nuevo=True
    )


@admin_bp.route('/clientes/<int:cliente_id>/editar', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_cliente(cliente_id):
    c = Cliente.query.get_or_404(cliente_id)
    form = AdminClienteForm(obj=c)

    if form.validate_on_submit():
        # Actualizar datos básicos
        form.populate_obj(c)
        c.fecha_ultima_actividad = datetime.utcnow()
        db.session.commit()

        flash('Cliente actualizado correctamente.', 'success')
        return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))

    return render_template(
        'admin/cliente_form.html',
        cliente_form=form,
        nuevo=False
    )


@admin_bp.route('/clientes/<int:cliente_id>/eliminar', methods=['POST'])
@login_required
@admin_required
def eliminar_cliente(cliente_id):
    c = Cliente.query.get_or_404(cliente_id)
    db.session.delete(c)
    db.session.commit()
    flash('Cliente eliminado.', 'success')
    return redirect(url_for('admin.listar_clientes'))

@admin_bp.route('/clientes/<int:cliente_id>')
@login_required
@admin_required
def detalle_cliente(cliente_id):
    c = Cliente.query.get_or_404(cliente_id)
    return render_template('admin/cliente_detail.html', cliente=c)

# ——— Rutas de Solicitudes y Reemplazos ———

AREAS_COMUNES_CHOICES = [
    ('sala', 'Sala'), ('comedor', 'Comedor'),
    ('cocina','Cocina'), ('salon_juegos','Salón de juegos'),
    ('terraza','Terraza'), ('jardin','Jardín'),
    ('estudio','Estudio'), ('patio','Patio'),
    ('piscina','Piscina'), ('marquesina','Marquesina'),
    ('todas_anteriores','Todas las anteriores'),
]



# Rutas en tu blueprint admin (reemplaza las funciones existentes)

@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/nueva', methods=['GET','POST'])
@login_required
@admin_required
def nueva_solicitud_admin(cliente_id):
    c    = Cliente.query.get_or_404(cliente_id)
    form = AdminSolicitudForm()
    form.areas_comunes.choices = AREAS_COMUNES_CHOICES

    if request.method == 'GET':
        form.funciones.data        = []
        form.funciones_otro.data   = ''
        form.areas_comunes.data    = []
        form.area_otro.data        = ''
        form.edad_otro.data        = ''
        form.tipo_lugar_otro.data  = ''
        form.mascota.data          = ''

    if form.validate_on_submit():
        # ➤ Código único
        count        = Solicitud.query.filter_by(cliente_id=c.id).count()
        nuevo_codigo = f"{c.codigo}-{letra_por_indice(count)}"

        # ➤ Instanciar y poblar modelo
        s = Solicitud(
            cliente_id       = c.id,
            fecha_solicitud  = datetime.utcnow(),
            codigo_solicitud = nuevo_codigo
        )
        form.populate_obj(s)

        # ➤ Tipo de lugar
        if form.tipo_lugar.data == 'otro':
            s.tipo_lugar = form.tipo_lugar_otro.data.strip()
        else:
            s.tipo_lugar = form.tipo_lugar.data

        # ➤ Edad requerida
        choices_age = dict(form.edad_requerida.choices)
        if form.edad_requerida.data == 'otra':
            s.edad_requerida = form.edad_otro.data.strip()
        else:
            s.edad_requerida = choices_age.get(form.edad_requerida.data,
                                              form.edad_requerida.data)

        # ➤ Mascota
        s.mascota = form.mascota.data.strip() if form.mascota.data else None

        # ➤ Funciones
        s.funciones = form.funciones.data
        if form.funciones_otro.data:
            s.funciones.append(form.funciones_otro.data.strip())
            s.funciones_otro = form.funciones_otro.data.strip()
        else:
            s.funciones_otro = None

        # ➤ Áreas comunes y pasaje
        s.areas_comunes = form.areas_comunes.data
        s.area_otro     = form.area_otro.data
        s.pasaje_aporte = form.pasaje_aporte.data

        # ➤ Guardar en la base de datos
        db.session.add(s)
        c.total_solicitudes      += 1
        c.fecha_ultima_solicitud  = datetime.utcnow()
        c.fecha_ultima_actividad  = datetime.utcnow()
        db.session.commit()

        flash(f'Solicitud {nuevo_codigo} creada.', 'success')
        return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))

    return render_template(
        'admin/solicitud_form.html',
        form=form,
        cliente_id=cliente_id,
        nuevo=True
    )


@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/editar', methods=['GET','POST'])
@login_required
@admin_required
def editar_solicitud_admin(cliente_id, id):
    s    = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()
    form = AdminSolicitudForm(obj=s)
    form.areas_comunes.choices = AREAS_COMUNES_CHOICES

    if request.method == 'GET':
        # ➤ Tipo de lugar
        guard_lugar = s.tipo_lugar or ''
        opts_lugar  = {v for v,_ in form.tipo_lugar.choices}
        if guard_lugar in opts_lugar:
            form.tipo_lugar.data      = guard_lugar
            form.tipo_lugar_otro.data = ''
        else:
            form.tipo_lugar.data      = 'otro'
            form.tipo_lugar_otro.data = guard_lugar

        # ➤ Edad requerida
        guard_edad = s.edad_requerida or ''
        opts_edad  = {v for v,_ in form.edad_requerida.choices}
        if guard_edad in opts_edad:
            form.edad_requerida.data = guard_edad
            form.edad_otro.data      = ''
        else:
            form.edad_requerida.data = 'otra'
            form.edad_otro.data      = guard_edad

        # ➤ Funciones
        form.funciones.data      = s.funciones or []
        form.funciones_otro.data = s.funciones_otro or ''

        # ➤ Mascota
        form.mascota.data        = s.mascota or ''

        # ➤ Áreas comunes y pasaje
        form.areas_comunes.data = s.areas_comunes or []
        form.area_otro.data     = s.area_otro or ''
        form.pasaje_aporte.data = s.pasaje_aporte

    if form.validate_on_submit():
        form.populate_obj(s)

        # ➤ Tipo de lugar
        if form.tipo_lugar.data == 'otro':
            s.tipo_lugar = form.tipo_lugar_otro.data.strip()
        else:
            s.tipo_lugar = form.tipo_lugar.data

        # ➤ Edad requerida
        choices_age = dict(form.edad_requerida.choices)
        if form.edad_requerida.data == 'otra':
            s.edad_requerida = form.edad_otro.data.strip()
        else:
            s.edad_requerida = choices_age.get(form.edad_requerida.data,
                                              form.edad_requerida.data)

        # ➤ Mascota
        s.mascota = form.mascota.data.strip() if form.mascota.data else None

        # ➤ Funciones
        s.funciones = form.funciones.data
        if form.funciones_otro.data:
            s.funciones.append(form.funciones_otro.data.strip())
            s.funciones_otro = form.funciones_otro.data.strip()
        else:
            s.funciones_otro = None

        # ➤ Áreas comunes y pasaje
        s.areas_comunes             = form.areas_comunes.data
        s.area_otro                 = form.area_otro.data
        s.pasaje_aporte             = form.pasaje_aporte.data
        s.fecha_ultima_modificacion = datetime.utcnow()

        db.session.commit()
        flash(f'Solicitud {s.codigo_solicitud} actualizada.', 'success')
        return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))

    return render_template(
        'admin/solicitud_form.html',
        form=form,
        cliente_id=cliente_id,
        solicitud=s,
        nuevo=False
    )

@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/eliminar', methods=['POST'])
@login_required
@admin_required
def eliminar_solicitud_admin(cliente_id, id):
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()
    db.session.delete(s)
    c = Cliente.query.get(cliente_id)
    c.total_solicitudes = max((c.total_solicitudes or 1) - 1, 0)
    c.fecha_ultima_actividad = datetime.utcnow()
    db.session.commit()
    flash('Solicitud eliminada.', 'success')
    return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))


@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/plan', methods=['GET','POST'])
@login_required
@admin_required
def gestionar_plan(cliente_id, id):
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()
    form = AdminGestionPlanForm(obj=s)
    if form.validate_on_submit():
        s.tipo_plan = form.tipo_plan.data
        s.abono     = form.abono.data
        s.estado    = 'activa'
        s.fecha_ultima_actividad = datetime.utcnow()
        db.session.commit()
        flash('Plan y abono actualizados correctamente.', 'success')
        return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))
    return render_template(
        'admin/gestionar_plan.html',
        form=form,
        cliente_id=cliente_id,
        solicitud=s
    )


@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/pago', methods=['GET','POST'])
@login_required
@admin_required
def registrar_pago(cliente_id, id):
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()
    form = AdminPagoForm()
    form.candidata_id.choices = [
        (c.fila, c.nombre_completo) for c in Candidata.query.all()
    ]
    if form.validate_on_submit():
        # 1) Registramos los datos del pago
        s.candidata_id             = form.candidata_id.data
        s.monto_pagado             = form.monto_pagado.data
        s.estado                   = 'pagada'
        # 2) Actualizamos los timestamps
        s.fecha_ultima_actividad   = datetime.utcnow()
        s.fecha_ultima_modificacion = datetime.utcnow()
        db.session.commit()

        flash('Pago registrado y solicitud marcada como pagada.', 'success')
        return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))

    return render_template(
        'admin/registrar_pago.html',
        form=form,
        cliente_id=cliente_id,
        solicitud=s
    )



@admin_bp.route('/solicitudes/<int:s_id>/reemplazos/nuevo', methods=['GET','POST'])
@login_required
@admin_required
def nuevo_reemplazo(s_id):
    sol  = Solicitud.query.get_or_404(s_id)
    form = AdminReemplazoForm()
    if form.validate_on_submit():
        r = Reemplazo(
            solicitud_id           = s_id,
            candidata_old_id       = form.candidata_old_id.data,
            motivo_fallo           = form.motivo_fallo.data,
            fecha_inicio_reemplazo = form.fecha_inicio_reemplazo.data,
        )
        db.session.add(r)
        sol.estado = 'reemplazo'
        sol.fecha_ultima_actividad = datetime.utcnow()
        db.session.commit()
        flash('Reemplazo activado y solicitud marcada como reemplazo.', 'success')
        return redirect(url_for('admin.listar_clientes'))
    return render_template(
        'admin/reemplazo_form.html',
        form=form,
        solicitud=sol
    )


@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>')
@login_required
@admin_required
def detalle_solicitud(cliente_id, id):
    # 1) Carga la solicitud
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()

    # 2) Historial de envíos (inicial + reemplazos)
    envios = []
    if s.candidata:
        envios.append({
            'tipo':     'Envío inicial',
            'candidata': s.candidata,
            'fecha':     s.fecha_solicitud
        })
    for idx, r in enumerate(s.reemplazos, start=1):
        if r.candidata_new:
            envios.append({
                'tipo':     f'Reemplazo {idx}',
                'candidata': r.candidata_new,
                'fecha':     r.fecha_inicio_reemplazo or r.created_at
            })

    # 3) Historial de cancelaciones (puede ser 0 o 1)
    cancelaciones = []
    if s.estado == 'cancelada' and s.fecha_cancelacion:
        cancelaciones.append({
            'fecha':  s.fecha_cancelacion,
            'motivo': s.motivo_cancelacion
        })

    # 4) Reemplazos directos (detalle)
    reemplazos = s.reemplazos

    return render_template(
        'admin/solicitud_detail.html',
        solicitud      = s,
        envios         = envios,
        cancelaciones  = cancelaciones,
        reemplazos     = reemplazos
    )

@admin_bp.route('/api/candidatas')
@login_required
@admin_required
def api_candidatas():
    term = request.args.get('q','').strip()
    query = Candidata.query
    if term:
        query = query.filter(Candidata.nombre_completo.ilike(f'%{term}%'))
    resultados = query.order_by(Candidata.nombre_completo).limit(20).all()
    return jsonify(results=[{'id':c.fila,'text':c.nombre_completo} for c in resultados])


@admin_bp.route('/solicitudes')
@login_required
@admin_required
def listar_solicitudes():
    proc_count = Solicitud.query.filter_by(estado='proceso').count()
    copiable_count = Solicitud.query \
        .filter(
            or_(
                Solicitud.estado == 'activa',
                Solicitud.estado == 'reemplazo'
            )
        ) \
        .filter(
            or_(
                Solicitud.last_copiado_at.is_(None),
                func.date(Solicitud.last_copiado_at) < date.today()
            )
        ).count()
    return render_template(
        'admin/solicitudes_list.html',
        proc_count=proc_count,
        copiable_count=copiable_count
    )


# En admin/routes.py, asegúrate de tener estos imports:


@admin_bp.route('/solicitudes/resumen')
@login_required
@admin_required
def resumen_solicitudes():
    hoy         = date.today()
    week_start  = hoy - timedelta(days=hoy.weekday())
    month_start = date(hoy.year, hoy.month, 1)

    # — Totales y estados —
    total_sol    = Solicitud.query.count()
    proc_count   = Solicitud.query.filter_by(estado='proceso').count()
    act_count    = Solicitud.query.filter_by(estado='activa').count()
    pag_count    = Solicitud.query.filter_by(estado='pagada').count()
    cancel_count = Solicitud.query.filter_by(estado='cancelada').count()
    repl_count   = Solicitud.query.filter_by(estado='reemplazo').count()

    # — Tasas de conversión, reemplazo y abandono —
    conversion_rate  = (pag_count   / total_sol * 100) if total_sol else 0
    replacement_rate = (repl_count  / total_sol * 100) if total_sol else 0
    abandon_rate     = (cancel_count/ total_sol * 100) if total_sol else 0

    # — Promedios de tiempo (en días) —
    avg_pub_secs = db.session.query(
        func.avg(func.extract('epoch',
            Solicitud.last_copiado_at - Solicitud.fecha_solicitud))
    ).filter(Solicitud.last_copiado_at.isnot(None)).scalar() or 0
    avg_pub_days = avg_pub_secs / 86400

    avg_pay_secs = db.session.query(
        func.avg(func.extract('epoch',
            Solicitud.fecha_ultima_modificacion - Solicitud.fecha_solicitud))
    ).filter(Solicitud.estado=='pagada').scalar() or 0
    avg_pay_days = avg_pay_secs / 86400

    avg_cancel_secs = db.session.query(
        func.avg(func.extract('epoch',
            Solicitud.fecha_cancelacion - Solicitud.fecha_solicitud))
    ).filter(Solicitud.fecha_cancelacion.isnot(None)).scalar() or 0
    avg_cancel_days = avg_cancel_secs / 86400

    # — Top 5 ciudades por número de solicitudes —
    top_cities = (
        db.session.query(
            Solicitud.ciudad_sector,
            func.count(Solicitud.id).label('cnt')
        )
        .group_by(Solicitud.ciudad_sector)
        .order_by(desc('cnt'))
        .limit(5)
        .all()
    )

    # — Distribución por modalidad de trabajo —
    modality_dist = (
        db.session.query(
            Solicitud.modalidad_trabajo,
            func.count(Solicitud.id)
        )
        .group_by(Solicitud.modalidad_trabajo)
        .all()
    )

    # — Backlog: en proceso >7 días —
    backlog_threshold_days = 7
    backlog_alert = (
        Solicitud.query
        .filter_by(estado='proceso')
        .filter(Solicitud.fecha_solicitud < datetime.utcnow() - timedelta(days=backlog_threshold_days))
        .count()
    )

    # — Tendencias de nuevas solicitudes —
    trend_new_weekly  = (
        db.session.query(
            func.date_trunc('week', Solicitud.fecha_solicitud).label('period'),
            func.count(Solicitud.id)
        )
        .group_by('period').order_by('period')
        .all()
    )
    trend_new_monthly = (
        db.session.query(
            func.date_trunc('month', Solicitud.fecha_solicitud).label('period'),
            func.count(Solicitud.id)
        )
        .group_by('period').order_by('period')
        .all()
    )

    # — Tendencias de pagos —
    trend_paid_weekly  = (
        db.session.query(
            func.date_trunc('week', Solicitud.fecha_ultima_modificacion).label('period'),
            func.count(Solicitud.id)
        )
        .filter(Solicitud.estado=='pagada')
        .group_by('period').order_by('period')
        .all()
    )
    trend_paid_monthly = (
        db.session.query(
            func.date_trunc('month', Solicitud.fecha_ultima_modificacion).label('period'),
            func.count(Solicitud.id)
        )
        .filter(Solicitud.estado=='pagada')
        .group_by('period').order_by('period')
        .all()
    )

    # — Tendencias de cancelaciones —
    trend_cancel_weekly  = (
        db.session.query(
            func.date_trunc('week', Solicitud.fecha_cancelacion).label('period'),
            func.count(Solicitud.id)
        )
        .filter(Solicitud.estado=='cancelada')
        .group_by('period').order_by('period')
        .all()
    )
    trend_cancel_monthly = (
        db.session.query(
            func.date_trunc('month', Solicitud.fecha_cancelacion).label('period'),
            func.count(Solicitud.id)
        )
        .filter(Solicitud.estado=='cancelada')
        .group_by('period').order_by('period')
        .all()
    )

    # — Órdenes realizadas (basadas en fecha_solicitud) —
    orders_today = Solicitud.query.filter(func.date(Solicitud.fecha_solicitud)==hoy).count()
    orders_week  = Solicitud.query.filter(Solicitud.fecha_solicitud>=week_start).count()
    orders_month = Solicitud.query.filter(Solicitud.fecha_solicitud>=month_start).count()

    # — Solicitudes Publicadas (copiadas) —
    daily_copy   = Solicitud.query.filter(func.date(Solicitud.last_copiado_at)==hoy).count()
    weekly_copy  = Solicitud.query.filter(func.date(Solicitud.last_copiado_at)>=week_start).count()
    monthly_copy = Solicitud.query.filter(Solicitud.last_copiado_at>=month_start).count()

    # — Pagos por periodo —
    daily_paid   = Solicitud.query.filter_by(estado='pagada')\
                     .filter(func.date(Solicitud.fecha_ultima_modificacion)==hoy).count()
    weekly_paid  = Solicitud.query.filter_by(estado='pagada')\
                     .filter(func.date(Solicitud.fecha_ultima_modificacion)>=week_start).count()
    monthly_paid = Solicitud.query.filter_by(estado='pagada')\
                     .filter(Solicitud.fecha_ultima_modificacion>=month_start).count()

    # — Cancelaciones por periodo —
    daily_cancel   = Solicitud.query.filter_by(estado='cancelada')\
                       .filter(func.date(Solicitud.fecha_cancelacion)==hoy).count()
    weekly_cancel  = Solicitud.query.filter_by(estado='cancelada')\
                       .filter(func.date(Solicitud.fecha_cancelacion)>=week_start).count()
    monthly_cancel = Solicitud.query.filter_by(estado='cancelada')\
                       .filter(Solicitud.fecha_cancelacion>=month_start).count()

    # — Reemplazos por periodo (solo semana/mes) —
    weekly_repl  = Solicitud.query.filter_by(estado='reemplazo')\
                     .filter(func.date(Solicitud.fecha_ultima_modificacion)>=week_start).count()
    monthly_repl = Solicitud.query.filter_by(estado='reemplazo')\
                     .filter(Solicitud.fecha_ultima_modificacion>=month_start).count()

    # — Estadísticas mensuales de ingreso (pagadas) —
    stats_mensual = (
        db.session.query(
            func.date_trunc('month', Solicitud.fecha_solicitud).label('mes'),
            func.count(Solicitud.id).label('cantidad'),
            func.sum(
                cast(func.replace(Solicitud.monto_pagado, ',', ''), Numeric(12,2))
            ).label('total_pagado')
        )
        .filter(Solicitud.estado=='pagada')
        .group_by('mes').order_by('mes')
        .all()
    )

    return render_template(
        'admin/solicitudes_resumen.html',
        # Totales y estados
        total_sol=total_sol,
        proc_count=proc_count,
        act_count=act_count,
        pag_count=pag_count,
        cancel_count=cancel_count,
        repl_count=repl_count,
        # Tasas y promedios
        conversion_rate=conversion_rate,
        replacement_rate=replacement_rate,
        abandon_rate=abandon_rate,
        avg_pub_days=avg_pub_days,
        avg_pay_days=avg_pay_days,
        avg_cancel_days=avg_cancel_days,
        # Top y distribución
        top_cities=top_cities,
        modality_dist=modality_dist,
        backlog_threshold_days=backlog_threshold_days,
        backlog_alert=backlog_alert,
        # Tendencias
        trend_new_weekly=trend_new_weekly,
        trend_new_monthly=trend_new_monthly,
        trend_paid_weekly=trend_paid_weekly,
        trend_paid_monthly=trend_paid_monthly,
        trend_cancel_weekly=trend_cancel_weekly,
        trend_cancel_monthly=trend_cancel_monthly,
        # Órdenes realizadas
        orders_today=orders_today,
        orders_week=orders_week,
        orders_month=orders_month,
        # Publicadas (copias)
        daily_copy=daily_copy,
        weekly_copy=weekly_copy,
        monthly_copy=monthly_copy,
        # Pagos
        daily_paid=daily_paid,
        weekly_paid=weekly_paid,
        monthly_paid=monthly_paid,
        # Cancelaciones
        daily_cancel=daily_cancel,
        weekly_cancel=weekly_cancel,
        monthly_cancel=monthly_cancel,
        # Reemplazos
        weekly_repl=weekly_repl,
        monthly_repl=monthly_repl,
        # Ingreso mensual
        stats_mensual=stats_mensual
    )


# routes/admin/solicitudes.py  (o donde tengas estas rutas)

def _norm_area(text: str) -> str:
    """Reemplaza guiones bajos por espacios y colapsa espacios múltiples."""
    if not text:
        return ""
    s = str(text)
    s = s.replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


@admin_bp.route('/solicitudes/copiar')
@login_required
@admin_required
def copiar_solicitudes():
    hoy = date.today()
    base_q = (
        Solicitud.query
        .filter(Solicitud.estado.in_(['activa', 'reemplazo']))
        .filter(
            or_(
                Solicitud.last_copiado_at.is_(None),
                func.date(Solicitud.last_copiado_at) < hoy
            )
        )
    )

    # Primero reemplazos, luego activas
    con_reemp = (
        base_q
        .filter(Solicitud.estado == 'reemplazo')
        .order_by(Solicitud.fecha_solicitud.desc())
        .all()
    )
    sin_reemp = (
        base_q
        .filter(Solicitud.estado == 'activa')
        .order_by(Solicitud.fecha_solicitud.desc())
        .all()
    )
    raw_sols = con_reemp + sin_reemp

    form = AdminSolicitudForm()
    FUNCIONES_CHOICES = form.funciones.choices

    solicitudes = []
    for s in raw_sols:
        # Reemplazos (si es estado reemplazo, todos; si no, solo los nuevos)
        reems = s.reemplazos if s.estado == 'reemplazo' else [r for r in s.reemplazos if r.oportunidad_nueva]

        # Funciones (sin la opción genérica 'otro')
        funcs = [lbl for code, lbl in FUNCIONES_CHOICES if code in s.funciones and code != 'otro']
        if getattr(s, 'funciones_otro', None):
            funcs.append(s.funciones_otro)

        # Niños (si aplica)
        ninos_text = ""
        if s.ninos:
            ninos_text = f", Niños: {s.ninos}"
            if s.edades_ninos:
                ninos_text += f" ({s.edades_ninos})"

        # Modalidad (sin Dirección)
        modalidad = (
            getattr(s, 'modalidad_trabajo', None)
            or getattr(s, 'modalidad', None)
            or getattr(s, 'tipo_modalidad', None)
            or ''
        )

        # ===== Hogar (debajo de Funciones, como pediste) =====
        hogar_partes_detalle = []

        # Habitaciones
        if s.habitaciones:
            hogar_partes_detalle.append(f"{s.habitaciones} habitaciones")

        # Baños (entero sin .0, decimal si aplica)
        if s.banos is not None:
            try:
                f = float(s.banos)
                banos_txt = str(int(f)) if f.is_integer() else str(f)
            except Exception:
                banos_txt = str(s.banos)
            hogar_partes_detalle.append(f"{banos_txt} baños")

        # Dos pisos
        if getattr(s, 'dos_pisos', False):
            hogar_partes_detalle.append("2 pisos")

        # Áreas comunes + otro (NORMALIZADAS: "_" -> " ")
        areas = []
        if getattr(s, 'areas_comunes', None):
            areas.extend([_norm_area(a) for a in s.areas_comunes if str(a).strip()])
        if getattr(s, 'area_otro', None) and s.area_otro.strip():
            areas.append(_norm_area(s.area_otro))

        # Tipo de lugar al inicio con guion si hay detalles
        tipo_lugar = (getattr(s, 'tipo_lugar', None) or "").strip()
        if areas:
            hogar_partes_detalle.append(", ".join(areas))

        if tipo_lugar and hogar_partes_detalle:
            hogar_line = f"{tipo_lugar} - {', '.join(hogar_partes_detalle)}"
        elif tipo_lugar:
            hogar_line = tipo_lugar
        else:
            hogar_line = ", ".join(hogar_partes_detalle)

        hogar_line = f"\n{hogar_line}" if hogar_line else ""
        # ======================================================

        # Mascota SOLO si trae texto real
        mascota_val = (getattr(s, 'mascota', None) or '').strip()
        mascota_line = f"\nMascota: {mascota_val}" if mascota_val else ""

        # ===== Texto final a copiar (mismo orden que tenías) =====
        order_text = f"""Disponible ( {s.codigo_solicitud} )
📍 {s.ciudad_sector}
Ruta más cercana: {s.rutas_cercanas}

Modalidad: {modalidad}

Edad: {s.edad_requerida or ''}
Dominicana
Que sepa leer y escribir
Experiencia en: {s.experiencia}
Horario: {s.horario}

Funciones: {', '.join(funcs)}{hogar_line}

Adultos: {s.adultos}{ninos_text}""" + f"""{mascota_line}

Sueldo: ${s.sueldo} mensual{', más ayuda del pasaje' if s.pasaje_aporte else ', pasaje incluido'}

{f'Nota: {s.nota_cliente}' if s.nota_cliente else ''}"""
        # ========================================================

        solicitudes.append({
            'id': s.id,
            'codigo_solicitud': s.codigo_solicitud,
            'ciudad_sector': s.ciudad_sector,
            'reemplazos': reems,
            'funcs': funcs,
            'modalidad': modalidad,
            'order_text': order_text
        })

    return render_template('admin/solicitudes_copiar.html', solicitudes=solicitudes)


@admin_bp.route('/solicitudes/<int:id>/copiar', methods=['POST'])
@login_required
@admin_required
def copiar_solicitud(id):
    s = Solicitud.query.get_or_404(id)
    s.last_copiado_at = func.now()
    db.session.commit()
    flash(f'Solicitud {s.codigo_solicitud} copiada. Ya no se mostrará hasta mañana.', 'success')
    return redirect(url_for('admin.copiar_solicitudes'))


@admin_bp.route(
    '/clientes/<int:cliente_id>/solicitudes/<int:id>/cancelar',
    methods=['GET', 'POST']
)
@login_required
@admin_required
def cancelar_solicitud(cliente_id, id):
    s = Solicitud.query.filter_by(
        id=id, cliente_id=cliente_id
    ).first_or_404()

    if request.method == 'POST':
        motivo = request.form.get('motivo', '').strip()
        if not motivo:
            flash('Debes indicar un motivo de cancelación.', 'warning')
        else:
            s.estado = 'cancelada'
            s.fecha_cancelacion = datetime.utcnow()
            s.motivo_cancelacion = motivo
            db.session.commit()
            flash('Solicitud cancelada con éxito.', 'success')
            return redirect(
                url_for('admin.detalle_cliente', cliente_id=cliente_id)
            )

    return render_template(
        'admin/cancelar_solicitud.html',
        solicitud=s
    )

from sqlalchemy import func  # si no está ya importado
from config_app import db    # si no está ya importado

# 1) Clientes con solicitudes en proceso
@admin_bp.route('/solicitudes/proceso/clients')
@login_required
@admin_required
def listar_clientes_con_proceso():
    resultados = db.session.query(
        Cliente, func.count(Solicitud.id).label('pendientes')
    ).join(Solicitud, Solicitud.cliente_id == Cliente.id) \
     .filter(Solicitud.estado == 'proceso') \
     .group_by(Cliente.id) \
     .order_by(Cliente.nombre_completo) \
     .all()

    return render_template(
        'admin/solicitudes_proceso_clients.html',
        resultados=resultados
    )


# 2) Listar solicitudes en proceso de un cliente
@admin_bp.route('/solicitudes/proceso/<int:cliente_id>')
@login_required
@admin_required
def listar_solicitudes_de_cliente_proceso(cliente_id):
    c = Cliente.query.get_or_404(cliente_id)
    solicitudes = Solicitud.query \
        .filter_by(cliente_id=cliente_id, estado='proceso') \
        .order_by(Solicitud.fecha_solicitud.desc()) \
        .all()

    return render_template(
        'admin/solicitudes_proceso_list.html',
        cliente=c,
        solicitudes=solicitudes
    )


# 3) Editar/Finalizar una solicitud en proceso
@admin_bp.route('/solicitudes/proceso/acciones')
@login_required
@admin_required
def acciones_solicitudes_proceso():
    solicitudes = Solicitud.query \
        .filter_by(estado='proceso') \
        .order_by(Solicitud.fecha_solicitud.desc()) \
        .all()
    return render_template(
        'admin/solicitudes_proceso_acciones.html',
        solicitudes=solicitudes
    )

@admin_bp.route('/solicitudes/<int:id>/activar', methods=['POST'])
@login_required
@admin_required
def activar_solicitud_directa(id):
    s = Solicitud.query.get_or_404(id)
    if s.estado == 'proceso':
        s.estado = 'activa'
        s.fecha_ultima_modificacion = datetime.utcnow()
        db.session.commit()
        flash(f'Solicitud {s.codigo_solicitud} marcada como activa.', 'success')
    return redirect(url_for('admin.acciones_solicitudes_proceso'))

@admin_bp.route('/solicitudes/<int:id>/cancelar_directo', methods=['POST'])
@login_required
@admin_required
def cancelar_solicitud_directa(id):
    s = Solicitud.query.get_or_404(id)
    if s.estado == 'proceso':
        s.estado = 'cancelada'
        s.fecha_cancelacion = datetime.utcnow()
        s.fecha_ultima_modificacion = datetime.utcnow()
        db.session.commit()
        flash(f'Solicitud {s.codigo_solicitud} cancelada.', 'success')
    return redirect(url_for('admin.acciones_solicitudes_proceso'))


from datetime import date
from sqlalchemy import func

@admin_bp.route('/clientes/resumen_diario')
@login_required
@admin_required
def resumen_diario_clientes():
    hoy = date.today()

    # Agrupa sólo las solicitudes de hoy por cliente
    resumen = (
        db.session.query(
            Cliente.nombre_completo,
            Cliente.codigo,
            Cliente.telefono,
            func.count(Solicitud.id).label('total_solicitudes')
        )
        .join(Solicitud, Solicitud.cliente_id == Cliente.id)
        .filter(func.date(Solicitud.fecha_solicitud) == hoy)
        .group_by(Cliente.id, Cliente.nombre_completo, Cliente.codigo, Cliente.telefono)
        .order_by(Cliente.nombre_completo)
        .all()
    )

    return render_template(
        'admin/clientes_resumen_diario.html',
        resumen=resumen,
        hoy=hoy
    )
