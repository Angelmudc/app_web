from flask import (
    Blueprint, render_template, redirect, url_for,
    flash, request, jsonify, abort
)
from flask_login import (
    login_user, logout_user, login_required,
    UserMixin, current_user
)
from werkzeug.security import check_password_hash
from datetime import datetime, date

from sqlalchemy import or_, func, cast
from sqlalchemy.types import Date, Numeric

from config_app import db, USUARIOS
from models import Cliente, Solicitud, Candidata, Reemplazo
from admin.forms import (
    AdminClienteForm,
    AdminSolicitudForm,
    AdminPagoForm,
    AdminReemplazoForm,
    AdminGestionPlanForm
)


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
    cliente_form   = AdminClienteForm()
    solicitud_form = AdminSolicitudForm(prefix='sol')
    if cliente_form.validate_on_submit():
        c = Cliente()
        cliente_form.populate_obj(c)
        c.fecha_registro = datetime.utcnow()
        db.session.add(c)
        db.session.flush()
        if solicitud_form.codigo_solicitud.data:
            s = Solicitud(cliente_id=c.id, fecha_solicitud=datetime.utcnow())
            solicitud_form.populate_obj(s)
            db.session.add(s)
            c.total_solicitudes      = 1
            c.fecha_ultima_solicitud = datetime.utcnow()
        db.session.commit()
        flash('Cliente y solicitud creados correctamente.', 'success')
        return redirect(url_for('admin.listar_clientes'))
    return render_template(
        'admin/cliente_form.html',
        cliente_form=cliente_form,
        solicitud_form=solicitud_form,
        nuevo=True
    )


@admin_bp.route('/clientes/<int:cliente_id>')
@login_required
@admin_required
def detalle_cliente(cliente_id):
    c = Cliente.query.get_or_404(cliente_id)
    return render_template('admin/cliente_detail.html', cliente=c)


@admin_bp.route('/clientes/<int:cliente_id>/editar', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_cliente(cliente_id):
    c = Cliente.query.get_or_404(cliente_id)
    form = AdminClienteForm(obj=c)
    if form.validate_on_submit():
        form.populate_obj(c)
        c.fecha_ultima_actividad = datetime.utcnow()
        db.session.commit()
        flash('Cliente actualizado.', 'success')
        return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))
    return render_template('admin/cliente_form.html', cliente_form=form, nuevo=False)


@admin_bp.route('/clientes/<int:cliente_id>/eliminar', methods=['POST'])
@login_required
@admin_required
def eliminar_cliente(cliente_id):
    c = Cliente.query.get_or_404(cliente_id)
    db.session.delete(c)
    db.session.commit()
    flash('Cliente eliminado.', 'success')
    return redirect(url_for('admin.listar_clientes'))


# ——— Rutas de Solicitudes y Reemplazos ———

AREAS_COMUNES_CHOICES = [
    ('sala', 'Sala'), ('comedor', 'Comedor'),
    ('cocina','Cocina'), ('salon_juegos','Salón de juegos'),
    ('terraza','Terraza'), ('jardin','Jardín'),
    ('estudio','Estudio'), ('patio','Patio'),
    ('piscina','Piscina'), ('marquesina','Marquesina'),
    ('todas_anteriores','Todas las anteriores'),
]

