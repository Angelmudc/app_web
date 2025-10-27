# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from datetime import datetime, date, timedelta

from flask import render_template, redirect, url_for, flash, request, jsonify, abort, session
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
    """Wrapper mínimo para flask-login basado en USUARIOS del config."""
    def __init__(self, username: str):
        self.id = username
        self.role = USUARIOS[username]['role']


# —— Anti fuerza-bruta por sesión (simple, sin dependencias externas) ——
_MAX_LOGIN_ATTEMPTS = 5
_LOCK_MINUTES = 10

def _is_login_locked() -> bool:
    """Devuelve True si la sesión actual está bloqueada por intentos fallidos."""
    data = session.get('admin_login_fail')
    if not data:
        return False
    tries = data.get('tries', 0)
    locked_until_ts = data.get('locked_until')
    now_ts = int(datetime.utcnow().timestamp())
    if locked_until_ts and now_ts < locked_until_ts:
        return True
    # si ya pasó el tiempo, limpia el lock
    if locked_until_ts and now_ts >= locked_until_ts:
        session.pop('admin_login_fail', None)
        return False
    return tries >= _MAX_LOGIN_ATTEMPTS

def _register_login_fail() -> None:
    """Incrementa el contador de fallos y bloquea si supera el máximo."""
    now_ts = int(datetime.utcnow().timestamp())
    data = session.get('admin_login_fail', {'tries': 0, 'first_ts': now_ts, 'locked_until': None})
    data['tries'] = int(data.get('tries', 0)) + 1
    if data['tries'] >= _MAX_LOGIN_ATTEMPTS:
        data['locked_until'] = now_ts + (_LOCK_MINUTES * 60)
    session['admin_login_fail'] = data

def _reset_login_fail() -> None:
    session.pop('admin_login_fail', None)


@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    """
    Login de admin basado en diccionario USUARIOS.
    Protecciones:
      - Anti fuerza bruta simple por sesión: 5 intentos -> 10 minutos de bloqueo.
      - Normalización y strip de credenciales de entrada.
    Nota: Si ya tienes CSRF global (Flask-WTF CSRFProtect), esta vista queda cubierta.
    """
    error = None

    # Bloqueo por intentos fallidos
    if request.method == 'POST' and _is_login_locked():
        error = f'Has excedido el máximo de intentos. Intenta de nuevo en {_LOCK_MINUTES} minutos.'
        return render_template('admin/login.html', error=error), 429

    if request.method == 'POST':
        usuario = (request.form.get('usuario') or '').strip()
        clave   = (request.form.get('clave') or '').strip()

        user_data = USUARIOS.get(usuario)
        if user_data and check_password_hash(user_data['pwd_hash'], clave):
            user = AdminUser(usuario)
            login_user(user)
            _reset_login_fail()
            return redirect(url_for('admin.listar_clientes'))

        _register_login_fail()
        error = 'Credenciales inválidas.'

    return render_template('admin/login.html', error=error)


@admin_bp.route('/logout')
@login_required
def logout():
    """Cerrar sesión siempre debe estar disponible para cualquier usuario autenticado."""
    logout_user()
    return redirect(url_for('admin.login'))


# =============================================================================
#                            CLIENTES (CRUD BÁSICO)
# =============================================================================
@admin_bp.route('/clientes')
@login_required
@admin_required
def listar_clientes():
    """
    Lista de clientes con búsqueda básica.
    - Evita escaneos completos si la query de texto es de 1 carácter (excepto ID numérica).
    """
    q = (request.args.get('q') or '').strip()
    query = Cliente.query

    if q:
        filtros = []
        # Si es un ID exacto (entero), permite búsqueda directa por ID
        if q.isdigit():
            try:
                filtros.append(Cliente.id == int(q))
            except Exception:
                pass

        # Para búsquedas textuales muy cortas (1 char), no aplicar ilike para evitar full scan
        if len(q) >= 2:
            filtros.extend([
                Cliente.nombre_completo.ilike(f'%{q}%'),
                Cliente.telefono.ilike(f'%{q}%'),
                Cliente.codigo.ilike(f'%{q}%'),
            ])

        if filtros:
            query = query.filter(or_(*filtros))

    clientes = query.order_by(Cliente.fecha_registro.desc()).all()
    return render_template('admin/clientes_list.html', clientes=clientes, q=q)


# =============================================================================
#                       HELPERS DE LIMPIEZA / NORMALIZACIÓN
# =============================================================================
def _only_digits(text: str) -> str:
    """Retorna solo dígitos de un texto (para teléfonos, etc.)."""
    return re.sub(r"\D+", "", text or "")

def _normalize_email(value: str) -> str:
    """Email normalizado (lower + strip)."""
    return (value or '').strip().lower()

def _normalize_phone(value: str) -> str:
    """
    Normaliza teléfono manteniendo dígitos. Si quieres guardar con formato,
    hazlo en la vista; persiste solo dígitos en la BD si tu modelo lo permite.
    """
    digits = _only_digits(value)
    return digits

def _strip_if_str(x):
    return x.strip() if isinstance(x, str) else x

def _norm_cliente_form(form: AdminClienteForm) -> None:
    """
    Normaliza/limpia entradas de texto del formulario de cliente.
    """
    if hasattr(form, 'codigo') and form.codigo.data:
        form.codigo.data = _strip_if_str(form.codigo.data)

    if hasattr(form, 'nombre_completo') and form.nombre_completo.data:
        form.nombre_completo.data = _strip_if_str(form.nombre_completo.data)

    if hasattr(form, 'email') and form.email.data:
        form.email.data = _normalize_email(form.email.data)

    if hasattr(form, 'telefono') and form.telefono.data:
        # guarda limpio; si prefieres mantener guiones para UI, renderízalos en plantilla
        form.telefono.data = _normalize_phone(form.telefono.data)

    if hasattr(form, 'ciudad') and form.ciudad.data:
        form.ciudad.data = _strip_if_str(form.ciudad.data)

    if hasattr(form, 'sector') and form.sector.data:
        form.sector.data = _strip_if_str(form.sector.data)

    if hasattr(form, 'notas_admin') and form.notas_admin.data:
        form.notas_admin.data = _strip_if_str(form.notas_admin.data)


def parse_integrity_error(err: IntegrityError) -> str:
    """
    Intenta detectar qué constraint única falló.
    Retorna 'codigo', 'email' o '' si no se pudo identificar.
    Funciona para SQLite, MySQL y PostgreSQL en la mayoría de casos.
    """
    msg = ""
    try:
        msg = str(getattr(err, "orig", err))
    except Exception:
        msg = str(err)

    m = msg.lower()

    # PostgreSQL: nombre del constraint si está disponible
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

    # Heurísticas por mensaje (MySQL/SQLite)
    if "codigo" in m:
        return "codigo"
    if "email" in m or "correo" in m:
        return "email"

    if "for key" in m and "email" in m:
        return "email"
    if "for key" in m and "codigo" in m:
        return "codigo"

    return ""


# =============================================================================
#                 HELPERS CONSISTENTES PARA EDAD Y LISTAS (ADMIN)
#            (VERSIÓN CANÓNICA — ELIMINAR CUALQUIER DUPLICADO LUEGO)
# =============================================================================
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

def _clean_list(seq):
    """Lista sin vacíos/guiones, preservando orden y quitando duplicados."""
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
    """Mapeos code<->label a partir de choices [(code, label), ...]."""
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
    """
    Recibe lista de CÓDIGOS marcados en el form, choices y el texto de 'otro'.
    Devuelve lista final de LABELS legibles (lo que se guarda en BD).
    """
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
            result.extend([x.strip() for x in extra.split(',') if x.strip()])

    return _clean_list(result)

def _split_edad_for_form(stored_list, edad_choices):
    """
    Convierte lo guardado en BD (LABELS legibles) a (CÓDIGOS seleccionados, texto_otro)
    para precargar el formulario.
    """
    stored_list = _clean_list(stored_list)
    code_to_label, label_to_code = _choices_maps(edad_choices)

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


# =============================================================================
#                      CONSTANTES / CHOICES PARA FORMULARIOS
# =============================================================================
AREAS_COMUNES_CHOICES = [
    ('sala', 'Sala'), ('comedor', 'Comedor'),
    ('cocina', 'Cocina'), ('salon_juegos', 'Salón de juegos'),
    ('terraza', 'Terraza'), ('jardin', 'Jardín'),
    ('estudio', 'Estudio'), ('patio', 'Patio'),
    ('piscina', 'Piscina'), ('marquesina', 'Marquesina'),
    ('todas_anteriores', 'Todas las anteriores'),
]


# =============================================================================
#                              HELPERS NUEVOS (HOGAR)
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

def _map_funciones(vals, extra_text):
    """
    Combina funciones seleccionadas con valores personalizados de 'otro',
    eliminando duplicados y vacíos.
    """
    vals = _clean_list(vals)
    if 'otro' in vals:
        vals = [v for v in vals if v != 'otro']
        extra = (extra_text or '').strip()
        if extra:
            vals.extend([x.strip() for x in extra.split(',') if x.strip()])
    return _clean_list(vals)

def _map_tipo_lugar(value, extra):
    """
    Si el valor es 'otro', usa el texto extra; en otro caso retorna el valor tal cual.
    """
    value = (value or '').strip()
    if value == 'otro':
        return (extra or '').strip() or value
    return value


# ─────────────────────────────────────────────────────────────
# Helpers internos específicos de Solicitud
# ─────────────────────────────────────────────────────────────
def _allowed_codes_from_choices(choices):
    """Devuelve el set de códigos válidos a partir de choices [(code,label), ...]."""
    try:
        return {str(v).strip() for v, _ in (choices or []) if str(v).strip()}
    except Exception:
        return set()

def _next_codigo_solicitud(cliente: Cliente) -> str:
    """
    Genera un código único del tipo <CODCLI>-<LETRA>.
    Usa un loop defensivo para evitar colisiones si hubo borrados o concurrencia.
    """
    prefix = (cliente.codigo or str(cliente.id)).strip()
    base_count = Solicitud.query.filter_by(cliente_id=cliente.id).count()
    intento = 0
    while True:
        suf = letra_por_indice(base_count + intento)
        code = f"{prefix}-{suf}"
        exists = Solicitud.query.filter(Solicitud.codigo_solicitud == code).first()
        if not exists:
            return code
        intento += 1


# =============================================================================
#                         CLIENTES – CREAR / EDITAR / ELIMINAR / DETALLE
# =============================================================================

