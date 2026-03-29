# -*- coding: utf-8 -*-

import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from flask import session

from app import app as flask_app
import admin.routes as admin_routes


class _SolicitudQueryStub:
    def __init__(self, rows, *, by_id=None, by_cliente=None):
        self._rows = list(rows)
        self._by_id = by_id
        self._by_cliente = by_cliente

    def _clone(self, **updates):
        return _SolicitudQueryStub(
            self._rows,
            by_id=updates.get("by_id", self._by_id),
            by_cliente=updates.get("by_cliente", self._by_cliente),
        )

    def filter_by(self, **kwargs):
        return self._clone(
            by_id=kwargs.get("id", self._by_id),
            by_cliente=kwargs.get("cliente_id", self._by_cliente),
        )

    def _filtered(self):
        rows = list(self._rows)
        if self._by_id is not None:
            rows = [r for r in rows if int(getattr(r, "id", 0) or 0) == int(self._by_id)]
        if self._by_cliente is not None:
            rows = [r for r in rows if int(getattr(r, "cliente_id", 0) or 0) == int(self._by_cliente)]
        return rows

    def first_or_404(self):
        rows = self._filtered()
        if rows:
            return rows[0]
        raise AssertionError("Solicitud no encontrada")

    def first(self):
        rows = self._filtered()
        return rows[0] if rows else None

    def get_or_404(self, _id):
        rows = [r for r in self._rows if int(getattr(r, "id", 0) or 0) == int(_id)]
        if rows:
            return rows[0]
        raise AssertionError("Solicitud no encontrada")

    def options(self, *_args, **_kwargs):
        return self


class _CandidataQueryStub:
    def __init__(self, candidata):
        self._candidata = candidata

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def all(self):
        return [self._candidata]

    def get(self, fila):
        if int(getattr(self._candidata, "fila", 0) or 0) == int(fila):
            return self._candidata
        return None


class _ReemplazoQueryStub:
    def __init__(self, rows, *, by_id=None, by_solicitud=None):
        self._rows = list(rows)
        self._by_id = by_id
        self._by_solicitud = by_solicitud

    def _clone(self, **updates):
        return _ReemplazoQueryStub(
            self._rows,
            by_id=updates.get("by_id", self._by_id),
            by_solicitud=updates.get("by_solicitud", self._by_solicitud),
        )

    def filter_by(self, **kwargs):
        return self._clone(
            by_id=kwargs.get("id", self._by_id),
            by_solicitud=kwargs.get("solicitud_id", self._by_solicitud),
        )

    def _filtered(self):
        rows = list(self._rows)
        if self._by_id is not None:
            rows = [r for r in rows if int(getattr(r, "id", 0) or 0) == int(self._by_id)]
        if self._by_solicitud is not None:
            rows = [r for r in rows if int(getattr(r, "solicitud_id", 0) or 0) == int(self._by_solicitud)]
        return rows

    def first_or_404(self):
        rows = self._filtered()
        if rows:
            return rows[0]
        raise AssertionError("Reemplazo no encontrado")


class _CandidataQueryByFilaStub:
    def __init__(self, rows):
        self._rows = list(rows)
        self._by_fila = {int(getattr(r, "fila", 0) or 0): r for r in self._rows}
        self._fila = None

    def _clone(self, *, fila=None):
        nxt = _CandidataQueryByFilaStub(self._rows)
        nxt._fila = self._fila if fila is None else fila
        return nxt

    def filter_by(self, **kwargs):
        return self._clone(fila=kwargs.get("fila", self._fila))

    def first(self):
        try:
            fid = int(self._fila or 0)
        except Exception:
            fid = 0
        return self._by_fila.get(fid)

    def get(self, fila):
        try:
            fid = int(fila or 0)
        except Exception:
            fid = 0
        return self._by_fila.get(fid)


