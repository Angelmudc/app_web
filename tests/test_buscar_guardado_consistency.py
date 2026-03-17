# -*- coding: utf-8 -*-

import re
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
import core.legacy_handlers as legacy_handlers


def _login_secretaria(client):
    return client.post("/admin/login", data={"usuario": "Karla", "clave": "9989"}, follow_redirects=False)


def _build_candidata_stub(fila: int = 77):
    return SimpleNamespace(
        fila=fila,
        nombre_completo="Demo",
        edad="30",
        numero_telefono="8091111111",
        direccion_completa="Santiago",
        modalidad_trabajo_preferida="salida diaria",
        rutas_cercanas="Centro",
        empleo_anterior="Casa A",
        anos_experiencia="2",
        areas_experiencia="limpieza",
        contactos_referencias_laborales="Ref laboral vieja",
        referencias_familiares_detalle="Ref familiar vieja",
        referencias_laboral="Ref laboral vieja",
        referencias_familiares="Ref familiar vieja",
        cedula="001-0000000-1",
        sabe_planchar=True,
        acepta_porcentaje_sueldo=True,
        disponibilidad_inicio=None,
        trabaja_con_ninos=None,
        trabaja_con_mascotas=None,
        puede_dormir_fuera=None,
        sueldo_esperado=None,
        motivacion_trabajo=None,
    )


def test_verify_candidata_fields_saved_no_usa_fallback_si_falla_lookup():
    with flask_app.app_context():
        with patch("core.legacy_handlers._get_candidata_by_fila_or_pk", return_value=None):
            ok = legacy_handlers._verify_candidata_fields_saved(123, {"nombre_completo": "Ana"})
    assert ok is False


def test_buscar_editar_sincroniza_columnas_referencias():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = _build_candidata_stub(fila=77)
    with flask_app.app_context():
        with patch("core.legacy_handlers.get_candidata_by_id", return_value=cand), \
             patch("core.legacy_handlers._get_candidata_by_fila_or_pk", return_value=cand), \
             patch("core.legacy_handlers.db.session.flush"), \
             patch("core.legacy_handlers.db.session.commit"):
            resp = client.post(
                "/buscar",
                data={
                    "guardar_edicion": "1",
                    "candidata_id": "77",
                    "nombre": "Demo Editada",
                    "contactos_referencias_laborales": "Ref laboral nueva",
                    "referencias_familiares_detalle": "Ref familiar nueva",
                },
                follow_redirects=False,
            )

    assert resp.status_code in (302, 303)
    assert cand.contactos_referencias_laborales == "Ref laboral nueva"
    assert cand.referencias_familiares_detalle == "Ref familiar nueva"
    assert cand.referencias_laboral == "Ref laboral nueva"
    assert cand.referencias_familiares == "Ref familiar nueva"


def test_buscar_no_confirma_guardado_si_no_se_puede_verificar_post_commit():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = _build_candidata_stub(fila=88)
    with flask_app.app_context():
        with patch("core.legacy_handlers.get_candidata_by_id", return_value=cand), \
             patch("core.legacy_handlers._get_candidata_by_fila_or_pk", return_value=None), \
             patch("core.legacy_handlers.db.session.flush"), \
             patch("core.legacy_handlers.db.session.commit"):
            resp = client.post(
                "/buscar",
                data={
                    "guardar_edicion": "1",
                    "candidata_id": "88",
                    "nombre": "No Verificable",
                },
                follow_redirects=False,
            )

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Error al guardar" in body


def test_referencias_sync_escribe_en_columnas_legacy_y_canonica():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = _build_candidata_stub(fila=99)
    with flask_app.app_context():
        with patch("core.legacy_handlers.get_candidata_by_id", return_value=cand), \
             patch("core.legacy_handlers._get_candidata_by_fila_or_pk", return_value=cand), \
             patch("core.legacy_handlers.db.session.flush"), \
             patch("core.legacy_handlers.db.session.commit"):
            resp = client.post(
                "/referencias",
                data={
                    "candidata_id": "99",
                    "referencias_laboral": "Laboral sincronizada",
                    "referencias_familiares": "Familiar sincronizada",
                },
                follow_redirects=False,
            )

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Referencias actualizadas" in body
    assert cand.referencias_laboral == "Laboral sincronizada"
    assert cand.referencias_familiares == "Familiar sincronizada"
    assert cand.contactos_referencias_laborales == "Laboral sincronizada"
    assert cand.referencias_familiares_detalle == "Familiar sincronizada"


