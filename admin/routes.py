# -*- coding: utf-8 -*-
import re
from datetime import datetime, date, timedelta

from flask import render_template, redirect, url_for, flash, request, jsonify, abort
from flask_login import login_user, logout_user, login_required, UserMixin, current_user
from werkzeug.security import check_password_hash, generate_password_hash

from sqlalchemy import or_, func, cast, desc
from sqlalchemy.types import Numeric
from sqlalchemy.orm import joinedload  # ➜ para evitar N+1 en copiar_solicitudes
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from functools import wraps  # si otros decoradores locales lo usan

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


# =============================================================================
#                                AUTH
# =============================================================================
class AdminUser(UserMixin):
    def __init__(self, username: str):
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


# =============================================================================
#                            CLIENTES (CRUD BÁSICO)
# =============================================================================
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


# ─────────────────────────────────────────────────────────────
# Helpers de limpieza
# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
# Helpers locales
# ─────────────────────────────────────────────────────────────
def _only_digits(text: str) -> str:
    return re.sub(r"\D+", "", text or "")

def _norm_cliente_form(form: AdminClienteForm) -> None:
    """
    Normaliza/limpia entradas de texto del formulario de cliente.
    """
    def _strip(x):
        return x.strip() if isinstance(x, str) else x

    if hasattr(form, 'codigo') and form.codigo.data:
        form.codigo.data = _strip(form.codigo.data)

    if hasattr(form, 'nombre_completo') and form.nombre_completo.data:
        form.nombre_completo.data = _strip(form.nombre_completo.data)

    if hasattr(form, 'email') and form.email.data:
        # email siempre minúsculas y sin espacios
        form.email.data = _strip(form.email.data).lower()

    if hasattr(form, 'telefono') and form.telefono.data:
        # quita espacios extra; respeta guiones si los usas en la UI,
        # pero además guarda un formato limpio si quisieras
        form.telefono.data = _strip(form.telefono.data)

    if hasattr(form, 'ciudad') and form.ciudad.data:
        form.ciudad.data = _strip(form.ciudad.data)

    if hasattr(form, 'sector') and form.sector.data:
        form.sector.data = _strip(form.sector.data)

    if hasattr(form, 'notas_admin') and form.notas_admin.data:
        form.notas_admin.data = _strip(form.notas_admin.data)


def parse_integrity_error(err: IntegrityError) -> str:
    """
    Intenta detectar qué constraint única falló.
    Retorna 'codigo', 'email' o '' si no se pudo identificar.
    Funciona para SQLite, MySQL y PostgreSQL en la mayoría de casos.
    """
    # MySQL: (1062, "Duplicate entry 'x' for key 'clientes.email'") o ... for key 'email'
    # SQLite: UNIQUE constraint failed: clientes.email
    # Postgres: err.orig.diag.constraint_name ej. clientes_email_key
    msg = ""
    try:
        msg = str(getattr(err, "orig", err))
    except Exception:
        msg = str(err)

    m = msg.lower()

    # PostgreSQL: usa nombre del constraint
    try:
        cstr = getattr(getattr(err, "orig", None), "diag", None)
        if cstr and getattr(cstr, "constraint_name", None):
            cname = cstr.constraint_name.lower()
            if "codigo" in cname:
                return "codigo"
            if "email" in cname or "correo" in cname:
                return "email"
    except Exception:
        pass

    # MySQL/SQLite heurística por mensaje
    if "codigo" in m:
        return "codigo"
    if "email" in m or "correo" in m:
        return "email"

    # MySQL 'for key ...'
    if "for key" in m and "email" in m:
        return "email"
    if "for key" in m and "codigo" in m:
        return "codigo"

    return ""


