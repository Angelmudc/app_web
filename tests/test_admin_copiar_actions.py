# -*- coding: utf-8 -*-

import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from flask import request

from app import app as flask_app
import admin.routes as admin_routes
from utils.envejeciente import format_envejeciente_resumen


class _SolicitudStub:
    def __init__(self, estado="activa"):
        self.id = 10
        self.cliente_id = 7
        self.estado = estado
        self.codigo_solicitud = "SOL-010"
        self.last_copiado_at = None
        self.fecha_solicitud = datetime.utcnow()
        self.fecha_ultima_actividad = None
        self.fecha_ultima_modificacion = None
        self.estado_previo_espera_pago = None
        self.fecha_cambio_espera_pago = None
        self.usuario_cambio_espera_pago = None
        self.motivo_cancelacion = None
        self.fecha_cancelacion = None
        self.candidata_id = None
        self.monto_pagado = None
        self.reemplazos = []
        self.ciudad_sector = "Santiago"
        self.direccion = "Calle 1"
        self.modalidad_trabajo = "Con dormida"
        self.rutas_cercanas = "27 de Febrero"
        self.funciones = ["limpieza"]
        self.funciones_otro = ""
        self.tipo_lugar = "Casa"
        self.habitaciones = 3
        self.banos = 2
        self.dos_pisos = False
        self.areas_comunes = []
        self.area_otro = ""
        self.adultos = 2
        self.ninos = 0
        self.edades_ninos = ""
        self.mascota = ""
        self.edad_requerida = ["25-45"]
        self.experiencia = "Limpieza general"
        self.horario = "L-V"
        self.sueldo = "25000"
        self.pasaje_aporte = False
        self.nota_cliente = "Nota interna"
        self.detalles_servicio = {}
        self.tipo_servicio = "DOMESTICA_LIMPIEZA"
        self.envejeciente_tipo_cuidado = None
        self.envejeciente_responsabilidades = None
        self.envejeciente_solo_acompanamiento = False
        self.envejeciente_nota = None


class _CandidataStub:
    def __init__(self, fila=33, nombre_completo="Ana Demo"):
        self.fila = fila
        self.estado = "trabajando"
        self.nombre_completo = nombre_completo
        self.nota_descalificacion = None
        self.fecha_cambio_estado = None
        self.usuario_cambio_estado = None