@admin_bp.route('/clientes/nuevo', methods=['GET', 'POST'])
@login_required
@admin_required
def nuevo_cliente():
    """🟢 Crear un nuevo cliente desde el panel de administración."""
    form = AdminClienteForm()

    if form.validate_on_submit():
        _norm_cliente_form(form)

        # --- Validación de código único (case-sensitive) ---
        if Cliente.query.filter(Cliente.codigo == form.codigo.data).first():
            form.codigo.errors.append("Este código ya está en uso.")
            flash("El código ya está en uso.", "danger")
            return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)

        # --- Validación de email único (case-insensitive) ---
        email_norm = (form.email.data or "").lower().strip()
        if Cliente.query.filter(func.lower(Cliente.email) == email_norm).first():
            form.email.errors.append("Este email ya está registrado.")
            flash("El email ya está registrado.", "danger")
            return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)

        # --- Validación de contraseña y confirmación ---
        pwd = (form.password_new.data or '').strip()
        pwd2 = (form.password_confirm.data or '').strip()

        if not pwd:
            form.password_new.errors.append("Debes establecer una contraseña.")
            flash("Debes establecer una contraseña.", "danger")
            return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)

        if pwd != pwd2:
            form.password_confirm.errors.append("La confirmación de contraseña no coincide.")
            flash("La confirmación de contraseña no coincide.", "danger")
            return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)

        # --- Creación del cliente ---
        try:
            c = Cliente()
            form.populate_obj(c)
            c.email = email_norm
            c.password_hash = generate_password_hash(pwd)
            c.fecha_registro = datetime.utcnow()

            db.session.add(c)
            db.session.flush()
            db.session.commit()

            flash('Cliente creado correctamente ✅', 'success')
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

        except Exception:
            db.session.rollback()
            flash('Ocurrió un error al crear el cliente. Intenta de nuevo.', 'danger')

    elif request.method == 'POST':
        flash('Revisa los campos marcados en rojo.', 'danger')

    return render_template('admin/cliente_form.html', cliente_form=form, nuevo=True)


# ─────────────────────────────────────────────────────────────
# 🔵 Editar cliente
# ─────────────────────────────────────────────────────────────
@admin_bp.route('/clientes/<int:cliente_id>/editar', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_cliente(cliente_id):
    """✏️ Editar la información de un cliente existente."""
    c = Cliente.query.get_or_404(cliente_id)
    form = AdminClienteForm(obj=c)

    if form.validate_on_submit():
        _norm_cliente_form(form)

        # --- Validar código si se modifica ---
        if form.codigo.data != c.codigo:
            if Cliente.query.filter(Cliente.codigo == form.codigo.data).first():
                form.codigo.errors.append("Este código ya está en uso.")
                flash("El código ya está en uso.", "danger")
                return render_template('admin/cliente_form.html', cliente_form=form, nuevo=False)

        # --- Validar email si se modifica ---
        email_norm = (form.email.data or "").lower().strip()
        if email_norm != (c.email or "").lower().strip():
            if Cliente.query.filter(func.lower(Cliente.email) == email_norm).first():
                form.email.errors.append("Este email ya está registrado.")
                flash("Este email ya está registrado.", "danger")
                return render_template('admin/cliente_form.html', cliente_form=form, nuevo=False)

        # --- Guardar cambios ---
        try:
            form.populate_obj(c)
            c.email = email_norm

            # Cambio de contraseña (solo si el usuario envía una nueva)
            pwd = (form.password_new.data or '').strip()
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

            flash('Cliente actualizado correctamente ✅', 'success')
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


# ─────────────────────────────────────────────────────────────
# 🔴 Eliminar cliente
# ─────────────────────────────────────────────────────────────
@admin_bp.route('/clientes/<int:cliente_id>/eliminar', methods=['POST'])
@login_required
@admin_required
def eliminar_cliente(cliente_id):
    """🗑️ Eliminar un cliente definitivamente."""
    c = Cliente.query.get_or_404(cliente_id)

    try:
        db.session.delete(c)
        db.session.commit()
        flash('Cliente eliminado correctamente 🗑️', 'success')
    except Exception:
        db.session.rollback()
        flash('No se pudo eliminar el cliente.', 'danger')

    return redirect(url_for('admin.listar_clientes'))


# ─────────────────────────────────────────────────────────────
# 🔍 Detalle de cliente
# ─────────────────────────────────────────────────────────────
@admin_bp.route('/clientes/<int:cliente_id>')
@login_required
@admin_required
def detalle_cliente(cliente_id):
    """📋 Muestra el detalle completo del cliente."""
    c = Cliente.query.get_or_404(cliente_id)
    return render_template('admin/cliente_detail.html', cliente=c)


# ─────────────────────────────────────────────────────────────
# ADMIN: Nueva solicitud
# ─────────────────────────────────────────────────────────────
@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/nueva', methods=['GET', 'POST'])
@login_required
@admin_required
def nueva_solicitud_admin(cliente_id):
    c = Cliente.query.get_or_404(cliente_id)
    form = AdminSolicitudForm()

    # Mantener en sync con constantes
    form.areas_comunes.choices = AREAS_COMUNES_CHOICES

    if request.method == 'GET':
        # Valores iniciales limpios/seguros
        if hasattr(form, 'funciones'):        form.funciones.data = []
        if hasattr(form, 'funciones_otro'):   form.funciones_otro.data = ''
        if hasattr(form, 'areas_comunes'):    form.areas_comunes.data = []
        if hasattr(form, 'area_otro'):        form.area_otro.data = ''
        if hasattr(form, 'edad_requerida'):   form.edad_requerida.data = []  # lista de CÓDIGOS
        if hasattr(form, 'edad_otro'):        form.edad_otro.data = ''
        if hasattr(form, 'tipo_lugar_otro'):  form.tipo_lugar_otro.data = ''
        if hasattr(form, 'mascota'):          form.mascota.data = ''

    # POST válido
    if form.validate_on_submit():
        try:
            # Código único robusto
            nuevo_codigo = _next_codigo_solicitud(c)

            # Instanciar con mínimos obligatorios
            s = Solicitud(
                cliente_id=c.id,
                fecha_solicitud=datetime.utcnow(),
                codigo_solicitud=nuevo_codigo
            )

            # Carga general desde WTForms
            form.populate_obj(s)

            # Tipo de lugar ('otro')
            s.tipo_lugar = _map_tipo_lugar(
                getattr(s, 'tipo_lugar', ''),
                getattr(form, 'tipo_lugar_otro', None).data if hasattr(form, 'tipo_lugar_otro') else ''
            )

            # Edad requerida → guardar LABELS legibles
            s.edad_requerida = _map_edad_choices(
                codes_selected=(form.edad_requerida.data if hasattr(form, 'edad_requerida') else []),
                edad_choices=(form.edad_requerida.choices if hasattr(form, 'edad_requerida') else []),
                otro_text=(form.edad_otro.data if hasattr(form, 'edad_otro') else '')
            )

            # Mascota
            if hasattr(form, 'mascota'):
                s.mascota = (form.mascota.data or '').strip() or None

            # ===== Funciones (códigos válidos) + texto "otro" =====
            selected_codes = _clean_list(form.funciones.data) if hasattr(form, 'funciones') else []
            extra_text    = (form.funciones_otro.data or '').strip() if hasattr(form, 'funciones_otro') else ''
            if hasattr(form, 'funciones') and hasattr(form.funciones, 'choices'):
                valid_codes = _allowed_codes_from_choices(form.funciones.choices)
                s.funciones = [c for c in selected_codes if c in valid_codes and c != 'otro']
            else:
                s.funciones = [c for c in selected_codes if c != 'otro']
            if hasattr(s, 'funciones_otro'):
                s.funciones_otro = extra_text or None

            # Áreas comunes válidas
            allowed_areas = _allowed_codes_from_choices(form.areas_comunes.choices) if hasattr(form, 'areas_comunes') else set()
            s.areas_comunes = [a for a in _clean_list(getattr(form, 'areas_comunes', type('x',(object,),{'data':[]})).data) if a in allowed_areas]

            # Área "otro"
            if hasattr(s, 'area_otro') and hasattr(form, 'area_otro'):
                s.area_otro = (form.area_otro.data or '').strip() or None

            # Pasaje
            s.pasaje_aporte = bool(getattr(form, 'pasaje_aporte', type('x', (object,), {'data': False})).data)

            # Métricas cliente
            db.session.add(s)
            c.total_solicitudes = (c.total_solicitudes or 0) + 1
            c.fecha_ultima_solicitud = datetime.utcnow()
            c.fecha_ultima_actividad = datetime.utcnow()

            db.session.commit()
            flash(f'Solicitud {nuevo_codigo} creada.', 'success')
            return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))

        except IntegrityError:
            db.session.rollback()
            flash('Conflicto de datos. Verifica los campos (códigos únicos, etc.).', 'danger')
        except SQLAlchemyError:
            db.session.rollback()
            flash('Error de base de datos al crear la solicitud.', 'danger')
        except Exception:
            db.session.rollback()
            flash('Ocurrió un error al crear la solicitud.', 'danger')

    elif request.method == 'POST':
        flash('Revisa los campos marcados en rojo.', 'danger')

    return render_template(
        'admin/solicitud_form.html',
        form=form,
        cliente_id=cliente_id,
        nuevo=True
    )