# ─────────────────────────────────────────────────────────────
# Crear cliente
# ─────────────────────────────────────────────────────────────
@admin_bp.route('/clientes/nuevo', methods=['GET', 'POST'])
@login_required
@admin_required
def nuevo_cliente():
    form = AdminClienteForm()

    if form.validate_on_submit():
        _norm_cliente_form(form)

        # Unicidad de código (case-sensitive tal como lo tengas en la BD)
        if Cliente.query.filter(Cliente.codigo == form.codigo.data).first():
            form.codigo.errors.append("Este código ya está en uso.")
            flash("El código ya está en uso.", "danger")
            return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)

        # Unicidad de email, de forma case-insensitive
        email_norm = (form.email.data or "").lower().strip()
        if Cliente.query.filter(func.lower(Cliente.email) == email_norm).first():
            form.email.errors.append("Este email ya está registrado.")
            flash("El email ya está registrado.", "danger")
            return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)

        # Contraseña requerida + confirmación
        pwd  = (form.password_new.data or '').strip()
        pwd2 = (form.password_confirm.data or '').strip()
        if not pwd:
            form.password_new.errors.append("Debes establecer una contraseña.")
            flash("Debes establecer una contraseña.", "danger")
            return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)
        if pwd != pwd2:
            form.password_confirm.errors.append("La confirmación de contraseña no coincide.")
            flash("La confirmación de contraseña no coincide.", "danger")
            return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)

        try:
            c = Cliente()
            form.populate_obj(c)
            # Asegura que email quede normalizado
            c.email = email_norm
            # Genera hash
            c.password_hash = generate_password_hash(pwd)
            c.fecha_registro = datetime.utcnow()

            db.session.add(c)
            # fuerza verificación de constraints aquí para capturar el error arriba
            db.session.flush()
            db.session.commit()

            flash('Cliente creado correctamente.', 'success')
            return redirect(url_for('admin.listar_clientes'))

        except IntegrityError as e:
            db.session.rollback()
            which = parse_integrity_error(e)
            if which == "codigo":
                form.codigo.errors.append("Este código ya está en uso.")
                flash("El código ya está en uso.", "danger")
            elif which == "email":
                form.email.errors.append("Este email ya está registrado.")
                flash("Este email ya está registrado.", "danger")
            else:
                flash("Conflicto con datos únicos. Verifica código y/o email.", "danger")
            return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)

        except Exception:
            db.session.rollback()
            flash('Ocurrió un error al crear el cliente. Intenta de nuevo.', 'danger')
            return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)

    # GET o POST inválido
    if request.method == 'POST':
        flash('Revisa los campos marcados en rojo.', 'danger')

    return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)


# ─────────────────────────────────────────────────────────────
# Editar cliente
# ─────────────────────────────────────────────────────────────
@admin_bp.route('/clientes/<int:cliente_id>/editar', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_cliente(cliente_id):
    c = Cliente.query.get_or_404(cliente_id)
    form = AdminClienteForm(obj=c)

    if form.validate_on_submit():
        _norm_cliente_form(form)

        # Si (en el futuro) permites editar código en UI, valida unicidad
        if form.codigo.data != c.codigo:
            if Cliente.query.filter(Cliente.codigo == form.codigo.data).first():
                form.codigo.errors.append("Este código ya está en uso.")
                flash("El código ya está en uso.", "danger")
                return render_template('admin/cliente_form.html', cliente_form=form, nuevo=False)

        # Validar email (case-insensitive) si cambió
        email_norm = (form.email.data or "").lower().strip()
        if email_norm != (c.email or "").lower().strip():
            if Cliente.query.filter(func.lower(Cliente.email) == email_norm).first():
                form.email.errors.append("Este email ya está registrado.")
                flash("Este email ya está registrado.", "danger")
                return render_template('admin/cliente_form.html', cliente_form=form, nuevo=False)

        try:
            form.populate_obj(c)
            c.email = email_norm
            # Cambio de contraseña solo si viene y coincide
            pwd  = (form.password_new.data or '').strip()
            pwd2 = (form.password_confirm.data or '').strip()
            if pwd:
                if pwd != pwd2:
                    form.password_confirm.errors.append("La confirmación de contraseña no coincide.")
                    flash("La confirmación de contraseña no coincide.", "danger")
                    return render_template('admin/cliente_form.html', cliente_form=form, nuevo=False)
                c.password_hash = generate_password_hash(pwd)

            c.fecha_ultima_actividad = datetime.utcnow()

            db.session.flush()
            db.session.commit()
            flash('Cliente actualizado correctamente.', 'success')
            return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))

        except IntegrityError as e:
            db.session.rollback()
            which = parse_integrity_error(e)
            if which == "codigo":
                form.codigo.errors.append("Este código ya está en uso.")
                flash("El código ya está en uso.", "danger")
            elif which == "email":
                form.email.errors.append("Este email ya está registrado.")
                flash("Este email ya está registrado.", "danger")
            else:
                flash('No se pudo actualizar: conflicto con datos únicos (p. ej., código o email).', 'danger')
        except Exception:
            db.session.rollback()
            flash('Ocurrió un error al actualizar el cliente. Intenta de nuevo.', 'danger')

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


