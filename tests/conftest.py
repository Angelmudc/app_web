# -*- coding: utf-8 -*-

import os
import tempfile


# Fuerza entorno de pruebas aislado para que pytest nunca use la BD real.
os.environ["APP_ENV"] = "testing"
_tmp_db = os.path.join(tempfile.gettempdir(), "app_web_pytest.sqlite")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_db}"
