import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import render_template

from app import app as flask_app
from core.handlers.entrevistas_handlers import _add_interview_history_event
from models import Entrevista
from utils.robust_save import execute_robust_save


def _load_history_migration():
    path = Path(__file__).parents[1] / "migrations" / "versions" / "20261005_1300_interview_history.py"
    spec = importlib.util.spec_from_file_location("interview_history_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path.read_text(encoding="utf-8")


def test_history_migration_has_single_published_parent_and_bounded_revision_id():
    migration, source = _load_history_migration()

    assert migration.revision == "20261005_1300_interview_history"
    assert len(migration.revision) <= 32
    assert migration.down_revision == "20261005_1200_interview_auth"
    assert "create_table" in source
    assert "ondelete=\"SET NULL\"" in source
    assert "drop_table" in source


def test_history_event_captures_staff_snapshot_and_json_details():
    session = Mock()
    interview = Entrevista(id=55, candidata_id=1)
    with patch("core.handlers.entrevistas_handlers.db.session", session), \
         patch(
             "core.handlers.entrevistas_handlers.current_user",
             SimpleNamespace(id=17, is_authenticated=True, username="Maria"),
         ):
        _add_interview_history_event(
            interview,
            event_type="updated",
            changes=[{"field": "respuesta:nombre", "label": "Nombre", "old": "A", "new": "B"}],
        )

    event = session.add.call_args.args[0]
    assert event.entrevista_id == 55
    assert event.event_type == "updated"
    assert event.staff_user_id == 17
    assert event.staff_display_name == "Maria"
    assert event.changes_json["items"][0]["new"] == "B"


def test_history_template_shows_creation_edit_and_historical_empty_state():
    event = SimpleNamespace(
        id=1,
        event_type="updated",
        occurred_at=None,
        staff_display_name="Maria",
        staff_user=None,
        changes_json={"items": [{"label": "Nombre", "old": "A", "new": "B"}]},
    )
    interview = SimpleNamespace(id=9, tipo="domestica")
    candidate = SimpleNamespace(fila=3, nombre_completo="Ana")

    with flask_app.test_request_context("/entrevistas/9/historial"):
        html = render_template(
            "entrevistas/entrevista_historial.html",
            entrevista=interview,
            candidata=candidate,
            historial=[event],
            next_url="",
        )
    compact = re.sub(r"\s+", " ", html)
    assert "Edición" in compact
    assert "Maria" in compact
    assert "Antes: <span" in compact
    assert "Después: <span" in compact

    with flask_app.test_request_context("/entrevistas/9/historial"):
        empty_html = render_template(
            "entrevistas/entrevista_historial.html",
            entrevista=interview,
            candidata=candidate,
            historial=[],
            next_url="",
        )
    assert "entrevista histórica anterior" in empty_html


def test_history_route_requires_internal_staff_authentication():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()
    response = client.get("/entrevistas/9/historial", follow_redirects=False)
    assert response.status_code in (302, 303, 401, 403)


def test_history_failure_rolls_back_interview_and_event_unit_of_work():
    session = Mock()

    def persist(_attempt):
        # La modificación de entrevista y el evento se registran antes del commit.
        session.pending_interview = True
        session.pending_history_event = True
        raise RuntimeError("history insert failed")

    result = execute_robust_save(
        session=session,
        persist_fn=persist,
        verify_fn=lambda: True,
        max_retries=0,
    )

    assert result.ok is False
    session.commit.assert_not_called()
    session.rollback.assert_called_once()
