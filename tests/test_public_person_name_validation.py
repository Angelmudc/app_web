from types import SimpleNamespace

import pytest

from clientes.forms import SolicitudClienteNuevoPublicaForm, SolicitudPublicaForm
from utils.person_name import is_valid_person_name, normalize_person_name, validate_person_name


@pytest.mark.parametrize(
    "value",
    ["María de la Cruz", "José Ángel", "D'Ávila", "Jean-Pierre", "Ana", "Muñoz"],
)
def test_public_person_name_accepts_human_names(value):
    assert is_valid_person_name(value)


@pytest.mark.parametrize(
    "value",
    [
        "maria.prueba@example.com",
        "https://ejemplo.com",
        "Juan123",
        "@Maria",
        "___",
        "😀 Pedro",
        "",
        "   ",
    ],
)
def test_public_person_name_rejects_emails_urls_digits_symbols_and_empty(value):
    assert not is_valid_person_name(value)


def test_public_person_name_normalizes_whitespace():
    assert normalize_person_name("  María   de la Cruz  ") == "María de la Cruz"


@pytest.mark.parametrize("form_class, field_name", [(SolicitudPublicaForm, "nombre_cliente"), (SolicitudClienteNuevoPublicaForm, "nombre_completo")])
def test_both_public_wtforms_use_the_shared_backend_validator(form_class, field_name):
    field = getattr(form_class, field_name)
    assert validate_person_name in field.kwargs["validators"]
    assert normalize_person_name in field.kwargs["filters"]
    invalid = SimpleNamespace(data="maria.prueba@example.com")
    with pytest.raises(Exception, match="Escribe un nombre válido"):
        validate_person_name(None, invalid)
