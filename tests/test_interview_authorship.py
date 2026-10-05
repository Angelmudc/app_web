import re
from types import SimpleNamespace
from unittest.mock import patch

from flask import render_template

from app import app as flask_app
from core.handlers.entrevistas_handlers import (
    _current_staff_user_id,
    _interview_change_items,
    _set_interview_audit_actor,
)
from models import Entrevista


def _staff(user_id: int):
    return SimpleNamespace(id=user_id, is_authenticated=True, username=f"staff-{user_id}")


def test_interview_authorship_preserves_creator_and_rotates_last_editor():
    entrevista = Entrevista(candidata_id=1)

    with patch("core.handlers.entrevistas_handlers.current_user", _staff(101)):
        assert _current_staff_user_id() == 101
        _set_interview_audit_actor(entrevista, creating=True)

    assert entrevista.created_by_staff_user_id == 101
    assert entrevista.updated_by_staff_user_id is None

    with patch("core.handlers.entrevistas_handlers.current_user", _staff(202)):
        _set_interview_audit_actor(entrevista)
    assert entrevista.created_by_staff_user_id == 101
    assert entrevista.updated_by_staff_user_id == 202

    with patch("core.handlers.entrevistas_handlers.current_user", _staff(303)):
        _set_interview_audit_actor(entrevista)
    assert entrevista.created_by_staff_user_id == 101
    assert entrevista.updated_by_staff_user_id == 303


def test_interview_authorship_does_not_invent_creator_for_historical_record():
    entrevista = Entrevista(candidata_id=1)

    with patch("core.handlers.entrevistas_handlers.current_user", _staff(404)):
        _set_interview_audit_actor(entrevista)

    assert entrevista.created_by_staff_user_id is None
    assert entrevista.updated_by_staff_user_id == 404


def test_interview_authorship_is_compact_and_handles_unknown_history():
    recent = [
        SimpleNamespace(
            id=1,
            tipo="domestica",
            estado="completa",
            creada_en=None,
            actualizada_en=None,
            created_by_staff_user=SimpleNamespace(username="Maria"),
            updated_by_staff_user=SimpleNamespace(username="Juan"),
        ),
        SimpleNamespace(
            id=2,
            tipo="enfermera",
            estado="completa",
            creada_en=None,
            actualizada_en=None,
            created_by_staff_user=SimpleNamespace(username="Maria"),
            updated_by_staff_user=SimpleNamespace(username="Maria"),
        ),
        SimpleNamespace(
            id=3,
            tipo="empleo_general",
            estado="completa",
            creada_en=None,
            actualizada_en=None,
            created_by_staff_user=None,
            updated_by_staff_user=SimpleNamespace(username="Pedro"),
        ),
    ]
    with flask_app.test_request_context("/admin/candidatas/1/_entrevistas"):
        html = render_template(
            "admin/_candidata_operativo_entrevistas_recent_fragment.html",
            candidata=SimpleNamespace(),
            entrevista_summary={"recent": recent, "reference_map": {}},
            center_next_url="/admin/candidatas/1",
        )

    compact_html = re.sub(r"\s+", " ", html)
    assert "Creada por Maria · Editada por Juan" in compact_html
    assert "Creada por Maria · Editada por Maria" in compact_html
    assert "Creada por Maria" in compact_html
    assert "Editada por Pedro" in compact_html


def test_interview_history_diff_contains_only_real_answer_changes_and_supports_noop():
    preguntas = [
        SimpleNamespace(id=1, clave="domestica.nombre", texto="Nombre"),
        SimpleNamespace(id=2, clave="domestica.edad", texto="Edad"),
    ]

    cambios = _interview_change_items(
        preguntas,
        {1: "Ana", 2: "30"},
        {1: " Ana ", 2: "31"},
    )

    assert cambios == [
        {
            "field": "respuesta:domestica.edad",
            "label": "Edad",
            "old": "30",
            "new": "31",
        }
    ]
    assert _interview_change_items(preguntas, {1: "Ana", 2: "30"}, {1: "Ana", 2: "30"}) == []