@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/nueva', methods=['GET','POST'])
@login_required
@admin_required
def nueva_solicitud_admin(cliente_id):
    c = Cliente.query.get_or_404(cliente_id)
    form = AdminSolicitudForm()
    form.areas_comunes.choices = AREAS_COMUNES_CHOICES
    if form.validate_on_submit():
        idx = c.total_solicitudes or 0
        def letra_por_indice(i):
            res = ''
            while True:
                res = chr(ord('A') + (i % 26)) + res
                i = i // 26 - 1
                if i < 0: break
            return res
        nuevo_codigo = f"{c.codigo}-{letra_por_indice(idx)}"
        s = Solicitud(
            cliente_id       = c.id,
            fecha_solicitud  = datetime.utcnow(),
            codigo_solicitud = nuevo_codigo
        )
        form.populate_obj(s)
        s.codigo_solicitud = nuevo_codigo
        s.areas_comunes = form.areas_comunes.data
        s.area_otro     = form.area_otro.data
        db.session.add(s)
        c.total_solicitudes      = idx + 1
        c.fecha_ultima_solicitud = datetime.utcnow()
        c.fecha_ultima_actividad = datetime.utcnow()
        db.session.commit()
        flash(f'Solicitud {nuevo_codigo} creada correctamente.', 'success')
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
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()
    form = AdminSolicitudForm(obj=s)
    form.areas_comunes.choices = AREAS_COMUNES_CHOICES
    if request.method == 'GET':
        form.areas_comunes.data = s.areas_comunes or []
        form.area_otro.data     = s.area_otro or ''
    if form.validate_on_submit():
        form.populate_obj(s)
        s.areas_comunes = form.areas_comunes.data
        s.area_otro     = form.area_otro.data
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
        s.candidata_id = form.candidata_id.data
        s.monto_pagado = form.monto_pagado.data
        s.estado       = 'pagada'
        s.fecha_ultima_actividad = datetime.utcnow()
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
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()
    reemplazos = s.reemplazos
    envios = []
    if s.candidata:
        envios.append({'tipo':'Envío inicial','candidata':s.candidata,'fecha':s.fecha_solicitud})
    for idx, r in enumerate(reemplazos, start=1):
        if r.candidata_new:
            envios.append({
                'tipo': f'Reemplazo {idx}',
                'candidata': r.candidata_new,
                'fecha': r.fecha_inicio_reemplazo or r.created_at
            })
    return render_template(
        'admin/solicitud_detail.html',
        solicitud=s,
        envios=envios,
        reemplazos=reemplazos
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


@admin_bp.route('/solicitudes/resumen')
@login_required
@admin_required
def resumen_solicitudes():
    proc_count = Solicitud.query.filter_by(estado='proceso').count()
    act_count  = Solicitud.query.filter_by(estado='activa').count()
    pag_count  = Solicitud.query.filter_by(estado='pagada').count()
    stats_mensual = db.session.query(
        func.date_trunc('month', Solicitud.fecha_solicitud).label('mes'),
        func.count(Solicitud.id).label('cantidad'),
        func.sum(
            cast(func.replace(Solicitud.monto_pagado, ',',''), Numeric(12,2))
        ).label('total_pagado')
    ).filter(Solicitud.estado=='pagada') \
     .group_by('mes') \
     .order_by('mes') \
     .all()
    return render_template(
        'admin/solicitudes_resumen.html',
        proc_count=proc_count,
        act_count=act_count,
        pag_count=pag_count,
        stats_mensual=stats_mensual
    )


@admin_bp.route('/solicitudes/copiar')
@login_required
@admin_required
def copiar_solicitudes():
    hoy = date.today()
    base = Solicitud.query\
        .filter(Solicitud.estado.in_(['activa','reemplazo']))\
        .filter(
            or_(
                Solicitud.last_copiado_at.is_(None),
                func.date(Solicitud.last_copiado_at) < hoy
            )
        )
    con_reemp = base\
        .filter(Solicitud.estado=='reemplazo')\
        .order_by(Solicitud.fecha_solicitud.desc())\
        .all()
    sin_reemp =	base\
        .filter(Solicitud.estado=='activa')\
        .order_by(Solicitud.fecha_solicitud.desc())\
        .all()
    solicitudes = con_reemp + sin_reemp
    return render_template(
        'admin/solicitudes_copiar.html',
        solicitudes=solicitudes
    )


@admin_bp.route('/solicitudes/<int:id>/copiar', methods=['POST'])
@login_required
@admin_required
def copiar_solicitud(id):
    s = Solicitud.query.get_or_404(id)
    s.last_copiado_at = func.now()
    db.session.commit()
    flash(f'Solicitud {s.codigo_solicitud} copiada. Ya no se mostrará hasta mañana.', 'success')
    return redirect(url_for('admin.copiar_solicitudes'))
