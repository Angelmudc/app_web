from types import SimpleNamespace

from clientes.routes import _clear_adultos_if_not_household_funciones, _clear_house_structure_if_not_limpieza


def test_clear_house_structure_when_limpieza_is_not_selected():
    s = SimpleNamespace(
        tipo_lugar="casa",
        habitaciones=3,
        banos=2.0,
        dos_pisos=True,
        areas_comunes=["sala", "cocina"],
        area_otro="terraza techada",
        nota_cliente="Nota previa\nPisos reportados: 3+.\nOtra linea",
        adultos=2,
        ninos=1,
        mascota="perro",
    )

    _clear_house_structure_if_not_limpieza(s, ["cocinar", "lavar"])

    assert s.tipo_lugar is None
    assert s.habitaciones is None
    assert s.banos is None
    assert s.dos_pisos is False
    assert s.areas_comunes == []
    assert s.area_otro is None
    assert "Pisos reportados:" not in (s.nota_cliente or "")
    assert s.adultos == 2
    assert s.ninos == 1
    assert s.mascota == "perro"


def test_keep_house_structure_when_limpieza_is_selected():
    s = SimpleNamespace(
        tipo_lugar="apto",
        habitaciones=2,
        banos=1.5,
        dos_pisos=False,
        areas_comunes=["sala"],
        area_otro=None,
        nota_cliente="Pisos reportados: 3+.",
    )

    _clear_house_structure_if_not_limpieza(s, ["limpieza", "cocinar"])

    assert s.tipo_lugar == "apto"
    assert s.habitaciones == 2
    assert s.banos == 1.5
    assert s.dos_pisos is False
    assert s.areas_comunes == ["sala"]
    assert s.nota_cliente == "Pisos reportados: 3+."


def test_clear_adultos_when_only_cuidar_ninos_function_is_selected():
    s = SimpleNamespace(adultos=3)
    _clear_adultos_if_not_household_funciones(s, ["ninos"])
    assert s.adultos is None


def test_keep_adultos_when_household_function_is_selected():
    s = SimpleNamespace(adultos=3)
    _clear_adultos_if_not_household_funciones(s, ["ninos", "cocinar"])
    assert s.adultos == 3
