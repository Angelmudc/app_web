# --- Registro público de candidatas -----------------------------------------

from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from config_app import db, normalize_cedula
from models import Candidata

# Blueprint dedicado (NO es el "public" del website)
# OJO: el nombre del blueprint es "registro_publico" para que los url_for queden estables.
registro_bp = Blueprint("registro_publico", __name__)


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

    # --- POST: recoger datos del formulario (limitando tamaños) ---
    nombre       = (request.form.get('nombre_completo') or '').strip()[:150]
    edad_raw     = (request.form.get('edad') or '').strip()[:10]
    telefono     = (request.form.get('numero_telefono') or '').strip()[:30]
    direccion    = (request.form.get('direccion_completa') or '').strip()[:250]
    modalidad    = (request.form.get('modalidad_trabajo_preferida') or '').strip()[:100]
    rutas        = (request.form.get('rutas_cercanas') or '').strip()[:150]
    empleo_prev  = (request.form.get('empleo_anterior') or '').strip()[:150]
    anos_exp     = (request.form.get('anos_experiencia') or '').strip()[:50]
    areas_list   = request.form.getlist('areas_experiencia')  # checkboxes
    planchar_raw = (request.form.get('sabe_planchar') or '').strip().lower()[:3]
    planchar_raw = planchar_raw.replace('í', 'i')
    ref_lab      = (request.form.get('contactos_referencias_laborales') or '').strip()[:500]
    ref_fam      = (request.form.get('referencias_familiares_detalle') or '').strip()[:500]
    acepta_raw   = (request.form.get('acepta_porcentaje_sueldo') or '').strip()[:1]
    cedula_raw   = (request.form.get('cedula') or '').strip()[:20]

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

    cedula_norm = normalize_cedula(cedula_raw)
    if not cedula_norm:
        flash("📛 Cédula inválida. Debe contener 11 dígitos.", "warning")
        return render_template('registro/registro_publico.html'), 400

    if faltantes:
        flash("Por favor completa: " + ", ".join(faltantes), "warning")
        return render_template('registro/registro_publico.html'), 400

    # Convertir/normalizar algunos valores
    areas_str     = ', '.join([s.strip() for s in areas_list if s.strip()]) if areas_list else ''
    sabe_planchar = (planchar_raw == 'si')
    acepta_pct    = (acepta_raw == '1')

    # --- Comprobación de duplicado por cédula (pre-check) ---
    try:
        dup = Candidata.query.filter(Candidata.cedula == cedula_norm).first()
    except OperationalError:
        _safe_dispose_pool()
        db.session.rollback()
        dup = Candidata.query.filter(Candidata.cedula == cedula_norm).first()

    if dup:
        flash("⚠️ Ya existe una candidata registrada con esta cédula.", "warning")
        return render_template('registro/registro_publico.html'), 400

    # --- Crear objeto y guardar ---
    nueva = Candidata(
        marca_temporal                  = datetime.utcnow(),
        nombre_completo                 = nombre,
        edad                            = str(edad_num),
        numero_telefono                 = telefono,
        direccion_completa              = direccion,
        modalidad_trabajo_preferida     = modalidad,
        rutas_cercanas                  = rutas,
        empleo_anterior                 = empleo_prev,
        anos_experiencia                = anos_exp,
        areas_experiencia               = areas_str,
        sabe_planchar                   = sabe_planchar,
        contactos_referencias_laborales = ref_lab,
        referencias_familiares_detalle  = ref_fam,
        acepta_porcentaje_sueldo        = acepta_pct,
        cedula                          = cedula_norm,
        medio_inscripcion               = "Web",
        estado                          = "en_proceso",
        fecha_cambio_estado             = datetime.utcnow(),
        usuario_cambio_estado           = "registro_publico",
    )

    try:
        db.session.add(nueva)
        db.session.flush()
        db.session.commit()
    except OperationalError:
        _safe_dispose_pool()
        db.session.rollback()
        try:
            db.session.add(nueva)
            db.session.flush()
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("⚠️ Ya existe una candidata registrada con esta cédula.", "warning")
            return render_template('registro/registro_publico.html'), 400
        except Exception:
            db.session.rollback()
            flash("❌ Problema momentáneo con la conexión. Intenta de nuevo en unos segundos.", "danger")
            return render_template('registro/registro_publico.html'), 503
    except IntegrityError:
        db.session.rollback()
        flash("⚠️ Ya existe una candidata registrada con esta cédula.", "warning")
        return render_template('registro/registro_publico.html'), 400
    except SQLAlchemyError as e:
        db.session.rollback()
        flash(f"❌ No se pudo guardar el registro: {e.__class__.__name__}", "danger")
        return render_template('registro/registro_publico.html'), 500

    return redirect(url_for('registro_publico.registro_publico_gracias'))


@registro_bp.route('/registro_publico/gracias/', methods=['GET'], strict_slashes=False)
def registro_publico_gracias():
    """Pantalla de confirmación luego de enviar el formulario público."""
    return render_template('registro/registro_publico_gracias.html')