class _ReemplazoStub:
    def __init__(self):
        self.id = 20
        self.solicitud_id = 10
        self.candidata_old_id = 1
        self.candidata_new_id = None
        self.estado_previo_solicitud = "activa"
        self.fecha_fin_reemplazo = None
        self.oportunidad_nueva = True

    def cerrar_reemplazo(self, candidata_nueva_id=None):
        self.fecha_fin_reemplazo = datetime.utcnow()
        self.oportunidad_nueva = False
        if candidata_nueva_id is not None:
            self.candidata_new_id = candidata_nueva_id


class _PagoFormStub:
    class _FieldStub:
        def __init__(self, value=None, label="Campo"):
            self.data = value
            self.choices = []
            self.label = SimpleNamespace(text=label)

        def __call__(self, **_kwargs):
            return '<input>'

    def __init__(self):
        self.candidata_id = self._FieldStub(value=5, label="Candidata")
        self.monto_pagado = self._FieldStub(value="12000", label="Monto")
        self.submit = SimpleNamespace(label=SimpleNamespace(text="Registrar"))
        self.errors = {}

    def hidden_tag(self):
        return ""

    def validate_on_submit(self):
        return True


def _solicitud_stub(estado="activa", row_version=5):
    return SimpleNamespace(
        id=10,
        cliente_id=7,
        codigo_solicitud="SOL-010",
        estado=estado,
        row_version=row_version,
        candidata_id=None,
        candidata=None,
        sueldo="12000",
        monto_pagado=None,
        estado_previo_espera_pago="activa",
        fecha_cambio_espera_pago=None,
        usuario_cambio_espera_pago=None,
        ciudad_sector="Santiago",
        modalidad_trabajo="Con dormida",
        fecha_cancelacion=None,
        motivo_cancelacion=None,
        fecha_ultima_actividad=datetime.utcnow(),
        fecha_ultima_modificacion=datetime.utcnow(),
    )


class Phase1ConcurrencyIdempotencyTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self.client = flask_app.test_client()
        os.environ["ADMIN_LEGACY_ENABLED"] = "1"
        login = self.client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)
        self.assertIn(login.status_code, (302, 303))

    def _async_headers(self):
        return {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-Admin-Async": "1",
        }

    def _login_admin(self):
        self.client = flask_app.test_client()
        login = self.client.post("/admin/login", data={"usuario": "Cruz", "clave": "8998"}, follow_redirects=False)
        self.assertIn(login.status_code, (302, 303))

    def test_cancelar_solicitud_async_conflict_por_row_version(self):
        solicitud = _solicitud_stub(estado="activa", row_version=8)
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/clientes/7/solicitudes/10/cancelar",
                    data={
                        "motivo": "Cliente detuvo el proceso",
                        "row_version": "7",
                        "_async_target": "#cancelarSolicitudAsyncRegion",
                    },
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 409)
        payload = resp.get_json() or {}
        self.assertFalse(payload.get("success"))
        self.assertEqual(payload.get("error_code"), "conflict")
        commit_mock.assert_not_called()

    def test_cancelar_solicitud_async_idempotencia_duplicate_ok(self):
        solicitud = _solicitud_stub(estado="activa", row_version=8)
        idem_row = SimpleNamespace(response_status=200)
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch("admin.routes._claim_idempotency", return_value=(idem_row, True)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/clientes/7/solicitudes/10/cancelar",
                    data={
                        "motivo": "Cliente detuvo el proceso",
                        "row_version": "8",
                        "_async_target": "#cancelarSolicitudAsyncRegion",
                    },
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json() or {}
        self.assertTrue(payload.get("success"))
        commit_mock.assert_not_called()

    def test_poner_espera_pago_async_conflict_por_row_version(self):
        solicitud = _solicitud_stub(estado="activa", row_version=4)
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch("admin.routes._admin_block_sensitive_action", return_value=None), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/poner_espera_pago",
                    data={"row_version": "3", "_async_target": "#solicitudOperativaCoreAsyncRegion"},
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 409)
        payload = resp.get_json() or {}
        self.assertFalse(payload.get("success"))
        self.assertEqual(payload.get("error_code"), "conflict")
        commit_mock.assert_not_called()

    def test_cancelar_solicitud_async_idempotency_key_reutilizada_payload_distinto_devuelve_409(self):
        solicitud = _solicitud_stub(estado="activa", row_version=8)
        idem_row = SimpleNamespace(response_status=200, request_hash_conflict=True)
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch("admin.routes._claim_idempotency", return_value=(idem_row, True)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/clientes/7/solicitudes/10/cancelar",
                    data={
                        "motivo": "Cliente detuvo el proceso",
                        "row_version": "8",
                        "_async_target": "#cancelarSolicitudAsyncRegion",
                    },
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        response, status_code = resp if isinstance(resp, tuple) else (resp, resp.status_code)
        self.assertEqual(status_code, 409)
        payload = response.get_json() or {}
        self.assertFalse(payload.get("success"))
        self.assertEqual(payload.get("error_code"), "idempotency_conflict")
        commit_mock.assert_not_called()

    def test_poner_espera_pago_async_idempotency_key_reutilizada_payload_distinto_devuelve_409(self):
        solicitud = _solicitud_stub(estado="activa", row_version=4)
        idem_row = SimpleNamespace(response_status=200, request_hash_conflict=True)
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch("admin.routes._claim_idempotency", return_value=(idem_row, True)), \
                 patch("admin.routes._admin_block_sensitive_action", return_value=None), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/poner_espera_pago",
                    data={"row_version": "4", "_async_target": "#solicitudOperativaCoreAsyncRegion"},
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        response, status_code = resp if isinstance(resp, tuple) else (resp, resp.status_code)
        self.assertEqual(status_code, 409)
        payload = response.get_json() or {}
        self.assertFalse(payload.get("success"))
        self.assertEqual(payload.get("error_code"), "idempotency_conflict")
        commit_mock.assert_not_called()

    def test_registrar_pago_async_idempotency_key_reutilizada_payload_distinto_devuelve_409(self):
        solicitud = _solicitud_stub(estado="activa", row_version=8)
        candidata = SimpleNamespace(
            fila=5,
            nombre_completo="Candidata Uno",
            estado="activa",
            monto_total=None,
            porciento=None,
            fecha_de_pago=None,
        )
        idem_row = SimpleNamespace(response_status=200, request_hash_conflict=True)
        with flask_app.app_context():
            with flask_app.test_request_context(
                "/admin/clientes/7/solicitudes/10/pago",
                method="POST",
                data={
                    "row_version": "8",
                    "idempotency_key": "same-key-1",
                    "_async_target": "#registrarPagoAsyncRegion",
                },
                headers=self._async_headers(),
            ):
                session["usuario"] = "Cruz"
                session["role"] = "admin"
                with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                     patch.object(admin_routes.Candidata, "query", _CandidataQueryStub(candidata)), \
                     patch("admin.routes.AdminPagoForm", _PagoFormStub), \
                     patch("admin.routes.assert_candidata_no_descalificada", return_value=None), \
                     patch("admin.routes._claim_idempotency", return_value=(idem_row, True)), \
                     patch("admin.routes.db.session.commit") as commit_mock:
                    resp = admin_routes.registrar_pago.__wrapped__.__wrapped__(7, 10)

        response, status_code = resp if isinstance(resp, tuple) else (resp, resp.status_code)
        self.assertEqual(status_code, 409)
        payload = response.get_json() or {}
        self.assertFalse(payload.get("success"))
        self.assertEqual(payload.get("error_code"), "idempotency_conflict")
        commit_mock.assert_not_called()

    def test_cancelar_solicitud_async_emite_evento_outbox(self):
        solicitud = _solicitud_stub(estado="activa", row_version=8)
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch("admin.routes._emit_domain_outbox_event") as outbox_mock, \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/clientes/7/solicitudes/10/cancelar",
                    data={
                        "motivo": "Cliente detuvo el proceso",
                        "row_version": "8",
                        "_async_target": "#cancelarSolicitudAsyncRegion",
                    },
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        outbox_mock.assert_called_once()
        self.assertEqual(outbox_mock.call_args.kwargs.get("event_type"), "SOLICITUD_ESTADO_CAMBIADO")
        commit_mock.assert_called_once()

    def test_poner_espera_pago_async_emite_evento_outbox(self):
        solicitud = _solicitud_stub(estado="activa", row_version=4)
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch("admin.routes._emit_domain_outbox_event") as outbox_mock, \
                 patch("admin.routes._admin_block_sensitive_action", return_value=None), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/poner_espera_pago",
                    data={"row_version": "4", "_async_target": "#solicitudOperativaCoreAsyncRegion"},
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        outbox_mock.assert_called_once()
        self.assertEqual(outbox_mock.call_args.kwargs.get("event_type"), "SOLICITUD_ESTADO_CAMBIADO")
        commit_mock.assert_called_once()

    def test_registrar_pago_async_emite_evento_outbox(self):
        solicitud = _solicitud_stub(estado="activa", row_version=8)
        candidata = SimpleNamespace(
            fila=5,
            nombre_completo="Candidata Uno",
            estado="activa",
            monto_total=None,
            porciento=None,
            fecha_de_pago=None,
        )
        with flask_app.app_context():
            with flask_app.test_request_context(
                "/admin/clientes/7/solicitudes/10/pago",
                method="POST",
                data={
                    "row_version": "8",
                    "idempotency_key": "key-ok-1",
                    "_async_target": "#registrarPagoAsyncRegion",
                },
                headers=self._async_headers(),
            ):
                session["usuario"] = "Cruz"
                session["role"] = "admin"
                with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                     patch.object(admin_routes.Candidata, "query", _CandidataQueryStub(candidata)), \
                     patch("admin.routes.AdminPagoForm", _PagoFormStub), \
                     patch("admin.routes.assert_candidata_no_descalificada", return_value=None), \
                     patch("admin.routes._sync_solicitud_candidatas_after_assignment"), \
                     patch("admin.routes._mark_candidata_estado"), \
                     patch("admin.routes._emit_domain_outbox_event") as outbox_mock, \
                     patch("admin.routes.db.session.commit") as commit_mock:
                    resp = admin_routes.registrar_pago.__wrapped__.__wrapped__(7, 10)

        response, status_code = resp if isinstance(resp, tuple) else (resp, resp.status_code)
        self.assertEqual(status_code, 200)
        outbox_mock.assert_called_once()
        self.assertEqual(outbox_mock.call_args.kwargs.get("event_type"), "SOLICITUD_PAGO_REGISTRADO")
        commit_mock.assert_called_once()

    def test_cancelar_reemplazo_async_conflict_por_row_version(self):
        self._login_admin()
        solicitud = _solicitud_stub(estado="reemplazo", row_version=8)
        reemplazo = _ReemplazoStub()
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQueryStub([reemplazo])), \
                 patch("admin.routes._admin_block_sensitive_action", return_value=None), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/reemplazos/20/cancelar",
                    data={"row_version": "7", "_async_target": "#solicitudReemplazoActionsAsyncRegion-10"},
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 409)
        payload = resp.get_json() or {}
        self.assertFalse(payload.get("success"))
        self.assertEqual(payload.get("error_code"), "conflict")
        commit_mock.assert_not_called()

    def test_cerrar_reemplazo_asignando_async_idempotency_key_reutilizada_payload_distinto_devuelve_409(self):
        solicitud = _solicitud_stub(estado="reemplazo", row_version=8)
        solicitud.candidata_id = 1
        reemplazo = _ReemplazoStub()
        candidata = SimpleNamespace(fila=2, estado="lista_para_trabajar", nombre_completo="Nueva")
        idem_row = SimpleNamespace(response_status=200, request_hash_conflict=True)
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQueryStub([reemplazo])), \
                 patch.object(admin_routes.Candidata, "query", _CandidataQueryByFilaStub([candidata])), \
                 patch("admin.routes._admin_block_sensitive_action", return_value=None), \
                 patch("admin.routes.assert_candidata_no_descalificada", return_value=None), \
                 patch("admin.routes._claim_idempotency", return_value=(idem_row, True)), \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/reemplazos/20/cerrar_asignando",
                    data={
                        "row_version": "8",
                        "candidata_new_id": "2",
                        "_async_target": "#solicitudReemplazoActionsAsyncRegion-10",
                    },
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 409)
        payload = resp.get_json() or {}
        self.assertFalse(payload.get("success"))
        self.assertEqual(payload.get("error_code"), "idempotency_conflict")
        commit_mock.assert_not_called()

    def test_cancelar_reemplazo_async_emite_evento_outbox(self):
        self._login_admin()
        solicitud = _solicitud_stub(estado="reemplazo", row_version=8)
        reemplazo = _ReemplazoStub()
        with flask_app.app_context():
            with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub([solicitud])), \
                 patch.object(admin_routes.Reemplazo, "query", _ReemplazoQueryStub([reemplazo])), \
                 patch("admin.routes._admin_block_sensitive_action", return_value=None), \
                 patch("admin.routes._claim_idempotency", return_value=(SimpleNamespace(response_status=0), False)), \
                 patch("admin.routes._emit_domain_outbox_event") as outbox_mock, \
                 patch("admin.routes.db.session.commit") as commit_mock:
                resp = self.client.post(
                    "/admin/solicitudes/10/reemplazos/20/cancelar",
                    data={"row_version": "8", "_async_target": "#solicitudReemplazoActionsAsyncRegion-10"},
                    headers=self._async_headers(),
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 200)
        outbox_mock.assert_called_once()
        self.assertEqual(outbox_mock.call_args.kwargs.get("event_type"), "REEMPLAZO_CANCELADO")
        commit_mock.assert_called_once()

    def test_feature_flags_criticos_default_enabled(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENABLE_CRITICAL_CONCURRENCY_GUARDS", None)
            os.environ.pop("ENABLE_CRITICAL_IDEMPOTENCY", None)
            self.assertTrue(admin_routes._critical_concurrency_guards_enabled())
            self.assertTrue(admin_routes._idempotency_enabled())

    def test_feature_flags_criticos_off_cuando_env_en_cero(self):
        with patch.dict(
            os.environ,
            {
                "ENABLE_CRITICAL_CONCURRENCY_GUARDS": "0",
                "ENABLE_CRITICAL_IDEMPOTENCY": "0",
            },
            clear=False,
        ):
            self.assertFalse(admin_routes._critical_concurrency_guards_enabled())
            self.assertFalse(admin_routes._idempotency_enabled())

    def test_claim_idempotency_detecta_payload_distinto_con_misma_key(self):
        existing = SimpleNamespace(request_hash="hash-previo", last_seen_at=None)
        query_stub = SimpleNamespace(
            filter_by=lambda **_kwargs: SimpleNamespace(first=lambda: existing)
        )
        with flask_app.test_request_context(
            "/admin/solicitudes/10/poner_espera_pago",
            method="POST",
            data={"foo": "bar"},
        ):
            with patch.object(admin_routes.RequestIdempotencyKey, "query", query_stub), \
                 patch("admin.routes._incoming_idempotency_key", return_value="idem-fixed"), \
                 patch("admin.routes._build_request_hash", return_value="hash-nuevo"), \
                 patch("admin.routes.db.session.add"), \
                 patch("admin.routes.db.session.flush", side_effect=admin_routes.IntegrityError("stmt", "params", Exception("dup"))), \
                 patch("admin.routes.db.session.rollback"), \
                 patch("admin.routes.db.session.commit"):
                row, duplicate = admin_routes._claim_idempotency(
                    scope="solicitud_estado_espera_pago_poner",
                    entity_type="Solicitud",
                    entity_id=10,
                    action="poner_espera_pago",
                )

        self.assertTrue(duplicate)
        self.assertTrue(getattr(row, "request_hash_conflict", False))


if __name__ == "__main__":
    unittest.main()