# ─────────────────────────────────────────────────────────────
# ADMIN: Editar solicitud
# ─────────────────────────────────────────────────────────────
@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/editar', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_solicitud_admin(cliente_id, id):
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()
    form = AdminSolicitudForm(obj=s)

    # Mantener en sync con constantes
    form.areas_comunes.choices = AREAS_COMUNES_CHOICES

    if request.method == 'GET':
        # ---- Tipo de lugar ----
        try:
            if hasattr(form, 'tipo_lugar') and hasattr(form, 'tipo_lugar_otro'):
                allowed_tl = _allowed_codes_from_choices(form.tipo_lugar.choices)
                if s.tipo_lugar and s.tipo_lugar in allowed_tl:
                    form.tipo_lugar.data = s.tipo_lugar
                    form.tipo_lugar_otro.data = ''
                else:
                    form.tipo_lugar.data = 'otro'
                    form.tipo_lugar_otro.data = (s.tipo_lugar or '').strip()
        except Exception:
            pass

        # ---- Edad requerida (BD guarda LABELS) → form CÓDIGOS y “otro” ----
        if hasattr(form, 'edad_requerida'):
            selected_codes, otro_text = _split_edad_for_form(
                stored_list=s.edad_requerida,
                edad_choices=form.edad_requerida.choices
            )
            # Si hay texto libre, marca 'otro' en el form
            try:
                edad_codes = set(selected_codes or [])
                if (otro_text or '').strip():
                    allowed_edad = _allowed_codes_from_choices(form.edad_requerida.choices)
                    if 'otro' in allowed_edad:
                        edad_codes.add('otro')
                form.edad_requerida.data = list(edad_codes)
            except Exception:
                form.edad_requerida.data = selected_codes or []
            if hasattr(form, 'edad_otro'):
                form.edad_otro.data = (otro_text or '').strip()

        # ---- Funciones ----
        if hasattr(form, 'funciones'):
            allowed_fun_codes = _allowed_codes_from_choices(form.funciones.choices)
            funs_guardadas = _clean_list(s.funciones)
            form.funciones.data = [f for f in funs_guardadas if f in allowed_fun_codes]

            # Extras fuera de catálogo que antes se guardaron (por compatibilidad)
            extras = [f for f in funs_guardadas if f not in allowed_fun_codes and f != 'otro']

            # Valor base del campo libre
            base_otro = (getattr(s, 'funciones_otro', '') or '').strip()
            if hasattr(form, 'funciones_otro'):
                form.funciones_otro.data = (", ".join(extras) if extras else base_otro)

            # >>> FIX visible: si hay texto en funciones_otro, marca 'otro' seleccionado
            try:
                if (form.funciones_otro.data or '').strip():
                    fun_codes = set(form.funciones.data or [])
                    if 'otro' in allowed_fun_codes:
                        fun_codes.add('otro')
                    form.funciones.data = list(fun_codes)
            except Exception:
                # si algo falla, al menos no romper el render
                pass

        # ---- Mascota / Áreas / Pasaje ----
        if hasattr(form, 'mascota'):
            form.mascota.data = (getattr(s, 'mascota', '') or '')
        if hasattr(form, 'areas_comunes'):
            form.areas_comunes.data = _clean_list(s.areas_comunes)
        if hasattr(form, 'area_otro'):
            form.area_otro.data = (getattr(s, 'area_otro', '') or '')
        if hasattr(form, 'pasaje_aporte'):
            form.pasaje_aporte.data = bool(getattr(s, 'pasaje_aporte', False))

    # POST válido
    if form.validate_on_submit():
        try:
            # Carga general
            form.populate_obj(s)

            # Tipo de lugar
            s.tipo_lugar = _map_tipo_lugar(
                getattr(s, 'tipo_lugar', ''),
                getattr(form, 'tipo_lugar_otro', None).data if hasattr(form, 'tipo_lugar_otro') else ''
            )

            # Edad requerida (guardar LABELS)
            s.edad_requerida = _map_edad_choices(
                codes_selected=(form.edad_requerida.data if hasattr(form, 'edad_requerida') else []),
                edad_choices=(form.edad_requerida.choices if hasattr(form, 'edad_requerida') else []),
                otro_text=(form.edad_otro.data if hasattr(form, 'edad_otro') else '')
            )

            # Mascota
            if hasattr(form, 'mascota'):
                s.mascota = (form.mascota.data or '').strip() or None

            # ===== Funciones (códigos válidos) + texto "otro" =====
            selected_codes = _clean_list(form.funciones.data) if hasattr(form, 'funciones') else []
            extra_text    = (form.funciones_otro.data or '').strip() if hasattr(form, 'funciones_otro') else ''
            if hasattr(form, 'funciones') and hasattr(form.funciones, 'choices'):
                valid_codes = _allowed_codes_from_choices(form.funciones.choices)
                s.funciones = [c for c in selected_codes if c in valid_codes and c != 'otro']
            else:
                s.funciones = [c for c in selected_codes if c != 'otro']
            if hasattr(s, 'funciones_otro'):
                s.funciones_otro = extra_text or None

            # Áreas válidas
            if hasattr(form, 'areas_comunes'):
                allowed_areas = _allowed_codes_from_choices(form.areas_comunes.choices)
                s.areas_comunes = [a for a in _clean_list(form.areas_comunes.data) if a in allowed_areas]

            # Área "otro"
            if hasattr(s, 'area_otro') and hasattr(form, 'area_otro'):
                s.area_otro = (form.area_otro.data or '').strip() or None

            # Pasaje
            if hasattr(form, 'pasaje_aporte'):
                s.pasaje_aporte = bool(form.pasaje_aporte.data)

            # Timestamps
            s.fecha_ultima_modificacion = datetime.utcnow()

            db.session.commit()
            flash(f'Solicitud {s.codigo_solicitud} actualizada.', 'success')
            return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))

        except IntegrityError:
            db.session.rollback()
            flash('No se pudo actualizar por conflicto de datos (únicos/relaciones).', 'danger')
        except SQLAlchemyError:
            db.session.rollback()
            flash('Error de base de datos al actualizar la solicitud.', 'danger')
        except Exception:
            db.session.rollback()
            flash('Ocurrió un error al actualizar la solicitud.', 'danger')

    elif request.method == 'POST':
        flash('Revisa los campos marcados en rojo.', 'danger')

    return render_template(
        'admin/solicitud_form.html',
        form=form,
        cliente_id=cliente_id,
        solicitud=s,
        nuevo=False
    )


from decimal import Decimal, InvalidOperation

# ─────────────────────────────────────────────────────────────
# Helpers de apoyo (dinero, choices)
# ─────────────────────────────────────────────────────────────
def _parse_money_to_decimal_str(raw: str) -> str:
    """
    Convierte entradas como: "RD$ 1,234.50", "$1200", "1200,50", "  5000  "
    a string canónica con punto decimal y 2 dígitos: "1234.50".
    Lanza ValueError si no se puede parsear.
    """
    if raw is None:
        raise ValueError("Monto vacío")
    s = str(raw).strip()
    if not s:
        raise ValueError("Monto vacío")
    # quita símbolos comunes y espacios
    s = s.replace("RD$", "").replace("$", "").replace(" ", "")
    # normaliza separadores: si hay comas y punto, asumimos coma miles y punto decimal.
    # si hay solo coma, asumimos coma decimal -> reemplazar por punto.
    if "," in s and "." in s:
        s = s.replace(",", "")
    elif "," in s and "." not in s:
        s = s.replace(",", ".")
    # ahora s debe lucir como 1234.56 o 1234
    try:
        val = Decimal(s)
    except InvalidOperation:
        raise ValueError("Monto inválido")
    # No negativos
    if val < 0:
        raise ValueError("Monto negativo no permitido")
    # 2 decimales como estándar
    val = val.quantize(Decimal("0.01"))
    return format(val, "f")  # "1234.56"

def _choice_codes(choices):
    """Devuelve set de códigos válidos de un SelectField/RadioField."""
    try:
        return {str(v) for v, _ in (choices or [])}
    except Exception:
        return set()

# ─────────────────────────────────────────────────────────────
# ADMIN: Eliminar solicitud (seguro)
# ─────────────────────────────────────────────────────────────
@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/eliminar', methods=['POST'])
@login_required
@admin_required
def eliminar_solicitud_admin(cliente_id, id):
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()

    # Reglas de negocio: no permitir borrar pagadas o con reemplazos
    if s.estado == 'pagada':
        flash('No puedes eliminar una solicitud pagada. Cancélala o revierte el pago primero.', 'warning')
        return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))
    if getattr(s, 'reemplazos', None):
        if len(s.reemplazos) > 0:
            flash('No puedes eliminar la solicitud porque tiene reemplazos asociados.', 'warning')
            return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))

    try:
        c = Cliente.query.get_or_404(cliente_id)
        db.session.delete(s)

        # Métricas del cliente
        c.total_solicitudes = max((c.total_solicitudes or 1) - 1, 0)
        c.fecha_ultima_actividad = datetime.utcnow()

        db.session.commit()
        flash('Solicitud eliminada.', 'success')
    except IntegrityError:
        db.session.rollback()
        flash('No se pudo eliminar: existen relaciones asociadas (FK).', 'danger')
    except SQLAlchemyError:
        db.session.rollback()
        flash('Error de base de datos al eliminar la solicitud.', 'danger')
    except Exception:
        db.session.rollback()
        flash('Ocurrió un error al eliminar la solicitud.', 'danger')

    return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))


# ─────────────────────────────────────────────────────────────
# ADMIN: Gestionar plan (valida choices y abono OBLIGATORIO)
# ─────────────────────────────────────────────────────────────
from decimal import Decimal, InvalidOperation
from datetime import datetime
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from flask import render_template, request, redirect, url_for, flash

@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/plan', methods=['GET','POST'])
@login_required
@admin_required
def gestionar_plan(cliente_id, id):
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()
    form = AdminGestionPlanForm(obj=s)

    # --- helpers locales y seguros ---
    def _choice_codes(choices):
        """
        choices puede venir como [('Básico','Básico'), ...] o [("Básico","Básico")].
        Devuelve solo los códigos (primer elemento).
        """
        out = []
        for c in choices or []:
            try:
                out.append(str(c[0]))
            except Exception:
                pass
        return set(out)

    def _parse_money_to_decimal_str(raw: str, places=2) -> str:
        """
        Convierte entrada humana a string decimal normalizado con punto y N decimales:
        '1500'        -> '1500.00'
        '1,500'       -> '1500.00'
        '1.500'       -> '1500.00'
        '1,500.50'    -> '1500.50'
        '1.500,50'    -> '1500.50'
        """
        if raw is None:
            raise ValueError("vacío")
        s = str(raw).strip()
        if not s:
            raise ValueError("vacío")

        # quitar símbolos y espacios
        s = s.replace("RD$", "").replace("$", "").replace(" ", "")

        # caso mixto: tiene punto y coma
        if "." in s and "," in s:
            # si la última coma está a la derecha del último punto -> coma es decimal
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")
            else:
                # punto decimal, coma miles
                s = s.replace(",", "")
        else:
            # solo comas -> asumir decimal con coma
            if "," in s:
                parts = s.split(",")
                if len(parts) > 2:
                    s = "".join(parts[:-1]) + "." + parts[-1]
                else:
                    s = s.replace(",", ".")
            # solo puntos -> si hay varios, últimos 2 dígitos suelen ser decimales
            elif "." in s:
                parts = s.split(".")
                if len(parts) > 2:
                    s = "".join(parts[:-1]) + "." + parts[-1]
                # con un solo punto lo aceptamos tal cual

        try:
            val = Decimal(s)
        except InvalidOperation:
            raise ValueError("formato inválido")

        q = Decimal(10) ** -places
        norm = val.quantize(q)
        return f"{norm:.{places}f}"

    if form.validate_on_submit():
        try:
            # --- Validar tipo_plan contra choices si existen ---
            if hasattr(form, 'tipo_plan') and getattr(form.tipo_plan, "choices", None):
                allowed = _choice_codes(form.tipo_plan.choices)
                if str(form.tipo_plan.data) not in allowed:
                    flash('Tipo de plan inválido.', 'danger')
                    return render_template('admin/gestionar_plan.html', form=form, cliente_id=cliente_id, solicitud=s)

            s.tipo_plan = form.tipo_plan.data

            # --- Abono OBLIGATORIO + parseo robusto ---
            if not hasattr(form, 'abono'):
                flash('Falta el campo abono en el formulario.', 'danger')
                return render_template('admin/gestionar_plan.html', form=form, cliente_id=cliente_id, solicitud=s)

            raw_abono = (form.abono.data or '').strip()
            if not raw_abono:
                flash('El abono es obligatorio.', 'danger')
                return render_template('admin/gestionar_plan.html', form=form, cliente_id=cliente_id, solicitud=s)

            try:
                s_abono = _parse_money_to_decimal_str(raw_abono)  # '1500.00'
            except ValueError as e:
                flash(f'Abono inválido: {e}. Formatos válidos: 1500, 1,500, 1.500,50', 'danger')
                return render_template('admin/gestionar_plan.html', form=form, cliente_id=cliente_id, solicitud=s)

            # Si tu columna s.abono es Numeric(10,2), puedes guardar Decimal(s_abono) en su lugar.
            s.abono = s_abono

            # --- Estado ---
            if s.estado not in ('pagada', 'cancelada'):
                s.estado = 'activa'

            # --- Timestamps ---
            now = datetime.utcnow()
            s.fecha_ultima_actividad = now
            s.fecha_ultima_modificacion = now

            db.session.commit()
            flash('Plan y abono actualizados correctamente.', 'success')
            return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))

        except IntegrityError:
            db.session.rollback()
            flash('Conflicto al guardar el plan (valores únicos/relaciones).', 'danger')
        except SQLAlchemyError:
            db.session.rollback()
            flash('Error de base de datos al guardar el plan.', 'danger')
        except Exception as e:
            db.session.rollback()
            flash('Ocurrió un error al guardar el plan.', 'danger')

    return render_template(
        'admin/gestionar_plan.html',
        form=form,
        cliente_id=cliente_id,
        solicitud=s
    )



