# -*- coding: utf-8 -*-
from __future__ import annotations

from flask import flash, redirect, render_template, request, session, url_for

from decorators import roles_required
from core.services.candidatas_shared import get_candidata_by_id
from core.services.search import _prioritize_candidata_result, search_candidatas_limited

from core import legacy_handlers as legacy_h


@roles_required("admin", "secretaria")
def buscar_candidata():
    busqueda = (
        (request.form.get("busqueda") if request.method == "POST" else request.args.get("busqueda")) or ""
    ).strip()[:128]

    resultados, candidata, mensaje = [], None, None
    edit_form_overrides = {}
    legacy_h._legacy_buscar_db_trace(
        "request_start",
        method=request.method,
        path=request.path,
        candidata_id_form=(request.form.get("candidata_id") or "").strip() if request.method == "POST" else None,
        candidata_id_query=(request.args.get("candidata_id") or "").strip() if request.method == "GET" else None,
        busqueda=(request.values.get("busqueda") or "").strip()[:128] or None,
    )
    if request.method == "POST":
        legacy_h._legacy_buscar_trace(
            "post_form_snapshot",
            keys=sorted(list(request.form.keys())),
            form=legacy_h._trace_request_form_snapshot(),
            guardar_edicion_present=("guardar_edicion" in request.form),
            guardar_edicion_value=legacy_h._trace_preview(request.form.get("guardar_edicion")),
        )

    if request.method == "POST" and request.form.get("guardar_edicion"):
        cid = (request.form.get("candidata_id") or "").strip()
        legacy_h._legacy_buscar_trace(
            "post_received",
            candidata_id=cid,
            busqueda=busqueda,
            path=request.path,
        )
        if cid.isdigit():
            obj = get_candidata_by_id(cid)
            if obj:
                audit_fields = [
                    "nombre_completo",
                    "edad",
                    "numero_telefono",
                    "direccion_completa",
                    "modalidad_trabajo_preferida",
                    "rutas_cercanas",
                    "empleo_anterior",
                    "anos_experiencia",
                    "areas_experiencia",
                    "contactos_referencias_laborales",
                    "referencias_familiares_detalle",
                    "cedula",
                    "sabe_planchar",
                    "acepta_porcentaje_sueldo",
                    "disponibilidad_inicio",
                    "trabaja_con_ninos",
                    "trabaja_con_mascotas",
                    "puede_dormir_fuera",
                    "sueldo_esperado",
                    "motivacion_trabajo",
                ]
                before_snapshot = legacy_h.snapshot_model_fields(obj, audit_fields)
                legacy_h._legacy_buscar_trace(
                    "post_target_loaded",
                    candidata_id_form=cid,
                    fila_obj=getattr(obj, "fila", None),
                    before_nombre=getattr(obj, "nombre_completo", None),
                    before_telefono=getattr(obj, "numero_telefono", None),
                    before_empleo=getattr(obj, "empleo_anterior", None),
                )

                def _trace_field_apply(field_name: str, form_key: str, before_value, after_value):
                    legacy_h._legacy_buscar_trace(
                        "post_field_apply",
                        candidata_id_form=cid,
                        fila_obj=getattr(obj, "fila", None),
                        field=field_name,
                        form_key=form_key,
                        in_form=(form_key in request.form),
                        before=legacy_h._trace_preview(before_value),
                        received=legacy_h._trace_preview(request.form.get(form_key)),
                        after=legacy_h._trace_preview(after_value),
                    )

                before_value = obj.nombre_completo
                obj.nombre_completo = (request.form.get("nombre") or "").strip()[:150] or obj.nombre_completo
                _trace_field_apply("nombre_completo", "nombre", before_value, obj.nombre_completo)
                before_value = obj.edad
                obj.edad = (request.form.get("edad") or "").strip()[:10] or obj.edad
                _trace_field_apply("edad", "edad", before_value, obj.edad)
                before_value = obj.numero_telefono
                obj.numero_telefono = (request.form.get("telefono") or "").strip()[:30] or obj.numero_telefono
                _trace_field_apply("numero_telefono", "telefono", before_value, obj.numero_telefono)
                before_value = obj.direccion_completa
                obj.direccion_completa = (request.form.get("direccion") or "").strip()[:250] or obj.direccion_completa
                _trace_field_apply("direccion_completa", "direccion", before_value, obj.direccion_completa)
                before_value = obj.modalidad_trabajo_preferida
                obj.modalidad_trabajo_preferida = (
                    (request.form.get("modalidad") or "").strip()[:100] or obj.modalidad_trabajo_preferida
                )
                _trace_field_apply("modalidad_trabajo_preferida", "modalidad", before_value, obj.modalidad_trabajo_preferida)
                before_value = obj.rutas_cercanas
                obj.rutas_cercanas = (request.form.get("rutas") or "").strip()[:150] or obj.rutas_cercanas
                _trace_field_apply("rutas_cercanas", "rutas", before_value, obj.rutas_cercanas)
                before_value = obj.empleo_anterior
                obj.empleo_anterior = (request.form.get("empleo_anterior") or "").strip()[:150] or obj.empleo_anterior
                _trace_field_apply("empleo_anterior", "empleo_anterior", before_value, obj.empleo_anterior)
                before_value = obj.anos_experiencia
                obj.anos_experiencia = (request.form.get("anos_experiencia") or "").strip()[:50] or obj.anos_experiencia
                _trace_field_apply("anos_experiencia", "anos_experiencia", before_value, obj.anos_experiencia)
                before_value = obj.areas_experiencia
                obj.areas_experiencia = (request.form.get("areas_experiencia") or "").strip()[:200] or obj.areas_experiencia
                _trace_field_apply("areas_experiencia", "areas_experiencia", before_value, obj.areas_experiencia)
                before_value = obj.contactos_referencias_laborales
                obj.contactos_referencias_laborales = (
                    (request.form.get("contactos_referencias_laborales") or "").strip()[:250]
                    or obj.contactos_referencias_laborales
                )
                _trace_field_apply(
                    "contactos_referencias_laborales",
                    "contactos_referencias_laborales",
                    before_value,
                    obj.contactos_referencias_laborales,
                )
                before_value = obj.referencias_familiares_detalle
                obj.referencias_familiares_detalle = (
                    (request.form.get("referencias_familiares_detalle") or "").strip()[:250]
                    or obj.referencias_familiares_detalle
                )
                _trace_field_apply(
                    "referencias_familiares_detalle",
                    "referencias_familiares_detalle",
                    before_value,
                    obj.referencias_familiares_detalle,
                )
                obj.referencias_laboral = obj.contactos_referencias_laborales
                obj.referencias_familiares = obj.referencias_familiares_detalle
                _trace_field_apply(
                    "referencias_laboral",
                    "contactos_referencias_laborales",
                    before_snapshot.get("referencias_laboral"),
                    obj.referencias_laboral,
                )
                _trace_field_apply(
                    "referencias_familiares",
                    "referencias_familiares_detalle",
                    before_snapshot.get("referencias_familiares"),
                    obj.referencias_familiares,
                )

                if "disponibilidad_inicio" in request.form:
                    before_value = obj.disponibilidad_inicio
                    obj.disponibilidad_inicio = (request.form.get("disponibilidad_inicio") or "").strip()[:80] or None
                    _trace_field_apply("disponibilidad_inicio", "disponibilidad_inicio", before_value, obj.disponibilidad_inicio)
                if "sueldo_esperado" in request.form:
                    before_value = obj.sueldo_esperado
                    obj.sueldo_esperado = (request.form.get("sueldo_esperado") or "").strip()[:80] or None
                    _trace_field_apply("sueldo_esperado", "sueldo_esperado", before_value, obj.sueldo_esperado)
                if "motivacion_trabajo" in request.form:
                    before_value = obj.motivacion_trabajo
                    obj.motivacion_trabajo = (request.form.get("motivacion_trabajo") or "").strip()[:350] or None
                    _trace_field_apply("motivacion_trabajo", "motivacion_trabajo", before_value, obj.motivacion_trabajo)

                def _parse_optional_bool(raw: str):
                    val = (raw or "").strip().lower().replace("í", "i")
                    if val in ("si", "1", "true", "on"):
                        return True
                    if val in ("no", "0", "false", "off"):
                        return False
                    return None

                if "trabaja_con_ninos" in request.form:
                    before_value = obj.trabaja_con_ninos
                    obj.trabaja_con_ninos = _parse_optional_bool(request.form.get("trabaja_con_ninos"))
                    _trace_field_apply("trabaja_con_ninos", "trabaja_con_ninos", before_value, obj.trabaja_con_ninos)
                if "trabaja_con_mascotas" in request.form:
                    before_value = obj.trabaja_con_mascotas
                    obj.trabaja_con_mascotas = _parse_optional_bool(request.form.get("trabaja_con_mascotas"))
                    _trace_field_apply("trabaja_con_mascotas", "trabaja_con_mascotas", before_value, obj.trabaja_con_mascotas)
                if "puede_dormir_fuera" in request.form:
                    before_value = obj.puede_dormir_fuera
                    obj.puede_dormir_fuera = _parse_optional_bool(request.form.get("puede_dormir_fuera"))
                    _trace_field_apply("puede_dormir_fuera", "puede_dormir_fuera", before_value, obj.puede_dormir_fuera)

                cedula_edit_raw = (request.form.get("cedula") or "").strip()[:50]
                cedula_valid_for_update = False
                if cedula_edit_raw:
                    cedula_edit_digits = legacy_h.normalize_cedula_for_compare(cedula_edit_raw)
                    if not cedula_edit_digits:
                        legacy_h._legacy_buscar_trace(
                            "post_validation_warning",
                            reason="cedula_invalid",
                            candidata_id_form=cid,
                            received_cedula=legacy_h._trace_preview(cedula_edit_raw),
                        )
                        mensaje = "⚠️ Cédula inválida: se guardaron los demás campos, pero la cédula no se actualizó."
                        edit_form_overrides["cedula"] = cedula_edit_raw

                    dup = None
                    if not mensaje:
                        dup, _ = legacy_h.find_duplicate_candidata_by_cedula(
                            cedula_edit_raw,
                            exclude_fila=getattr(obj, "fila", None),
                        )
                    if dup:
                        legacy_h._legacy_buscar_trace(
                            "post_validation_warning",
                            reason="cedula_duplicate",
                            candidata_id_form=cid,
                            received_cedula=legacy_h._trace_preview(cedula_edit_raw),
                            duplicate_fila=getattr(dup, "fila", None),
                        )
                        mensaje = "⚠️ Cédula duplicada: se guardaron los demás campos, pero la cédula no se actualizó."
                        edit_form_overrides["cedula"] = cedula_edit_raw

                    if not mensaje:
                        before_value = obj.cedula
                        obj.cedula = cedula_edit_raw
                        cedula_valid_for_update = True
                        _trace_field_apply("cedula", "cedula", before_value, obj.cedula)

                if "sabe_planchar" in request.form:
                    v_planchar = (request.form.get("sabe_planchar") or "").strip().lower()
                    before_value = obj.sabe_planchar
                    obj.sabe_planchar = v_planchar in ("si", "sí", "true", "1", "on")
                    _trace_field_apply("sabe_planchar", "sabe_planchar", before_value, obj.sabe_planchar)

                if "acepta_porcentaje" in request.form:
                    v_pct = (request.form.get("acepta_porcentaje") or "").strip().lower()
                    before_value = obj.acepta_porcentaje_sueldo
                    obj.acepta_porcentaje_sueldo = v_pct in ("si", "sí", "true", "1", "on")
                    _trace_field_apply("acepta_porcentaje_sueldo", "acepta_porcentaje", before_value, obj.acepta_porcentaje_sueldo)

                expected_verify = {
                    "nombre_completo": (obj.nombre_completo or "").strip(),
                    "edad": (obj.edad or "").strip(),
                    "numero_telefono": (obj.numero_telefono or "").strip(),
                    "direccion_completa": (obj.direccion_completa or "").strip(),
                    "modalidad_trabajo_preferida": (obj.modalidad_trabajo_preferida or "").strip(),
                    "rutas_cercanas": (obj.rutas_cercanas or "").strip(),
                    "empleo_anterior": (obj.empleo_anterior or "").strip(),
                    "anos_experiencia": (obj.anos_experiencia or "").strip(),
                    "areas_experiencia": (obj.areas_experiencia or "").strip(),
                    "contactos_referencias_laborales": (obj.contactos_referencias_laborales or "").strip(),
                    "referencias_familiares_detalle": (obj.referencias_familiares_detalle or "").strip(),
                    "referencias_laboral": (obj.referencias_laboral or "").strip(),
                    "referencias_familiares": (obj.referencias_familiares or "").strip(),
                }
                if cedula_valid_for_update:
                    expected_verify["cedula"] = (obj.cedula or "").strip()
                if "disponibilidad_inicio" in request.form:
                    expected_verify["disponibilidad_inicio"] = (obj.disponibilidad_inicio or "").strip() or None
                if "sueldo_esperado" in request.form:
                    expected_verify["sueldo_esperado"] = (obj.sueldo_esperado or "").strip() or None
                if "motivacion_trabajo" in request.form:
                    expected_verify["motivacion_trabajo"] = (obj.motivacion_trabajo or "").strip() or None
                if "sabe_planchar" in request.form:
                    expected_verify["sabe_planchar"] = bool(obj.sabe_planchar)
                if "acepta_porcentaje" in request.form:
                    expected_verify["acepta_porcentaje_sueldo"] = bool(obj.acepta_porcentaje_sueldo)
                if "trabaja_con_ninos" in request.form:
                    expected_verify["trabaja_con_ninos"] = obj.trabaja_con_ninos
                if "trabaja_con_mascotas" in request.form:
                    expected_verify["trabaja_con_mascotas"] = obj.trabaja_con_mascotas
                if "puede_dormir_fuera" in request.form:
                    expected_verify["puede_dormir_fuera"] = obj.puede_dormir_fuera

                legacy_h._legacy_buscar_trace(
                    "post_before_persist",
                    candidata_id_form=cid,
                    fila_obj=getattr(obj, "fila", None),
                    expected_verify_keys=sorted(list(expected_verify.keys())),
                )
                result = legacy_h.execute_robust_save(
                    session=legacy_h.db.session,
                    persist_fn=lambda _attempt: None,
                    verify_fn=lambda: legacy_h._verify_candidata_fields_saved(int(obj.fila), expected_verify),
                )
                legacy_h._legacy_buscar_trace(
                    "post_after_persist",
                    candidata_id_form=cid,
                    fila_obj=getattr(obj, "fila", None),
                    ok=bool(result.ok),
                    attempts=int(result.attempts),
                    error=(result.error_message or ""),
                )

                if result.ok:
                    after_snapshot = legacy_h.snapshot_model_fields(obj, audit_fields)
                    changes = legacy_h.diff_snapshots(before_snapshot, after_snapshot)
                    session["last_edited_candidata_fila"] = int(obj.fila)
                    legacy_h._legacy_buscar_db_trace(
                        "post_persist_consistency",
                        candidata_id_form=cid,
                        fila_obj=getattr(obj, "fila", None),
                        value_same_session=legacy_h._query_candidata_snapshot_session(int(obj.fila)),
                        value_fresh_connection=legacy_h._query_candidata_snapshot_fresh_connection(int(obj.fila)),
                    )
                    legacy_h._legacy_buscar_trace(
                        "post_saved_ok",
                        candidata_id_form=cid,
                        fila_obj=getattr(obj, "fila", None),
                        attempts=int(result.attempts),
                        after_nombre=getattr(obj, "nombre_completo", None),
                        after_telefono=getattr(obj, "numero_telefono", None),
                        after_empleo=getattr(obj, "empleo_anterior", None),
                    )
                    legacy_h.log_candidata_action(
                        action_type="CANDIDATA_EDIT",
                        candidata=obj,
                        summary=f"Edición de candidata {obj.nombre_completo or obj.fila}",
                        metadata={"candidata_id": obj.fila, "attempt_count": int(result.attempts)},
                        changes=changes,
                        success=True,
                    )
                    if mensaje:
                        flash("✅ Datos actualizados (cédula no actualizada).", "warning")
                        candidata = obj
                        return render_template(
                            "buscar.html",
                            busqueda=busqueda,
                            resultados=resultados,
                            candidata=candidata,
                            mensaje=mensaje,
                            edit_form_overrides=edit_form_overrides,
                        )
                    flash("✅ Datos actualizados correctamente.", "success")
                    return redirect(url_for("buscar_candidata", candidata_id=cid))

                error_message = (result.error_message or "").lower()
                legacy_h._legacy_buscar_trace(
                    "post_saved_fail",
                    candidata_id_form=cid,
                    fila_obj=getattr(obj, "fila", None),
                    attempts=int(result.attempts),
                    error=(result.error_message or ""),
                )
                if "unique" in error_message or "duplicate" in error_message or "cedula" in error_message:
                    legacy_h.log_candidata_action(
                        action_type="CANDIDATA_EDIT",
                        candidata=obj,
                        summary=f"Fallo edición de candidata {obj.fila}",
                        metadata={"attempt_count": int(result.attempts)},
                        success=False,
                        error="Conflicto de cédula duplicada.",
                    )
                    mensaje = "⚠️ Ya existe una candidata con esta cédula (aunque esté escrita diferente)."
                else:
                    legacy_h.app.logger.error(
                        "❌ Error al guardar edición de candidata fila=%s attempts=%s error=%s",
                        obj.fila,
                        result.attempts,
                        result.error_message,
                    )
                    legacy_h.log_candidata_action(
                        action_type="CANDIDATA_EDIT",
                        candidata=obj,
                        summary=f"Fallo edición de candidata {obj.fila}",
                        metadata={"attempt_count": int(result.attempts)},
                        success=False,
                        error="Error al guardar edición de candidata.",
                    )
                    mensaje = "❌ Error al guardar. Intenta de nuevo."
            else:
                legacy_h._legacy_buscar_trace(
                    "post_early_return",
                    reason="candidata_not_found",
                    candidata_id_form=cid,
                )
                mensaje = "⚠️ Candidata no encontrada."
        else:
            legacy_h._legacy_buscar_trace(
                "post_early_return",
                reason="invalid_candidata_id",
                candidata_id_form=cid,
            )
            mensaje = "❌ ID de candidata inválido."
    elif request.method == "POST":
        legacy_h._legacy_buscar_trace(
            "post_skip_update_branch",
            reason="guardar_edicion_missing_or_empty",
            keys=sorted(list(request.form.keys())),
        )

    cid = (request.args.get("candidata_id") or "").strip()
    if cid.isdigit():
        candidata = get_candidata_by_id(cid)
        if not candidata:
            mensaje = "⚠️ Candidata no encontrada."
        else:
            session["last_edited_candidata_fila"] = int(candidata.fila)
            legacy_h._legacy_buscar_trace(
                "get_open_candidate",
                candidata_id_query=cid,
                fila_obj=getattr(candidata, "fila", None),
                nombre=getattr(candidata, "nombre_completo", None),
            )

    if busqueda and not candidata:
        try:
            resultados = search_candidatas_limited(
                busqueda,
                limit=300,
                order_mode="id_desc",
                log_label="buscar",
            )
            resultados = _prioritize_candidata_result(
                resultados,
                session.get("last_edited_candidata_fila"),
            )
            legacy_h._legacy_buscar_trace(
                "search_results",
                busqueda=busqueda,
                filas=[int(getattr(r, "fila", 0) or 0) for r in (resultados or [])[:10]],
                total=len(resultados or []),
                last_edited=session.get("last_edited_candidata_fila"),
            )
            legacy_h._legacy_buscar_db_trace(
                "search_results_db",
                busqueda=busqueda,
                first_fila=int(getattr((resultados or [None])[0], "fila", 0) or 0) if resultados else None,
            )

            if not resultados:
                mensaje = "⚠️ No se encontraron coincidencias."
        except Exception:
            legacy_h.db.session.rollback()
            legacy_h.app.logger.exception("❌ Error buscando candidatas")
            mensaje = "❌ Ocurrió un error al buscar."

    return render_template(
        "buscar.html",
        busqueda=busqueda,
        resultados=resultados,
        candidata=candidata,
        mensaje=mensaje,
        edit_form_overrides=edit_form_overrides,
    )
