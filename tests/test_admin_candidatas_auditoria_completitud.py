# -*- coding: utf-8 -*-

import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app


def _mk_row(
    fila: int,
    nombre: str,
    incompleta: bool,
    faltantes: list[str],
    tiene: list[str],
    estado: str = "lista_para_trabajar",
):
    flags = {
        "entrevista": "entrevista" not in faltantes,
        "foto_perfil": "foto_perfil" not in faltantes,
        "depuracion": "depuracion" not in faltantes,
        "perfil": "perfil" not in faltantes,
        "cedula1": "cedula1" not in faltantes,
        "cedula2": "cedula2" not in faltantes,
        "referencias_laboral": "referencias_laboral" not in faltantes,
        "referencias_familiares": "referencias_familiares" not in faltantes,
    }
    return {
        "candidata": SimpleNamespace(
            fila=fila,
            nombre_completo=nombre,
            cedula=f"001-000000{fila}-0",
            codigo=f"C-{fila}",
            estado=estado,
        ),
        "flags": flags,
        "faltantes": list(faltantes),
        "tiene": list(tiene),
        "incompleta": bool(incompleta),
    }


class AdminCandidatasAuditoriaCompletitudTest(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["WTF_CSRF_ENABLED"] = False
        self.client = flask_app.test_client()
        os.environ["ADMIN_LEGACY_ENABLED"] = "1"

        login_resp = self.client.post(
            "/admin/login",
            data={"usuario": "Karla", "clave": "9989"},
            follow_redirects=False,
        )
        if login_resp.status_code == 429:
            with self.client.session_transaction() as sess:
                sess["usuario"] = "Karla"
                sess["role"] = "secretaria"
                sess["logged_at"] = datetime.utcnow().isoformat(timespec="seconds")
                sess["is_admin_session"] = True
        else:
            self.assertIn(login_resp.status_code, (302, 303))

    def test_render_200_y_links_reales(self):
        rows = [
            _mk_row(
                fila=10,
                nombre="Ana Incompleta",
                incompleta=True,
                faltantes=["entrevista", "cedula1", "referencias_laboral", "foto_perfil"],
                tiene=["depuracion", "perfil", "cedula2", "referencias_familiares"],
                estado="descalificada",
            ),
            _mk_row(
                fila=11,
                nombre="Bea Completa",
                incompleta=False,
                faltantes=[],
                tiene=[
                    "entrevista",
                    "foto_perfil",
                    "depuracion",
                    "perfil",
                    "cedula1",
                    "cedula2",
                    "referencias_laboral",
                    "referencias_familiares",
                ],
            ),
        ]

        with flask_app.app_context():
            with patch("admin.routes._build_auditoria_completitud_rows", return_value=rows):
                resp = self.client.get("/admin/candidatas/auditoria-completitud")

        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        self.assertIn("Ana Incompleta", html)
        self.assertNotIn("Bea Completa", html)
        self.assertIn("/subir_fotos", html)
        self.assertIn("/finalizar_proceso", html)
        self.assertIn("/entrevistas/candidata/10", html)
        self.assertIn("/referencias?candidata=10", html)
        self.assertIn("/buscar?candidata_id=10", html)

    def test_endpoints_existen_en_url_map(self):
        rules = flask_app.url_map._rules_by_endpoint
        self.assertIn("subir_fotos.subir_fotos", rules)
        self.assertIn("finalizar_proceso", rules)
        self.assertIn("entrevistas_de_candidata", rules)
        self.assertIn("referencias", rules)
        self.assertIn("buscar_candidata", rules)


if __name__ == "__main__":
    unittest.main()
