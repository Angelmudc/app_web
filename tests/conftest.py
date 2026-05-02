# -*- coding: utf-8 -*-

import os
import tempfile


# Fuerza entorno de pruebas aislado para que pytest nunca use la BD real.
os.environ["APP_ENV"] = "test"
_tmp_db = os.path.join(tempfile.gettempdir(), "app_web_pytest.sqlite")
os.environ["DATABASE_URL_TEST"] = f"sqlite:///{_tmp_db}"
os.environ.setdefault("DATABASE_URL", "postgresql://prod-user:prod-pass@prod-host/prod_db")
