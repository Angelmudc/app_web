# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import admin.routes as admin_routes


class _Field:
    def __init__(self, data=None, choices=None):
        self.data = data
        self.choices = list(choices or [])


class _CreateFormStub:
    def __init__(self, *args, **kwargs):
        self.tipo_servicio = _Field("DOMESTICA_LIMPIEZA")
        self.sueldo = _Field("18000")
        self.tipo_lugar = _Field("casa")
        self.tipo_lugar_otro = _Field("")
        self.edad_requerida = _Field(["26-35"], choices=[("26-35", "26-35"), ("otro", "Otro")])
        self.edad_otro = _Field("")
        self.mascota = _Field("")
        self.funciones = _Field(["limpieza"], choices=[("limpieza", "Limpieza"), ("otro", "Otro")])
        self.funciones_otro = _Field("")
        self.areas_comunes = _Field(["sala"], choices=[("sala", "Sala"), ("otro", "Otro")])
        self.area_otro = _Field("")
        self.pasaje_aporte = _Field(False)

    def validate_on_submit(self):
        return True

    def populate_obj(self, obj):
        obj.tipo_servicio = self.tipo_servicio.data
        obj.estado = getattr(obj, "estado", None) or "proceso"


class _EditFormStub(_CreateFormStub):
    pass


class _SolicitudQueryStub:
    def __init__(self, row):
        self._row = row

    def filter_by(self, **kwargs):
        return self

    def first_or_404(self):
        return self._row


def _unwrap(view, layers=3):
    target = view
    for _ in range(layers):
        target = target.__wrapped__
    return target


def _ok_execute_form_save(*, persist_fn, **_kwargs):
    persist_fn(1)
    return SimpleNamespace(ok=True, error_message="")


def test_admin_nueva_solicitud_emite_eventos_live_cliente():
    flask_app.config["TESTING"] = True
    cliente = SimpleNamespace(id=7, total_solicitudes=0, fecha_ultima_solicitud=None, fecha_ultima_actividad=None)

    def _solicitud_factory(**kwargs):
        data = {
            "id": 901,
            "row_version": 1,
            "estado": "proceso",
            "codigo_solicitud": kwargs.get("codigo_solicitud", "SOL-901"),
            "tipo_servicio": kwargs.get("tipo_servicio", "DOMESTICA_LIMPIEZA"),
            "detalle_servicio": None,
            "nota_cliente": "",
            "pasaje_aporte": False,
        }
        data.update(kwargs)
        return SimpleNamespace(**data)

    with flask_app.app_context():
        with patch.object(admin_routes.Cliente, "query", SimpleNamespace(get_or_404=lambda _cid: cliente)), \
             patch("admin.routes.AdminSolicitudForm", _CreateFormStub), \
             patch("admin.routes.Solicitud", _solicitud_factory), \
             patch("admin.routes._execute_form_save", side_effect=_ok_execute_form_save), \
             patch("admin.routes._next_codigo_solicitud", return_value="SOL-901"), \
             patch("admin.routes.db.session.add"), \
             patch("admin.routes.db.session.flush"), \
             patch("admin.routes._resolve_modalidad_ui_context_from_request", return_value=("", "", "")), \
             patch("admin.routes.normalize_pasaje_mode_text", return_value=("incluido", "")), \
             patch("admin.routes._emit_domain_outbox_event") as emit_mock:
            with flask_app.test_request_context("/admin/clientes/7/solicitudes/nueva", method="POST", data={"csrf_token": "ok"}):
                resp = _unwrap(admin_routes.nueva_solicitud_admin)(7)

    assert resp.status_code in (302, 303)
    event_types = [str(c.kwargs.get("event_type") or "") for c in emit_mock.call_args_list]
    assert "CLIENTE_SOLICITUD_CREATED" in event_types
    assert "CLIENTE_DASHBOARD_UPDATED" in event_types


def test_admin_editar_solicitud_emite_eventos_live_cliente():
    flask_app.config["TESTING"] = True
    solicitud = SimpleNamespace(
        id=10,
        cliente_id=7,
        codigo_solicitud="SOL-010",
        row_version=2,
        estado="proceso",
        tipo_servicio="DOMESTICA_LIMPIEZA",
        ciudad_sector="Santiago",
        rutas_cercanas="Ruta K",
        modalidad_trabajo="Con dormida",
        horario="L-V",
        experiencia="Experiencia base",
        edad_requerida=["26-35"],
        funciones=["limpieza"],
        funciones_otro="",
        tipo_lugar="casa",
        habitaciones=2,
        banos=1,
        dos_pisos=False,
        areas_comunes=["sala"],
        area_otro="",
        adultos=2,
        ninos=0,
        mascota="",
        sueldo="18000",
        pasaje_aporte=False,
        nota_cliente="",
        detalles_servicio=None,
    )

    with flask_app.app_context():
        with patch.object(admin_routes.Solicitud, "query", _SolicitudQueryStub(solicitud)), \
             patch("admin.routes.AdminSolicitudForm", _EditFormStub), \
             patch("admin.routes._execute_form_save", side_effect=_ok_execute_form_save), \
             patch("admin.routes.db.session.flush"), \
             patch("admin.routes._resolve_modalidad_ui_context_from_request", return_value=("", "", "")), \
             patch("admin.routes.normalize_pasaje_mode_text", return_value=("incluido", "")), \
             patch("admin.routes._emit_domain_outbox_event") as emit_mock:
            with flask_app.test_request_context("/admin/clientes/7/solicitudes/10/editar", method="POST", data={"csrf_token": "ok"}):
                resp = _unwrap(admin_routes.editar_solicitud_admin)(7, 10)

    assert resp.status_code in (302, 303)
    event_types = [str(c.kwargs.get("event_type") or "") for c in emit_mock.call_args_list]
    assert "CLIENTE_SOLICITUD_UPDATED" in event_types
    assert "CLIENTE_DASHBOARD_UPDATED" in event_types
