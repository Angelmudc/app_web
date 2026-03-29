from flask import Blueprint, current_app, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user
from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError

from config_app import db, cache
from models import ReclutaPerfil, ReclutaCambio, TIPOS_EMPLEO_GENERAL
from .forms import ReclutaForm, ReclutaPublicForm
from decorators import staff_required
from utils.public_intake import get_request_ip, hit_rate_limit
from utils.business_guard import enforce_business_limit, enforce_min_human_interval
from utils.staff_notifications import create_staff_notification

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
@staff_required
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

    if request.method == "POST":
        ip = get_request_ip(request)
        if not bool(current_app.config.get("TESTING")):
            if hit_rate_limit(cache=cache, scope="registro_general", actor=ip, limit=8, window_seconds=600):
                flash("Demasiados intentos en poco tiempo. Espera unos minutos e intenta de nuevo.", "warning")
                return render_template(
                    'reclutas/registro_publico.html',
                    form=form,
                    modo='publico',
                    is_public_page=True
                ), 429
            blocked, _ = enforce_business_limit(
                cache_obj=cache,
                scope="reclutas_publico_ip_1h",
                actor=ip,
                limit=20,
                window_seconds=3600,
                reason="ip_hourly_limit",
                summary="Bloqueo por actividad excesiva en reclutas público",
                metadata={"route": (request.path or ""), "channel": "empleo_general"},
            )
            if blocked:
                flash("Detectamos demasiados envíos en poco tiempo. Intenta nuevamente más tarde.", "warning")
                return render_template(
                    'reclutas/registro_publico.html',
                    form=form,
                    modo='publico',
                    is_public_page=True
                ), 429
            blocked_fast, _ = enforce_min_human_interval(
                cache_obj=cache,
                scope="reclutas_publico_submit_interval",
                actor=ip,
                min_seconds=2,
                reason="timing_too_fast",
                summary="Patrón no humano detectado en reclutas público",
                metadata={"route": (request.path or ""), "channel": "empleo_general"},
            )
            if blocked_fast:
                flash("Espera un momento antes de volver a enviar el formulario.", "warning")
                return render_template(
                    'reclutas/registro_publico.html',
                    form=form,
                    modo='publico',
                    is_public_page=True
                ), 429

        # Honeypot anti-bot: aceptamos y respondemos sin procesar.
        if (form.bot_field.data or "").strip():
            return redirect(url_for('reclutas.registro_publico_ok'))

    if form.validate_on_submit():
        try:
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
                creado_por='publico',
                origen_registro='publico_empleo_general',
                creado_desde_ruta=(request.path or '').strip()[:120] or '/reclutas/registro',
            )

            db.session.add(recluta)
            db.session.flush()
            cambio = ReclutaCambio(
                recluta_id=recluta.id,
                accion='creado_publico',
                usuario='publico',
                nota='Perfil creado desde registro público'
            )
            db.session.add(cambio)
            db.session.commit()
            try:
                create_staff_notification(
                    tipo="publico_empleo_general_nuevo",
                    entity_type="recluta_perfil",
                    entity_id=int(getattr(recluta, "id", 0) or 0),
                    titulo="Nuevo recluta por formulario público",
                    mensaje=(getattr(recluta, "nombre_completo", None) or "").strip()[:300] or None,
                    payload={
                        "origen_registro": "publico_empleo_general",
                        "source_route": (request.path or "").strip(),
                        "recluta_id": int(getattr(recluta, "id", 0) or 0),
                    },
                )
            except Exception:
                pass
        except SQLAlchemyError:
            db.session.rollback()
            flash('No se pudo enviar el formulario en este momento. Intenta de nuevo en unos minutos.', 'danger')
            return render_template(
                'reclutas/registro_publico.html',
                form=form,
                modo='publico',
                is_public_page=True
            ), 500

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
@staff_required
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
            creado_por=current_user.email if hasattr(current_user, 'email') else str(current_user),
            origen_registro='interno',
            creado_desde_ruta=(request.path or '').strip()[:120] or '/reclutas/nuevo',
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
@staff_required
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
@staff_required
def detalle(recluta_id):
    recluta = ReclutaPerfil.query.get_or_404(recluta_id)
    cambios = ReclutaCambio.query.filter_by(recluta_id=recluta.id).order_by(ReclutaCambio.creado_en.desc()).all()
    return render_template('reclutas/detalle.html', recluta=recluta, cambios=cambios)


# ─────────────────────────────────────────────────────────────
# CAMBIAR ESTADO (APROBAR / RECHAZAR)
# ─────────────────────────────────────────────────────────────
@reclutas_bp.route('/<int:recluta_id>/estado/<string:nuevo_estado>', methods=['POST'])
@login_required
@staff_required
def cambiar_estado(recluta_id, nuevo_estado):
    if nuevo_estado not in ('nuevo', 'aprobado', 'rechazado'):
        flash('Estado inválido', 'danger')
        return redirect(url_for('reclutas.lista'))

    actor = str(getattr(current_user, "id", "") or getattr(current_user, "email", "") or "staff")
    blocked, _ = enforce_business_limit(
        cache_obj=cache,
        scope="reclutas_staff_estado_10m",
        actor=actor,
        limit=30,
        window_seconds=600,
        reason="state_change_burst",
        summary="Bloqueo por cambios masivos de estado en reclutas",
        metadata={"route": (request.path or ""), "target_state": nuevo_estado},
    )
    if blocked:
        flash('Demasiados cambios de estado en poco tiempo. Intenta nuevamente en unos minutos.', 'warning')
        return redirect(url_for('reclutas.lista'))

    recluta = ReclutaPerfil.query.get_or_404(recluta_id)
    if (recluta.estado or "") == nuevo_estado:
        flash('Este perfil ya tiene ese estado.', 'info')
        return redirect(url_for('reclutas.detalle', recluta_id=recluta.id))

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
@staff_required
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