# ─────────────────────────────────────────────────────────────
# ADMIN: Registrar pago (robusto y consistente)
# ─────────────────────────────────────────────────────────────
@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/pago', methods=['GET', 'POST'])
@login_required
@admin_required
def registrar_pago(cliente_id, id):
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()
    form = AdminPagoForm()

    # GET: precarga solo la candidata ya asociada (si existe)
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

    # POST: inyecta la opción seleccionada para que WTForms valide el valor
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
        # Reglas de negocio
        if s.estado == 'cancelada':
            flash('No puedes registrar pago de una solicitud cancelada.', 'warning')
            return render_template('admin/registrar_pago.html', form=form, cliente_id=cliente_id, solicitud=s)
        if s.estado == 'pagada':
            flash('La solicitud ya está pagada.', 'info')
            return render_template('admin/registrar_pago.html', form=form, cliente_id=cliente_id, solicitud=s)

        try:
            # 1) Validar candidata
            if not form.candidata_id.data:
                flash('Debes seleccionar una candidata.', 'danger')
                return render_template('admin/registrar_pago.html', form=form, cliente_id=cliente_id, solicitud=s)

            cand = Candidata.query.get(form.candidata_id.data)
            if not cand:
                flash('La candidata seleccionada no existe.', 'danger')
                return render_template('admin/registrar_pago.html', form=form, cliente_id=cliente_id, solicitud=s)

            s.candidata_id = cand.fila

            # 2) Parseo de monto en formato canónico "1234.56"
            raw = (form.monto_pagado.data or "").strip()
            monto_canon = _parse_money_to_decimal_str(raw)
            # Guarda canónico (sirve para tus consultas con CAST a Numeric)
            s.monto_pagado = monto_canon

            # 3) Estado + timestamps
            s.estado = 'pagada'
            s.fecha_ultima_actividad = datetime.utcnow()
            s.fecha_ultima_modificacion = datetime.utcnow()

            db.session.commit()
            flash('Pago registrado y solicitud marcada como pagada.', 'success')
            return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))

        except ValueError as e:
            db.session.rollback()
            flash(f'Monto inválido: {e}', 'danger')
        except IntegrityError:
            db.session.rollback()
            flash('No se pudo registrar el pago por conflicto de datos.', 'danger')
        except SQLAlchemyError:
            db.session.rollback()
            flash('Error de base de datos al registrar el pago.', 'danger')
        except Exception:
            db.session.rollback()
            flash('Ocurrió un error al registrar el pago.', 'danger')

    elif request.method == 'POST':
        flash('Revisa los campos marcados en rojo.', 'danger')

    return render_template(
        'admin/registrar_pago.html',
        form=form,
        cliente_id=cliente_id,
        solicitud=s
    )


from decimal import Decimal
from sqlalchemy.orm import joinedload, selectinload

# ============================================================
# Helpers comunes (UTC, validaciones, sumas)
# ============================================================
def _now_utc() -> datetime:
    return datetime.utcnow()

def _today_utc_bounds():
    """Devuelve (inicio_utc, fin_utc) del día actual en UTC."""
    today = datetime.utcnow().date()
    start = datetime(today.year, today.month, today.day)
    end = start + timedelta(days=1)
    return start, end

def _safe_bool(val, default=False):
    try:
        return bool(val)
    except Exception:
        return default

def _nonempty_str(x: str) -> str:
    return (x or '').strip()

# ============================================================
#                           REEMPLAZOS
# ============================================================
@admin_bp.route('/solicitudes/<int:s_id>/reemplazos/nuevo', methods=['GET','POST'])
@login_required
@admin_required
def nuevo_reemplazo(s_id):
    # Eager loading para evitar N+1 si la plantilla muestra relaciones
    sol = (Solicitud.query
           .options(joinedload(Solicitud.reemplazos))
           .get_or_404(s_id))

    # Reglas: no permitir reemplazo si está cancelada o pagada
    if request.method == 'POST':
        if sol.estado == 'cancelada':
            flash('No puedes crear reemplazos en una solicitud cancelada.', 'warning')
            return redirect(url_for('admin.detalle_cliente', cliente_id=sol.cliente_id, id=sol.id))
        if sol.estado == 'pagada':
            flash('No puedes crear reemplazos en una solicitud pagada.', 'warning')
            return redirect(url_for('admin.detalle_cliente', cliente_id=sol.cliente_id, id=sol.id))

    form = AdminReemplazoForm()

    if form.validate_on_submit():
        try:
            # Validaciones mínimas de IDs de candidatas si tu modelo lo exige
            cand_old_id = form.candidata_old_id.data
            if cand_old_id:
                cand_old = Candidata.query.get(cand_old_id)
                if not cand_old:
                    flash('La candidata anterior no existe.', 'danger')
                    return render_template('admin/reemplazo_form.html', form=form, solicitud=sol)

            fecha_ini = form.fecha_inicio_reemplazo.data
            # Si envían fecha a futuro muy lejos, opcionalmente podrías limitarlo
            # if fecha_ini and fecha_ini > datetime.utcnow() + timedelta(days=30): ...

            r = Reemplazo(
                solicitud_id           = s_id,
                candidata_old_id       = cand_old_id,
                motivo_fallo           = _nonempty_str(form.motivo_fallo.data),
                fecha_inicio_reemplazo = fecha_ini,
            )
            db.session.add(r)

            # Estado y métricas
            sol.estado = 'reemplazo'
            sol.fecha_ultima_actividad     = _now_utc()
            sol.fecha_ultima_modificacion  = _now_utc()

            db.session.commit()
            flash('Reemplazo activado y solicitud marcada como reemplazo.', 'success')
            return redirect(url_for('admin.listar_clientes'))

        except IntegrityError:
            db.session.rollback()
            flash('No se pudo crear el reemplazo por conflicto de datos.', 'danger')
        except SQLAlchemyError:
            db.session.rollback()
            flash('Error de base de datos al crear el reemplazo.', 'danger')
        except Exception:
            db.session.rollback()
            flash('Ocurrió un error al crear el reemplazo.', 'danger')

    return render_template(
        'admin/reemplazo_form.html',
        form=form,
        solicitud=sol
    )


@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>')
@login_required
@admin_required
def detalle_solicitud(cliente_id, id):
    # Carga completa para evitar N+1 en plantilla
    s = (Solicitud.query
         .options(
             joinedload(Solicitud.reemplazos).joinedload(Reemplazo.candidata_new),
             joinedload(Solicitud.candidata)
         )
         .filter_by(id=id, cliente_id=cliente_id)
         .first_or_404())

    # Historial de envíos (inicial + reemplazos válidos)
    envios = []
    if s.candidata:
        envios.append({
            'tipo':     'Envío inicial',
            'candidata': s.candidata,
            'fecha':     s.fecha_solicitud
        })
    # Ordena por fecha para consistencia
    reemplazos_ordenados = sorted(list(s.reemplazos or []),
                                  key=lambda r: r.fecha_inicio_reemplazo or r.created_at or datetime.min)
    for idx, r in enumerate(reemplazos_ordenados, start=1):
        if r.candidata_new:
            envios.append({
                'tipo':     f'Reemplazo {idx}',
                'candidata': r.candidata_new,
                'fecha':     r.fecha_inicio_reemplazo or r.created_at
            })

    # Cancelaciones
    cancelaciones = []
    if s.estado == 'cancelada' and s.fecha_cancelacion:
        cancelaciones.append({
            'fecha':  s.fecha_cancelacion,
            'motivo': s.motivo_cancelacion
        })

    return render_template(
        'admin/solicitud_detail.html',
        solicitud      = s,
        envios         = envios,
        cancelaciones  = cancelaciones,
        reemplazos     = reemplazos_ordenados
    )


# ============================================================
#                                   API
# ============================================================
@admin_bp.route('/api/candidatas')
@login_required
@admin_required
def api_candidatas():
    """
    Búsqueda paginada y con límite:
    - q: término (mín. 2 chars), case-insensitive
    - page: 1..n
    - per_page: 1..50 (default 20)
    """
    term = (request.args.get('q') or '').strip()
    page = max(1, int(request.args.get('page', 1) or 1))
    per_page = int(request.args.get('per_page', 20) or 20)
    per_page = max(1, min(per_page, 50))

    query = Candidata.query

    if term:
        if len(term) < 2:
            # Evita escaneos con términos de 1 carácter
            return jsonify(results=[], meta={'page': page, 'per_page': per_page, 'total': 0, 'has_more': False})
        query = query.filter(Candidata.nombre_completo.ilike(f'%{term}%'))

    total = query.count()
    resultados = (query
                  .order_by(Candidata.nombre_completo)
                  .offset((page - 1) * per_page)
                  .limit(per_page)
                  .all())

    has_more = (page * per_page) < total
    return jsonify(
        results=[{'id': c.fila, 'text': c.nombre_completo} for c in resultados],
        meta={'page': page, 'per_page': per_page, 'total': total, 'has_more': has_more}
    )


