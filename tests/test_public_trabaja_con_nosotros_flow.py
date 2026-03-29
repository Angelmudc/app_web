# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
from utils.candidate_registration import CandidateCreateState
from utils.robust_save import RobustSaveResult


def _domestica_base_data(**overrides):
    data = {
        "nombre_completo": "Maria Fernanda Perez",
        "edad": "29",
        "numero_telefono": "809-123-4567",
        "direccion_completa": "Santiago Centro",
        "modalidad_trabajo_preferida": "Salida diaria",
        "rutas_cercanas": "Centro",
        "empleo_anterior": "Limpieza y cocina",
        "anos_experiencia": "3 años o más",
        "areas_experiencia": ["Limpieza", "Cocina"],
        "sabe_planchar": "si",
        "contactos_referencias_laborales": "Ref laboral 1",
        "referencias_familiares_detalle": "Ref familiar 1",
        "acepta_porcentaje_sueldo": "1",
        "cedula": "001-1234567-8",
    }
    data.update(overrides or {})
    return data


def _general_base_data(**overrides):
    data = {
        "nombre_completo": "Carlos Manuel Diaz",
        "cedula": "001-1234567-8",
        "telefono": "809-555-1111",
        "edad": "28",
        "sexo": "masculino",
        "nacionalidad": "Dominicana",
        "email": "carlos@example.com",
        "direccion_completa": "Santiago, Centro",
        "ciudad": "Santiago",
        "sector": "Centro",
        "modalidad": "tiempo_completo",
        "horario_disponible": "8am-5pm",
        "sueldo_esperado": "25000",
        "tiene_experiencia": "y",
        "anos_experiencia": "3",
        "experiencia_resumen": "Atencion al cliente y ventas",
        "nivel_educativo": "Secundaria",
        "habilidades": "Excel, caja",
        "documentos_al_dia": "y",
        "disponible_fines_o_noches": "y",
        "referencias_laborales": "Ref laboral",
        "referencias_familiares": "Ref familiar",
    }
    data.update(overrides or {})
    return data


def test_landing_publica_trabaja_con_nosotros_disponible():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()

    resp = client.get("/trabaja-con-nosotros")
    html = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert "/registro/registro_publico/" in html
    assert "/reclutas/registro" in html
    assert "Portal de reclutamiento" in html


def test_web_comercial_y_reclutamiento_quedan_separadas_en_navegacion():
    flask_app.config["TESTING"] = True
    client = flask_app.test_client()

    comercial = client.get("/")
    html_comercial = comercial.get_data(as_text=True)
    assert comercial.status_code == 200
    assert "/trabaja-con-nosotros" in html_comercial

    reclu = client.get("/trabaja-con-nosotros")
    html_reclu = reclu.get_data(as_text=True)
    assert reclu.status_code == 200
    assert "/registro/registro_publico/" in html_reclu
    assert "/reclutas/registro" in html_reclu
    assert "/admin/" not in html_reclu


def test_formulario_domestica_valido_redirige_gracias():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    fake_candidate = SimpleNamespace(fila=505, cedula="001-1234567-8", nombre_completo="Maria Fernanda Perez")
    with patch("registro.routes.find_duplicate_candidata_by_cedula", return_value=(None, "00112345678")), \
         patch("registro.routes.create_staff_notification", return_value=True) as notif_mock, \
         patch(
             "registro.routes.robust_create_candidata",
             return_value=(RobustSaveResult(ok=True, attempts=1, error_message=""), CandidateCreateState(candidate=fake_candidate, candidate_id=505)),
         ):
        resp = client.post("/registro/registro_publico/", data=_domestica_base_data(), follow_redirects=False)

    assert resp.status_code in (302, 303)
    assert resp.headers.get("Location", "").endswith("/registro/registro_publico/gracias/")
    notif_mock.assert_called_once()


def test_formulario_domestica_bloquea_abuso_por_ip_con_respuesta_controlada():
    flask_app.config["TESTING"] = False
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with patch("registro.routes.hit_rate_limit", return_value=False), \
         patch("registro.routes.enforce_business_limit", return_value=(True, 21)):
        resp = client.post("/registro/registro_publico/", data=_domestica_base_data(), follow_redirects=False)

    assert resp.status_code == 429
    assert "demasiados envíos".lower() in resp.get_data(as_text=True).lower()