def test_buscar_prioriza_ultima_fila_editada_en_resultados():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    fila_a = _build_candidata_stub(fila=10)
    fila_b = _build_candidata_stub(fila=20)
    fila_b.nombre_completo = "Demo B"

    with client.session_transaction() as sess:
        sess["last_edited_candidata_fila"] = 20

    with flask_app.app_context():
        with patch(
            "core.legacy_handlers.search_candidatas_limited",
            return_value=[fila_a, fila_b],
        ):
            resp = client.get("/buscar?busqueda=demo", follow_redirects=False)

    body = resp.get_data(as_text=True)
    ids = re.findall(r"\?candidata_id=(\d+)", body)
    ordered = []
    for cid in ids:
        if cid not in ordered:
            ordered.append(cid)
    assert resp.status_code == 200
    assert ordered and ordered[0] == "20"


def test_referencias_prioriza_ultima_fila_editada_en_resultados():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    fila_a = _build_candidata_stub(fila=10)
    fila_b = _build_candidata_stub(fila=20)
    fila_a.nombre_completo = "Alpha"
    fila_b.nombre_completo = "Beta"

    with client.session_transaction() as sess:
        sess["last_edited_candidata_fila"] = 20

    with flask_app.app_context():
        with patch(
            "core.legacy_handlers.search_candidatas_limited",
            return_value=[fila_a, fila_b],
        ):
            resp = client.post(
                "/referencias",
                data={"busqueda": "demo"},
                follow_redirects=False,
            )

    body = resp.get_data(as_text=True)
    ids = re.findall(r"/referencias\?candidata=(\d+)", body)
    assert resp.status_code == 200
    assert ids and ids[0] == "20"


def test_buscar_template_no_reordena_por_id_asc():
    with open("templates/buscar.html", "r", encoding="utf-8") as fh:
        tpl = fh.read()
    assert "order: [[0,'asc']]" not in tpl
    assert "order: []" in tpl


def test_buscar_post_trace_loguea_form_y_asignacion(caplog, monkeypatch):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    monkeypatch.setenv("LEGACY_BUSCAR_TRACE", "1")
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = _build_candidata_stub(fila=123)
    with flask_app.app_context():
        with patch("core.legacy_handlers.get_candidata_by_id", return_value=cand), \
             patch("core.legacy_handlers._get_candidata_by_fila_or_pk", return_value=cand), \
             patch("core.legacy_handlers.db.session.flush"), \
             patch("core.legacy_handlers.db.session.commit"), \
             caplog.at_level("INFO"):
            resp = client.post(
                "/buscar",
                data={
                    "guardar_edicion": "1",
                    "candidata_id": "123",
                    "nombre": "Demo",
                    "edad": "30",
                    "telefono": "8099990000",
                    "direccion": "Santiago",
                    "modalidad": "salida diaria",
                    "rutas": "Centro",
                    "empleo_anterior": "Casa A",
                    "anos_experiencia": "2",
                    "areas_experiencia": "limpieza",
                    "contactos_referencias_laborales": "Ref laboral vieja",
                    "referencias_familiares_detalle": "Ref familiar vieja",
                },
                follow_redirects=False,
            )

    assert resp.status_code in (302, 303)
    logs = "\n".join(r.getMessage() for r in caplog.records if "legacy.buscar." in r.getMessage())
    assert "legacy.buscar.post_form_snapshot" in logs
    assert '"telefono"' in logs
    assert "legacy.buscar.post_field_apply" in logs
    assert '"field": "numero_telefono"' in logs
    assert '"received": "8099990000"' in logs
    assert "legacy.buscar.post_before_persist" in logs
    assert "legacy.buscar.post_after_persist" in logs


