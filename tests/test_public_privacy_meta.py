from app import app as flask_app


def test_privacy_policy_is_public_and_describes_sophia_data_handling():
    response = flask_app.test_client().get("/privacidad")

    assert response.status_code == 200
    assert b"Sophia" in response.data
    assert b"WhatsApp Business Platform / Cloud API" in response.data
    assert b"OpenAI" in response.data
    assert "no establece un plazo" in response.get_data(as_text=True)
    assert b"info@domesticadelcibao.com" in response.data


def test_data_deletion_instructions_are_public_and_explain_request_channel():
    response = flask_app.test_client().get("/eliminacion-de-datos")

    assert response.status_code == 200
    assert "no requiere iniciar sesi" in response.get_data(as_text=True)
    assert b"mailto:info@domesticadelcibao.com" in response.data
    assert "no ofrece un" in response.get_data(as_text=True)