# ============================================================
#                           LISTADO / CONTADORES
# ============================================================
@admin_bp.route('/solicitudes')
@login_required
@admin_required
def listar_solicitudes():
    """
    Muestra contadores clave:
    - En proceso
    - Copiables (activa/reemplazo) cuya última copia fue antes del inicio del día UTC actual
    """
    proc_count = Solicitud.query.filter_by(estado='proceso').count()

    # Consistencia UTC para "copiable hasta hoy"
    start_utc, _ = _today_utc_bounds()
    copiable_count = (Solicitud.query
        .filter(Solicitud.estado.in_(('activa', 'reemplazo')))
        .filter(
            or_(
                Solicitud.last_copiado_at.is_(None),
                Solicitud.last_copiado_at < start_utc
            )
        )
        .count()
    )

    return render_template(
        'admin/solicitudes_list.html',
        proc_count=proc_count,
        copiable_count=copiable_count
    )


# ============================================================
#                               RESUMEN KPI
# ============================================================
@admin_bp.route('/solicitudes/resumen')
@login_required
@admin_required
def resumen_solicitudes():
    """
    KPIs con fechas coherentes en UTC y casteo numérico robusto.
    Requiere Postgres (usa date_trunc/extract). Si usas otro motor, adaptar funciones.
    """
    # Bordes UTC para hoy/semana/mes
    hoy = datetime.utcnow().date()
    week_start = hoy - timedelta(days=hoy.weekday())
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
    # Promedio publicación (last_copiado_at - fecha_solicitud)
    avg_pub_secs = (db.session.query(
        func.avg(func.extract('epoch', Solicitud.last_copiado_at - Solicitud.fecha_solicitud))
    ).filter(Solicitud.last_copiado_at.isnot(None)).scalar()) or 0
    avg_pub_days = avg_pub_secs / 86400

    # Promedio hasta pago (fecha_ultima_modificacion - fecha_solicitud) solo pagadas
    avg_pay_secs = (db.session.query(
        func.avg(func.extract('epoch', Solicitud.fecha_ultima_modificacion - Solicitud.fecha_solicitud))
    ).filter(Solicitud.estado == 'pagada').scalar()) or 0
    avg_pay_days = avg_pay_secs / 86400

    # Promedio hasta cancelación
    avg_cancel_secs = (db.session.query(
        func.avg(func.extract('epoch', Solicitud.fecha_cancelacion - Solicitud.fecha_solicitud))
    ).filter(Solicitud.fecha_cancelacion.isnot(None)).scalar()) or 0
    avg_cancel_days = avg_cancel_secs / 86400

    # — Top 5 ciudades (ignora NULL/'' para calidad de dato) —
    top_cities = (
        db.session.query(
            Solicitud.ciudad_sector,
            func.count(Solicitud.id).label('cnt')
        )
        .filter(Solicitud.ciudad_sector.isnot(None))
        .filter(func.length(func.trim(Solicitud.ciudad_sector)) > 0)
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
        .filter(Solicitud.fecha_solicitud < _now_utc() - timedelta(days=backlog_threshold_days))
        .count()
    )

    # — Tendencias (semanal/mensual) —
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

    trend_paid_weekly  = (
        db.session.query(
            func.date_trunc('week', Solicitud.fecha_ultima_modificacion).label('period'),
            func.count(Solicitud.id)
        )
        .filter(Solicitud.estado == 'pagada')
        .group_by('period').order_by('period')
        .all()
    )
    trend_paid_monthly = (
        db.session.query(
            func.date_trunc('month', Solicitud.fecha_ultima_modificacion).label('period'),
            func.count(Solicitud.id)
        )
        .filter(Solicitud.estado == 'pagada')
        .group_by('period').order_by('period')
        .all()
    )

    trend_cancel_weekly  = (
        db.session.query(
            func.date_trunc('week', Solicitud.fecha_cancelacion).label('period'),
            func.count(Solicitud.id)
        )
        .filter(Solicitud.estado == 'cancelada')
        .group_by('period').order_by('period')
        .all()
    )
    trend_cancel_monthly = (
        db.session.query(
            func.date_trunc('month', Solicitud.fecha_cancelacion).label('period'),
            func.count(Solicitud.id)
        )
        .filter(Solicitud.estado == 'cancelada')
        .group_by('period').order_by('period')
        .all()
    )

    # Bordes para filtros por periodo (UTC)
    start_today_utc, _ = _today_utc_bounds()
    start_week_utc = datetime(week_start.year, week_start.month, week_start.day)
    start_month_utc = datetime(month_start.year, month_start.month, month_start.day)

    # — Órdenes realizadas (fecha_solicitud) —
    orders_today = Solicitud.query.filter(
        Solicitud.fecha_solicitud >= start_today_utc,
        Solicitud.fecha_solicitud < start_today_utc + timedelta(days=1)
    ).count()
    orders_week  = Solicitud.query.filter(Solicitud.fecha_solicitud >= start_week_utc).count()
    orders_month = Solicitud.query.filter(Solicitud.fecha_solicitud >= start_month_utc).count()

    # — Publicadas (copias) —
    daily_copy   = Solicitud.query.filter(
        Solicitud.last_copiado_at >= start_today_utc,
        Solicitud.last_copiado_at < start_today_utc + timedelta(days=1)
    ).count()
    weekly_copy  = Solicitud.query.filter(Solicitud.last_copiado_at >= start_week_utc).count()
    monthly_copy = Solicitud.query.filter(Solicitud.last_copiado_at >= start_month_utc).count()

    # — Pagos por periodo —
    daily_paid   = (Solicitud.query.filter_by(estado='pagada')
                    .filter(
                        Solicitud.fecha_ultima_modificacion >= start_today_utc,
                        Solicitud.fecha_ultima_modificacion < start_today_utc + timedelta(days=1)
                    ).count())
    weekly_paid  = (Solicitud.query.filter_by(estado='pagada')
                    .filter(Solicitud.fecha_ultima_modificacion >= start_week_utc).count())
    monthly_paid = (Solicitud.query.filter_by(estado='pagada')
                    .filter(Solicitud.fecha_ultima_modificacion >= start_month_utc).count())

    # — Cancelaciones por periodo —
    daily_cancel   = (Solicitud.query.filter_by(estado='cancelada')
                      .filter(
                          Solicitud.fecha_cancelacion >= start_today_utc,
                          Solicitud.fecha_cancelacion < start_today_utc + timedelta(days=1)
                      ).count())
    weekly_cancel  = (Solicitud.query.filter_by(estado='cancelada')
                      .filter(Solicitud.fecha_cancelacion >= start_week_utc).count())
    monthly_cancel = (Solicitud.query.filter_by(estado='cancelada')
                      .filter(Solicitud.fecha_cancelacion >= start_month_utc).count())

    # — Reemplazos por periodo (usa fecha_ultima_modificacion como proxy de cambio) —
    weekly_repl  = (Solicitud.query.filter_by(estado='reemplazo')
                    .filter(Solicitud.fecha_ultima_modificacion >= start_week_utc).count())
    monthly_repl = (Solicitud.query.filter_by(estado='reemplazo')
                    .filter(Solicitud.fecha_ultima_modificacion >= start_month_utc).count())

    # — Estadísticas mensuales de ingreso (pagadas) —
    # NOTA: con el monto guardado en formato canónico "1234.56",
    # el casteo directo a NUMERIC es seguro.
    stats_mensual = (
        db.session.query(
            func.date_trunc('month', Solicitud.fecha_solicitud).label('mes'),
            func.count(Solicitud.id).label('cantidad'),
            func.sum(cast(Solicitud.monto_pagado, Numeric(12, 2))).label('total_pagado')
        )
        .filter(Solicitud.estado == 'pagada')
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
#                     COPIAR SOLICITUDES (LISTA + POST) — ROBUSTO
# =============================================================================
from datetime import datetime, timedelta, timezone
from sqlalchemy import or_, desc, cast
from sqlalchemy.sql import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload
import json
import re
from decimal import Decimal, InvalidOperation

# ──────────────────────────────────────────────────────────────────────────────
# AREAS_COMUNES_CHOICES centralizado (con fallback)
# ──────────────────────────────────────────────────────────────────────────────
try:
    from .routes import AREAS_COMUNES_CHOICES  # type: ignore
except Exception:
    AREAS_COMUNES_CHOICES = [
        ('sala', 'Sala'), ('comedor', 'Comedor'), ('cocina', 'Cocina'),
        ('salon_juegos', 'Salón de juegos'), ('terraza', 'Terraza'),
        ('jardin', 'Jardín'), ('estudio', 'Estudio'), ('patio', 'Patio'),
        ('piscina', 'Piscina'), ('marquesina', 'Marquesina'),
        ('todas_anteriores', 'Todas las anteriores'), ('otro', 'Otro'),
    ]
AREAS_MAP = {k: v for k, v in AREAS_COMUNES_CHOICES}

# --------------------------- HELPERS SEGUROS ---------------------------------

def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (int, float, bool)):
        return str(v)
    try:
        return str(v).strip()
    except Exception:
        return str(v)

def _to_naive_utc(dt):
    if dt is None:
        return None
    try:
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return dt

def _utc_day_bounds(dt: datetime | None = None):
    base = (dt or datetime.utcnow()).date()
    start = datetime(base.year, base.month, base.day)
    end = start + timedelta(days=1)
    return start, end

def _safe_join(parts, sep="\n"):
    out = []
    for p in parts or []:
        s = _s(p)
        if s:
            out.append(s)
    return sep.join(out)

def _first_nonempty_attr(obj, names: list[str], default=""):
    for n in names:
        if hasattr(obj, n):
            v = getattr(obj, n)
            s = _s(v)
            if s:
                return s
    return default

def _as_list(v):
    if v is None:
        return []
    if isinstance(v, (list, tuple, set)):
        return [_s(x) for x in v if _s(x)]
    if isinstance(v, dict):
        return [_s(x) for x in v.values() if _s(x)]
    if isinstance(v, str):
        txt = v.strip()
        if not txt:
            return []
        try:
            parsed = json.loads(txt)
            return _as_list(parsed)
        except Exception:
            if "," in txt:
                return [_s(x) for x in txt.split(",") if _s(x)]
            if ";" in txt:
                return [_s(x) for x in txt.split(";") if _s(x)]
            return [txt]
    return [_s(v)]

def _unique_keep_order(seq):
    """Devuelve únicos preservando el orden de aparición."""
    seen = set()
    out = []
    for x in seq or []:
        sx = _s(x)
        if not sx:
            continue
        if sx in seen:
            continue
        seen.add(sx)
        out.append(sx)
    return out

def _fmt_banos(val) -> str:
    if val is None:
        return ""
    s = _s(val).lower().replace("½", ".5").replace(" 1/2", ".5").replace("1/2", ".5")
    try:
        x = float(s) if any(ch.isdigit() for ch in s) else None
        if x is None:
            return _s(val)
        if abs(x - int(x)) < 1e-9:
            return str(int(x))
        return str(x)
    except Exception:
        return _s(val)

