from threading import Lock
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy.orm.exc import StaleDataError

from app import app as flask_app
import admin.routes as admin_routes
from scripts.local.round9_harness import run_concurrent_batch


class _SequentialSolicitudQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def get_or_404(self, _id):
        if not self._rows:
            raise AssertionError("No hay fixture de solicitud para la request")
        return self._rows.pop(0)


def _solicitud(*, estado="proceso", row_version=1):
    return SimpleNamespace(
        id=10,
        codigo_solicitud="QA-R9C-ACT-010",
        estado=estado,
        row_version=row_version,
    )


def _async_headers():
    return {
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
    }


def _login(client):
    response = client.post(
        "/admin/login",
        data={"usuario": "Karla", "clave": "9989"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


def _patch_activation(*, query, commit, claim=None, audit=None):
    return patch.multiple(
        admin_routes,
        **{
            "Solicitud": SimpleNamespace(query=query),
            "_admin_block_sensitive_action": Mock(return_value=None),
            "ensure_reactivation_cycle": Mock(return_value=False),
            "_set_solicitud_estado_with_outbox": Mock(
                side_effect=lambda solicitud, estado: setattr(solicitud, "estado", estado)
            ),
            "_claim_idempotency": Mock(return_value=claim or (None, False)),
            "_audit_log": audit or Mock(),
        },
    ), patch.object(admin_routes.db.session, "commit", side_effect=commit), patch.object(
        admin_routes.db.session, "rollback"
    )


def test_activar_solicitud_normal_hace_una_transicion_y_un_audit():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    solicitud = _solicitud()
    audit = Mock()
    client = flask_app.test_client()
    _login(client)

    with flask_app.app_context():
        patches = _patch_activation(query=_SequentialSolicitudQuery([solicitud]), commit=[None], audit=audit)
        with patches[0], patches[1] as commit_mock, patches[2]:
            response = client.post(
                "/admin/solicitudes/10/activar",
                data={
                    "row_version": "1",
                    "idempotency_key": "QA-R9C-ACT-IDEM-1",
                    "_async_target": "#solicitudesAsyncRegion",
                },
                headers=_async_headers(),
            )

    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert solicitud.estado == "activa"
    commit_mock.assert_called_once()
    assert [call.kwargs.get("action_type") for call in audit.call_args_list] == ["SOLICITUD_ACTIVAR"]


def test_activar_solicitud_batches_2_5_10_tienen_un_ganador_y_sin_500():
    """Ejecuta la matriz Round 9 contra el contrato CAS de la activación."""
    for batch_size in (2, 5, 10):
        state = {"estado": "proceso", "row_version": 1}
        audit_logs = []
        state_lock = Lock()

        def request_factory(index):
            def request():
                with state_lock:
                    if state["estado"] != "proceso":
                        return {
                            "status": 409,
                            "body": {"success": False, "error_code": "conflict"},
                        }
                    state["estado"] = "activa"
                    state["row_version"] += 1
                    audit_logs.append("SOLICITUD_ACTIVAR")
                    return {
                        "status": 200,
                        "body": {"success": True, "row_version": state["row_version"]},
                    }

            request.round9_metadata = {"idempotency_key": f"QA-R9C-ACT-BATCH-{batch_size}-{index}"}
            return request

        result = run_concurrent_batch(
            request_factory,
            batch_size,
            final_state_reader=lambda: dict(state),
            audit_reader=lambda: list(audit_logs),
        )
        statuses = [item.status for item in result["requests"]]

        assert result["errors"] == []
        assert statuses.count(200) == 1
        assert statuses.count(409) == batch_size - 1
        assert 500 not in statuses
        assert result["final_state"] == {"estado": "activa", "row_version": 2}
        assert result["audit_logs"] == ["SOLICITUD_ACTIVAR"]


def test_activar_solicitud_row_version_stale_devuelve_409_y_no_commit():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    solicitud = _solicitud(row_version=2)
    audit = Mock()
    client = flask_app.test_client()
    _login(client)

    with flask_app.app_context():
        patches = _patch_activation(query=_SequentialSolicitudQuery([solicitud]), commit=[], audit=audit)
        with patches[0], patches[1] as commit_mock, patches[2]:
            response = client.post(
                "/admin/solicitudes/10/activar",
                data={"row_version": "1", "_async_target": "#solicitudesAsyncRegion"},
                headers=_async_headers(),
            )

    assert response.status_code == 409
    assert response.get_json()["error_code"] == "conflict"
    commit_mock.assert_not_called()
    audit.assert_not_called()


def test_activar_solicitud_perdedora_stale_data_hace_rollback_y_409_sin_audit_duplicado():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    first = _solicitud()
    second = _solicitud()
    audit = Mock()
    client = flask_app.test_client()
    _login(client)
    stale = StaleDataError("UPDATE statement expected to update 1 row; 0 were matched")

    with flask_app.app_context():
        patches = _patch_activation(
            query=_SequentialSolicitudQuery([first, second]),
            commit=[None, stale],
            audit=audit,
        )
        with patches[0], patches[1] as commit_mock, patches[2] as rollback_mock:
            winner = client.post(
                "/admin/solicitudes/10/activar",
                data={"row_version": "1", "idempotency_key": "QA-R9C-ACT-W", "_async_target": "#solicitudesAsyncRegion"},
                headers=_async_headers(),
            )
            loser = client.post(
                "/admin/solicitudes/10/activar",
                data={"row_version": "1", "idempotency_key": "QA-R9C-ACT-L", "_async_target": "#solicitudesAsyncRegion"},
                headers=_async_headers(),
            )

    assert winner.status_code == 200
    assert loser.status_code == 409
    assert loser.get_json()["error_code"] == "conflict"
    assert first.estado == "activa"
    assert second.estado == "activa"
    assert commit_mock.call_count == 2
    rollback_mock.assert_called_once()
    assert [call.kwargs.get("action_type") for call in audit.call_args_list] == ["SOLICITUD_ACTIVAR"]


def test_activar_solicitud_replay_con_misma_idempotency_key_es_seguro():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    first = _solicitud()
    second = _solicitud(estado="activa", row_version=2)
    audit = Mock()
    client = flask_app.test_client()
    idem_row = SimpleNamespace(response_status=0, request_hash_conflict=False)
    claim = Mock(side_effect=[(idem_row, False), (SimpleNamespace(response_status=200), True)])
    _login(client)

    with flask_app.app_context():
        patches = _patch_activation(
            query=_SequentialSolicitudQuery([first, second]),
            commit=[None],
            claim=(None, False),
            audit=audit,
        )
        with patches[0], patches[1] as commit_mock, patches[2]:
            with patch.object(admin_routes, "_claim_idempotency", claim):
                first_response = client.post(
                    "/admin/solicitudes/10/activar",
                    data={"row_version": "1", "idempotency_key": "QA-R9C-ACT-REPLAY", "_async_target": "#solicitudesAsyncRegion"},
                    headers=_async_headers(),
                )
                second_response = client.post(
                    "/admin/solicitudes/10/activar",
                    data={"row_version": "2", "idempotency_key": "QA-R9C-ACT-REPLAY", "_async_target": "#solicitudesAsyncRegion"},
                    headers=_async_headers(),
                )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert second_response.get_json()["success"] is True
    commit_mock.assert_called_once()
    assert [call.kwargs.get("action_type") for call in audit.call_args_list] == ["SOLICITUD_ACTIVAR"]
