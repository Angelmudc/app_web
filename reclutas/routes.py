from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user
from sqlalchemy import or_

from app import db
from models import ReclutaPerfil, ReclutaCambio, TIPOS_EMPLEO_GENERAL
from .forms import ReclutaForm, ReclutaPublicForm

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
# REGISTRO PÚBLICO (NO REQUIERE LOGIN)
#  - NO toca /reclutas/nuevo
#  - Se usa para que cualquiera pueda enviar su perfil
# ─────────────────────────────────────────────────────────────
@reclutas_bp.route('/registro', methods=['GET', 'POST'])
def registro_publico():
    form = ReclutaPublicForm()

    if form.validate_on_submit():
        recluta = ReclutaPerfil(
            estado='nuevo',
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
            empleo_principal=None,
            modalidad=form.modalidad.data,
            horario_disponible=form.horario_disponible.data,
            sueldo_esperado=form.sueldo_esperado.data,
            tiene_experiencia=form.tiene_experiencia.data,
            anos_experiencia=form.anos_experiencia.data,
            experiencia_resumen=form.experiencia_resumen.data,
            nivel_educativo=form.nivel_educativo.data,
            habilidades=form.habilidades.data,
            referencias_laborales=form.referencias_laborales.data,
            referencias_familiares=form.referencias_familiares.data,
            documentos_al_dia=form.documentos_al_dia.data,
            disponible_fines_o_noches=form.disponible_fines_o_noches.data,
            # Público: no se guardan observaciones internas
            observaciones_internas=None,
            # Público: no hay usuario autenticado
            creado_por='publico'
        )

        db.session.add(recluta)
        db.session.commit()

        cambio = ReclutaCambio(
            recluta_id=recluta.id,
            accion='creado_publico',
            usuario='publico',
            nota='Perfil creado desde registro público'
        )
        db.session.add(cambio)
        db.session.commit()

        flash('Gracias. Tu información fue enviada correctamente.', 'success')
        return redirect(url_for('reclutas.registro_publico_ok'))

    return render_template(
        'reclutas/registro_publico.html',
        form=form,
        modo='publico',
        is_public_page=True
    )


@reclutas_bp.route('/registro/gracias', methods=['GET'])
def registro_publico_ok():
    return render_template(
        'reclutas/registro_publico_ok.html',
        is_public_page=True
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
            referencias_laborales=form.referencias_laborales.data,
            referencias_familiares=form.referencias_familiares.data,
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
# ELIMINAR (BORRAR PERFIL)
#  - Solo usuarios autenticados (admin/secretaria)
#  - POST únicamente
# ─────────────────────────────────────────────────────────────
@reclutas_bp.route('/<int:recluta_id>/eliminar', methods=['POST'])
@login_required
def eliminar(recluta_id):
    recluta = ReclutaPerfil.query.get_or_404(recluta_id)

    # Guardar quién eliminó
    eliminado_por = current_user.email if hasattr(current_user, 'email') else str(current_user)

    try:
        # Borra primero el historial para evitar problemas de FK
        ReclutaCambio.query.filter_by(recluta_id=recluta.id).delete(synchronize_session=False)
        db.session.delete(recluta)
        db.session.commit()

        flash('Perfil eliminado correctamente', 'success')
    except Exception as e:
        db.session.rollback()
        flash('No se pudo eliminar el perfil. Intenta de nuevo.', 'danger')

    return redirect(url_for('reclutas.lista'))
