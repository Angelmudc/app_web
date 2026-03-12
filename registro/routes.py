# --- Registro público de candidatas -----------------------------------------

from typing import Optional

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from config_app import db, cache
from models import Candidata
from utils.candidate_registration import (
    error_looks_like_duplicate_cedula,
    log_candidate_create_fail,
    log_candidate_create_ok,
    normalize_person_name,
    robust_create_candidata,
)
from utils.cedula_guard import duplicate_cedula_message, find_duplicate_candidata_by_cedula
from utils.cedula_normalizer import normalize_cedula_for_compare, normalize_cedula_for_store
from utils.public_intake import (
    clean_spaces,
    digits_only,
    get_request_ip,
    has_min_real_chars,
    hit_rate_limit,
    normalize_phone_for_store,
)
from utils.timezone import utc_now_naive
from utils.staff_notifications import create_staff_notification

# Blueprint dedicado (NO es el "public" del website)
# OJO: el nombre del blueprint es "registro_publico" para que los url_for queden estables.
registro_bp = Blueprint("registro_publico", __name__)


def _yn_to_bool(value: str) -> Optional[bool]:
    v = (value or "").strip().lower()
    if v in {"si", "sí", "1", "true", "yes"}:
        return True
    if v in {"no", "0", "false"}:
        return False
    return None


def _safe_dispose_pool():
    """Libera conexiones del pool por si hubo un corte SSL."""
    try:
        engine = db.get_engine()
        engine.dispose()
    except Exception:
        pass