def _norm_area(a: str) -> str:
    k = _s(a).lower()
    if k in AREAS_MAP:
        return AREAS_MAP[k]
    alias = {
        "balcon": "Balcón", "balcón": "Balcón",
        "lavado": "Lavado", "terraza": "Terraza",
        "jardin": "Jardín", "salon_juegos": "Salón de juegos",
    }
    if k in alias:
        return alias[k]
    return a.strip().title()

def _fmt_codigo_humano(codigo: str) -> str:
    c = (codigo or "").strip()
    if not c:
        return ""
    if "-" in c:
        left, right = c.split("-", 1)
    else:
        left, right = c, ""
    try:
        digits = re.findall(r"(\d+)", left)
        if digits:
            n_str = digits[-1]
            n = int(n_str)
            left_fmt = left[: left.rfind(n_str)] + f"{n:,}"
        else:
            left_fmt = left
    except Exception:
        left_fmt = left
    return f"{left_fmt}-{right}" if right else left_fmt

def _format_money_usd(raw) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    s = s.replace("RD$", "").replace("$", "").replace(" ", "")
    us_pattern = r"^\d{1,3}(,\d{3})+(\.\d+)?$"
    eu_pattern = r"^\d{1,3}(\.\d{3})+(,\d+)?$"
    plain_digits = r"^\d+$"
    try:
        if re.match(us_pattern, s):
            num = s.replace(",", "")
            val = Decimal(num)
        elif re.match(eu_pattern, s):
            num = s.replace(".", "").replace(",", ".")
            val = Decimal(num)
        elif re.match(plain_digits, s):
            val = Decimal(s)
        else:
            if "," in s and "." not in s and re.match(r"^\d{1,3}(,\d{3})+$", s):
                val = Decimal(s.replace(",", ""))
            else:
                if "," in s and "." not in s and s.count(",") == 1:
                    val = Decimal(s.replace(",", "."))
                else:
                    val = Decimal(s.replace(",", ""))
        if val == val.to_integral():
            return f"${int(val):,}"
        return f"${val:,.2f}"
    except Exception:
        return f"${s}"

# ------------------------------ RUTAS ----------------------------------------

# RUTAS ADMIN – copiar solicitudes (con nota_cliente al final si existe)

