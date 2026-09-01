from types import SimpleNamespace
from unittest.mock import patch

from core.services.interview_references import collect_pdf_reference_section, is_interview_reference_question


def test_interview_reference_questions_are_explicit_keys_not_substrings():
    assert is_interview_reference_question(
        SimpleNamespace(clave="domestica.referencia_laboral", texto="Referencia laboral mencionada")
    )
    assert is_interview_reference_question(
        SimpleNamespace(clave="domestica.referencia_familiar", texto="Referencia familiar mencionada")
    )
    assert not is_interview_reference_question(
        SimpleNamespace(clave="domestica.tipo_familia", texto="¿Con qué tipo de familia has trabajado anteriormente?")
    )
    assert not is_interview_reference_question(
        SimpleNamespace(clave="domestica.revision_salida", texto="¿Puedes ser revisada a la salida?")
    )


def test_collect_pdf_reference_section_prefiere_refs_de_entrevista_sobre_candidata():
    entrevista = SimpleNamespace(
        id=1,
        candidata_id=10,
    )
    candidata = SimpleNamespace(
        referencias_laboral="CAND-LAB",
        referencias_familiares="CAND-FAM",
    )

    with patch("core.services.interview_references.collect_entrevista_reference_items", return_value=[
        {"tipo": "laboral", "label": "Laboral", "respuesta": "INT-LAB", "texto": "INT-LAB", "datos_json": {}, "source": "explicit"},
    ]):
        section = collect_pdf_reference_section(entrevista, candidata)

    assert section["source"] == "entrevista"
    assert section["title"] == "Referencias de la entrevista"
    assert [item["respuesta"] for item in section["items"]] == ["INT-LAB"]


def test_collect_pdf_reference_section_usa_candidata_si_no_hay_refs_de_entrevista():
    entrevista = SimpleNamespace(id=1, candidata_id=10)
    candidata = SimpleNamespace(
        referencias_laboral="CAND-LAB",
        referencias_familiares="CAND-FAM",
    )

    with patch("core.services.interview_references.collect_entrevista_reference_items", return_value=[]):
        section = collect_pdf_reference_section(entrevista, candidata)

    assert section["source"] == "candidata"
    assert section["title"] == "Referencias verificadas de la candidata"
    assert [(item["tipo"], item["respuesta"]) for item in section["items"]] == [
        ("laboral", "CAND-LAB"),
        ("familiar", "CAND-FAM"),
    ]