@admin_bp.route('/clientes/<int:cliente_id>')
@login_required
@admin_required
def detalle_cliente(cliente_id):
    c = Cliente.query.get_or_404(cliente_id)
    return render_template('admin/cliente_detail.html', cliente=c)


# =============================================================================
#                      CONSTANTES / CHOICES PARA FORMULARIOS
# =============================================================================
AREAS_COMUNES_CHOICES = [
    ('sala', 'Sala'), ('comedor', 'Comedor'),
    ('cocina','Cocina'), ('salon_juegos','Salón de juegos'),
    ('terraza','Terraza'), ('jardin','Jardín'),
    ('estudio','Estudio'), ('patio','Patio'),
    ('piscina','Piscina'), ('marquesina','Marquesina'),
    ('todas_anteriores','Todas las anteriores'),
]


# =============================================================================
#                              HELPERS NUEVOS
# =============================================================================
def _norm_area(text: str) -> str:
    """Reemplaza guiones bajos por espacios y colapsa espacios múltiples."""
    if not text:
        return ""
    s = str(text)
    s = s.replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _fmt_banos(value) -> str:
    """Devuelve baños sin .0 si es entero; si no, muestra el decimal tal cual."""
    if value is None or value == "":
        return ""
    try:
        f = float(value)
        return str(int(f)) if f.is_integer() else str(f)
    except Exception:
        return str(value)

