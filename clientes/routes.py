from flask import (
    Blueprint, render_template, redirect,
    url_for, flash, request, abort
)
from flask_login import login_required, current_user, login_user, logout_user
from werkzeug.security import check_password_hash
from datetime import datetime, date
from functools import wraps

from .forms import (
    ClienteLoginForm,
    ClienteCancelForm,
    SolicitudForm,
    ClienteSolicitudForm
)
from models import Cliente, Solicitud
from config_app import db
from utils import letra_por_indice


def cliente_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # Si no está autenticado o no es instancia de Cliente, redirige al login de clientes
        if not current_user.is_authenticated or not isinstance(current_user, Cliente):
            return redirect(url_for('clientes.login', next=request.url))
        return f(*args, **kwargs)
    return decorated


clientes_bp = Blueprint(
    'clientes',
    __name__,
    url_prefix='/clientes',
    template_folder='../templates/clientes'
)

@clientes_bp.route('/login', methods=['GET', 'POST'])
def login():
    form = ClienteLoginForm()
    if form.validate_on_submit():
        user = Cliente.query.filter_by(username=form.username.data).first()
        if user and check_password_hash(user.password_hash, form.password.data):
            login_user(user)
            return redirect(url_for('clientes.dashboard'))
        flash('Usuario o contraseña inválidos.', 'danger')
    return render_template('clientes/login.html', form=form)

@clientes_bp.route('/logout')
@login_required
@cliente_required
def logout():
    logout_user()
    return redirect(url_for('clientes.login'))

@clientes_bp.route('/')
@login_required
@cliente_required
def dashboard():
    total = Solicitud.query.filter_by(cliente_id=current_user.id).count()
    recientes = (
        Solicitud.query
        .filter_by(cliente_id=current_user.id)
        .order_by(Solicitud.fecha_solicitud.desc())
        .limit(3)
        .all()
    )
    return render_template(
        'clientes/dashboard.html',
        total_solicitudes=total,
        recientes=recientes
    )

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
    return render_template('clientes/ayuda.html')



@clientes_bp.route('/solicitudes')
@login_required
@cliente_required
def listar_solicitudes():
    solicitudes = (
        Solicitud.query
        .filter_by(cliente_id=current_user.id)
        .order_by(Solicitud.fecha_solicitud.desc())
        .all()
    )
    # pasamos también la fecha de hoy para el badge “Publicado hoy”
    return render_template(
        'clientes/solicitudes_list.html',
        solicitudes=solicitudes,
        hoy=date.today()
    )




@clientes_bp.route('/solicitudes/nueva', methods=['GET', 'POST'])
@login_required
@cliente_required
def nueva_solicitud():
    form = SolicitudForm()

    if form.validate_on_submit():
        # Generar código único
        count  = Solicitud.query.filter_by(cliente_id=current_user.id).count()
        codigo = f"{current_user.codigo}-{letra_por_indice(count)}"

        # Crear instancia de Solicitud (edad_requerida como CSV)
        s = Solicitud(
            cliente_id        = current_user.id,
            fecha_solicitud   = datetime.utcnow(),
            codigo_solicitud  = codigo,
            ciudad_sector     = form.ciudad_sector.data,
            rutas_cercanas    = form.rutas_cercanas.data,
            modalidad_trabajo = form.modalidad_trabajo.data,
            edad_requerida    = ','.join(form.edad_requerida.data),
            experiencia       = form.experiencia.data,
            horario           = form.horario.data,
            funciones         = form.funciones.data,
            tipo_lugar        = form.tipo_lugar.data,
            habitaciones      = form.habitaciones.data,
            banos             = form.banos.data,
            dos_pisos         = form.dos_pisos.data,
            adultos           = form.adultos.data,
            ninos             = form.ninos.data,
            edades_ninos      = form.edades_ninos.data,
            sueldo            = form.sueldo.data,
            pasaje_aporte     = form.pasaje_aporte.data,
            nota_cliente      = form.nota_cliente.data,
            areas_comunes     = form.areas_comunes.data,
            area_otro         = form.area_otro.data
        )

        db.session.add(s)
        db.session.commit()

        flash(f'Solicitud {codigo} creada correctamente.', 'success')
        return redirect(url_for('clientes.listar_solicitudes'))

    return render_template('clientes/solicitud_form.html', form=form)


@clientes_bp.route('/solicitudes/<int:id>/editar', methods=['GET','POST'])
@login_required
@cliente_required
def editar_solicitud(id):
    s = Solicitud.query.filter_by(id=id, cliente_id=current_user.id).first_or_404()
    form = SolicitudForm(obj=s)

    if request.method == 'GET':
        # Pre-carga manual de todos los campos múltiples
        form.edad_requerida.data    = s.edad_requerida or []
        form.areas_comunes.data      = s.areas_comunes   or []
        form.area_otro.data          = s.area_otro       or ''
        form.nota_cliente.data       = s.nota_cliente    or ''

    if form.validate_on_submit():
        form.populate_obj(s)
        # los que populate_obj no maneja bien
        s.edad_requerida             = form.edad_requerida.data
        s.areas_comunes              = form.areas_comunes.data
        s.area_otro                  = form.area_otro.data
        s.nota_cliente               = form.nota_cliente.data
        s.fecha_ultima_modificacion  = datetime.utcnow()

        db.session.commit()
        flash('Solicitud actualizada.', 'success')
        return redirect(url_for('clientes.detalle_solicitud', id=id))

    return render_template(
        'clientes/solicitud_form.html',
        form=form,
        editar=True,
        solicitud=s
    )



from datetime import datetime

@clientes_bp.route('/solicitudes/<int:id>')
@login_required
@cliente_required
def detalle_solicitud(id):
    s = Solicitud.query.filter_by(id=id, cliente_id=current_user.id).first_or_404()

    # 1) Historial de envíos (inicial + reemplazos)
    envios = []
    if s.candidata:
        envios.append({
            'tipo': 'Envío inicial',
            'candidata': s.candidata.nombre_completo,
            'fecha': s.fecha_solicitud
        })
    for idx, r in enumerate(s.reemplazos, start=1):
        if getattr(r, 'candidata_new', None):
            envios.append({
                'tipo': f'Reemplazo #{idx}',
                'candidata': r.candidata_new.nombre_completo,
                'fecha': r.fecha_inicio_reemplazo or r.created_at
            })

    # 2) Historial de cancelaciones
    cancelaciones = []
    if s.estado == 'cancelada' and s.fecha_cancelacion:
        cancelaciones.append({
            'fecha': s.fecha_cancelacion,
            'motivo': s.motivo_cancelacion
        })

    return render_template(
        'clientes/solicitud_detail.html',
        s=s,
        envios=envios,
        cancelaciones=cancelaciones,
        hoy=date.today()
    )

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