# Acepta con y sin / final (para evitar 404 cuando el navegador entra a /registro/)
@registro_bp.route('/registro_publico/', methods=['GET', 'POST'], strict_slashes=False)
def registro_publico():
    """Formulario público de registro de candidatas."""
    if request.method == 'GET':
        return render_template('registro/registro_publico.html')

    # Honeypot anti-bot: si viene con datos, responder sin procesar.
    if (request.form.get('website') or '').strip():
        return redirect(url_for('registro_publico.registro_publico_gracias'))

    if not bool(current_app.config.get("TESTING")):
        ip = get_request_ip(request)
        if hit_rate_limit(cache=cache, scope="registro_domestica", actor=ip, limit=8, window_seconds=600):
            flash("Demasiados intentos en poco tiempo. Espera unos minutos e intenta de nuevo.", "warning")
            return render_template('registro/registro_publico.html'), 429

    # --- POST: recoger datos del formulario (limitando tamaños) ---
    nombre       = normalize_person_name(request.form.get('nombre_completo'))
    edad_raw     = (request.form.get('edad') or '').strip()[:10]
    telefono     = normalize_phone_for_store(request.form.get('numero_telefono'))
    direccion    = (request.form.get('direccion_completa') or '').strip()[:250]
    modalidad    = clean_spaces(request.form.get('modalidad_trabajo_preferida'), max_len=100)
    rutas        = (request.form.get('rutas_cercanas') or '').strip()[:150]
    empleo_prev  = (request.form.get('empleo_anterior') or '').strip()[:1200]
    anos_exp     = (request.form.get('anos_experiencia') or '').strip()[:50]
    areas_list   = request.form.getlist('areas_experiencia')  # checkboxes
    planchar_raw = (request.form.get('sabe_planchar') or '').strip().lower()[:3]
    planchar_raw = planchar_raw.replace('í', 'i')
    ref_lab      = (request.form.get('contactos_referencias_laborales') or '').strip()[:500]
    ref_fam      = (request.form.get('referencias_familiares_detalle') or '').strip()[:500]
    acepta_raw   = (request.form.get('acepta_porcentaje_sueldo') or '').strip()[:1]
    cedula_raw   = (request.form.get('cedula') or '').strip()[:50]
    disponibilidad = clean_spaces(request.form.get('disponibilidad_inicio'), max_len=80)
    convive_ninos = clean_spaces(request.form.get('trabajo_con_ninos'), max_len=32).lower()
    convive_mascotas = clean_spaces(request.form.get('trabajo_con_mascotas'), max_len=32).lower()
    puede_dormir = clean_spaces(request.form.get('puede_dormir_fuera'), max_len=32).lower()
    sueldo_esperado = clean_spaces(request.form.get('sueldo_esperado'), max_len=80)
    motivacion = clean_spaces(request.form.get('motivacion_trabajo'), max_len=350)

    def _fail(message: str, category: str, status_code: int, *, error_message: str, attempts: int = 0):
        flash(message, category)
        log_candidate_create_fail(
            registration_type="publico",
            candidate=None,
            attempt_count=attempts,
            error_message=error_message,
            nombre=nombre,
            cedula=cedula_raw,
        )
        return render_template('registro/registro_publico.html'), status_code

    # --- Validaciones mínimas y mensajes claros ---
    faltantes = []
    for campo, valor in [
        ("Nombre completo", nombre),
        ("Edad", edad_raw),
        ("Número de teléfono", telefono),
        ("Dirección completa", direccion),
        ("Modalidad de trabajo", modalidad),
        ("Rutas cercanas", rutas),
        ("Empleo anterior", empleo_prev),
        ("Años de experiencia", anos_exp),
        ("Referencias laborales", ref_lab),
        ("Referencias familiares", ref_fam),
        ("Cédula", cedula_raw),
    ]:
        if not valor:
            faltantes.append(campo)

    if planchar_raw not in ('si', 'no'):
        faltantes.append("Sabe planchar (sí/no)")

    if acepta_raw not in ('1', '0'):
        faltantes.append("Acepta % de sueldo (sí/no)")

    # Edad razonable
    try:
        edad_num = int(''.join(ch for ch in edad_raw if ch.isdigit()))
        if edad_num < 16 or edad_num > 75:
            flash("📛 La edad debe estar entre 16 y 75 años.", "warning")
            return render_template('registro/registro_publico.html'), 400
    except ValueError:
        faltantes.append("Edad (número)")
        edad_num = None

    if not cedula_raw:
        return _fail(
            "📛 Cédula requerida.",
            "warning",
            400,
            error_message="cedula_required",
        )

    cedula_digits_input = normalize_cedula_for_compare(cedula_raw)

    if faltantes:
        return _fail(
            "Por favor completa: " + ", ".join(faltantes),
            "warning",
            400,
            error_message="missing_required_fields",
        )

    if not has_min_real_chars(nombre, min_chars=6):
        return _fail(
            "📛 El nombre completo debe tener al menos 6 letras.",
            "warning",
            400,
            error_message="invalid_full_name_length",
        )

    phone_digits = digits_only(telefono)
    if len(phone_digits) != 10:
        return _fail(
            "📛 Número de teléfono inválido. Debe contener exactamente 10 dígitos.",
            "warning",
            400,
            error_message="invalid_phone_number",
        )

    # Convertir/normalizar algunos valores
    areas_str     = ', '.join([s.strip() for s in areas_list if s.strip()]) if areas_list else ''
    sabe_planchar = (planchar_raw == 'si')
    acepta_pct    = (acepta_raw == '1')

    # --- Comprobación de duplicado por cédula (DB-safe) ---
    try:
        dup, _ = find_duplicate_candidata_by_cedula(cedula_raw)
    except OperationalError:
        _safe_dispose_pool()
        db.session.rollback()
        dup, _ = find_duplicate_candidata_by_cedula(cedula_raw)

    if dup:
        return _fail(
            duplicate_cedula_message(dup),
            "warning",
            400,
            error_message="duplicate_cedula_precheck",
        )

    if len(cedula_digits_input) != 11:
        return _fail(
            "📛 Cédula inválida. Debe contener 11 dígitos.",
            "warning",
            400,
            error_message="invalid_cedula_digits",
        )

    cedula_store = normalize_cedula_for_store(cedula_raw)
    if not cedula_store:
        return _fail(
            "📛 Cédula requerida.",
            "warning",
            400,
            error_message="cedula_required",
        )

    try:
        source_route = (request.path or "").strip()[:120] or "/registro/registro_publico/"
        result, create_state = robust_create_candidata(
            build_candidate=lambda _attempt: Candidata(
                marca_temporal=utc_now_naive(),
                nombre_completo=nombre,
                edad=str(edad_num),
                numero_telefono=phone_digits,
                direccion_completa=direccion,
                modalidad_trabajo_preferida=modalidad,
                rutas_cercanas=rutas,
                empleo_anterior=empleo_prev,
                anos_experiencia=anos_exp,
                areas_experiencia=areas_str,
                sabe_planchar=sabe_planchar,
                contactos_referencias_laborales=ref_lab,
                referencias_familiares_detalle=ref_fam,
                acepta_porcentaje_sueldo=acepta_pct,
                cedula=cedula_store,
                medio_inscripcion="Web",
                origen_registro="publico_domestica",
                creado_por_staff=None,
                creado_desde_ruta=source_route,
                estado="en_proceso",
                fecha_cambio_estado=utc_now_naive(),
                usuario_cambio_estado="registro_publico",
                disponibilidad_inicio=disponibilidad or None,
                trabaja_con_ninos=_yn_to_bool(convive_ninos),
                trabaja_con_mascotas=_yn_to_bool(convive_mascotas),
                puede_dormir_fuera=_yn_to_bool(puede_dormir),
                sueldo_esperado=sueldo_esperado or None,
                motivacion_trabajo=motivacion or None,
            ),
            expected_fields={
                "cedula": cedula_store,
                "nombre_completo": nombre,
                "numero_telefono": phone_digits,
                "edad": str(edad_num),
            },
            max_retries=2,
            dispose_pool_fn=_safe_dispose_pool,
        )
    except SQLAlchemyError as e:
        return _fail(
            "❌ No se pudo guardar el registro en este momento. Intenta nuevamente en unos minutos.",
            "danger",
            500,
            error_message=f"{e.__class__.__name__}: {str(e)[:200]}",
        )

    if not result.ok:
        error_msg = (result.error_message or "").strip()
        if error_looks_like_duplicate_cedula(error_msg):
            return _fail(
                "⚠️ Ya existe una candidata con esta cédula (aunque esté escrita diferente).",
                "warning",
                400,
                error_message=error_msg or "duplicate_cedula_commit",
                attempts=result.attempts,
            )
        return _fail(
            "❌ No se pudo verificar el registro guardado. Intenta de nuevo en unos segundos.",
            "danger",
            503,
            error_message=error_msg or "create_verification_failed",
            attempts=result.attempts,
        )

    if not create_state.candidate:
        return _fail(
            "❌ No se pudo verificar el registro guardado. Intenta de nuevo en unos segundos.",
            "danger",
            503,
            error_message="candidate_instance_missing_after_commit",
            attempts=result.attempts,
        )

    log_candidate_create_ok(
        registration_type="publico",
        candidate=create_state.candidate,
        attempt_count=result.attempts,
    )
    try:
        create_staff_notification(
            tipo="publico_domestica_nueva",
            entity_type="candidata",
            entity_id=int(getattr(create_state.candidate, "fila", 0) or 0),
            titulo="Nueva candidata por formulario público",
            mensaje=(getattr(create_state.candidate, "nombre_completo", None) or "").strip()[:300] or None,
            payload={
                "origen_registro": "publico_domestica",
                "source_route": (request.path or "").strip(),
                "candidata_fila": int(getattr(create_state.candidate, "fila", 0) or 0),
            },
        )
    except Exception:
        pass

    return redirect(url_for('registro_publico.registro_publico_gracias'))


@registro_bp.route('/registro_publico/gracias/', methods=['GET'], strict_slashes=False)
def registro_publico_gracias():
    """Pantalla de confirmación luego de enviar el formulario público."""
    return render_template('registro/registro_publico_gracias.html')