@admin_bp.route('/solicitudes/copiar')
@login_required
@admin_required
def copiar_solicitudes():
    """
    Lista solicitudes copiables y arma el texto final:
    - Modalidad/Hogar sin prefijos fijos.
    - Mascotas solo si hay.
    - Líneas en blanco entre bloques.
    - Funciones en el MISMO ORDEN seleccionado (y 'otro' al final si aplica).
    """
    q = _s(request.args.get('q'))
    try:
        page = max(1, int(request.args.get('page', 1) or 1))
    except Exception:
        page = 1
    try:
        per_page = int(request.args.get('per_page', 50) or 50)
    except Exception:
        per_page = 50
    per_page = max(10, min(per_page, 200))

    start_utc, _ = _utc_day_bounds()

    base_q = (
        Solicitud.query
        .options(
            joinedload(Solicitud.reemplazos).joinedload(Reemplazo.candidata_new)
        )
        .filter(Solicitud.estado.in_(('activa', 'reemplazo')))
        .filter(or_(
            Solicitud.last_copiado_at.is_(None),
            Solicitud.last_copiado_at < start_utc
        ))
    )

    if q:
        like = f"%{q}%"
        filtros = []
        for col in (Solicitud.ciudad_sector,
                    Solicitud.codigo_solicitud,
                    Solicitud.rutas_cercanas,
                    Solicitud.modalidad_trabajo):
            filtros.append(col.ilike(like))
        filtros.append(cast(Solicitud.funciones, db.Text).ilike(like))
        base_q = base_q.filter(or_(*filtros))

    query_ordenada = base_q.order_by(
        desc(Solicitud.estado == 'reemplazo'),
        Solicitud.fecha_solicitud.desc()
    )

    total = query_ordenada.count()
    raw_sols = (
        query_ordenada
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    form = AdminSolicitudForm()
    FUNCIONES_CHOICES = list(getattr(form.funciones, "choices", []) or [])
    FUNCIONES_LABELS = {k: v for k, v in FUNCIONES_CHOICES}

    solicitudes = []
    for s in raw_sols:
        if s.estado == 'reemplazo':
            reems = list(s.reemplazos or [])
        else:
            reems = [r for r in (s.reemplazos or []) if bool(getattr(r, 'oportunidad_nueva', False))]

        # ====================== FUNCIONES (ORDEN CORRECTO) ======================
        # 1) Toma lo guardado (list) preservando orden y únicos.
        raw_codes = _unique_keep_order(_as_list(getattr(s, 'funciones', None)))
        # 2) Excluye 'otro' de códigos (el texto libre va aparte).
        raw_codes = [c for c in raw_codes if c != 'otro']
        # 3) Mapea a labels respetando ese orden.
        funcs = []
        for code in raw_codes:
            label = FUNCIONES_LABELS.get(code)
            if label:
                funcs.append(label)
        # 4) Texto libre de "otro" al final (si existe).
        custom_f = _s(getattr(s, 'funciones_otro', None))
        if custom_f:
            funcs.append(custom_f)

        # ====================== ADULTOS / NIÑOS ======================
        adultos_val = _s(getattr(s, 'adultos', None))
        ninos_line = ""
        ninos_raw = getattr(s, 'ninos', None)
        if ninos_raw not in (None, "", 0, "0"):
            ninos_line = f"Niños: {_s(ninos_raw)}"
            ed = _s(getattr(s, 'edades_ninos', None))
            if ed:
                ninos_line += f" ({ed})"

        # ====================== MODALIDAD ======================
        modalidad = _first_nonempty_attr(s, ['modalidad_trabajo', 'modalidad', 'tipo_modalidad'], '')
        modalidad_line = modalidad

        # ====================== HOGAR ======================
        hogar_partes_detalle = []
        habitaciones = getattr(s, 'habitaciones', None)
        if habitaciones not in (None, "", 0, "0"):
            hogar_partes_detalle.append(f"{_s(habitaciones)} habitaciones")
        banos_txt = _fmt_banos(getattr(s, 'banos', None))
        if banos_txt:
            hogar_partes_detalle.append(f"{banos_txt} baños")
        if bool(getattr(s, 'dos_pisos', False)):
            hogar_partes_detalle.append("2 pisos")

        # Áreas
        areas = []
        for a in _as_list(getattr(s, 'areas_comunes', None)):
            areas.append(_norm_area(a))
        area_otro = _s(getattr(s, 'area_otro', None))
        if area_otro:
            areas.append(_norm_area(area_otro))
        if areas:
            hogar_partes_detalle.append(", ".join(areas))

        tipo_lugar = _s(getattr(s, 'tipo_lugar', None))
        if tipo_lugar and hogar_partes_detalle:
            hogar_descr = f"{tipo_lugar} - {', '.join(hogar_partes_detalle)}"
        elif tipo_lugar:
            hogar_descr = tipo_lugar
        else:
            hogar_descr = ", ".join(hogar_partes_detalle) if hogar_partes_detalle else ""

        # ====================== MASCOTAS ======================
        mascota_val = _s(getattr(s, 'mascota', None))
        mascota_line = f"Mascotas: {mascota_val}" if mascota_val else ""

        # ====================== CAMPOS BASE ======================
        codigo         = _s(getattr(s, 'codigo_solicitud', None))
        ciudad_sector  = _s(getattr(s, 'ciudad_sector', None))
        rutas_cercanas = _s(getattr(s, 'rutas_cercanas', None))

        # Edad requerida
        edad_req_val = getattr(s, 'edad_requerida', None)
        if isinstance(edad_req_val, (list, tuple, set, dict, str)):
            edad_req = ", ".join([_s(x) for x in _as_list(edad_req_val)])
        else:
            edad_req = _s(edad_req_val)

        experiencia    = _s(getattr(s, 'experiencia', None))
        experiencia_it = f"*{experiencia}*" if experiencia else ""
        horario        = _s(getattr(s, 'horario', None))

        # Sueldo
        sueldo_final  = _format_money_usd(getattr(s, 'sueldo', None))
        pasaje_aporte = bool(getattr(s, 'pasaje_aporte', False))

        # Nota del cliente (al final, sin prefijo)
        nota_cli = _s(getattr(s, 'nota_cliente', None))

        # ===== Texto final =====
        cod_fmt = _fmt_codigo_humano(codigo) if codigo else ""
        header_block = "\n".join([
            f"Disponible ( {cod_fmt} )" if cod_fmt else "Disponible",
            f"📍 {ciudad_sector}" if ciudad_sector else "📍",
            f"Ruta más cercana: {rutas_cercanas}" if rutas_cercanas else "Ruta más cercana: ",
        ])

        info_lines = []
        if modalidad_line:
            info_lines.append(modalidad_line)
        if edad_req:
            info_lines.append("")
            info_lines.append(f"Edad: {edad_req}")
        info_lines.extend(["", "Dominicana", "Que sepa leer y escribir"])
        if experiencia_it:
            info_lines.append(f"Experiencia en: {experiencia_it}")
        if horario:
            info_lines.append(f"Horario: {horario}")
        info_block = "\n".join([x for x in info_lines])

        funciones_block = f"Funciones: {', '.join(funcs)}" if funcs else ""
        hogar_line = hogar_descr

        familia_parts = []
        if adultos_val:
            familia_parts.append(f"Adultos: {adultos_val}")
        if ninos_line:
            familia_parts.append(ninos_line)
        if mascota_line:
            familia_parts.append(mascota_line)
        familia_block = "\n".join(familia_parts) if familia_parts else ""

        sueldo_block = ""
        if sueldo_final:
            sueldo_block = f"Sueldo: {sueldo_final} mensual" + (", más ayuda del pasaje" if pasaje_aporte else ", pasaje incluido")

        parts = [
            header_block,
            "",
            info_block.strip() if info_block.strip() else None,
            "",
            funciones_block if funciones_block else None,
            "",
            hogar_line if hogar_line else None,
            "",
            familia_block if familia_block else None,
            "",
            sueldo_block if sueldo_block else None,
            "",
            (nota_cli if nota_cli else None),  # Nota al final, sin prefijos
        ]

        cleaned = []
        for p in parts:
            if p is None:
                continue
            if p == "" and (not cleaned or cleaned[-1] == ""):
                continue
            cleaned.append(p)
        order_text = "\n".join(cleaned).rstrip()

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

    has_more = (page * per_page) < total
    return render_template(
        'admin/solicitudes_copiar.html',
        solicitudes=solicitudes,
        q=q,
        page=page,
        per_page=per_page,
        total=total,
        has_more=has_more
    )


@admin_bp.route('/solicitudes/<int:id>/copiar', methods=['POST'])
@login_required
@admin_required
def copiar_solicitud(id):
    s = Solicitud.query.get_or_404(id)

    if s.estado not in ('activa', 'reemplazo'):
        flash('Esta solicitud no es copiable en su estado actual.', 'warning')
        return redirect(url_for('admin.copiar_solicitudes'))

    start_utc, _ = _utc_day_bounds()
    last = _to_naive_utc(getattr(s, 'last_copiado_at', None))
    if last is not None and last >= start_utc:
        flash('Esta solicitud ya fue marcada como copiada hoy.', 'info')
        return redirect(url_for('admin.copiar_solicitudes'))

    try:
        s.last_copiado_at = func.now()
        db.session.commit()
        flash(f'Solicitud { _s(s.codigo_solicitud) } copiada. Ya no se mostrará hasta mañana.', 'success')
    except SQLAlchemyError:
        db.session.rollback()
        flash('No se pudo marcar la solicitud como copiada.', 'danger')
    except Exception:
        db.session.rollback()
        flash('Ocurrió un error al marcar como copiada.', 'danger')

    return redirect(url_for('admin.copiar_solicitudes'))



# =============================================================================
#                 VISTAS "EN PROCESO" Y RESUMEN DIARIO (MEJORADAS)
# =============================================================================

# Utilidades compartidas (si ya las definiste antes, no las dupliques):
def _now_utc() -> datetime:
    return datetime.utcnow()

def _utc_day_bounds(dt: datetime | None = None):
    """(inicio_día_utc, fin_día_utc) para dt (o hoy UTC)."""
    base = (dt or datetime.utcnow()).date()
    start = datetime(base.year, base.month, base.day)
    end = start + timedelta(days=1)
    return start, end

from urllib.parse import urlparse, urljoin

def _is_safe_redirect_url(target: str) -> bool:
    if not target:
        return False
    ref = urlparse(request.host_url)
    test = urlparse(urljoin(request.host_url, target))
    return (test.scheme in ('http', 'https')) and (ref.netloc == test.netloc)

# ---------------------------------------
# Clientes con solicitudes "en proceso"
# ---------------------------------------
@admin_bp.route('/solicitudes/proceso/clients')
@login_required
@admin_required
def listar_clientes_con_proceso():
    """
    Lista clientes con solicitudes en 'proceso' y el conteo de pendientes.
    Incluye paginación opcional: ?page=1&per_page=50 y búsqueda ?q=...
    """
    q = (request.args.get('q') or '').strip()
    page = max(1, int(request.args.get('page', 1) or 1))
    per_page = max(10, min(int(request.args.get('per_page', 50) or 50), 200))

    base = (
        db.session.query(
            Cliente.id,
            Cliente.nombre_completo,
            Cliente.codigo,
            Cliente.telefono,
            func.count(Solicitud.id).label('pendientes')
        )
        .join(Solicitud, Solicitud.cliente_id == Cliente.id)
        .filter(Solicitud.estado == 'proceso')
        .group_by(Cliente.id, Cliente.nombre_completo, Cliente.codigo, Cliente.telefono)
    )

    if q:
        like = f'%{q}%'
        base = base.filter(
            or_(
                Cliente.nombre_completo.ilike(like),
                Cliente.codigo.ilike(like),
                Cliente.telefono.ilike(like),
            )
        )

    total = base.count()
    resultados = (base
                  .order_by(Cliente.nombre_completo.asc())
                  .offset((page - 1) * per_page)
                  .limit(per_page)
                  .all())

    return render_template(
        'admin/solicitudes_proceso_clients.html',
        resultados=resultados,
        q=q,
        page=page,
        per_page=per_page,
        total=total,
        has_more=(page * per_page) < total
    )

# ---------------------------------------
# Listado de solicitudes "en proceso" por cliente
# ---------------------------------------
@admin_bp.route('/solicitudes/proceso/<int:cliente_id>')
@login_required
@admin_required
def listar_solicitudes_de_cliente_proceso(cliente_id):
    c = Cliente.query.get_or_404(cliente_id)

    # Paginación ligera por si hay muchas
    page = max(1, int(request.args.get('page', 1) or 1))
    per_page = max(10, min(int(request.args.get('per_page', 50) or 50), 200))

    base = (Solicitud.query
            .filter_by(cliente_id=cliente_id, estado='proceso')
            .order_by(Solicitud.fecha_solicitud.desc()))
    total = base.count()
    solicitudes = (base
                   .offset((page - 1) * per_page)
                   .limit(per_page)
                   .all())

    return render_template(
        'admin/solicitudes_proceso_list.html',
        cliente=c,
        solicitudes=solicitudes,
        page=page,
        per_page=per_page,
        total=total,
        has_more=(page * per_page) < total
    )

# ---------------------------------------
# Acciones rápidas sobre "proceso"
# ---------------------------------------
@admin_bp.route('/solicitudes/proceso/acciones')
@login_required
@admin_required
def acciones_solicitudes_proceso():
    # Paginación opcional
    page = max(1, int(request.args.get('page', 1) or 1))
    per_page = max(10, min(int(request.args.get('per_page', 50) or 50), 200))

    base = (Solicitud.query
            .filter_by(estado='proceso')
            .order_by(Solicitud.fecha_solicitud.desc()))
    total = base.count()
    solicitudes = (base
                   .offset((page - 1) * per_page)
                   .limit(per_page)
                   .all())

    return render_template(
        'admin/solicitudes_proceso_acciones.html',
        solicitudes=solicitudes,
        page=page,
        per_page=per_page,
        total=total,
        has_more=(page * per_page) < total
    )

# ---------------------------------------
# Activar solicitud (de proceso -> activa)
# ---------------------------------------
@admin_bp.route('/solicitudes/<int:id>/activar', methods=['POST'])
@login_required
@admin_required
def activar_solicitud_directa(id):
    s = Solicitud.query.get_or_404(id)
    try:
        if s.estado != 'proceso':
            flash(f'La solicitud {s.codigo_solicitud} no está en "proceso".', 'warning')
            return redirect(url_for('admin.acciones_solicitudes_proceso'))

        s.estado = 'activa'
        s.fecha_ultima_modificacion = _now_utc()
        s.fecha_ultima_actividad = _now_utc()
        db.session.commit()
        flash(f'Solicitud {s.codigo_solicitud} marcada como activa.', 'success')
    except SQLAlchemyError:
        db.session.rollback()
        flash('No se pudo activar la solicitud.', 'danger')
    except Exception:
        db.session.rollback()
        flash('Ocurrió un error al activar la solicitud.', 'danger')

    return redirect(url_for('admin.acciones_solicitudes_proceso'))

# -----------------------------------------------------------------------------
# Cancelación con confirmación (GET muestra formulario, POST ejecuta)
# URL: /admin/clientes/<cliente_id>/solicitudes/<id>/cancelar
# -----------------------------------------------------------------------------
@admin_bp.route('/clientes/<int:cliente_id>/solicitudes/<int:id>/cancelar', methods=['GET', 'POST'])
@login_required
@admin_required
def cancelar_solicitud(cliente_id, id):
    s = Solicitud.query.filter_by(id=id, cliente_id=cliente_id).first_or_404()

    # Destino preferido de regreso
    next_url = request.args.get('next') or request.form.get('next') or request.referrer
    fallback = url_for('admin.detalle_cliente', cliente_id=cliente_id)

    if request.method == 'GET':
        # Idempotencia y reglas de estado
        if s.estado == 'cancelada':
            flash(f'La solicitud {s.codigo_solicitud} ya estaba cancelada.', 'warning')
            return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)
        if s.estado == 'pagada':
            flash(f'La solicitud {s.codigo_solicitud} está pagada y no puede cancelarse.', 'warning')
            return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

        return render_template(
            'admin/cancelar_solicitud.html',
            solicitud=s,
            next_url=next_url
        )

    # POST (confirma cancelación)
    motivo = (request.form.get('motivo') or '').strip()
    if len(motivo) < 5:
        flash('Indica un motivo de cancelación (mínimo 5 caracteres).', 'danger')
        return render_template(
            'admin/cancelar_solicitud.html',
            solicitud=s,
            next_url=next_url,
            form={'motivo': {'errors': ['Indica un motivo válido.']}}
        )

    if s.estado not in ('proceso', 'activa', 'reemplazo'):
        flash(f'No se puede cancelar la solicitud en estado «{s.estado}».', 'warning')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    try:
        s.estado = 'cancelada'
        s.motivo_cancelacion = motivo
        s.fecha_cancelacion = _now_utc()
        s.fecha_ultima_modificacion = _now_utc()
        s.fecha_ultima_actividad = _now_utc()
        db.session.commit()
        flash(f'Solicitud {s.codigo_solicitud} cancelada.', 'success')
    except SQLAlchemyError:
        db.session.rollback()
        flash('No se pudo cancelar la solicitud.', 'danger')
    except Exception:
        db.session.rollback()
        flash('Ocurrió un error al cancelar la solicitud.', 'danger')

    return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

# -----------------------------------------------------------------------------
# Cancelación directa (sin formulario)
# URL: /admin/solicitudes/<id>/cancelar_directo  (POST)
# -----------------------------------------------------------------------------
@admin_bp.route('/solicitudes/<int:id>/cancelar_directo', methods=['POST'])
@login_required
@admin_required
def cancelar_solicitud_directa(id):
    s = Solicitud.query.get_or_404(id)

    # Destino preferido de regreso
    next_url = request.args.get('next') or request.form.get('next') or request.referrer
    fallback = url_for('admin.acciones_solicitudes_proceso')

    if s.estado == 'cancelada':
        flash(f'La solicitud {s.codigo_solicitud} ya estaba cancelada.', 'warning')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    if s.estado == 'pagada':
        flash(f'La solicitud {s.codigo_solicitud} está pagada y no puede cancelarse.', 'warning')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    if s.estado not in ('proceso', 'activa', 'reemplazo'):
        flash(f'No se puede cancelar la solicitud en estado «{s.estado}».', 'warning')
        return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

    try:
        s.estado = 'cancelada'
        s.fecha_cancelacion = _now_utc()
        s.fecha_ultima_modificacion = _now_utc()
        s.fecha_ultima_actividad = _now_utc()
        s.motivo_cancelacion = (request.form.get('motivo') or '').strip() or 'Cancelación directa (sin motivo)'
        db.session.commit()
        flash(f'Solicitud {s.codigo_solicitud} cancelada.', 'success')
    except SQLAlchemyError:
        db.session.rollback()
        flash('No se pudo cancelar la solicitud.', 'danger')
    except Exception:
        db.session.rollback()
        flash('Ocurrió un error al cancelar la solicitud.', 'danger')

    return redirect(next_url if _is_safe_redirect_url(next_url) else fallback)