def test_formulario_general_bloquea_timing_no_humano():
    flask_app.config["TESTING"] = False
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with patch("reclutas.routes.hit_rate_limit", return_value=False), \
         patch("reclutas.routes.enforce_business_limit", return_value=(False, 1)), \
         patch("reclutas.routes.enforce_min_human_interval", return_value=(True, 1)):
        resp = client.post("/reclutas/registro", data=_general_base_data(), follow_redirects=False)

    assert resp.status_code == 429
    assert "espera un momento".lower() in resp.get_data(as_text=True).lower()


def test_formulario_domestica_guarda_campos_nuevos_en_columnas_reales():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    captured = {"candidate": None}

    def _fake_robust(**kwargs):
        candidate = kwargs["build_candidate"](1)
        captured["candidate"] = candidate
        return (
            RobustSaveResult(ok=True, attempts=1, error_message=""),
            CandidateCreateState(candidate=SimpleNamespace(fila=606), candidate_id=606),
        )

    payload = _domestica_base_data(
        disponibilidad_inicio="inmediata",
        trabajo_con_ninos="si",
        trabajo_con_mascotas="no",
        puede_dormir_fuera="si",
        sueldo_esperado="RD$20,000",
        motivacion_trabajo="Necesito empleo estable",
    )

    with patch("registro.routes.find_duplicate_candidata_by_cedula", return_value=(None, "00112345678")), \
         patch("registro.routes.robust_create_candidata", side_effect=_fake_robust):
        resp = client.post("/registro/registro_publico/", data=payload, follow_redirects=False)

    assert resp.status_code in (302, 303)
    cand = captured["candidate"]
    assert cand is not None
    assert cand.disponibilidad_inicio == "inmediata"
    assert cand.trabaja_con_ninos is True
    assert cand.trabaja_con_mascotas is False
    assert cand.puede_dormir_fuera is True
    assert cand.sueldo_esperado == "RD$20,000"
    assert cand.motivacion_trabajo == "Necesito empleo estable"
    assert cand.origen_registro == "publico_domestica"
    assert cand.creado_por_staff is None
    assert cand.creado_desde_ruta == "/registro/registro_publico/"
    assert "Datos complementarios" not in (cand.empleo_anterior or "")


def test_formulario_domestica_rechaza_nombre_corto():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with patch("registro.routes.find_duplicate_candidata_by_cedula", return_value=(None, "00112345678")):
        resp = client.post(
            "/registro/registro_publico/",
            data=_domestica_base_data(nombre_completo="Ana"),
            follow_redirects=False,
        )

    assert resp.status_code == 400
    assert "al menos 6 letras" in resp.get_data(as_text=True)


def test_formulario_domestica_rechaza_cedula_y_telefono_invalidos():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with patch("registro.routes.find_duplicate_candidata_by_cedula", return_value=(None, "001123")):
        cedula_bad = client.post(
            "/registro/registro_publico/",
            data=_domestica_base_data(cedula="001-123"),
            follow_redirects=False,
        )
    assert cedula_bad.status_code == 400
    assert "11 dígitos" in cedula_bad.get_data(as_text=True)

    with patch("registro.routes.find_duplicate_candidata_by_cedula", return_value=(None, "00112345678")):
        phone_bad = client.post(
            "/registro/registro_publico/",
            data=_domestica_base_data(numero_telefono="809-12"),
            follow_redirects=False,
        )
    assert phone_bad.status_code == 400
    assert "exactamente 10 dígitos" in phone_bad.get_data(as_text=True)


def test_formulario_general_valido_redirige_gracias():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with patch("reclutas.routes.db.session.add"), \
         patch("reclutas.routes.db.session.commit"), \
         patch("reclutas.routes.create_staff_notification", return_value=True) as notif_mock, \
         patch("reclutas.routes.ReclutaPerfil", side_effect=lambda **kwargs: SimpleNamespace(id=99, **kwargs)):
        resp = client.post("/reclutas/registro", data=_general_base_data(), follow_redirects=False)

    assert resp.status_code in (302, 303)
    assert resp.headers.get("Location", "").endswith("/reclutas/registro/gracias")
    notif_mock.assert_called_once()


