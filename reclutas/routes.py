from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app
from flask_login import login_required, current_user
from sqlalchemy import or_

from app import db
from models import ReclutaPerfil, ReclutaCambio, TIPOS_EMPLEO_GENERAL
from .forms import ReclutaForm

reclutas_bp = Blueprint(
    'reclutas',
    __name__,
    url_prefix='/reclutas'
)


# ─────────────────────────────────────────────────────────────
# LISTA + FILTROS
# ─────────────────────────────────────────────────────────────
@reclutas_bp.route('/', methods=['GET'])
@login_required
def lista():
    query = ReclutaPerfil.query

    # Filtros simples por GET
    estado = request.args.get('estado')
    empleo = request.args.get('empleo')
    ciudad = request.args.get('ciudad')
    q = request.args.get('q')

    if estado:
        query = query.filter(ReclutaPerfil.estado == estado)

    if empleo:
        query = query.filter(
            or_(
                ReclutaPerfil.empleo_principal == empleo,
                ReclutaPerfil.tipos_empleo_busca.any(empleo)
            )
        )

    if ciudad:
        query = query.filter(ReclutaPerfil.ciudad.ilike(f"%{ciudad}%"))

    if q:
        query = query.filter(
            or_(
                ReclutaPerfil.nombre_completo.ilike(f"%{q}%"),
                ReclutaPerfil.cedula.ilike(f"%{q}%"),
                ReclutaPerfil.telefono.ilike(f"%{q}%")
            )
        )

    reclutas = query.order_by(ReclutaPerfil.created_at.desc()).all()

    return render_template(
        'reclutas/lista.html',
        reclutas=reclutas,
        tipos_empleo=TIPOS_EMPLEO_GENERAL
    )


# ─────────────────────────────────────────────────────────────
# CREAR
# ─────────────────────────────────────────────────────────────
@reclutas_bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo():
    form = ReclutaForm()

    if form.validate_on_submit():
        recluta = ReclutaPerfil(
            codigo=None,  # se asigna al inscribir, no al crear
            nombre_completo=form.nombre_completo.data,
            cedula=form.cedula.data,
            edad=form.edad.data,
            sexo=form.sexo.data,
            nacionalidad=form.nacionalidad.data,
            telefono=form.telefono.data,
            email=form.email.data,
            direccion_completa=form.direccion_completa.data,
            ciudad=form.ciudad.data,
            sector=form.sector.data,
            tipos_empleo_busca=form.tipos_empleo_busca.data,
            empleo_principal=form.empleo_principal.data,
            modalidad=form.modalidad.data,
            horario_disponible=form.horario_disponible.data,
            sueldo_esperado=form.sueldo_esperado.data,
            tiene_experiencia=form.tiene_experiencia.data,
            anos_experiencia=form.anos_experiencia.data,
            experiencia_resumen=form.experiencia_resumen.data,
            nivel_educativo=form.nivel_educativo.data,
            habilidades=form.habilidades.data,
            documentos_al_dia=form.documentos_al_dia.data,
            disponible_fines_o_noches=form.disponible_fines_o_noches.data,
            observaciones_internas=form.observaciones_internas.data,
            creado_por=current_user.email if hasattr(current_user, 'email') else str(current_user)
        )

        db.session.add(recluta)
        db.session.commit()

        cambio = ReclutaCambio(
            recluta_id=recluta.id,
            accion='creado',
            usuario=recluta.creado_por,
            nota='Perfil creado'
        )
        db.session.add(cambio)
        db.session.commit()

        flash('Perfil creado correctamente', 'success')
        return redirect(url_for('reclutas.lista'))

    return render_template('reclutas/form.html', form=form, modo='nuevo')


# ─────────────────────────────────────────────────────────────
# EDITAR
# ─────────────────────────────────────────────────────────────
@reclutas_bp.route('/<int:recluta_id>/editar', methods=['GET', 'POST'])
@login_required
def editar(recluta_id):
    recluta = ReclutaPerfil.query.get_or_404(recluta_id)
    form = ReclutaForm(obj=recluta)

    if form.validate_on_submit():
        form.populate_obj(recluta)
        recluta.actualizado_por = current_user.email if hasattr(current_user, 'email') else str(current_user)

        db.session.commit()

        cambio = ReclutaCambio(
            recluta_id=recluta.id,
            accion='editado',
            usuario=recluta.actualizado_por,
            nota='Perfil editado'
        )
        db.session.add(cambio)
        db.session.commit()

        flash('Perfil actualizado', 'success')
        return redirect(url_for('reclutas.lista'))

    return render_template('reclutas/form.html', form=form, modo='editar', recluta=recluta)


