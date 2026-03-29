# -*- coding: utf-8 -*-

import os
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import admin.routes as admin_routes


class _SolicitudStub:
    def __init__(self):
        self.id = 590
        self.cliente_id = 77
        self.estado = "activa"
        self.candidata_id = 10
        self.candidata = SimpleNamespace(fila=10, nombre_completo="Candidata Demo", estado="trabajando")
        self.reemplazos = []
        self.row_version = 1


class _SolicitudQuery:
    def __init__(self, sol):
        self.sol = sol

    def options(self, *args, **kwargs):
        return self

    def get_or_404(self, _sid):
        return self.sol


class _FormStub:
    def __init__(self, *args, **kwargs):
        self.motivo_fallo = SimpleNamespace(data="")
        self.nota_adicional = SimpleNamespace(data="")
        self.candidata_old_id = SimpleNamespace(data="")
        self.candidata_old_name = SimpleNamespace(data="")

    def validate_on_submit(self):
        return False


def _login(client, user, pwd):
    return client.post("/admin/login", data={"usuario": user, "clave": pwd}, follow_redirects=False)


def test_secretaria_can_open_reemplazo_nuevo_get_and_post():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"

    client = flask_app.test_client()
    assert _login(client, "Karla", "9989").status_code in (302, 303)

    sol = _SolicitudStub()
    with flask_app.app_context():
        with patch.object(admin_routes.Solicitud, "query", _SolicitudQuery(sol)), \
             patch("admin.routes.AdminReemplazoForm", _FormStub), \
             patch("admin.routes.render_template", return_value="ok"):
            get_resp = client.get("/admin/solicitudes/590/reemplazos/nuevo", follow_redirects=False)
            post_resp = client.post("/admin/solicitudes/590/reemplazos/nuevo", data={}, follow_redirects=False)

    assert get_resp.status_code == 200
    assert post_resp.status_code == 200
