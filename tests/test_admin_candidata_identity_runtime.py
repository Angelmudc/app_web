from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "static/js/admin/candidatas_operativo_detail_ui.js"


NODE_HARNESS = r"""
const fs = require("fs");
const source = fs.readFileSync(process.env.CANDIDATA_UI_SOURCE, "utf8");
const start = source.indexOf("  function syncIdentity(detailRoot, payload) {");
const end = source.indexOf("\n  function refreshFinance", start);
if (start < 0 || end < 0) throw new Error("syncIdentity not found");
const syncIdentity = Function("return (" + source.slice(start, end) + ")")();

global.document = { title: "Original · Domésticas" };
function root() {
  const nodes = {
    nombre: { textContent: "María Pérez" },
    edad: { textContent: "35" },
    telefono: { textContent: "809-555-1234" },
    codigo: { textContent: "CAN-123" },
    stickyName: { textContent: "María Pérez" },
    stickyCode: { textContent: "CAN-123" },
    state: { textContent: "Disponible" },
    breadcrumb: { textContent: "María Pérez" },
  };
  return {
    nodes,
    querySelector(selector) {
      const map = {
        '[data-cand-header="nombre"]': nodes.nombre,
        '[data-cand-header="edad"]': nodes.edad,
        '[data-cand-header="telefono"]': nodes.telefono,
        '[data-cand-header="codigo"]': nodes.codigo,
        '[data-cand-identity-name]': nodes.stickyName,
        '[data-cand-identity-code]': nodes.stickyCode,
        '[data-cand-identity-state]': nodes.state,
        '[data-cand-breadcrumb-name]': nodes.breadcrumb,
      };
      return map[selector] || null;
    },
  };
}
function values(r) {
  return Object.fromEntries(Object.entries(r.nodes).map(([key, node]) => [key, node.textContent]));
}
const cases = {};
let r = root();
syncIdentity(r, { entrevistas: [] });
cases.no_header = values(r);

r = root();
syncIdentity(r, { header: { telefono: "829-555-0000" } });
cases.partial = values(r);

r = root();
syncIdentity(r, { header: { nombre: "Ana López", edad: "41", telefono: "800-000-0000", codigo: "CAN-999", estado: "trabajando" } });
cases.complete = values(r);

r = root();
syncIdentity(r, { candidate: { codigo: "CAN-123" }, doc_flags: {} });
cases.upload_without_header = values(r);
console.log(JSON.stringify(cases));
"""


def test_candidata_identity_runtime_preserves_partial_payloads():
    env = dict(os.environ, CANDIDATA_UI_SOURCE=str(SOURCE))
    result = subprocess.run(
        ["node", "-e", NODE_HARNESS],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(result.stdout)

    assert data["no_header"]["codigo"] == "CAN-123"
    assert data["no_header"]["edad"] == "35"
    assert data["no_header"]["telefono"] == "809-555-1234"
    assert data["no_header"]["nombre"] == "María Pérez"

    assert data["partial"]["telefono"] == "829-555-0000"
    assert data["partial"]["codigo"] == "CAN-123"
    assert data["partial"]["edad"] == "35"
    assert data["partial"]["nombre"] == "María Pérez"

    assert data["complete"]["codigo"] == "CAN-999"
    assert data["complete"]["edad"] == "41"
    assert data["complete"]["telefono"] == "800-000-0000"
    assert data["complete"]["nombre"] == "Ana López"

    assert data["upload_without_header"]["codigo"] == "CAN-123"
    assert data["upload_without_header"]["edad"] == "35"
    assert data["upload_without_header"]["telefono"] == "809-555-1234"
    assert data["upload_without_header"]["nombre"] == "María Pérez"
