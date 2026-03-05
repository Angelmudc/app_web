# -*- coding: utf-8 -*-

import unittest

from app import app as flask_app


class TestingDbIsolationTest(unittest.TestCase):
    def test_database_url_para_pytest_es_sqlite(self):
        cfg_url = flask_app.config.get("SQLALCHEMY_DATABASE_URI", "")

        self.assertTrue(str(cfg_url).startswith("sqlite://"))


if __name__ == "__main__":
    unittest.main()
