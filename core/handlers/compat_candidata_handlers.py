# -*- coding: utf-8 -*-
from __future__ import annotations

from flask import current_app, flash, redirect, render_template, request, url_for

from decorators import roles_required
from core.services.search import search_candidatas_limited
from utils.guards import assert_candidata_no_descalificada, candidatas_activas_filter

from core import legacy_handlers as legacy_h


COMPAT_RITMOS = [
    ('tranquilo', 'Tranquilo'),
    ('activo', 'Activo'),
    ('muy_activo', 'Muy activo'),
]
COMPAT_ESTILOS = [
    ('seguimiento', 'Prefiere instrucciones claras'),
    ('toma_iniciativa', 'Toma iniciativa'),
]
COMPAT_COMUNICACION = [
    ('directa', 'Directa'),
    ('suave', 'Suave'),
    ('mixta', 'Mixta'),
]
COMPAT_RELACION_NINOS = [
    ('comoda', 'Cómoda'),
    ('neutral', 'Neutral'),
    ('prefiere_sin_ninos', 'Prefiere sin niños'),
]
COMPAT_EXPERIENCIA_NIVEL = [
    ('baja', 'Baja'),
    ('media', 'Media'),
    ('alta', 'Alta'),
]
COMPAT_MASCOTAS = list(legacy_h.MASCOTAS_CHOICES)
COMPAT_MASCOTAS_IMPORTANCIA = list(legacy_h.MASCOTAS_IMPORTANCIA_CHOICES)

FORTALEZAS = [
    ('limpieza_general',   'Limpieza general'),
    ('organizacion',       'Organización'),
    ('cocina_basica',      'Cocina básica'),
    ('cuidado_ninos',      'Cuidado de niños'),
    ('cuidado_mayores',    'Cuidado de personas mayores'),
    ('compras',            'Compras / mandados'),
    ('inventario',         'Orden / inventario'),
    ('electrodomesticos',  'Manejo de electrodomésticos'),
]
TAREAS_EVITAR = [
    ('cocinar',          'Cocinar'),
    ('planchar',         'Planchar'),
    ('animales_grandes', 'Mascotas grandes'),
    ('subir_escaleras',  'Subir muchas escaleras'),
    ('nocturno',         'Trabajar de noche'),
    ('dormir_fuera',     'Dormir fuera de casa'),
    ('altas_exigencias', 'Hogares de alta exigencia'),
]
LIMITES_NO_NEG = [
    ('no_cocinar',       'No cocinar'),
    ('no_planchar',      'No planchar'),
    ('no_cuidar_ninos',  'No cuidado de niños'),
    ('no_mascotas',      'No mascotas'),
    ('no_fines_semana',  'No fines de semana'),
    ('no_nocturno',      'No horario nocturno'),
]
DIAS_SEMANA = [
    ('lun', 'Lunes'), ('mar', 'Martes'), ('mie', 'Miércoles'),
    ('jue', 'Jueves'), ('vie', 'Viernes'), ('sab', 'Sábado'), ('dom', 'Domingo')
]
HORARIOS = [
    ("8am-5pm", "8:00 AM a 5:00 PM"),
    ("9am-6pm", "9:00 AM a 6:00 PM"),
    ("10am-6pm", "10:00 AM a 6:00 PM"),
    ("medio_tiempo", "Medio tiempo"),
    ("fin_de_semana", "Fin de semana"),
    ("noche_solo", "Solo de noche"),
    ("dormida_l-v", "Dormida (Lunes a Viernes)"),
    ("dormida_l-s", "Dormida (Lunes a Sábado)"),
    ("salida_quincenal", "Salida quincenal (cada 15 días)"),
]


def _getlist_clean(name: str):
    return [x.strip() for x in request.form.getlist(name) if x and x.strip()]


def _int_1a5(name: str):
    try:
        v = int((request.form.get(name) or '').strip())
        return v if 1 <= v <= 5 else None
    except Exception:
        return None


def _norm_choice(v: str, allowed: set):
    v = (v or '').strip().lower()
    return v if v in allowed else None


def _filter_allowed(items, allowed: set):
    out = []
    for it in items or []:
        key = (it or '').strip().lower()
        if key in allowed:
            out.append(key)
    return out