def test_formulario_general_rechaza_nombre_cedula_y_telefono_invalidos():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    short_name = client.post("/reclutas/registro", data=_general_base_data(nombre_completo="Ana"), follow_redirects=False)
    assert short_name.status_code == 200
    assert "al menos 6 letras" in short_name.get_data(as_text=True)

    bad_cedula = client.post("/reclutas/registro", data=_general_base_data(cedula="123"), follow_redirects=False)
    assert bad_cedula.status_code == 200
    assert "11 dígitos" in bad_cedula.get_data(as_text=True)

    bad_phone = client.post("/reclutas/registro", data=_general_base_data(telefono="809-11"), follow_redirects=False)
    assert bad_phone.status_code == 200
    assert "exactamente 10 dígitos" in bad_phone.get_data(as_text=True)


def test_registro_interno_guarda_trazabilidad_como_interno():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    login = client.post("/admin/login", data={"usuario": "Cruz", "clave": "8998"}, follow_redirects=False)
    assert login.status_code in (302, 303)

    captured = {"candidate": None}

    def _fake_robust(**kwargs):
        candidate = kwargs["build_candidate"](1)
        captured["candidate"] = candidate
        return (
            RobustSaveResult(ok=True, attempts=1, error_message=""),
            CandidateCreateState(candidate=SimpleNamespace(fila=707), candidate_id=707),
        )

    with patch("core.legacy_handlers.find_duplicate_candidata_by_cedula", return_value=(None, "00112345678")), \
         patch("core.legacy_handlers.robust_create_candidata", side_effect=_fake_robust), \
         patch("core.legacy_handlers.create_staff_notification", return_value=True) as notif_mock:
        resp = client.post("/registro_interno/", data=_domestica_base_data(), follow_redirects=False)

    assert resp.status_code in (302, 303)
    cand = captured["candidate"]
    assert cand is not None
    assert cand.origen_registro == "interno"
    assert cand.creado_por_staff is not None
    assert cand.creado_desde_ruta == "/registro_interno/"
    notif_mock.assert_not_called()


def test_honeypot_domestica_no_persiste_y_redirige_gracias():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with patch("registro.routes.robust_create_candidata") as robust_mock:
        resp = client.post(
            "/registro/registro_publico/",
            data=_domestica_base_data(website="https://bot.invalid"),
            follow_redirects=False,
        )
    assert resp.status_code in (302, 303)
    assert resp.headers.get("Location", "").endswith("/registro/registro_publico/gracias/")
    robust_mock.assert_not_called()


def test_honeypot_general_no_persiste_y_redirige_gracias():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    with patch("reclutas.routes.db.session.add") as add_mock:
        resp = client.post(
            "/reclutas/registro",
            data=_general_base_data(bot_field="bot-spam"),
            follow_redirects=False,
        )
    assert resp.status_code in (302, 303)
    assert resp.headers.get("Location", "").endswith("/reclutas/registro/gracias")
    add_mock.assert_not_called()


def test_rate_limit_domestica_devuelve_429_controlado():
    prev_testing = flask_app.config.get("TESTING", False)
    flask_app.config["TESTING"] = False
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    try:
        with patch("registro.routes.hit_rate_limit", return_value=True):
            resp = client.post("/registro/registro_publico/", data=_domestica_base_data(), follow_redirects=False)
    finally:
        flask_app.config["TESTING"] = prev_testing

    assert resp.status_code == 429
    assert "Demasiados intentos en poco tiempo" in resp.get_data(as_text=True)


def test_rate_limit_general_devuelve_429_controlado():
    prev_testing = flask_app.config.get("TESTING", False)
    flask_app.config["TESTING"] = False
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    try:
        with patch("reclutas.routes.hit_rate_limit", return_value=True):
            resp = client.post("/reclutas/registro", data=_general_base_data(), follow_redirects=False)
    finally:
        flask_app.config["TESTING"] = prev_testing

    assert resp.status_code == 429
    assert "Demasiados intentos en poco tiempo" in resp.get_data(as_text=True)


def test_envio_vacio_domestica_y_general_manejan_error_sin_detalle_interno():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()

    empty_dom = client.post("/registro/registro_publico/", data={}, follow_redirects=False)
    assert empty_dom.status_code == 400
    html_dom = empty_dom.get_data(as_text=True)
    assert "Por favor completa" in html_dom or "Cédula requerida" in html_dom
    assert "Traceback" not in html_dom

    empty_gen = client.post("/reclutas/registro", data={}, follow_redirects=False)
    assert empty_gen.status_code == 200
    html_gen = empty_gen.get_data(as_text=True)
    assert "Revisa los campos resaltados" in html_gen
    assert "Traceback" not in html_gen
