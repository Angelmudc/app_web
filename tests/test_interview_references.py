from types import SimpleNamespace

from core.services.interview_references import is_interview_reference_question


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
