from app import app as flask_app
from unittest.mock import patch


def _get_public_form_html() -> str:
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    with patch("clientes.routes._ensure_public_new_token_usage_table", return_value=True), \
         patch("clientes.routes._public_new_link_usage_by_hash", return_value=None), \
         patch("clientes.routes._resolve_public_new_link_token", return_value=(True, "", {})):
        resp = client.get("/clientes/solicitudes/nueva-publica/tok123")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_envejeciente_section_renders_new_header_and_subtitle():
    html = _get_public_form_html()
    assert "Informacion del envejeciente" in html
    assert "Indica el nivel de asistencia que necesita la persona." in html
    assert "id=\"wrap_envejeciente_info_independiente\"" in html
    assert "id=\"wrap_envejeciente_info_encamado\"" in html


def test_envejeciente_section_contains_expected_notes_and_only_encamado_companion_option():
    html = _get_public_form_html()
    assert "puede movilizarse o realizar parte de sus actividades por si mismo" in html
    assert "requiere asistencia directa para higiene, alimentacion, movilidad o cuidado diario" in html
    assert "id=\"wrap_envejeciente_solo_acompanamiento\"" in html
    assert "solo acompanamiento o supervision" in html


def test_envejeciente_js_hides_and_cleans_when_switching_to_independiente():
    html = _get_public_form_html()
    assert "var isEncamado = selected && tipo === 'encamado';" in html
    assert "if (soloWrap) soloWrap.style.display = isEncamado ? '' : 'none';" in html
    assert "if ((!selected || tipo !== 'encamado') && solo) {" in html
    assert "if ((!selected || tipo !== 'encamado') && fromUserEvent) {" in html
    assert "input[name=\"envejeciente_responsabilidades\"]" in html