def _as_list(value):
    """Devuelve siempre una lista (acepta None, str o list/tuple/set)."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(',') if p.strip()]
        return parts if parts else ([value.strip()] if value.strip() else [])
    return [value]

def _map_edad_choices(codes_selected, edad_choices, otro_text):
    """
    Recibe lista de selecciones (códigos o textos), choices [(code,label), ...],
    y el texto de 'otro'. Devuelve una lista final de textos legibles.
    """
    codes_selected = _as_list(codes_selected)
    code_to_label = dict(edad_choices)
    label_to_code = {lbl: code for code, lbl in edad_choices}

    result = []
    for item in codes_selected:
        if not item:
            continue
        # a) Código conocido -> guardar label
        if item in code_to_label:
            result.append(code_to_label[item])
            continue
        # b) Ya venía como label conocido -> dejarlo
        if item in label_to_code:
            result.append(item)
            continue
        # c) Palabra 'otro' -> se maneja luego con otro_text
        if str(item).lower() == 'otro':
            continue
        # d) Cualquier otro texto -> guardarlo tal cual
        result.append(item)

    otro_text = (otro_text or '').strip()
    if any(str(x).lower() == 'otro' for x in codes_selected) and otro_text:
        result.append(otro_text)

    # quitar duplicados conservando orden
    dedup, seen = [], set()
    for x in result:
        if x not in seen:
            dedup.append(x); seen.add(x)
    return dedup

def _split_edad_for_form(stored_list, edad_choices):
    """
    Prepara datos para el FORM (GET):
    - stored_list: lista de textos guardados (labels/otros)
    - edad_choices: [(code,label), ...]
    Devuelve (selected_codes, otro_text)
    """
    stored = _as_list(stored_list)
    if not stored:
        return [], ""

    code_by_label = {lbl: code for code, lbl in edad_choices}
    valid_codes = set(code for code, _ in edad_choices)
    selected_codes = []
    otros = []

    for item in stored:
        if item in code_by_label:
            selected_codes.append(code_by_label[item])
        elif item in valid_codes:
            # por si se guardó el code literal por error en algún momento
            selected_codes.append(item)
        else:
            otros.append(item)

    otro_text = ", ".join(otros) if otros else ""
    if otro_text and 'otro' not in selected_codes:
        selected_codes.append('otro')
    return selected_codes, otro_text


# =============================================================================
#                             SOLICITUDES (CRUD)
# =============================================================================
@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/nueva', methods=['GET','POST'])
@login_required
@admin_required
def nueva_solicitud_admin(cliente_id):
    c    = Cliente.query.get_or_404(cliente_id)
    form = AdminSolicitudForm()
    form.areas_comunes.choices = AREAS_COMUNES_CHOICES

    if request.method == 'GET':
        # Valores iniciales seguros
        form.funciones.data        = []
        form.funciones_otro.data   = ''
        form.areas_comunes.data    = []
        form.area_otro.data        = ''
        form.edad_requerida.data   = []   # ahora lista en Admin
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
            s.tipo_lugar = (form.tipo_lugar_otro.data or '').strip()
        else:
            s.tipo_lugar = form.tipo_lugar.data

        # ➤ Edad requerida (lista final de textos legibles)
        s.edad_requerida = _map_edad_choices(
            codes_selected=form.edad_requerida.data,
            edad_choices=form.edad_requerida.choices,
            otro_text=form.edad_otro.data
        )

        # ➤ Mascota
        s.mascota = (form.mascota.data or '').strip() or None

        # ➤ Funciones
        s.funciones = _as_list(form.funciones.data)
        extra_fun = (form.funciones_otro.data or '').strip()
        s.funciones_otro = extra_fun or None
        if extra_fun:
            # No mezcles code 'otro' en la lista final
            s.funciones = [f for f in s.funciones if f != 'otro']

        # ➤ Áreas comunes y pasaje
        s.areas_comunes = _as_list(form.areas_comunes.data)
        s.area_otro     = (form.area_otro.data or '').strip()
        s.pasaje_aporte = bool(form.pasaje_aporte.data)

        # ➤ Métricas de cliente
        db.session.add(s)
        c.total_solicitudes       = (c.total_solicitudes or 0) + 1
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
        guard_lugar = (s.tipo_lugar or '').strip()
        opts_lugar  = {v for v,_ in form.tipo_lugar.choices}
        if guard_lugar in opts_lugar:
            form.tipo_lugar.data      = guard_lugar
            form.tipo_lugar_otro.data = ''
        else:
            form.tipo_lugar.data      = 'otro'
            form.tipo_lugar_otro.data = guard_lugar

        # ➤ Edad requerida (s.edad_requerida ahora es lista)
        selected_codes, otro_text = _split_edad_for_form(
            stored_list=s.edad_requerida,
            edad_choices=form.edad_requerida.choices
        )
        form.edad_requerida.data = selected_codes
        form.edad_otro.data      = otro_text

        # ➤ Funciones
        allowed_fun_codes = {v for v, _ in form.funciones.choices}
        funs_guardadas = _as_list(s.funciones)
        form.funciones.data = [f for f in funs_guardadas if f in allowed_fun_codes]
        # Empujar cualquier custom a funciones_otro (si existiera)
        extras = [f for f in funs_guardadas if f not in allowed_fun_codes and f != 'otro']
        base_otro = (s.funciones_otro or '').strip()
        form.funciones_otro.data = (", ".join(extras) if extras else base_otro)

        # ➤ Mascota
        form.mascota.data = (s.mascota or '')

        # ➤ Áreas comunes y pasaje
        form.areas_comunes.data = _as_list(s.areas_comunes)
        form.area_otro.data     = (s.area_otro or '')
        form.pasaje_aporte.data = bool(s.pasaje_aporte)

    if form.validate_on_submit():
        form.populate_obj(s)

        # ➤ Tipo de lugar
        if form.tipo_lugar.data == 'otro':
            s.tipo_lugar = (form.tipo_lugar_otro.data or '').strip()
        else:
            s.tipo_lugar = form.tipo_lugar.data

        # ➤ Edad requerida (lista final de textos legibles)
        s.edad_requerida = _map_edad_choices(
            codes_selected=form.edad_requerida.data,
            edad_choices=form.edad_requerida.choices,
            otro_text=form.edad_otro.data
        )

        # ➤ Mascota
        s.mascota = (form.mascota.data or '').strip() or None

        # ➤ Funciones
        s.funciones = _as_list(form.funciones.data)
        extra_fun = (form.funciones_otro.data or '').strip()
        s.funciones_otro = extra_fun or None
        if extra_fun:
            s.funciones = [f for f in s.funciones if f != 'otro']

        # ➤ Áreas comunes y pasaje
        s.areas_comunes             = _as_list(form.areas_comunes.data)
        s.area_otro                 = (form.area_otro.data or '').strip()
        s.pasaje_aporte             = bool(form.pasaje_aporte.data)
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


@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/pago', methods=['GET', 'POST'])
@login_required
@admin_required
def registrar_pago(cliente_id, id):
    from sqlalchemy.exc import SQLAlchemyError

    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()
    form = AdminPagoForm()

    # ⚡️ No cargues todo: en GET solo carga la seleccionada (si existe)
    if request.method == 'GET':
        if s.candidata_id:
            cand = Candidata.query.get(s.candidata_id)
            if cand:
                form.candidata_id.choices = [(cand.fila, cand.nombre_completo)]
                form.candidata_id.data = cand.fila
            else:
                form.candidata_id.choices = []
        else:
            form.candidata_id.choices = []

    # En POST, añade dinámicamente la candidata seleccionada para validar
    if request.method == 'POST':
        raw_id = request.form.get('candidata_id', type=int)
        if raw_id:
            cand = Candidata.query.get(raw_id)
            if cand:
                form.candidata_id.choices = [(cand.fila, cand.nombre_completo)]
            else:
                form.candidata_id.choices = []
        else:
            form.candidata_id.choices = []

    if form.validate_on_submit():
        try:
            # 1) Datos de pago
            s.candidata_id = form.candidata_id.data

            # Limpieza suave del monto (quita separadores)
            raw = (form.monto_pagado.data or "").strip()
            limpio = raw.replace('$', '').replace('RD$', '').replace(' ', '').replace('.', '').replace(',', '')
            s.monto_pagado = raw if not limpio.isdigit() else "{:,}".format(int(limpio)).replace(',', ',')

            s.estado = 'pagada'

            # 2) Timestamps
            s.fecha_ultima_actividad = datetime.utcnow()
            s.fecha_ultima_modificacion = datetime.utcnow()

            db.session.commit()
            flash('Pago registrado y solicitud marcada como pagada.', 'success')
            return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))
        except SQLAlchemyError:
            db.session.rollback()
            flash('No se pudo registrar el pago. Intenta nuevamente.', 'danger')

    return render_template(
        'admin/registrar_pago.html',
        form=form,
        cliente_id=cliente_id,
        solicitud=s
    )



# =============================================================================
#                               REEMPLAZOS
# =============================================================================
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


# =============================================================================
#                                  API
# =============================================================================
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


# =============================================================================
#                           LISTADO / CONTADORES
# =============================================================================
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


# =============================================================================
#                               RESUMEN KPI
# =============================================================================
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

    # — Tasas —
    conversion_rate  = (pag_count    / total_sol * 100) if total_sol else 0
    replacement_rate = (repl_count   / total_sol * 100) if total_sol else 0
    abandon_rate     = (cancel_count / total_sol * 100) if total_sol else 0

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


# =============================================================================
#                     COPIAR SOLICITUDES (LISTA + POST)
# =============================================================================
@admin_bp.route('/solicitudes/copiar')
@login_required
@admin_required
def copiar_solicitudes():
    """
    Lista solicitudes copiables y arma el texto:
    - 'Funciones' separado de 'Hogar'
    - 'Adultos' en una línea, luego 'Niños' (si aplica) y luego 'Mascota' (si aplica)
    """
    hoy = date.today()

    base_q = (
        Solicitud.query
        .options(
            joinedload(Solicitud.reemplazos).joinedload(Reemplazo.candidata_new)
        )
        .filter(Solicitud.estado.in_(('activa', 'reemplazo')))
        .filter(
            or_(
                Solicitud.last_copiado_at.is_(None),
                func.date(Solicitud.last_copiado_at) < hoy
            )
        )
    )

    con_reemp = (
        base_q.filter(Solicitud.estado == 'reemplazo')
              .order_by(Solicitud.fecha_solicitud.desc())
              .all()
    )
    sin_reemp = (
        base_q.filter(Solicitud.estado == 'activa')
              .order_by(Solicitud.fecha_solicitud.desc())
              .all()
    )
    raw_sols = con_reemp + sin_reemp

    form = AdminSolicitudForm()
    FUNCIONES_CHOICES = dict(form.funciones.choices)  # code -> label

    solicitudes = []
    for s in raw_sols:
        # Reemplazos
        reems = list(s.reemplazos or []) if s.estado == 'reemplazo' \
            else [r for r in (s.reemplazos or []) if getattr(r, 'oportunidad_nueva', False)]

        # Funciones (labels + otro)
        funcs = []
        try:
            seleccion = set(_as_list(s.funciones))
        except Exception:
            seleccion = set()
        for code in seleccion:
            if code == 'otro':
                continue
            label = FUNCIONES_CHOICES.get(code)
            if label:
                funcs.append(label)
        if getattr(s, 'funciones_otro', None):
            custom = str(s.funciones_otro).strip()
            if custom:
                funcs.append(custom)

        # Adultos / Niños (separados)
        adultos = s.adultos or ""
        ninos_line = ""
        if getattr(s, 'ninos', None):
            ninos_line = f"Niños: {s.ninos}"
            if getattr(s, 'edades_ninos', None):
                ninos_line += f" ({s.edades_ninos})"

        # Modalidad
        modalidad = (
            getattr(s, 'modalidad_trabajo', None)
            or getattr(s, 'modalidad', None)
            or getattr(s, 'tipo_modalidad', None)
            or ''
        ).strip()

        # ===== Hogar (SECCIÓN INDEPENDIENTE) =====
        hogar_partes_detalle = []

        if getattr(s, 'habitaciones', None):
            hogar_partes_detalle.append(f"{s.habitaciones} habitaciones")

        banos_txt = _fmt_banos(getattr(s, 'banos', None))
        if banos_txt:
            hogar_partes_detalle.append(f"{banos_txt} baños")

        if bool(getattr(s, 'dos_pisos', False)):
            hogar_partes_detalle.append("2 pisos")

        areas = []
        if getattr(s, 'areas_comunes', None):
            try:
                for a in s.areas_comunes:
                    a = str(a).strip()
                    if a:
                        areas.append(_norm_area(a))
            except Exception:
                pass
        area_otro = (getattr(s, 'area_otro', None) or "").strip()
        if area_otro:
            areas.append(_norm_area(area_otro))

        if areas:
            hogar_partes_detalle.append(", ".join(areas))

        tipo_lugar = (getattr(s, 'tipo_lugar', "") or "").strip()
        if tipo_lugar and hogar_partes_detalle:
            hogar_descr = f"{tipo_lugar} - {', '.join(hogar_partes_detalle)}"
        elif tipo_lugar:
            hogar_descr = tipo_lugar
        else:
            hogar_descr = ", ".join(hogar_partes_detalle)
        hogar_section = f"Hogar: {hogar_descr}" if hogar_descr else ""

        # Mascota (debajo de Niños, si aplica)
        mascota_val = (getattr(s, 'mascota', None) or '').strip()
        mascota_line = f"Mascota: {mascota_val}" if mascota_val else ""

        # Campos base
        codigo         = s.codigo_solicitud or ""
        ciudad_sector  = s.ciudad_sector or ""
        rutas_cercanas = s.rutas_cercanas or ""
        # Edad: ahora es lista → texto legible
        if isinstance(s.edad_requerida, (list, tuple, set)):
            edad_req = ", ".join([str(x).strip() for x in s.edad_requerida if str(x).strip()])
        else:
            edad_req = s.edad_requerida or ""

        experiencia    = s.experiencia or ""
        horario        = s.horario or ""
        sueldo         = s.sueldo or ""
        pasaje_aporte  = bool(getattr(s, 'pasaje_aporte', False))
        nota_cli       = (s.nota_cliente or "").strip()
        nota_line      = f"Nota: {nota_cli}" if nota_cli else ""
        funciones_line = f"Funciones: {', '.join(funcs)}" if funcs else "Funciones: "

        # ===== Texto final (nuevo orden) =====
        # Funciones -> Hogar (separado)
        # Adultos -> Niños (si hay) -> Mascota (si hay)
        lines = [
            f"Disponible ( {codigo} )",
            f"📍 {ciudad_sector}",
            f"Ruta más cercana: {rutas_cercanas}",
            "",
            f"Modalidad: {modalidad}",
            "",
            f"Edad: {edad_req}",
            "Dominicana",
            "Que sepa leer y escribir",
            f"Experiencia en: {experiencia}",
            f"Horario: {horario}",
            "",
            funciones_line,
        ]
        if hogar_section:
            lines += ["", hogar_section]  # ← espacio extra intencional

        lines += [
            "",
            f"Adultos: {adultos}"
        ]
        if ninos_line:
            lines.append(ninos_line)
        if mascota_line:
            lines.append(mascota_line)

        lines += [
            "",
            f"Sueldo: ${sueldo} mensual{', más ayuda del pasaje' if pasaje_aporte else ', pasaje incluido'}",
        ]
        if nota_line:
            lines += ["", nota_line]

        order_text = "\n".join(lines).strip()

        solicitudes.append({
            'id': s.id,
            'codigo_solicitud': codigo,
            'ciudad_sector': ciudad_sector,
            'direccion': getattr(s, 'direccion', None),
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
    """
    Marca una solicitud como copiada hoy para esconderla hasta mañana.
    Usa func.now() (tiempo DB) para evitar desajustes de TZ del servidor.
    """
    s = Solicitud.query.get_or_404(id)
    s.last_copiado_at = func.now()
    db.session.commit()
    flash(f'Solicitud {s.codigo_solicitud} copiada. Ya no se mostrará hasta mañana.', 'success')
    return redirect(url_for('admin.copiar_solicitudes'))


# =============================================================================
#                       VISTAS "EN PROCESO" Y RESUMEN DIARIO
# =============================================================================
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


# ─────────────────────────────────────────────────────────────
# Cancelación con confirmación (GET muestra formulario, POST ejecuta)
# URL: /admin/clientes/<cliente_id>/solicitudes/<id>/cancelar
# Endpoint: admin.cancelar_solicitud
# ─────────────────────────────────────────────────────────────
from urllib.parse import urlparse, urljoin

def _is_safe_redirect_url(target: str) -> bool:
    if not target:
        return False
    ref = urlparse(request.host_url)
    test = urlparse(urljoin(request.host_url, target))
    return (test.scheme in ('http', 'https')) and (ref.netloc == test.netloc)

@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/cancelar', methods=['GET', 'POST'])
@login_required
@admin_required
def cancelar_solicitud(cliente_id, id):
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()

    # Destino preferido de regreso
    next_url = request.args.get('next') or request.form.get('next') or request.referrer
    fallback = url_for('admin.detalle_cliente', cliente_id=cliente_id)

    if request.method == 'GET':
        # No dejes cancelar si ya está cancelada o pagada
        if s.estado == 'cancelada':
            flash(f'La solicitud {s.codigo_solicitud} ya estaba cancelada.', 'warning')
            return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)
        if s.estado == 'pagada':
            flash(f'La solicitud {s.codigo_solicitud} está pagada y no puede cancelarse.', 'warning')
            return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

        # Render de confirmación
        return render_template(
            'admin/cancelar_solicitud.html',
            solicitud=s,
            next_url=next_url
        )

    # POST (confirma cancelación)
    motivo = (request.form.get('motivo') or '').strip()
    if len(motivo) < 5:
        flash('Indica un motivo de cancelación (mínimo 5 caracteres).', 'danger')
        # Volvemos a mostrar el formulario con el texto que puso el usuario
        return render_template(
            'admin/cancelar_solicitud.html',
            solicitud=s,
            next_url=next_url,
            # Para que el textarea conserve lo que escribió
            form={'motivo': {'errors': ['Indica un motivo válido.']}}
        )

    # Estados cancelables: proceso, activa, reemplazo (evita re-cancelar/pagada)
    if s.estado in ('proceso', 'activa', 'reemplazo'):
        s.estado = 'cancelada'
        s.motivo_cancelacion = motivo
        s.fecha_cancelacion = datetime.utcnow()
        s.fecha_ultima_modificacion = datetime.utcnow()
        db.session.commit()
        flash(f'Solicitud {s.codigo_solicitud} cancelada.', 'success')
    else:
        flash(f'No se puede cancelar la solicitud en estado «{s.estado}».', 'warning')

    return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)


# ─────────────────────────────────────────────────────────────
# Atajo “directo” (botón en listas) sin formulario
# URL: /admin/solicitudes/<id>/cancelar_directo  (POST)
# Endpoint: admin.cancelar_solicitud_directa
# ─────────────────────────────────────────────────────────────
@admin_bp.route('/solicitudes/<int:id>/cancelar_directo', methods=['POST'])
@login_required
@admin_required
def cancelar_solicitud_directa(id):
    s = Solicitud.query.get_or_404(id)

    # Destino preferido de regreso
    next_url = request.args.get('next') or request.form.get('next') or request.referrer
    # Por compatibilidad con tu flujo actual, lo dejamos en la pantalla de “proceso” como fallback
    fallback = url_for('admin.acciones_solicitudes_proceso')

    if s.estado == 'cancelada':
        flash(f'La solicitud {s.codigo_solicitud} ya estaba cancelada.', 'warning')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    if s.estado == 'pagada':
        flash(f'La solicitud {s.codigo_solicitud} está pagada y no puede cancelarse.', 'warning')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    if s.estado in ('proceso', 'activa', 'reemplazo'):
        s.estado = 'cancelada'
        s.fecha_cancelacion = datetime.utcnow()
        s.fecha_ultima_modificacion = datetime.utcnow()
        # Si quieres registrar un motivo por defecto en el directo:
        s.motivo_cancelacion = (request.form.get('motivo') or '').strip() or 'Cancelación directa (sin motivo)'
        db.session.commit()
        flash(f'Solicitud {s.codigo_solicitud} cancelada.', 'success')
    else:
        flash(f'No se puede cancelar la solicitud en estado «{s.estado}».', 'warning')

    return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)


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
