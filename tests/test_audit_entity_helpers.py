# -*- coding: utf-8 -*-

from app import app as flask_app
from config_app import db
from models import StaffAuditLog
from utils.audit_entity import candidata_entity_id, log_candidata_action


def test_candidata_entity_id_is_stable():
    class Dummy:
        id = 99
        fila = 777

    assert candidata_entity_id(Dummy()) == "99"

    class DummyFila:
        id = None
        fila = 321

    assert candidata_entity_id(DummyFila()) == "321"


def test_log_candidata_action_sets_entity_type_and_id():
    flask_app.config["TESTING"] = True

    with flask_app.app_context():
        class Cand:
            fila = 777
            codigo = "AUD-001"
            cedula = "001-1111111-1"
            nombre_completo = "Candidata Audit"
            estado = "lista_para_trabajar"

        cand = Cand()

        with flask_app.test_request_context("/admin/monitoreo", method="POST"):
            log_candidata_action(
                action_type="CANDIDATA_EDIT",
                candidata=cand,
                summary="edicion de prueba",
                metadata={"telefono": "8099999999", "solicitud_id": 50},
                changes={"nombre_completo": {"from": "A", "to": "B"}},
                success=True,
            )

        row = (
            StaffAuditLog.query
            .filter_by(action_type="CANDIDATA_EDIT", entity_type="candidata", entity_id=str(cand.fila))
            .order_by(StaffAuditLog.id.desc())
            .first()
        )
        assert row is not None
        assert row.entity_type == "candidata"
        assert row.entity_id == str(cand.fila)
        assert "telefono" not in (row.metadata_json or {})
        assert (row.metadata_json or {}).get("codigo") == "AUD-001"
