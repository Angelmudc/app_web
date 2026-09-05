# -*- coding: utf-8 -*-

import os
import unittest


class AdminResponsiveTablesTest(unittest.TestCase):
    def _read(self, *parts):
        path = os.path.join(os.getcwd(), *parts)
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()

    def test_shared_table_css_includes_stackable_mobile_mode(self):
        css = self._read("static", "css", "components.css")
        self.assertIn(".data-table--stackable", css)
        self.assertIn("@media (max-width: 767.98px)", css)
        self.assertIn("scrollbar-gutter: stable both-edges;", css)
        self.assertIn("overscroll-behavior-x: contain;", css)

    def test_solicitudes_list_results_uses_stackable_labels(self):
        txt = self._read("templates", "admin", "_solicitudes_list_results.html")
        self.assertIn("data-table--stackable", txt)
        self.assertIn('data-label="Solicitud"', txt)
        self.assertIn('data-label="Estado"', txt)
        self.assertIn('data-label="Situación"', txt)
        self.assertIn('data-label="Responsable"', txt)
        self.assertIn('data-label="Acciones"', txt)

    def test_cliente_detail_solicitudes_region_uses_stackable_labels(self):
        txt = self._read("templates", "admin", "_cliente_detail_solicitudes_region.html")
        self.assertIn("data-table--stackable", txt)
        self.assertIn('data-label="Código"', txt)
        self.assertIn('data-label="Fecha"', txt)
        self.assertIn('data-label="Ciudad/Sector"', txt)
        self.assertIn('data-label="Acciones"', txt)

    def test_other_active_tables_marked_for_stackable_fallback(self):
        procesos_txt = self._read("templates", "admin", "_solicitudes_proceso_acciones_results.html")
        self.assertIn("data-table--stackable", procesos_txt)
        self.assertIn('data-label="Acciones"', procesos_txt)

        descal_txt = self._read("templates", "admin", "candidatas_descalificacion.html")
        self.assertIn("data-table--stackable", descal_txt)
        self.assertIn('data-label="Acciones"', descal_txt)


if __name__ == "__main__":
    unittest.main()