CHOICES_DICT = {
    "RITMOS": COMPAT_RITMOS,
    "ESTILOS": COMPAT_ESTILOS,
    "COMUNICACION": COMPAT_COMUNICACION,
    "REL_NINOS": COMPAT_RELACION_NINOS,
    "EXP_NIVEL": COMPAT_EXPERIENCIA_NIVEL,
    "FORTALEZAS": FORTALEZAS,
    "TAREAS_EVITAR": TAREAS_EVITAR,
    "LIMITES": LIMITES_NO_NEG,
    "DIAS": DIAS_SEMANA,
    "HORARIOS": HORARIOS,
    "MASCOTAS": COMPAT_MASCOTAS,
    "MASCOTAS_IMPORTANCIA": COMPAT_MASCOTAS_IMPORTANCIA,
}
HORARIO_ORDER = {tok: idx for idx, (tok, _lbl) in enumerate(legacy_h.HORARIO_OPTIONS)}


@roles_required('admin', 'secretaria')
def compat_candidata():
    """
    - GET sin ?fila  → buscador (acepta ?q= en GET).
    - GET con ?fila  → muestra formulario del test.
    - POST (accion=guardar & fila) → guarda el test y redirige.
    """
    fila = request.values.get('fila', type=int)

    if request.method == 'POST' and request.form.get('accion') == 'guardar' and fila:
        c = legacy_h.Candidata.query.get_or_404(fila)
        blocked = assert_candidata_no_descalificada(
            c,
            action="test de compatibilidad",
            redirect_endpoint="compat_candidata",
        )
        if blocked is not None:
            return blocked

        raw_comunicacion = request.form.get('comunicacion')
        raw_experiencia_nivel = request.form.get('experiencia_nivel')
        raw_puntualidad_1a5 = request.form.get('puntualidad_1a5')
        raw_mascotas = request.form.get('mascotas')
        raw_mascotas_importancia = request.form.get('mascotas_importancia')

        ritmo = _norm_choice(request.form.get('ritmo'), {k for k, _ in COMPAT_RITMOS})
        estilo = _norm_choice(request.form.get('estilo'), {k for k, _ in COMPAT_ESTILOS})
        comun = _norm_choice(raw_comunicacion, {k for k, _ in COMPAT_COMUNICACION})
        rel_n = _norm_choice(request.form.get('relacion_ninos'), {k for k, _ in COMPAT_RELACION_NINOS})
        exp_niv = _norm_choice(raw_experiencia_nivel, {k for k, _ in COMPAT_EXPERIENCIA_NIVEL})
        mascotas = legacy_h.normalize_mascotas_token(raw_mascotas)
        mascotas_importancia = legacy_h.normalize_mascotas_importancia(raw_mascotas_importancia, default=None)
        puntual = _int_1a5('puntualidad_1a5')
        current_app.logger.debug(
            "compat_candidata POST raw values fila=%s comunicacion=%r experiencia_nivel=%r puntualidad_1a5=%r mascotas=%r mascotas_importancia=%r",
            fila, raw_comunicacion, raw_experiencia_nivel, raw_puntualidad_1a5, raw_mascotas, raw_mascotas_importancia
        )
        current_app.logger.debug(
            "compat_candidata POST normalized fila=%s comun=%r exp_niv=%r puntual=%r mascotas=%r mascotas_importancia=%r",
            fila, comun, exp_niv, puntual, mascotas, mascotas_importancia
        )

        fortalezas = _filter_allowed(_getlist_clean('fortalezas'), {k for k, _ in FORTALEZAS})
        evitar = _filter_allowed(_getlist_clean('tareas_evitar'), {k for k, _ in TAREAS_EVITAR})
        limites = _filter_allowed(_getlist_clean('limites_no_negociables'), {k for k, _ in LIMITES_NO_NEG})
        dias = _filter_allowed(_getlist_clean('disponibilidad_dias'), {k for k, _ in DIAS_SEMANA})
        allowed_horarios = {k for k, _ in HORARIOS} | {
            'interna', 'manana', 'mañana', 'tarde', 'noche', 'flexible',
            'fin de semana', 'findesemana', 'weekend'
        }
        horarios_raw = _filter_allowed(_getlist_clean('disponibilidad_horarios'), allowed_horarios)
        horarios = sorted(legacy_h.normalize_horarios_tokens(horarios_raw), key=lambda t: HORARIO_ORDER.get(t, 999))

        notas = (request.form.get('nota') or '').strip()[:2000]

        err = []
        if not ritmo:
            err.append("Ritmo de hogar")
        if not estilo:
            err.append("Estilo de trabajo")
        if not comun:
            err.append("Comunicación preferida")
        if not rel_n:
            err.append("Relación con niños")
        if puntual is None:
            err.append("Puntualidad (1 a 5)")
        if not mascotas:
            err.append("Compatibilidad con mascotas")
        if not mascotas_importancia:
            err.append("Importancia de mascotas")
        if not fortalezas:
            err.append("Fortalezas (al menos una)")
        if not dias:
            err.append("Disponibilidad en días")
        if not horarios:
            err.append("Disponibilidad en horarios")

        if err:
            flash("Completa: " + ", ".join(err), "warning")
            data = {
                "ritmo": ritmo, "estilo": estilo, "comunicacion": comun,
                "relacion_ninos": rel_n, "experiencia_nivel": exp_niv,
                "puntualidad_1a5": puntual, "fortalezas": fortalezas,
                "tareas_evitar": evitar, "limites_no_negociables": limites,
                "disponibilidad_dias": dias, "disponibilidad_horarios": horarios,
                "mascotas": mascotas, "mascotas_importancia": mascotas_importancia, "nota": notas
            }
            return render_template('compat_candidata_form.html', candidata=c, data=data, CHOICES=CHOICES_DICT)

        try:
            if hasattr(c, 'compat_ritmo_preferido'):
                c.compat_ritmo_preferido = ritmo
            if hasattr(c, 'compat_estilo_trabajo'):
                c.compat_estilo_trabajo = estilo
            if hasattr(c, 'compat_comunicacion'):
                c.compat_comunicacion = comun
            if hasattr(c, 'compat_relacion_ninos'):
                c.compat_relacion_ninos = rel_n
            if hasattr(c, 'compat_experiencia_nivel'):
                c.compat_experiencia_nivel = exp_niv
            if hasattr(c, 'compat_puntualidad_1a5'):
                c.compat_puntualidad_1a5 = puntual

            if hasattr(c, 'compat_mascotas'):
                c.compat_mascotas = mascotas
            if hasattr(c, 'compat_mascotas_ok'):
                c.compat_mascotas_ok = (mascotas == 'si')

            if hasattr(c, 'compat_habilidades_fuertes'):
                c.compat_habilidades_fuertes = fortalezas
            elif hasattr(c, 'compat_fortalezas'):
                c.compat_fortalezas = fortalezas

            if hasattr(c, 'compat_habilidades_evitar'):
                c.compat_habilidades_evitar = evitar
            elif hasattr(c, 'compat_tareas_evitar'):
                c.compat_tareas_evitar = evitar

            if hasattr(c, 'compat_limites_no_negociables'):
                c.compat_limites_no_negociables = limites
            if hasattr(c, 'compat_disponibilidad_dias'):
                c.compat_disponibilidad_dias = dias
            if hasattr(c, 'compat_disponibilidad_horarios'):
                c.compat_disponibilidad_horarios = horarios
            if hasattr(c, 'compat_disponibilidad_horario'):
                c.compat_disponibilidad_horario = ", ".join(horarios)

            if hasattr(c, 'compat_observaciones'):
                c.compat_observaciones = notas

            profile = {
                "ritmo": ritmo,
                "estilo": estilo,
                "comunicacion": comun,
                "relacion_ninos": rel_n,
                "experiencia_nivel": exp_niv,
                "puntualidad_1a5": puntual,
                "fortalezas": fortalezas,
                "tareas_evitar": evitar,
                "limites_no_negociables": limites,
                "disponibilidad_dias": dias,
                "disponibilidad_horarios": horarios,
                "mascotas": mascotas,
                "mascotas_importancia": mascotas_importancia,
                "nota": notas,
            }
            payload = {
                "version": legacy_h.COMPAT_TEST_CANDIDATA_VERSION,
                "timestamp": legacy_h.iso_utc_z(),
                "engine": legacy_h.ENGINE_VERSION,
                "profile": profile,
            }
            if hasattr(c, 'compat_test_candidata_json'):
                c.compat_test_candidata_json = payload
            if hasattr(c, 'compat_test_candidata_version'):
                c.compat_test_candidata_version = legacy_h.COMPAT_TEST_CANDIDATA_VERSION
            if hasattr(c, 'compat_test_candidata_at'):
                c.compat_test_candidata_at = legacy_h.utc_now_naive()

            def _verify_compat_saved() -> bool:
                cand_db = legacy_h._get_candidata_by_fila_or_pk(int(fila)) or c
                if not cand_db:
                    return False
                payload_db = getattr(cand_db, 'compat_test_candidata_json', None) or {}
                profile_db = payload_db.get('profile', {}) if isinstance(payload_db, dict) else {}
                if (profile_db.get("ritmo") or "") != (ritmo or ""):
                    return False
                if (profile_db.get("estilo") or "") != (estilo or ""):
                    return False
                if (profile_db.get("comunicacion") or "") != (comun or ""):
                    return False
                if int(profile_db.get("puntualidad_1a5") or 0) != int(puntual or 0):
                    return False
                return True

            result = legacy_h.execute_robust_save(
                session=legacy_h.db.session,
                persist_fn=lambda _attempt: None,
                verify_fn=_verify_compat_saved,
            )
            if not result.ok:
                raise RuntimeError(result.error_message or "No se pudo verificar guardado.")

            flash("✅ Test de compatibilidad guardado correctamente.", "success")

            next_url = request.values.get('next')
            if next_url == 'home':
                return redirect(url_for('home'))
            return redirect(url_for('compat_candidata'))

        except Exception:
            legacy_h.db.session.rollback()
            current_app.logger.exception("❌ Error guardando test de compatibilidad")
            flash("❌ No se pudo guardar.", "danger")
            return redirect(url_for('compat_candidata', fila=fila))

    if request.method == 'GET' and fila:
        c = legacy_h.Candidata.query.get_or_404(fila)
        blocked = assert_candidata_no_descalificada(
            c,
            action="test de compatibilidad",
            redirect_endpoint="compat_candidata",
        )
        if blocked is not None:
            return blocked
        payload = getattr(c, 'compat_test_candidata_json', None) or {}
        profile = payload.get('profile', {}) if isinstance(payload, dict) else {}
        data = {
            "ritmo": getattr(c, 'compat_ritmo_preferido', None),
            "estilo": getattr(c, 'compat_estilo_trabajo', None),
            "comunicacion": getattr(c, 'compat_comunicacion', None) or profile.get('comunicacion'),
            "relacion_ninos": getattr(c, 'compat_relacion_ninos', None),
            "experiencia_nivel": getattr(c, 'compat_experiencia_nivel', None) or profile.get('experiencia_nivel'),
            "puntualidad_1a5": getattr(c, 'compat_puntualidad_1a5', None) or profile.get('puntualidad_1a5'),
            "fortalezas": getattr(c, 'compat_habilidades_fuertes', None)
            or getattr(c, 'compat_fortalezas', [])
            or profile.get('fortalezas', []) or [],
            "tareas_evitar": getattr(c, 'compat_habilidades_evitar', None)
            or getattr(c, 'compat_tareas_evitar', [])
            or profile.get('tareas_evitar', []) or [],
            "limites_no_negociables": getattr(c, 'compat_limites_no_negociables', [])
            or profile.get('limites_no_negociables', []) or [],
            "disponibilidad_dias": getattr(c, 'compat_disponibilidad_dias', [])
            or profile.get('disponibilidad_dias', []) or [],
            "disponibilidad_horarios": sorted(
                legacy_h.normalize_horarios_tokens(
                    getattr(c, 'compat_disponibilidad_horarios', [])
                    or profile.get('disponibilidad_horarios', [])
                    or []
                ),
                key=lambda t: HORARIO_ORDER.get(t, 999)
            ),
            "mascotas": (getattr(c, 'compat_mascotas', None)
            if hasattr(c, 'compat_mascotas')
            else ('si' if getattr(c, 'compat_mascotas_ok', False) else 'no')
            if hasattr(c, 'compat_mascotas_ok') else profile.get('mascotas')),
            "mascotas_importancia": profile.get('mascotas_importancia') or 'media',
            "nota": getattr(c, 'compat_observaciones', '') or profile.get('nota', '') or '',
        }
        return render_template('compat_candidata_form.html', candidata=c, data=data, CHOICES=CHOICES_DICT)

    q = (request.values.get('q') or '').strip()[:128]
    resultados = []
    mensaje = None

    if request.method == 'POST' and request.form.get('accion') == 'buscar':
        q = (request.form.get('q') or '').strip()[:128]

    if q:
        try:
            resultados = search_candidatas_limited(
                q,
                limit=80,
                base_query=legacy_h.Candidata.query.filter(candidatas_activas_filter(legacy_h.Candidata)),
                minimal_fields=True,
                order_mode="id_desc",
                log_label="compat_candidata",
            )
        except Exception:
            current_app.logger.exception("❌ Error buscando candidatas en compat_candidata")
            resultados = []
        if not resultados:
            mensaje = "⚠️ No se encontraron coincidencias."

    return render_template(
        'compat_candidata_buscar.html',
        resultados=resultados,
        mensaje=mensaje,
        q=q,
    )
