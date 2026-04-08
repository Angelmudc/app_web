# -*- coding: utf-8 -*-

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import clientes.routes as clientes_routes


class _SolicitudQueryOne:
    def __init__(self, solicitud):
        self.solicitud = solicitud

    def filter_by(self, **kwargs):
        return self

    def first_or_404(self):
        return self.solicitud


class _SolicitudCandidataQuery:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return self.rows


def _unwrap_cliente_view(fn):
    out = fn
    for _ in range(2):  # login_required + cliente_required
        out = out.__wrapped__
    return out


def _make_solicitud(**overrides):
    now = datetime(2026, 4, 7, 10, 0, 0)
    data = {
        "id": 10,
        "cliente_id": 7,
        "codigo_solicitud": "SOL-010",
        "fecha_solicitud": now - timedelta(days=4),
        "estado": "proceso",
        "estado_actual_desde": now - timedelta(days=2),
        "fecha_ultimo_estado": now - timedelta(days=1),
        "fecha_cambio_espera_pago": None,
        "fecha_inicio_seguimiento": None,
        "fecha_cancelacion": None,
        "motivo_cancelacion": None,
        "tipo_plan": "premium",
        "abono": None,
        "candidata": None,
        "candidata_id": None,
        "reemplazos": [],
        "compat_test_cliente_json": None,
        "compat_test_cliente": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _make_sc(status="enviada", created_at=None, updated_at=None):
    now = datetime(2026, 4, 7, 10, 0, 0)
    cand = SimpleNamespace(nombre_completo="Ana Perez", codigo="C-501", edad="31", modalidad_trabajo_preferida="salida diaria")
    return SimpleNamespace(
        id=90,
        status=status,
        created_at=created_at or now - timedelta(days=1),
        updated_at=updated_at or now - timedelta(hours=8),
        candidata=cand,
        breakdown_snapshot={},
        score_snapshot=80,
    )


class ClienteSolicitudDetailTimelineTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self.fake_user = SimpleNamespace(id=7, is_authenticated=True)
        self.target = _unwrap_cliente_view(clientes_routes.detalle_solicitud)

    def _call_detalle(self, solicitud, candidatas=None):
        candidatas = list(candidatas or [])
        with flask_app.app_context():
            with patch.object(clientes_routes, "current_user", self.fake_user), \
                 patch.object(clientes_routes.Solicitud, "query", _SolicitudQueryOne(solicitud)), \
                 patch.object(clientes_routes.SolicitudCandidata, "query", _SolicitudCandidataQuery(candidatas)), \
                 patch.object(clientes_routes, "_chat_enabled", return_value=True), \
                 patch.object(clientes_routes, "_candidate_public_payload", return_value={"sc": SimpleNamespace(id=1), "codigo": "C-1", "nombre_publico": "Ana", "edad": "31", "modalidad": "salida diaria", "match_score": 80, "status_label": "Enviada", "porque_bullets": []}), \
                 patch.object(clientes_routes, "render_template", side_effect=lambda template, **ctx: {"template": template, "ctx": ctx}):
                with flask_app.test_request_context("/clientes/solicitudes/10", method="GET"):
                    return self.target(10)

    def test_a_estado_actual_entendible(self):
        solicitud = _make_solicitud(estado="espera_pago", fecha_cambio_espera_pago=datetime(2026, 4, 7, 8, 0, 0))
        result = self._call_detalle(solicitud)
        ctx = result["ctx"]
        self.assertEqual(ctx["estado_legible"], "Pendiente de pago")
        self.assertIn("completar el pago", ctx["que_sigue"]["titulo"].lower())

    def test_b_que_sigue_cambia_segun_solicitud(self):
        proceso = self._call_detalle(_make_solicitud(estado="proceso"))["ctx"]["que_sigue"]["titulo"]
        pago = self._call_detalle(_make_solicitud(estado="espera_pago", fecha_cambio_espera_pago=datetime(2026, 4, 7, 8, 0, 0)))["ctx"]["que_sigue"]["titulo"]
        self.assertNotEqual(proceso, pago)

    def test_c_timeline_muestra_eventos_reales_relevantes(self):
        solicitud = _make_solicitud(
            estado="activa",
            candidata_id=501,
            candidata=SimpleNamespace(nombre_completo="Ana Perez"),
        )
        candidatas = [
            _make_sc(status="enviada", created_at=datetime(2026, 4, 6, 9, 0, 0)),
            _make_sc(status="seleccionada", created_at=datetime(2026, 4, 6, 10, 0, 0), updated_at=datetime(2026, 4, 6, 14, 0, 0)),
        ]
        timeline = self._call_detalle(solicitud, candidatas)["ctx"]["timeline_simple"]
        titles = [x.get("titulo") for x in timeline]
        self.assertIn("Solicitud creada", titles)
        self.assertIn("Candidatas enviadas", titles)
        self.assertIn("Entrevista coordinada", titles)
        self.assertIn("Candidata elegida", titles)

    def test_d_sin_datos_extra_no_rompe(self):
        solicitud = _make_solicitud(estado="proceso", estado_actual_desde=None, fecha_ultimo_estado=None, tipo_plan=None)
        result = self._call_detalle(solicitud, candidatas=[])
        self.assertEqual(result["template"], "clientes/solicitud_detail.html")
        self.assertGreaterEqual(len(result["ctx"]["timeline_simple"]), 1)
        self.assertIsInstance(result["ctx"]["que_sigue"], dict)

    def test_e_cambio_de_estado_mantiene_coherencia(self):
        solicitud = _make_solicitud(estado="proceso")
        ctx_proceso = self._call_detalle(solicitud)["ctx"]

        solicitud.estado = "espera_pago"
        solicitud.fecha_cambio_espera_pago = datetime(2026, 4, 7, 9, 30, 0)
        ctx_pago = self._call_detalle(solicitud)["ctx"]

        self.assertIn("validando", ctx_proceso["que_sigue"]["mensaje"].lower())
        self.assertIn("pago", ctx_pago["que_sigue"]["titulo"].lower())
        timeline_titles_pago = [x.get("titulo") for x in ctx_pago["timeline_simple"]]
        self.assertIn("Pendiente de pago", timeline_titles_pago)

    def test_f_a_acciones_en_proceso_son_coherentes(self):
        ctx = self._call_detalle(_make_solicitud(estado="proceso"))["ctx"]
        acciones = ctx["acciones_rapidas"]
        ids = [x["id"] for x in acciones]
        self.assertIn("seguimiento", ids)
        self.assertIn("editar", ids)
        self.assertIn("chat", ids)
        self.assertIn("proceso", ids)
        self.assertNotIn("pago", ids)
        self.assertIn("items", ctx["ayuda_contextual"])
        self.assertEqual(len(ctx["ayuda_contextual"]["items"]), 4)

    def test_g_b_espera_pago_muestra_accion_de_pago(self):
        solicitud = _make_solicitud(estado="espera_pago", fecha_cambio_espera_pago=datetime(2026, 4, 7, 8, 0, 0))
        ctx = self._call_detalle(solicitud)["ctx"]
        acciones = ctx["acciones_rapidas"]
        labels = [x["label"].lower() for x in acciones]
        ids = [x["id"] for x in acciones]
        self.assertIn("pago", ids)
        self.assertTrue(any("pago" in label for label in labels))
        ayuda_textos = " ".join((x.get("a") or "") for x in ctx["ayuda_contextual"]["items"]).lower()
        self.assertIn("pago", ayuda_textos)

    def test_h_c_con_candidatas_enviadas_habilita_acceso_relevante(self):
        solicitud = _make_solicitud(estado="activa")
        acciones = self._call_detalle(solicitud, candidatas=[_make_sc(status="enviada")])["ctx"]["acciones_rapidas"]
        candidatas_action = next((x for x in acciones if x["id"] == "candidatas"), None)
        self.assertIsNotNone(candidatas_action)
        self.assertEqual(candidatas_action.get("badge"), "1")

    def test_i_d_reemplazo_muestra_accion_relevante(self):
        solicitud = _make_solicitud(estado="reemplazo")
        ctx = self._call_detalle(solicitud, candidatas=[_make_sc(status="enviada")])["ctx"]
        acciones = ctx["acciones_rapidas"]
        reemplazo_action = next((x for x in acciones if x["id"] == "reemplazo"), None)
        self.assertIsNotNone(reemplazo_action)
        self.assertIn("reemplazo", (reemplazo_action.get("label") or "").lower())
        ayuda_textos = " ".join((x.get("a") or "") for x in ctx["ayuda_contextual"]["items"]).lower()
        self.assertIn("reemplazo", ayuda_textos)

    def test_j_e_solicitud_minima_no_rompe_ni_muestra_basura(self):
        solicitud = _make_solicitud(estado="proceso", tipo_plan=None)
        ctx = self._call_detalle(solicitud, candidatas=[])["ctx"]
        acciones = ctx["acciones_rapidas"]
        self.assertIsInstance(acciones, list)
        self.assertNotIn("candidatas", [x["id"] for x in acciones])

    def test_k_trust_espera_pago_explica_modelo_real(self):
        solicitud = _make_solicitud(estado="espera_pago", fecha_cambio_espera_pago=datetime(2026, 4, 7, 8, 0, 0))
        ctx = self._call_detalle(solicitud)["ctx"]
        trust_text = " ".join((x.get("text") or "") for x in ctx.get("trust_signals") or []).lower()
        self.assertIn("50% inicial", trust_text)
        self.assertIn("no es un cobro recurrente", trust_text)
        self.assertIn("25%", trust_text)

    def test_l_trust_proceso_refuerza_evaluacion_activa(self):
        solicitud = _make_solicitud(estado="proceso")
        ctx = self._call_detalle(solicitud)["ctx"]
        ids = [x.get("id") for x in (ctx.get("trust_signals") or [])]
        trust_text = " ".join((x.get("text") or "") for x in ctx.get("trust_signals") or []).lower()
        self.assertIn("proceso_activo", ids)
        self.assertIn("evaluando activamente", trust_text)

    def test_m_trust_reemplazo_y_candidata_elegida(self):
        solicitud = _make_solicitud(
            estado="reemplazo",
            candidata_id=501,
            candidata=SimpleNamespace(nombre_completo="Ana Perez"),
        )
        ctx = self._call_detalle(solicitud, candidatas=[_make_sc(status="seleccionada")])["ctx"]
        ids = [x.get("id") for x in (ctx.get("trust_signals") or [])]
        trust_text = " ".join((x.get("text") or "") for x in ctx.get("trust_signals") or []).lower()
        self.assertIn("reemplazo_cobertura", ids)
        self.assertIn("candidata_elegida", ids)
        self.assertIn("sigue cubierto", trust_text)


if __name__ == "__main__":
    unittest.main()