# ---------------------------------------
# Resumen diario por cliente (UTC)
# ---------------------------------------
@admin_bp.route('/clientes/resumen_diario')
@login_required
@admin_required
def resumen_diario_clientes():
    """
    Agrupa solo las solicitudes de HOY (UTC) por cliente.
    Evita usar func.date(...) → usamos rangos [start_utc, end_utc).
    """
    start_utc, end_utc = _utc_day_bounds()

    resumen = (
        db.session.query(
            Cliente.nombre_completo,
            Cliente.codigo,
            Cliente.telefono,
            func.count(Solicitud.id).label('total_solicitudes')
        )
        .join(Solicitud, Solicitud.cliente_id == Cliente.id)
        .filter(Solicitud.fecha_solicitud >= start_utc,
                Solicitud.fecha_solicitud < end_utc)
        .group_by(Cliente.id, Cliente.nombre_completo, Cliente.codigo, Cliente.telefono)
        .order_by(func.count(Solicitud.id).desc(), Cliente.nombre_completo.asc())
        .all()
    )

    return render_template(
        'admin/clientes_resumen_diario.html',
        resumen=resumen,
        hoy=start_utc.date()  # mostramos la fecha UTC usada
    )

# =============================================================================
#                              COMPATIBILIDAD (ADMIN)
# =============================================================================

# Helpers robustos (si ya existen en tu archivo, no los dupliques)
def _as_iter(val):
    if val is None:
        return []
    if isinstance(val, (list, tuple, set)):
        return list(val)
    s = str(val)
    parts = [p.strip() for p in s.split(',') if p.strip()]
    return parts if parts else ([s.strip()] if s.strip() else [])

def _as_set(val):
    return {str(x).strip().lower() for x in _as_iter(val)}

def _first_nonempty(obj, aliases, default=None):
    for name in aliases:
        if hasattr(obj, name):
            v = getattr(obj, name)
            if v not in (None, '', [], {}, ()):
                return v
    return default

def _first_text(obj, aliases, default=''):
    v = _first_nonempty(obj, aliases, default=None)
    if v is None:
        return default
    try:
        return str(v).strip()
    except Exception:
        return default

def _first_int(obj, aliases, default=0):
    v = _first_nonempty(obj, aliases, default=None)
    if v is None:
        return default
    try:
        return int(v)
    except Exception:
        try:
            return int(float(str(v).strip()))
        except Exception:
            return default

def _match_text(haystack: str, needle: str) -> bool:
    return (needle or "").lower() in (haystack or "").lower()

# -------------------------
# Cálculo de compatibilidad
# -------------------------
def calc_score_compat(solicitud: Solicitud, candidata: Candidata):
    """
    Devuelve dict con breakdown y score final (0-100).
    """
    total = 0
    breakdown = []

    # Alias de campos
    CLI_NINOS_ALIASES   = ['ninos']
    CLI_MASCOTA_ALIASES = ['mascota']
    CLI_FUNC_ALIASES    = ['funciones']
    CLI_HORARIO_ALIASES = ['horario']
    CLI_EXPERI_ALIASES  = ['experiencia']  # informativo

    CAND_RITMO_ALIASES     = ['compat_ritmo_preferido']
    CAND_ESTILO_ALIASES    = ['compat_estilo_trabajo']
    CAND_NINOS_ALIASES     = ['compat_relacion_ninos']         # comoda|neutral|prefiere_evitar
    CAND_ANOS_EXP_ALIASES  = ['anos_experiencia']
    CAND_CALIF_ALIASES     = ['calificacion']                  # 1–5
    CAND_FORTS_ALIASES     = ['compat_fortalezas']             # ARRAY
    CAND_DISP_HOR_ALIASES  = ['compat_disponibilidad_horario'] # "mañana, tarde, interna"
    CAND_DISP_DIAS_ALIASES = ['compat_disponibilidad_dias']    # no usado
    CAND_LIMITES_ALIASES   = ['compat_limites_no_negociables'] # ARRAY; p.ej. 'no_mascotas'

    # 1) Ritmo (informativo)
    cand_ritmo = _first_text(candidata, CAND_RITMO_ALIASES, default='')
    breakdown.append(("Ritmo (sin dato para comparar)", +0))

    # 2) Estilo (informativo)
    cand_estilo = _first_text(candidata, CAND_ESTILO_ALIASES, default='')
    breakdown.append(("Estilo (sin dato para comparar)", +0))

    # 3) Niños (±15/−20)
    cant_ninos = _first_int(solicitud, CLI_NINOS_ALIASES, default=0)
    hay_ninos  = cant_ninos > 0
    rel_ninos  = _first_text(candidata, CAND_NINOS_ALIASES, default='').lower()
    if hay_ninos:
        if rel_ninos == 'comoda':
            total += 15; breakdown.append(("Cómoda con niños (solicitud con niños)", +15))
        elif rel_ninos == 'neutral':
            total += 7;  breakdown.append(("Neutral con niños (solicitud con niños)", +7))
        elif rel_ninos == 'prefiere_evitar':
            total -= 20; breakdown.append(("Prefiere evitar niños (solicitud con niños)", -20))
        else:
            breakdown.append(("Relación con niños (sin dato)", +0))
    else:
        breakdown.append(("Sin niños en la solicitud", +0))

    # 4) Mascotas (±20)
    sol_mascota_txt = _first_text(solicitud, CLI_MASCOTA_ALIASES, default='')
    hay_mascota     = bool(sol_mascota_txt)
    cand_limites    = _as_set(_first_nonempty(candidata, CAND_LIMITES_ALIASES, default=[]))
    if hay_mascota:
        if 'no_mascotas' in cand_limites:
            total -= 20; breakdown.append((f"No apta con mascota ({sol_mascota_txt})", -20))
        else:
            total += 15; breakdown.append((f"Apta con mascota ({sol_mascota_txt})", +15))
    else:
        breakdown.append(("Solicitud sin mascotas", +0))

    # 5) Años experiencia (0/5/10)
    anos_exp = _first_int(candidata, CAND_ANOS_EXP_ALIASES, default=0)
    total += 10 if anos_exp >= 3 else 5 if anos_exp >= 1 else 0
    breakdown.append(("Experiencia (años)", 10 if anos_exp >= 3 else 5 if anos_exp >= 1 else 0))

    # 6) Calificación (0–5)
    punt_raw = _first_nonempty(candidata, CAND_CALIF_ALIASES, default=0) or 0
    try:
        punt = int(float(str(punt_raw).strip()))
        punt_pts = max(0, min(5, punt))
    except Exception:
        punt_pts = 0
    total += punt_pts
    breakdown.append(("Puntualidad / calificación", punt_pts))

    # 7) Fortalezas vs funciones requeridas (hasta 20)
    fun_req   = _as_set(_first_nonempty(solicitud, CLI_FUNC_ALIASES, default=[]))
    fort_cand = _as_set(_first_nonempty(candidata, CAND_FORTS_ALIASES, default=[]))
    overlap   = len(fun_req & fort_cand)
    fort_pts  = min(20, overlap * 4)   # 5 matches → 20
    total += fort_pts
    breakdown.append((f"Coincidencias en funciones/fortalezas ({overlap})", fort_pts))

    # 8) Disponibilidad (hasta 10)
    sol_hor_str = _first_text(solicitud, CLI_HORARIO_ALIASES, default='').lower()
    cand_disp_h = _first_text(candidata, CAND_DISP_HOR_ALIASES, default='').lower()
    disp_tokens = _as_set(cand_disp_h)
    disp_pts = 0
    if 'interna' in sol_hor_str and ('interna' in disp_tokens or 'interna' in cand_disp_h):
        disp_pts = 10
    elif any(t in sol_hor_str for t in ('mañana', 'manana', 'tarde', 'noche')) and disp_tokens:
        disp_pts = 8 if any(t in sol_hor_str for t in disp_tokens) else 3
    total += disp_pts
    breakdown.append(("Disponibilidad/horario", disp_pts))

    score_final = max(0, min(100, int(round(total))))
    return {"score": score_final, "breakdown": breakdown}

# ---------------------------------
# VISTA: resumen HTML de compatibilidad
# ---------------------------------
@admin_bp.route('/compatibilidad/<int:cliente_id>/<int:candidata_id>')
@login_required
@admin_required
def ver_compatibilidad(cliente_id, candidata_id):
    cliente = Cliente.query.get_or_404(cliente_id)
    solicitud = (Solicitud.query
                 .filter_by(cliente_id=cliente_id)
                 .order_by(Solicitud.fecha_solicitud.desc())
                 .first())
    if not solicitud:
        flash("Este cliente aún no tiene solicitudes para calcular compatibilidad.", "warning")
        return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))

    candidata = Candidata.query.get_or_404(candidata_id)
    res = calc_score_compat(solicitud, candidata)

    return render_template(
        'admin/compat_resumen.html',
        cliente=cliente,
        solicitud=solicitud,
        candidata=candidata,
        compat=res
    )

# ---------------------------------
# VISTA: PDF de compatibilidad (WeasyPrint)
# ---------------------------------
@admin_bp.route('/compatibilidad/<int:cliente_id>/<int:candidata_id>/pdf')
@login_required
@admin_required
def pdf_compatibilidad(cliente_id, candidata_id):
    cliente = Cliente.query.get_or_404(cliente_id)
    solicitud = (Solicitud.query
                 .filter_by(cliente_id=cliente_id)
                 .order_by(Solicitud.fecha_solicitud.desc())
                 .first())
    if not solicitud:
        flash("Este cliente aún no tiene solicitudes para PDF de compatibilidad.", "warning")
        return redirect(url_for('admin.detalle_cliente', cliente_id=cliente_id))

    candidata = Candidata.query.get_or_404(candidata_id)
    res = calc_score_compat(solicitud, candidata)

    html_str = render_template(
        'admin/compat_pdf.html',
        cliente=cliente,
        solicitud=solicitud,
        candidata=candidata,
        compat=res,
        generado_en=_now_utc()
    )

    try:
        from weasyprint import HTML
        pdf_bytes = HTML(string=html_str, base_url=request.host_url).write_pdf()
        filename = f"compat_{cliente.codigo or cliente.id}_{candidata.fila}.pdf"
        return Response(
            pdf_bytes,
            mimetype='application/pdf',
            headers={'Content-Disposition': f'inline; filename={filename}'}
        )
    except Exception:
        # Fallback/feature flag: no romper UX si WeasyPrint no está presente
        flash("WeasyPrint no está disponible. Mostrando versión HTML del reporte.", "warning")
        return html_str