# ─────────────────────────────────────────────────────────────
# DETALLE
# ─────────────────────────────────────────────────────────────
@reclutas_bp.route('/<int:recluta_id>', methods=['GET'])
@login_required
def detalle(recluta_id):
    recluta = ReclutaPerfil.query.get_or_404(recluta_id)
    cambios = ReclutaCambio.query.filter_by(recluta_id=recluta.id).order_by(ReclutaCambio.creado_en.desc()).all()
    return render_template('reclutas/detalle.html', recluta=recluta, cambios=cambios)


# ─────────────────────────────────────────────────────────────
# CAMBIAR ESTADO (APROBAR / RECHAZAR)
# ─────────────────────────────────────────────────────────────
@reclutas_bp.route('/<int:recluta_id>/estado/<string:nuevo_estado>', methods=['POST'])
@login_required
def cambiar_estado(recluta_id, nuevo_estado):
    if nuevo_estado not in ('nuevo', 'aprobado', 'rechazado'):
        flash('Estado inválido', 'danger')
        return redirect(url_for('reclutas.lista'))

    recluta = ReclutaPerfil.query.get_or_404(recluta_id)
    recluta.estado = nuevo_estado
    recluta.actualizado_por = current_user.email if hasattr(current_user, 'email') else str(current_user)

    db.session.commit()

    cambio = ReclutaCambio(
        recluta_id=recluta.id,
        accion=f'estado_{nuevo_estado}',
        usuario=recluta.actualizado_por,
        nota=f'Estado cambiado a {nuevo_estado}'
    )
    db.session.add(cambio)
    db.session.commit()

    flash(f'Estado actualizado a {nuevo_estado}', 'success')
    return redirect(url_for('reclutas.lista'))


# ─────────────────────────────────────────────────────────────
# INSCRIPCIÓN (PÁGINA + PROCESO)
# ─────────────────────────────────────────────────────────────
@reclutas_bp.route('/<int:recluta_id>/inscribir', methods=['GET', 'POST'])
@login_required
def inscribir(recluta_id):
    """Inscribe un recluta: asigna código único y registra pago/fecha/vía (RD$500)."""
    recluta = ReclutaPerfil.query.get_or_404(recluta_id)

    # Si ya está inscrito, no repetir
    if getattr(recluta, 'inscrito', False):
        flash('Este perfil ya está inscrito.', 'info')
        return redirect(url_for('reclutas.detalle', recluta_id=recluta.id))

    if request.method == 'GET':
        # Página dedicada (HTML lo hacemos después)
        return render_template('reclutas/inscribir.html', recluta=recluta)

    # ───────────── POST: procesar inscripción ─────────────
    via = (request.form.get('via') or '').strip().lower()
    if via not in ('oficina', 'transferencia'):
        flash('Selecciona una vía válida (oficina o transferencia).', 'danger')
        return redirect(url_for('reclutas.inscribir', recluta_id=recluta.id))

    # Monto fijo
    monto = 500

    # Fecha: si viene del form úsala; si no, usa hoy
    from datetime import date
    fecha = request.form.get('fecha')
    try:
        fecha_ins = date.fromisoformat(fecha) if fecha else date.today()
    except Exception:
        fecha_ins = date.today()

    # Asignar código SOLO al inscribir (si no tiene)
    if not getattr(recluta, 'codigo', None):
        last_with_code = ReclutaPerfil.query.filter(ReclutaPerfil.codigo.isnot(None)).order_by(ReclutaPerfil.id.desc()).first()
        next_id = (last_with_code.id + 1) if last_with_code else 1
        recluta.codigo = f"REC-{str(next_id).zfill(6)}"

    # Guardar datos de inscripción (campos nuevos del modelo)
    recluta.inscrito = True
    recluta.inscripcion_monto = monto
    recluta.inscripcion_fecha = fecha_ins
    recluta.inscripcion_via = via

    # Al inscribir queda aprobado
    recluta.estado = 'aprobado'
    recluta.actualizado_por = current_user.email if hasattr(current_user, 'email') else str(current_user)

    db.session.commit()

    cambio = ReclutaCambio(
        recluta_id=recluta.id,
        accion='inscrito',
        usuario=recluta.actualizado_por,
        nota=f'Inscripción registrada: RD$ {monto} vía {via}'
    )
    db.session.add(cambio)
    db.session.commit()

    flash('Inscripción registrada correctamente.', 'success')
    return redirect(url_for('reclutas.detalle', recluta_id=recluta.id))