def test_buscar_edicion_muestra_mensaje_error_en_modo_editar():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = _build_candidata_stub(fila=222)
    with flask_app.app_context():
        with patch("core.legacy_handlers.get_candidata_by_id", return_value=cand), \
             patch("core.legacy_handlers._get_candidata_by_fila_or_pk", return_value=cand), \
             patch("core.legacy_handlers.db.session.flush"), \
             patch("core.legacy_handlers.db.session.commit"):
            resp = client.post(
                "/buscar",
                data={
                    "guardar_edicion": "1",
                    "candidata_id": "222",
                    "cedula": "---",
                },
                follow_redirects=False,
            )

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Editar Candidata" in body
    assert "Cédula inválida" in body
    assert 'value="---"' in body


def test_buscar_cedula_invalida_no_bloquea_guardado_de_otros_campos():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = _build_candidata_stub(fila=226)
    with flask_app.app_context():
        with patch("core.legacy_handlers.get_candidata_by_id", return_value=cand), \
             patch("core.legacy_handlers._get_candidata_by_fila_or_pk", return_value=cand), \
             patch("core.legacy_handlers.db.session.flush"), \
             patch("core.legacy_handlers.db.session.commit"):
            resp = client.post(
                "/buscar",
                data={
                    "guardar_edicion": "1",
                    "candidata_id": "226",
                    "cedula": "---",
                    "telefono": "8091239999",
                },
                follow_redirects=False,
            )

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Cédula inválida" in body
    assert cand.numero_telefono == "8091239999"
    assert cand.cedula == "001-0000000-1"
    assert 'value="---"' in body


def test_buscar_cedula_duplicada_no_bloquea_guardado_de_otros_campos():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = _build_candidata_stub(fila=227)
    dup = _build_candidata_stub(fila=999)
    with flask_app.app_context():
        with patch("core.legacy_handlers.get_candidata_by_id", return_value=cand), \
             patch("core.legacy_handlers._get_candidata_by_fila_or_pk", return_value=cand), \
             patch("core.legacy_handlers.find_duplicate_candidata_by_cedula", return_value=(dup, "00100000001")), \
             patch("core.legacy_handlers.db.session.flush"), \
             patch("core.legacy_handlers.db.session.commit"):
            resp = client.post(
                "/buscar",
                data={
                    "guardar_edicion": "1",
                    "candidata_id": "227",
                    "cedula": "001-0000000-1",
                    "telefono": "8095551212",
                },
                follow_redirects=False,
            )

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Cédula duplicada" in body
    assert cand.numero_telefono == "8095551212"
    assert cand.cedula == "001-0000000-1"


def test_buscar_editar_guardar_y_reabrir_muestra_valor_actualizado():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = _build_candidata_stub(fila=333)
    with flask_app.app_context():
        with patch("core.legacy_handlers.get_candidata_by_id", return_value=cand), \
             patch("core.legacy_handlers._get_candidata_by_fila_or_pk", return_value=cand), \
             patch("core.legacy_handlers.db.session.flush"), \
             patch("core.legacy_handlers.db.session.commit"):
            resp = client.post(
                "/buscar",
                data={
                    "guardar_edicion": "1",
                    "candidata_id": "333",
                    "nombre": "Demo",
                    "edad": "30",
                    "telefono": "8090001234",
                    "direccion": "Santiago",
                    "modalidad": "salida diaria",
                    "rutas": "Centro",
                    "empleo_anterior": "Casa A",
                    "anos_experiencia": "2",
                    "areas_experiencia": "limpieza",
                    "contactos_referencias_laborales": "Ref laboral vieja",
                    "referencias_familiares_detalle": "Ref familiar vieja",
                },
                follow_redirects=True,
            )

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert cand.numero_telefono == "8090001234"
    assert 'id="telefono"' in body
    assert 'value="8090001234"' in body