class _QueryChain:
    def __init__(self, rows):
        self.rows = list(rows)

    def options(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def filter_by(self, **kwargs):
        if not kwargs:
            return self
        rows = self.rows
        for k, v in kwargs.items():
            rows = [r for r in rows if getattr(r, k, None) == v]
        return _QueryChain(rows)

    def order_by(self, *args, **kwargs):
        return self

    def count(self):
        return len(self.rows)

    def offset(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None

    def first_or_404(self):
        if not self.rows:
            raise RuntimeError("404")
        return self.rows[0]


class _DummyField:
    def __init__(self, choices):
        self.choices = choices


class _DummyForm:
    def __init__(self):
        self.funciones = _DummyField([
            ("limpieza", "Limpieza general"),
            ("cocinar", "Cocinar"),
            ("lavar", "Lavar"),
            ("planchar", "Planchar"),
            ("ninos", "Cuidar niños"),
            ("envejeciente", "Cuidar envejeciente"),
            ("otro", "Otro"),
        ])
        self.ninera_tareas = _DummyField([])
        self.enf_tareas = _DummyField([])
        self.enf_movilidad = _DummyField([])


class _CandidataQueryChain:
    def __init__(self, rows):
        self.rows = list(rows)

    def options(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None

    def first_or_404(self):
        if not self.rows:
            raise RuntimeError("404")
        return self.rows[0]


class _LookupCandidataQueryChain:
    def __init__(self, rows):
        self.rows = list(rows)
        self._q_applied = False

    def options(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        q = (request.args.get("q") or "").strip().lower()
        include_raw = (request.args.get("include_id") or "").strip()

        if q and not self._q_applied:
            self.rows = [
                r for r in self.rows
                if (q in (r.nombre_completo or "").lower()) or (q in str(r.fila))
            ]
            self._q_applied = True
            return self

        if include_raw and any("fila" in str(a) for a in args):
            try:
                include_id = int(include_raw)
            except Exception:
                return self
            self.rows = [r for r in self.rows if int(r.fila) == include_id]
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, n):
        self.rows = self.rows[:int(n)]
        return self

    def all(self):
        return list(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None


class AdminCopiarActionsTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self.client = flask_app.test_client()
        os.environ["ADMIN_LEGACY_ENABLED"] = "1"
        self._p_lock = patch("admin.routes._admin_action_is_locked", return_value=False)
        self._p_reg = patch("admin.routes._admin_action_register", return_value=1)
        self._p_lock.start()
        self._p_reg.start()

    def tearDown(self):
        self._p_reg.stop()
        self._p_lock.stop()

    def _login(self, user, pwd):
        resp = self.client.post("/admin/login", data={"usuario": user, "clave": pwd}, follow_redirects=False)
        self.assertIn(resp.status_code, (302, 303))

    def test_copiar_solicitudes_render_y_formato_base(self):
        self._login("Karla", "9989")
        solicitud = _SolicitudStub(estado="activa")

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _QueryChain([solicitud])), \
                 patch("admin.routes.AdminSolicitudForm", _DummyForm):
                resp = self.client.get("/admin/solicitudes/copiar", follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("data-text-url=", html)
        self.assertIn("Acciones", html)
        self.assertIn('data-no-loader="true"', html)
        self.assertIn('id="contextActionPanel"', html)
        self.assertIn('class="dropdown-item js-open-cancel"', html)
        self.assertIn("Cancelar solicitud", html)
        self.assertNotIn('data-bs-target="#cancelModalShared"', html)
        self.assertIn('id="cancelModalSharedForm" class="d-none" data-no-loader="true"', html)
        self.assertIn('<textarea class="form-control" name="motivo" rows="2" minlength="5" required></textarea>', html)
        self.assertIn('<button type="submit" class="btn btn-danger" data-no-loader="true">Confirmar cancelación</button>', html)
        self.assertIn('id="contextActionPanelClose"', html)
        self.assertIn('id="copiarSolicitudesSearchForm"', html)
        self.assertIn('data-async-debounce-ms="300"', html)
        self.assertIn('data-async-history="true"', html)

    def test_copiar_solicitudes_conserva_el_significado_del_pasaje_personalizado(self):
        self._login("Owner", "admin123")
        solicitud = _SolicitudStub(estado="activa")
        solicitud.pasaje_aporte = False
        solicitud.detalles_servicio = {"pasaje": {"mode": "otro", "text": "pasaje aparte por ruta"}}

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _QueryChain([solicitud])), \
                 patch("admin.routes.AdminSolicitudForm", _DummyForm):
                resp = self.client.get("/admin/solicitudes/copiar", follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _QueryChain([solicitud])), \
                 patch("admin.routes.AdminSolicitudForm", _DummyForm):
                text_resp = self.client.get("/admin/solicitudes/10/texto", follow_redirects=False)
        self.assertEqual(text_resp.status_code, 200)
        payload = text_resp.get_json() or {}
        self.assertIn("Sueldo: $25,000 mensual, pasaje aparte por ruta", payload.get("order_text") or "")

    def test_copiar_solicitudes_formatea_funciones_y_areas_humano(self):
        self._login("Owner", "admin123")
        solicitud = _SolicitudStub(estado="activa")
        solicitud.funciones = ["cocinar", "ninos", "limpieza", "lavar"]
        solicitud.funciones_otro = "Mandados"
        solicitud.areas_comunes = ["salon_juegos", "jardin"]
        solicitud.area_otro = "area_lavado"

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _QueryChain([solicitud])), \
                 patch("admin.routes.AdminSolicitudForm", _DummyForm):
                resp = self.client.get("/admin/solicitudes/10/texto", follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = (resp.get_json() or {}).get("order_text", "")
        self.assertIn(
            "Funciones:\nLimpieza general, Cocinar, Lavar, Cuidar niños, Mandados",
            html,
        )
        self.assertTrue(("Salón de juegos" in html) or ("Sal\\u00f3n de juegos" in html))
        self.assertTrue(("Jardín" in html) or ("Jard\\u00edn" in html))
        self.assertNotIn("area_lavado", html)
        self.assertIn("Mandados", html)
        self.assertNotIn("salon_juegos", html)
        self.assertNotIn("jardin", html)

    def test_copiar_solicitudes_horario_lunes_a_sabado_con_sabado_corto_se_muestra_correcto(self):
        self._login("Owner", "admin123")
        solicitud = _SolicitudStub(estado="activa")
        solicitud.modalidad_trabajo = "Salida diaria - lunes a sábado"
        solicitud.horario = "Lunes a viernes de 7:30 AM a 6:00 PM / sábado hasta 1:00 PM"

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _QueryChain([solicitud])), \
                 patch("admin.routes.AdminSolicitudForm", _DummyForm):
                resp = self.client.get("/admin/solicitudes/10/texto", follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = (resp.get_json() or {}).get("order_text", "")
        self.assertIn("Horario: Lunes a viernes: 7:30 AM - 6:00 PM", html)
        self.assertIn("Sábado: 7:30 AM - 1:00 PM", html)
        self.assertNotIn("Horario: Lunes a viernes / sábado hasta 1:00 PM, de 7:30 AM a 6:00 PM", html)

    def test_copiar_solicitudes_get_async_devuelve_reemplazo_parcial(self):
        self._login("Karla", "9989")
        solicitud = _SolicitudStub(estado="activa")

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _QueryChain([solicitud])), \
                 patch("admin.routes.AdminSolicitudForm", _DummyForm):
                resp = self.client.get(
                    "/admin/solicitudes/copiar?q=santiago&page=1",
                    headers={
                        "Accept": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                        "X-Admin-Async": "1",
                    },
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["update_target"], "#copiarSolicitudesResults")
        self.assertIn("list-group-item", data["replace_html"])

    def test_build_resumen_cliente_solicitud_pasaje_usa_mensajes_claros(self):
        solicitud_false = _SolicitudStub(estado="activa")
        solicitud_false.pasaje_aporte = False
        solicitud_false.detalles_servicio = {}

        solicitud_true = _SolicitudStub(estado="activa")
        solicitud_true.pasaje_aporte = True
        solicitud_true.detalles_servicio = {}

        with flask_app.app_context():
            with patch("admin.routes.AdminSolicitudForm", _DummyForm):
                resumen_false = admin_routes.build_resumen_cliente_solicitud(solicitud_false)
                resumen_true = admin_routes.build_resumen_cliente_solicitud(solicitud_true)

        self.assertIn("Pasaje incluido", resumen_false)
        self.assertIn("Más ayuda del pasaje", resumen_true)

    def test_customer_summary_redacts_structured_identity_and_operational_notes(self):
        s = _SolicitudStub(estado="activa")
        s.cliente = SimpleNamespace(
            nombre_completo="Cliente Ficticio de Prueba",
            telefono="8095550101",
            email="cliente.ficticio@example.test",
        )
        s.nota_cliente = (
            "Cliente Ficticio de Prueba, 8095550101, cliente.ficticio@example.test. "
            "[Operativo] Reemplazo cancelado (no cobrar nuevamente)."
        )

        with flask_app.app_context():
            with patch("admin.routes.AdminSolicitudForm", _DummyForm):
                resumen = admin_routes.build_resumen_cliente_solicitud(s)

        for private in ("Cliente Ficticio de Prueba", "8095550101", "cliente.ficticio@example.test", "[Operativo]", "no cobrar nuevamente"):
            self.assertNotIn(private, resumen)

    def test_pasaje_operativo_phrase_unifica_formato_con_copiado_interno(self):
        solicitud_incluido = _SolicitudStub(estado="activa")
        solicitud_incluido.pasaje_aporte = False
        solicitud_incluido.detalles_servicio = {}

        solicitud_aparte = _SolicitudStub(estado="activa")
        solicitud_aparte.pasaje_aporte = True
        solicitud_aparte.detalles_servicio = {}

        solicitud_otro = _SolicitudStub(estado="activa")
        solicitud_otro.pasaje_aporte = False
        solicitud_otro.detalles_servicio = {"pasaje": {"mode": "otro", "text": "Cliente cubre taxi nocturno"}}

        self.assertEqual(
            admin_routes._pasaje_operativo_phrase_from_solicitud(solicitud_incluido),
            "Pasaje incluido",
        )
        self.assertEqual(
            admin_routes._pasaje_operativo_phrase_from_solicitud(solicitud_aparte),
            "Más ayuda del pasaje",
        )
        self.assertEqual(
            admin_routes._pasaje_operativo_phrase_from_solicitud(solicitud_otro),
            "Cliente cubre taxi nocturno",
        )

    def test_public_copy_preserves_free_text_notes_and_omits_only_operational_lines(self):
        s = _SolicitudStub(estado="activa")
        marker = "[Operativo] Reemplazo cancelado / servicio pendiente (no cobrar nuevamente)."
        s.nota_cliente = "El envejeciente es independiente, solo es acompañarlo\n" + marker + "\n" + marker

        with flask_app.app_context():
            texto = admin_routes._admin_build_public_copy_text(
                s, label_maps=admin_routes._admin_copiar_form_label_maps()
            )

        self.assertNotIn("El envejeciente es independiente, solo es acompañarlo", texto)
        self.assertNotIn("[Operativo]", texto)
        self.assertNotIn("no cobrar nuevamente", texto.lower())
        self.assertTrue(texto.startswith("Disponible ( SOL-010 )\n📍 Santiago\nRuta más cercana: 27 de Febrero\n\n*DOMÉSTICA*"))

    def test_public_copy_preserves_notes_around_operational_lines(self):
        s = _SolicitudStub(estado="activa")
        marker = "[Operativo] Reemplazo cancelado / servicio pendiente (no cobrar nuevamente)."
        s.nota_cliente = f"Primera indicación\n{marker}\nSegunda indicación\n\n{marker}\nTercera indicación"

        with flask_app.app_context():
            texto = admin_routes._admin_build_public_copy_text(
                s, label_maps=admin_routes._admin_copiar_form_label_maps()
            )

        self.assertNotIn("Primera indicación", texto)
        self.assertNotIn("Segunda indicación", texto)
        self.assertNotIn("Tercera indicación", texto)
        self.assertNotIn(marker, texto)

    def test_public_copy_filters_only_explicit_operational_lines(self):
        s = _SolicitudStub(estado="activa")
        s.nota_cliente = (
            "Nota pública\n"
            "[Operativo] interno\n"
            "   [Operativo] interno con espacios\n"
            "El cliente indicó que el proceso operativo continúa normalmente."
        )

        with flask_app.app_context():
            texto = admin_routes._admin_build_public_copy_text(
                s, label_maps=admin_routes._admin_copiar_form_label_maps()
            )

        self.assertNotIn("Nota pública", texto)
        self.assertNotIn("El cliente indicó que el proceso operativo continúa normalmente.", texto)
        self.assertNotIn("[Operativo]", texto)
        self.assertNotIn("\n\n\n", texto)

    def test_public_summary_and_internal_copy_share_operational_note_filter(self):
        s = _SolicitudStub(estado="activa")
        s.nota_cliente = "Nota pública\n[Operativo] dato interno"

        with flask_app.app_context():
            with patch("admin.routes.AdminSolicitudForm", _DummyForm):
                resumen = admin_routes.build_resumen_cliente_solicitud(s)
            publicacion = admin_routes._admin_build_public_copy_text(
                s, label_maps=admin_routes._admin_copiar_form_label_maps()
            )

        self.assertIn("Nota pública", resumen)
        self.assertNotIn("[Operativo] dato interno", resumen)
        self.assertNotIn("Nota pública", publicacion)
        self.assertNotIn("[Operativo] dato interno", publicacion)
        with flask_app.app_context():
            interno = admin_routes._admin_build_order_text_for_copiar(
                s, label_maps=admin_routes._admin_copiar_form_label_maps()
            )
        self.assertIn("Nota pública", interno)
        self.assertIn("[Operativo] dato interno", interno)

    def test_build_resumen_cliente_solicitud_incluye_texto_exacto_envejeciente_independiente(self):
        s = _SolicitudStub(estado="activa")
        s.funciones = ["limpieza", "envejeciente"]
        s.envejeciente_tipo_cuidado = "independiente"
        with flask_app.app_context():
            with patch("admin.routes.AdminSolicitudForm", _DummyForm):
                resumen = admin_routes.build_resumen_cliente_solicitud(s)
        self.assertIn(
            "Envejeciente: independiente. Requiere acompañamiento, supervisión o apoyo ligero.",
            resumen,
        )

    def test_tres_copias_reusan_etiquetas_oficiales_y_orden_del_formulario(self):
        s = _SolicitudStub(estado="activa")
        s.funciones = ["envejeciente", "limpieza", "lavar", "cocinar", "planchar", "ninos"]
        s.detalles_servicio = {
            "tipo": "NINERA",
            "cantidad_ninos": 2,
            "edades_ninos": "2 y 6 años",
            "tareas": ["jugar"],
            "ayuda_cuidado_ninos": "con_ayuda",
        }
        s.tipo_servicio = "NINERA"
        with flask_app.app_context():
            cliente = admin_routes.build_resumen_cliente_solicitud(s)
            publicar = admin_routes._admin_build_order_text_for_copiar(
                s, label_maps=admin_routes._admin_copiar_form_label_maps()
            )

        self.assertTrue(publicar.startswith("Disponible ( SOL-010 )\n📍 Santiago\nRuta más cercana: 27 de Febrero\n\n*NIÑERA*"))
        self.assertNotIn("Funciones: Limpieza General", publicar)
        self.assertNotIn("Adultos:", publicar)
        self.assertNotIn("Casa -", publicar)
        expected = "Limpieza General, Cocinar, Lavar, Planchar, Cuidar Niños, Cuidar envejecientes"
        self.assertNotIn(expected, cliente)
        for wrong in ("Lavado", "Cuidar niños"):
            self.assertNotIn(wrong, cliente)
            self.assertNotIn(wrong, publicar)
        self.assertIn("Niños a cuidar: 2 (2 y 6 años)", cliente)
        self.assertIn(
            "Niños a cuidar: 2 (2 y 6 años)\nRecibirá ayuda con los niños.",
            cliente,
        )
        self.assertIn(
            "Funciones:\nRealizar actividades recreativas con los niños.",
            cliente,
        )
        self.assertIn("2 (2 y 6 años)", publicar)
        self.assertIn("Recibirá ayuda con los niños.", publicar)
        self.assertIn(
            "Funciones:\nRealizar actividades recreativas con los niños.",
            publicar,
        )
        self.assertIn("2 y 6 años", publicar)
        self.assertIn("Recibirá ayuda con los niños.", publicar)
        self.assertNotIn("Ayuda con el cuidado de los niños:", cliente)
        self.assertNotIn("Ayuda con el cuidado de los niños:", publicar)

    def test_oficina_public_copy_uses_explicit_service_fields_and_excludes_private_household_data(self):
        s = _SolicitudStub(estado="activa")
        s.tipo_servicio = "LIMPIEZA_ESTABLECIMIENTOS"
        s.detalles_servicio = {
            "tipo": "LIMPIEZA_ESTABLECIMIENTOS", "tipo_establecimiento": "Oficina",
            "tamano": "mediano", "niveles": 2, "banos": 3,
            "areas": ["Recepción", "Baños"], "funciones": ["limpieza diaria"],
            "frecuencia": "Lunes a viernes", "nombre_establecimiento": "NEGOCIO PRIVADO",
        }
        s.adultos = 12
        s.ninos = 4
        s.rutas_cercanas = "Calle exacta 123"
        s.nota_cliente = "Observación interna confidencial"
        with flask_app.app_context():
            with flask_app.test_request_context("/"):
                texto = admin_routes._admin_build_public_copy_text(
                    s, label_maps=admin_routes._admin_copiar_form_label_maps()
                )
        self.assertTrue(texto.startswith("Disponible ( SOL-010 )\n📍 Santiago\nRuta más cercana: Calle exacta 123\n\n*LIMPIEZA DE OFICINA*"))
        self.assertIn("Tipo de establecimiento: Oficina", texto)
        self.assertIn("Áreas que necesita limpiar: Recepción, Baños", texto)
        self.assertIn("Calle exacta 123", texto)
        self.assertNotIn("Observación interna confidencial", texto)
        with flask_app.test_request_context("/"):
            texto_interno = admin_routes._admin_build_order_text_for_copiar(
                s, label_maps=admin_routes._admin_copiar_form_label_maps()
            )
        self.assertIn("Observación interna confidencial", texto_interno)
        for private in ("NEGOCIO PRIVADO", "Adultos:", "Niños:"):
            self.assertNotIn(private, texto)
        self.assertIn("Baños: 3", texto)

    def test_nurse_publication_omits_unapproved_health_and_exact_address_details(self):
        s = _SolicitudStub(estado="activa")
        s.tipo_servicio = "ENFERMERA"
        s.detalles_servicio = {
            "tipo": "ENFERMERA",
            "cantidad_personas": 1,
            "condicion_principal": "Diagnóstico sintético no publicable",
            "condiciones": ["diabetes"],
            "direccion_precisa": "Calle Ficticia 987, casa 2",
            "tareas": ["acompanamiento"],
        }
        with flask_app.test_request_context("/"):
            texto = admin_routes._admin_build_public_copy_text(s)

        self.assertIn("ENFERMERA", texto)
        self.assertNotIn("Diagnóstico sintético no publicable", texto)
        self.assertNotIn("Diabetes", texto)
        self.assertNotIn("Calle Ficticia 987", texto)

    def test_copiar_solicitudes_incluye_envejeciente_encamado_responsabilidades_y_nota(self):
        self._login("Owner", "admin123")
        s = _SolicitudStub(estado="activa")
        s.funciones = ["limpieza", "envejeciente"]
        s.envejeciente_tipo_cuidado = "encamado"
        s.envejeciente_responsabilidades = ["pampers", "higiene", "medicamentos"]
        s.envejeciente_nota = "Usa cama clínica"
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _QueryChain([s])), \
                 patch("admin.routes.AdminSolicitudForm", _DummyForm):
                resp = self.client.get("/admin/solicitudes/10/texto", follow_redirects=False)
        html = (resp.get_json() or {}).get("order_text", "")
        expected = format_envejeciente_resumen(
            tipo_cuidado="encamado",
            responsabilidades=["pampers", "higiene", "medicamentos"],
            solo_acompanamiento=False,
            nota="Usa cama clínica",
        )
        self.assertEqual(
            expected[0],
            "Envejeciente: encamado. Requiere: cambiar pampers, higiene personal, darle medicamentos.",
        )
        self.assertEqual(expected[1], "Nota sobre el envejeciente: Usa cama clínica")
        self.assertIn(expected[0], html)
        self.assertIn(expected[1], html)

    def test_copiar_solicitudes_incluye_envejeciente_solo_acompanamiento(self):
        self._login("Owner", "admin123")
        s = _SolicitudStub(estado="activa")
        s.funciones = ["limpieza", "envejeciente"]
        s.envejeciente_tipo_cuidado = "encamado"
        s.envejeciente_solo_acompanamiento = True
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _QueryChain([s])), \
                 patch("admin.routes.AdminSolicitudForm", _DummyForm):
                resp = self.client.get("/admin/solicitudes/10/texto", follow_redirects=False)
        html = (resp.get_json() or {}).get("order_text", "")
        expected = format_envejeciente_resumen(
            tipo_cuidado="encamado",
            responsabilidades=[],
            solo_acompanamiento=True,
            nota=None,
        )[0]
        self.assertEqual(
            expected,
            "Envejeciente: encamado. La doméstica no realizará higiene, pañales, alimentación ni medicamentos; solo acompañamiento o supervisión.",
        )
        self.assertIn(expected, html)

    def test_build_resumen_cliente_solicitud_legacy_sin_campos_envejeciente_no_falla(self):
        s = _SolicitudStub(estado="activa")
        delattr(s, "envejeciente_tipo_cuidado")
        delattr(s, "envejeciente_responsabilidades")
        delattr(s, "envejeciente_solo_acompanamiento")
        delattr(s, "envejeciente_nota")
        with flask_app.app_context():
            with patch("admin.routes.AdminSolicitudForm", _DummyForm):
                resumen = admin_routes.build_resumen_cliente_solicitud(s)
        self.assertIn("DOMÉSTICA · SOLICITUD SOL-010", resumen)

    def test_build_resumen_encabeza_los_seis_servicios_en_mayusculas(self):
        cases = {
            "DOMESTICA_LIMPIEZA": "DOMÉSTICA",
            "NINERA": "NIÑERA",
            "ENFERMERA": "ENFERMERA / CUIDADORA",
            "CHOFER": "CHOFER",
            "LIMPIEZA_ESTABLECIMIENTOS": "LIMPIEZA DE ESTABLECIMIENTOS",
            "CONSERJERIA_RESIDENCIAL": "CONSERJERÍA RESIDENCIAL",
        }
        with flask_app.app_context(), patch("admin.routes.AdminSolicitudForm", _DummyForm):
            for service, title in cases.items():
                solicitud = _SolicitudStub(estado="activa")
                solicitud.tipo_servicio = service
                resumen = admin_routes.build_resumen_cliente_solicitud(solicitud)
                self.assertTrue(resumen.startswith(f"{title} · SOLICITUD SOL-010"), service)

    def test_copiar_solicitudes_admin_usa_modal_pagado_compartido(self):
        self._login("Cruz", "8998")
        solicitud = _SolicitudStub(estado="activa")
        candidata = _CandidataStub(fila=33)

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _QueryChain([solicitud])), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQueryChain([candidata])), \
                 patch("admin.routes.AdminSolicitudForm", _DummyForm):
                resp = self.client.get("/admin/solicitudes/copiar", follow_redirects=False)

        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="paidModalSharedForm"', html)
        self.assertIn('class="dropdown-item text-success js-open-paid"', html)
        self.assertNotIn('data-bs-target="#paidModalShared"', html)
        self.assertIn('data-no-loader="true"', html)
        self.assertIn('id="paidModalSharedSearch"', html)
        self.assertIn('placeholder="Escribe nombre o ID..."', html)
        self.assertIn('id="paidModalSharedSearchBtn"', html)
        self.assertIn('id="paidModalSharedClearBtn"', html)
        self.assertIn('id="paidModalSharedSearchStats"', html)
        self.assertIn('id="paidModalSharedSelected"', html)
        self.assertIn('class="alert d-none js-modal-feedback"', html)
        self.assertIn('id="paidModalSharedForm" class="d-none" data-lookup-url="/admin/solicitudes/copiar/candidatas_lookup" data-no-loader="true"', html)
        self.assertIn('<button type="submit" class="btn btn-success" data-no-loader="true">Guardar pago</button>', html)
        self.assertIn('data-lookup-url="/admin/solicitudes/copiar/candidatas_lookup"', html)
        self.assertIn('src="/static/js/admin/solicitudes_copiar.js"', html)

    def test_copiar_runtime_inicializa_tambien_si_el_script_se_carga_con_dom_listo(self):
        js_path = os.path.join(os.getcwd(), "static", "js", "admin", "solicitudes_copiar.js")
        with open(js_path, "r", encoding="utf-8") as f:
            js = f.read()

        self.assertIn("function initSolicitudesCopiar()", js)
        self.assertIn("document.readyState === 'loading'", js)
        self.assertIn("initSolicitudesCopiar();", js)
        self.assertIn("admin:content-updated", js)
        self.assertIn("#copiarSolicitudesResults", js)

    def test_candidatas_lookup_copiar_busca_fuera_de_subconjunto_inicial(self):
        self._login("Cruz", "8998")
        rows = [_CandidataStub(fila=i, nombre_completo=f"Ana {i:03d}") for i in range(1, 401)]
        rows.append(_CandidataStub(fila=999, nombre_completo="Zoe Final"))

        with flask_app.app_context():
            with patch.object(admin_routes.Candidata, "query", _LookupCandidataQueryChain(rows)):
                resp = self.client.get(
                    "/admin/solicitudes/copiar/candidatas_lookup?q=zoe&limit=50",
                    headers={"Accept": "application/json"},
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertGreaterEqual(data["count"], 1)
        self.assertTrue(any("Zoe Final" in item["text"] for item in data["items"]))

    def test_candidatas_lookup_copiar_include_id_en_busqueda_vacia(self):
        self._login("Cruz", "8998")
        rows = [_CandidataStub(fila=450, nombre_completo="Candidata Incluida")]

        with flask_app.app_context():
            with patch.object(admin_routes.Candidata, "query", _LookupCandidataQueryChain(rows)):
                resp = self.client.get(
                    "/admin/solicitudes/copiar/candidatas_lookup?include_id=450",
                    headers={"Accept": "application/json"},
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["items"][0]["value"], "450")

    def test_copiar_solicitud_post_vuelve_misma_pantalla(self):
        self._login("Karla", "9989")
        solicitud = _SolicitudStub(estado="activa")
        solicitud.last_copiado_at = datetime.utcnow() - timedelta(days=2)

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/copiar",
                    data={"next": "/admin/solicitudes/copiar?page=2#sol-10"},
                    follow_redirects=False,
                )

        self.assertIn(resp.status_code, (302, 303))
        self.assertIn("/admin/solicitudes/copiar?page=2#sol-10", resp.location)
        commit_mock.assert_called_once()

    def test_copiar_solicitud_ajax_devuelve_json_y_remueve_tarjeta(self):
        self._login("Karla", "9989")
        solicitud = _SolicitudStub(estado="activa")
        solicitud.last_copiado_at = datetime.utcnow() - timedelta(days=2)

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/copiar",
                    data={"next": "/admin/solicitudes/copiar?page=2#sol-10"},
                    headers={
                        "Accept": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["success"])
        self.assertEqual(data["category"], "success")
        self.assertEqual(data["solicitud_id"], 10)
        self.assertTrue(data["remove_card"])
        self.assertEqual(data["remove_element"], "#sol-10")
        self.assertIn("redirect_url", data)
        commit_mock.assert_called_once()

    def test_pausar_espera_perfil_ajax_devuelve_json_y_remueve_tarjeta(self):
        self._login("Karla", "9989")
        solicitud = _SolicitudStub(estado="activa")

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/pausar_espera_perfil",
                    data={"next": "/admin/solicitudes/copiar?page=1#sol-10"},
                    headers={
                        "Accept": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["category"], "success")
        self.assertEqual(data["solicitud_id"], 10)
        self.assertEqual(data["estado"], "espera_pago")
        self.assertTrue(data["remove_card"])
        self.assertEqual(data["remove_element"], "#sol-10")
        self.assertEqual(solicitud.estado, "espera_pago")
        commit_mock.assert_called_once()

    def test_reanudar_espera_perfil_ajax_usa_mismo_contrato_async(self):
        self._login("Karla", "9989")
        solicitud = _SolicitudStub(estado="espera_pago")
        solicitud.estado_previo_espera_pago = "reemplazo"

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/reanudar_espera_perfil",
                    data={"next": "/admin/solicitudes/copiar?page=1#sol-10"},
                    headers={
                        "Accept": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["success"])
        self.assertEqual(data["category"], "success")
        self.assertEqual(data["solicitud_id"], 10)
        self.assertEqual(data["estado"], "reemplazo")
        self.assertFalse(data["remove_card"])
        self.assertIsNone(data["remove_element"])
        self.assertEqual(solicitud.estado, "reemplazo")
        commit_mock.assert_called_once()

    def test_acciones_desde_copiar_cancelar_y_pausa_perfil(self):
        self._login("Karla", "9989")
        solicitud = _SolicitudStub(estado="activa")

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_pause:
                resp_pause = self.client.post(
                    "/admin/solicitudes/10/pausar_espera_perfil",
                    data={"next": "/admin/solicitudes/copiar?page=1#sol-10"},
                    follow_redirects=False,
                )
        self.assertIn(resp_pause.status_code, (302, 303))
        self.assertIn("/admin/solicitudes/copiar?page=1#sol-10", resp_pause.location)
        self.assertEqual(solicitud.estado, "espera_pago")
        commit_pause.assert_called_once()

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_cancel:
                resp_cancel = self.client.post(
                    "/admin/solicitudes/10/cancelar_desde_copiar",
                    data={
                        "motivo": "Cliente detuvo el proceso",
                        "next": "/admin/solicitudes/copiar?page=1#sol-10",
                    },
                    follow_redirects=False,
                )
        self.assertIn(resp_cancel.status_code, (302, 303))
        self.assertIn("/admin/solicitudes/copiar?page=1#sol-10", resp_cancel.location)
        self.assertEqual(solicitud.estado, "cancelada")
        self.assertEqual(solicitud.motivo_cancelacion, "Cliente detuvo el proceso")
        commit_cancel.assert_called_once()

    def test_marcar_pagada_desde_copiar_se_queda_en_la_misma_ruta(self):
        self._login("Cruz", "8998")
        solicitud = _SolicitudStub(estado="activa")
        candidata = _CandidataStub(fila=33)

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch.object(admin_routes.Candidata, "query", SimpleNamespace(get=lambda _id: candidata)), \
                 patch("admin.routes.get_payment_summary", side_effect=[
                     {"saldo_pendiente": "12000.00", "total_pagado": "0.00", "precio_plan": "12000.00", "abono_requerido": "0.00"},
                     {"saldo_pendiente": "0.00", "total_pagado": "12000.00", "precio_plan": "12000.00", "abono_requerido": "0.00"},
                 ]), \
                 patch("admin.routes.get_current_payment_cycle", return_value={"numero_ciclo": 1}), \
                 patch("admin.routes.crear_pago_solicitud") as create_payment_mock, \
                 patch("admin.routes._sync_solicitud_candidatas_after_assignment") as sync_mock, \
                 patch("admin.routes._mark_candidata_estado") as mark_mock, \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/marcar_pagada_desde_copiar",
                    data={
                        "candidata_id": "33",
                        "payment_mode": "manual",
                        "manual_reason": "Pago informado para la prueba",
                        "monto_pagado": "12000",
                        "next": "/admin/solicitudes/copiar?per_page=50#sol-10",
                    },
                    follow_redirects=False,
                )

        self.assertIn(resp.status_code, (302, 303))
        self.assertIn("/admin/solicitudes/copiar?per_page=50#sol-10", resp.location)
        self.assertEqual(solicitud.estado, "pagada")
        self.assertEqual(solicitud.candidata_id, 33)
        self.assertEqual(solicitud.monto_pagado, "12000.00")
        sync_mock.assert_called_once()
        mark_mock.assert_called_once()
        create_payment_mock.assert_called_once()
        commit_mock.assert_called_once()

    def test_cancelar_desde_copiar_ajax_devuelve_json(self):
        self._login("Karla", "9989")
        solicitud = _SolicitudStub(estado="activa")

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_cancel:
                resp = self.client.post(
                    "/admin/solicitudes/10/cancelar_desde_copiar",
                    data={
                        "motivo": "Cliente detuvo el proceso",
                        "next": "/admin/solicitudes/copiar?page=1",
                    },
                    headers={
                        "Accept": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["category"], "success")
        self.assertEqual(data["solicitud_id"], 10)
        self.assertTrue(data["remove_card"])
        self.assertEqual(solicitud.estado, "cancelada")
        commit_cancel.assert_called_once()

    def test_cancelar_desde_copiar_estado_pagada_devuelve_409_con_mensaje_claro(self):
        self._login("Karla", "9989")
        solicitud = _SolicitudStub(estado="pagada")

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_cancel:
                resp = self.client.post(
                    "/admin/solicitudes/10/cancelar_desde_copiar",
                    data={
                        "motivo": "Cliente detuvo el proceso",
                        "next": "/admin/solicitudes/copiar?page=1",
                    },
                    headers={
                        "Accept": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 409)
        data = resp.get_json() or {}
        self.assertFalse(data.get("ok"))
        self.assertEqual(data.get("error_code"), "state_conflict")
        self.assertIn("estado «pagada»", data.get("message", ""))
        commit_cancel.assert_not_called()

    def test_cancelar_desde_copiar_row_version_stale_devuelve_409_con_mensaje_claro(self):
        self._login("Karla", "9989")
        solicitud = _SolicitudStub(estado="activa")
        solicitud.row_version = 9

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_cancel:
                resp = self.client.post(
                    "/admin/solicitudes/10/cancelar_desde_copiar",
                    data={
                        "motivo": "Cliente detuvo el proceso",
                        "row_version": "8",
                        "next": "/admin/solicitudes/copiar?page=1",
                    },
                    headers={
                        "Accept": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 409)
        data = resp.get_json() or {}
        self.assertFalse(data.get("ok"))
        self.assertEqual(data.get("error_code"), "conflict")
        self.assertIn("La solicitud cambió mientras trabajabas", data.get("message", ""))
        commit_cancel.assert_not_called()

    def test_pagado_desde_copiar_ajax_devuelve_json(self):
        self._login("Cruz", "8998")
        solicitud = _SolicitudStub(estado="activa")
        candidata = _CandidataStub(fila=33)

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch.object(admin_routes.Candidata, "query", SimpleNamespace(get=lambda _id: candidata)), \
                 patch("admin.routes.get_payment_summary", side_effect=[
                     {"saldo_pendiente": "12000.00", "total_pagado": "0.00", "precio_plan": "12000.00", "abono_requerido": "0.00"},
                     {"saldo_pendiente": "0.00", "total_pagado": "12000.00", "precio_plan": "12000.00", "abono_requerido": "0.00"},
                 ]), \
                 patch("admin.routes.get_current_payment_cycle", return_value={"numero_ciclo": 1}), \
                 patch("admin.routes.crear_pago_solicitud") as create_payment_mock, \
                 patch("admin.routes._sync_solicitud_candidatas_after_assignment") as sync_mock, \
                 patch("admin.routes._mark_candidata_estado") as mark_mock, \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/marcar_pagada_desde_copiar",
                    data={
                        "candidata_id": "33",
                        "monto_pagado": "12000",
                        "next": "/admin/solicitudes/copiar?per_page=50",
                    },
                    headers={
                        "Accept": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["action"], "paid")
        self.assertEqual(data["category"], "success")
        self.assertEqual(data["solicitud_id"], 10)
        self.assertEqual(data["estado"], "pagada")
        self.assertEqual(data["candidata_id"], 33)
        self.assertTrue(data["remove_card"])
        self.assertIn("application/json", (resp.headers.get("Content-Type") or "").lower())
        self.assertFalse(bool(resp.location))
        self.assertEqual(solicitud.estado, "pagada")
        self.assertEqual(solicitud.candidata_id, 33)
        sync_mock.assert_called_once()
        mark_mock.assert_called_once()
        create_payment_mock.assert_called_once()
        commit_mock.assert_called_once()

    def test_pagado_desde_copiar_notify_falla_no_rompe_operacion(self):
        self._login("Cruz", "8998")
        solicitud = _SolicitudStub(estado="activa")
        candidata = _CandidataStub(fila=33)

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch.object(admin_routes.Candidata, "query", SimpleNamespace(get=lambda _id: candidata)), \
                 patch("admin.routes.get_payment_summary", side_effect=[
                     {"saldo_pendiente": "12000.00", "total_pagado": "0.00", "precio_plan": "12000.00", "abono_requerido": "0.00"},
                     {"saldo_pendiente": "0.00", "total_pagado": "12000.00", "precio_plan": "12000.00", "abono_requerido": "0.00"},
                 ]), \
                 patch("admin.routes.get_current_payment_cycle", return_value={"numero_ciclo": 1}), \
                 patch("admin.routes.crear_pago_solicitud") as create_payment_mock, \
                 patch("admin.routes._sync_solicitud_candidatas_after_assignment") as sync_mock, \
                 patch("admin.routes._mark_candidata_estado") as mark_mock, \
                 patch("admin.routes._notify_cliente_candidata_asignada", side_effect=RuntimeError("notif down")), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/marcar_pagada_desde_copiar",
                    data={
                        "candidata_id": "33",
                        "monto_pagado": "12000",
                        "next": "/admin/solicitudes/copiar?per_page=50",
                    },
                    headers={
                        "Accept": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json() or {}
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("action"), "paid")
        self.assertTrue(data.get("remove_card"))
        self.assertIn("application/json", (resp.headers.get("Content-Type") or "").lower())
        self.assertFalse(bool(resp.location))
        sync_mock.assert_called_once()
        mark_mock.assert_called_once()
        create_payment_mock.assert_called_once()
        commit_mock.assert_called_once()

    def test_pagado_desde_copiar_error_funcional_async_es_json_controlado(self):
        self._login("Cruz", "8998")
        solicitud = _SolicitudStub(estado="activa")

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch.object(admin_routes.Candidata, "query", SimpleNamespace(get=lambda _id: _CandidataStub(fila=33))), \
                 patch("admin.routes._claim_idempotency", return_value=(None, False)), \
                 patch("admin.routes.get_payment_summary", return_value={"saldo_pendiente": "12000.00", "total_pagado": "0.00"}), \
                 patch("admin.routes._matching_candidate_flags", return_value=(set(), set())), \
                 patch("admin.routes._sync_solicitud_candidatas_after_assignment"), \
                 patch("admin.routes._mark_candidata_estado"):
                resp = self.client.post(
                    "/admin/solicitudes/10/marcar_pagada_desde_copiar",
                    data={
                        "candidata_id": "33",
                        "payment_mode": "manual",
                        "manual_reason": "Pago informado para la prueba",
                        "monto_pagado": "",
                        "next": "/admin/solicitudes/copiar?per_page=50",
                    },
                    headers={
                        "Accept": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 400)
        data = resp.get_json() or {}
        self.assertFalse(data.get("ok"))
        self.assertEqual(data.get("action"), "paid")
        self.assertIn("pago manual requiere monto", data.get("message", "").lower())
        self.assertIn("application/json", (resp.headers.get("Content-Type") or "").lower())
        self.assertFalse(bool(resp.location))

    def test_pagado_desde_copiar_conflict_por_row_version_devuelve_409(self):
        self._login("Cruz", "8998")
        solicitud = _SolicitudStub(estado="activa")
        solicitud.row_version = 9

        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", SimpleNamespace(get_or_404=lambda _id: solicitud)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/marcar_pagada_desde_copiar",
                    data={
                        "row_version": "8",
                        "candidata_id": "33",
                        "monto_pagado": "12000",
                        "next": "/admin/solicitudes/copiar?per_page=50",
                    },
                    headers={
                        "Accept": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 409)
        data = resp.get_json() or {}
        self.assertFalse(data.get("ok"))
        self.assertEqual(data.get("error_code"), "conflict")
        commit_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
