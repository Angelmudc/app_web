# -*- coding: utf-8 -*-
from __future__ import annotations

from flask import request, session

from decorators import roles_required

from core import legacy_handlers as legacy_h


@roles_required("admin", "secretaria")
def registro_interno():
    if request.method == "GET":
        return legacy_h.render_template("registro_interno.html")

    nombre = legacy_h.normalize_person_name(request.form.get("nombre_completo"))
    edad_raw = (request.form.get("edad") or "").strip()[:10]
    telefono = legacy_h.normalize_phone(request.form.get("numero_telefono"))
    direccion = (request.form.get("direccion_completa") or "").strip()[:250]
    modalidad = (request.form.get("modalidad_trabajo_preferida") or "").strip()[:100]
    rutas = (request.form.get("rutas_cercanas") or "").strip()[:150]
    empleo_prev = (request.form.get("empleo_anterior") or "").strip()[:150]
    anos_exp = (request.form.get("anos_experiencia") or "").strip()[:50]
    areas_list = request.form.getlist("areas_experiencia")

    planchar_raw = (request.form.get("sabe_planchar") or "").strip().lower()[:3]
    planchar_raw = planchar_raw.replace("í", "i")

    ref_lab = (request.form.get("contactos_referencias_laborales") or "").strip()[:500]
    ref_fam = (request.form.get("referencias_familiares_detalle") or "").strip()[:500]
    acepta_raw = (request.form.get("acepta_porcentaje_sueldo") or "").strip()[:1]
    cedula_raw = (request.form.get("cedula") or "").strip()[:50]
    disponibilidad_inicio = (request.form.get("disponibilidad_inicio") or "").strip()[:80]
    trabaja_con_ninos_raw = (request.form.get("trabaja_con_ninos") or "").strip()[:10]
    trabaja_con_mascotas_raw = (request.form.get("trabaja_con_mascotas") or "").strip()[:10]
    puede_dormir_fuera_raw = (request.form.get("puede_dormir_fuera") or "").strip()[:10]
    sueldo_esperado = (request.form.get("sueldo_esperado") or "").strip()[:80]
    motivacion_trabajo = (request.form.get("motivacion_trabajo") or "").strip()[:350]

    def _fail(message: str, category: str, status_code: int, *, error_message: str, attempts: int = 0):
        legacy_h.flash(message, category)
        legacy_h.log_candidate_create_fail(
            registration_type="interno",
            candidate=None,
            attempt_count=attempts,
            error_message=error_message,
            nombre=nombre,
            cedula=cedula_raw,
        )
        return legacy_h.render_template("registro_interno.html"), status_code

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

    if planchar_raw not in ("si", "no"):
        faltantes.append("Sabe planchar (sí/no)")

    if acepta_raw not in ("1", "0"):
        faltantes.append("Acepta % de sueldo (sí/no)")

    try:
        edad_num = int("".join(ch for ch in edad_raw if ch.isdigit()))
        if edad_num < 16 or edad_num > 75:
            return _fail(
                "📛 La edad debe estar entre 16 y 75 años.",
                "warning",
                400,
                error_message="invalid_age_range",
            )
    except ValueError:
        faltantes.append("Edad (número)")
        edad_num = None

    cedula_digits_input = legacy_h.normalize_cedula_for_compare(cedula_raw)
    if not cedula_raw:
        return _fail("📛 Cédula requerida.", "warning", 400, error_message="cedula_required")

    if faltantes:
        return _fail(
            "Por favor completa: " + ", ".join(faltantes),
            "warning",
            400,
            error_message="missing_required_fields",
        )

    if not legacy_h.phone_has_valid_digits(telefono):
        return _fail(
            "📛 Número de teléfono inválido. Debe tener entre 10 y 15 dígitos.",
            "warning",
            400,
            error_message="invalid_phone_number",
        )

    areas_str = ", ".join([s.strip() for s in areas_list if s.strip()]) if areas_list else ""
    sabe_planchar = planchar_raw == "si"
    acepta_pct = acepta_raw == "1"

    def _parse_optional_yes_no(raw: str):
        val = (raw or "").strip().lower().replace("í", "i")
        if val in ("si", "1", "true", "on"):
            return True
        if val in ("no", "0", "false", "off"):
            return False
        return None

    trabaja_con_ninos = _parse_optional_yes_no(trabaja_con_ninos_raw)
    trabaja_con_mascotas = _parse_optional_yes_no(trabaja_con_mascotas_raw)
    puede_dormir_fuera = _parse_optional_yes_no(puede_dormir_fuera_raw)
    disponibilidad_inicio = disponibilidad_inicio or None
    sueldo_esperado = sueldo_esperado or None
    motivacion_trabajo = motivacion_trabajo or None

    try:
        dup, _ = legacy_h.find_duplicate_candidata_by_cedula(cedula_raw)
    except legacy_h.OperationalError:
        try:
            legacy_h.db.session.rollback()
        except Exception:
            pass
        try:
            legacy_h._get_engine().dispose()
        except Exception:
            pass
        dup, _ = legacy_h.find_duplicate_candidata_by_cedula(cedula_raw)

    if dup:
        return _fail(
            legacy_h.duplicate_cedula_message(dup),
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

    cedula_store = legacy_h.normalize_cedula_for_store(cedula_raw)
    if not cedula_store:
        return _fail("📛 Cédula requerida.", "warning", 400, error_message="cedula_required")

    usuario = (session.get("usuario") or "secretaria").strip()[:64]
    source_route = (request.path or "").strip()[:120] or "/registro_interno/"
    try:
        result, create_state = legacy_h.robust_create_candidata(
            build_candidate=lambda _attempt: legacy_h.Candidata(
                marca_temporal=legacy_h.utc_now_naive(),
                nombre_completo=nombre,
                edad=str(edad_num),
                numero_telefono=telefono,
                direccion_completa=direccion,
                modalidad_trabajo_preferida=modalidad,
                rutas_cercanas=rutas,
                empleo_anterior=empleo_prev,
                anos_experiencia=anos_exp,
                areas_experiencia=areas_str,
                sabe_planchar=sabe_planchar,
                contactos_referencias_laborales=ref_lab,
                referencias_familiares_detalle=ref_fam,
                referencias_laboral=ref_lab,
                referencias_familiares=ref_fam,
                acepta_porcentaje_sueldo=acepta_pct,
                cedula=cedula_store,
                disponibilidad_inicio=disponibilidad_inicio,
                trabaja_con_ninos=trabaja_con_ninos,
                trabaja_con_mascotas=trabaja_con_mascotas,
                puede_dormir_fuera=puede_dormir_fuera,
                sueldo_esperado=sueldo_esperado,
                motivacion_trabajo=motivacion_trabajo,
                medio_inscripcion="Oficina",
                origen_registro="interno",
                creado_por_staff=usuario,
                creado_desde_ruta=source_route,
                estado="en_proceso",
                fecha_cambio_estado=legacy_h.utc_now_naive(),
                usuario_cambio_estado=usuario,
            ),
            expected_fields={
                "cedula": cedula_store,
                "nombre_completo": nombre,
                "numero_telefono": telefono,
                "edad": str(edad_num),
            },
            max_retries=2,
            dispose_pool_fn=lambda: legacy_h._get_engine().dispose(),
        )
    except legacy_h.SQLAlchemyError as e:
        return _fail(
            f"❌ No se pudo guardar el registro: {e.__class__.__name__}",
            "danger",
            500,
            error_message=f"{e.__class__.__name__}: {str(e)[:200]}",
        )

    if not result.ok:
        error_msg = (result.error_message or "").strip()
        if legacy_h.error_looks_like_duplicate_cedula(error_msg):
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

    legacy_h.log_candidate_create_ok(
        registration_type="interno",
        candidate=create_state.candidate,
        attempt_count=result.attempts,
    )

    legacy_h.flash("✅ Candidata registrada correctamente.", "success")
    return legacy_h.redirect(legacy_h.url_for("registro_interno"))
