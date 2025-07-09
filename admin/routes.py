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

from utils import letra_por_indice


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
    cliente_form = AdminClienteForm()

    if cliente_form.validate_on_submit():
        # 1) Validar unicidad de código y usuario
        existe_codigo = Cliente.query.filter_by(codigo=cliente_form.codigo.data).first()
        existe_usuario = Cliente.query.filter_by(username=cliente_form.username.data).first()
        if existe_codigo:
            flash(f"El código «{cliente_form.codigo.data}» ya está en uso.", "danger")
        elif existe_usuario:
            flash(f"El usuario «{cliente_form.username.data}» ya existe.", "danger")
        else:
            # 2) Crear instancia y poblar campos
            c = Cliente()
            cliente_form.populate_obj(c)
            # 3) Asignar credenciales
            c.username = cliente_form.username.data
            c.set_password(cliente_form.password.data)
            # 4) Fecha de registro y guardado
            c.fecha_registro = datetime.utcnow()
            db.session.add(c)
            db.session.commit()
            flash('Cliente creado correctamente.', 'success')
            return redirect(url_for('admin.listar_clientes'))

    return render_template(
        'admin/cliente_form.html',
        cliente_form=cliente_form,
        nuevo=True
    )


@admin_bp.route('/clientes/<int:cliente_id>/editar', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_cliente(cliente_id):
    c = Cliente.query.get_or_404(cliente_id)
    form = AdminClienteForm(obj=c)

    if request.method == 'GET':
        # dejar el usuario para edición, pero no la contraseña
        form.password.data = ''
        form.confirm.data  = ''

    if form.validate_on_submit():
        # Actualizar datos básicos
        form.populate_obj(c)
        # Si vino contraseña, actualizar hash
        if form.password.data:
            c.set_password(form.password.data)
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


@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/nueva', methods=['GET','POST'])
@login_required
@admin_required
def nueva_solicitud_admin(cliente_id):
    c = Cliente.query.get_or_404(cliente_id)
    form = AdminSolicitudForm()
    form.areas_comunes.choices = AREAS_COMUNES_CHOICES

    if request.method == 'GET':
        form.funciones.data      = []
        form.areas_comunes.data  = []
        form.area_otro.data      = ''
        form.edad_requerida.data = ''

    if form.validate_on_submit():
        # Genera el código automáticamente, sin leer nada del formulario
        count       = Solicitud.query.filter_by(cliente_id=c.id).count()
        nuevo_codigo = f"{c.codigo}-{letra_por_indice(count)}"

        # Crea la solicitud con el código generado
        s = Solicitud(
            cliente_id       = c.id,
            fecha_solicitud  = datetime.utcnow(),
            codigo_solicitud = nuevo_codigo
        )
        # Sobreescribe los campos del modelo con los del formulario
        form.populate_obj(s)

        # Ajustes de arrays y campos especiales
        s.edad_requerida = [form.edad_requerida.data]
        s.funciones      = form.funciones.data
        s.areas_comunes  = form.areas_comunes.data
        s.area_otro      = form.area_otro.data

        # Guarda todo
        db.session.add(s)
        c.total_solicitudes      = count + 1
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
        form.edad_requerida.data = (s.edad_requerida or [''])[0]
        form.funciones.data      = s.funciones or []
        form.areas_comunes.data  = s.areas_comunes or []
        form.area_otro.data      = s.area_otro or ''

    if form.validate_on_submit():
        form.populate_obj(s)
        s.edad_requerida           = [form.edad_requerida.data]
        s.funciones                = form.funciones.data
        s.areas_comunes            = form.areas_comunes.data
        s.area_otro                = form.area_otro.data
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

@admin_bp.route(
    '/clientes/<int:cliente_id>/solicitudes/<int:id>/cancelar',
    methods=['GET', 'POST']
)
@login_required
@admin_required
def cancelar_solicitud(cliente_id, id):
    # Cargamos solo la solicitud de ese cliente
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()

    if request.method == 'POST':
        motivo = request.form.get('motivo', '').strip()
        if not motivo:
            flash('Debes indicar un motivo de cancelación.', 'warning')
        else:
            # Marcamos cancelada sin borrar nada más
            s.estado = 'cancelada'
            s.fecha_cancelacion = datetime.utcnow()
            s.motivo_cancelacion = motivo
            db.session.commit()
            flash('Solicitud cancelada con éxito.', 'success')
            # Volvemos al detalle de cliente
            return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))

    return render_template(
        'admin/cancelar_solicitud.html',
        solicitud=s
    )