# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
from config_app import db
from models import StaffAuditLog


def _login(client, usuario, clave):
    return client.post('/admin/login', data={'usuario': usuario, 'clave': clave}, follow_redirects=False)


def test_candidata_edit_diff_has_from_to_values():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        db.session.query(StaffAuditLog).delete()
        db.session.commit()

    login_resp = _login(client, 'Karla', '9989')
    assert login_resp.status_code in (302, 303)

    candidata_stub = SimpleNamespace(
        fila=33,
        nombre_completo='Candidata Diff',
        edad='31',
        numero_telefono='8092222222',
        direccion_completa='Santiago',
        modalidad_trabajo_preferida='sin dormida',
        rutas_cercanas='Centro',
        empleo_anterior='',
        anos_experiencia='4',
        areas_experiencia='limpieza',
        contactos_referencias_laborales='',
        referencias_familiares_detalle='',
        cedula='001-0000000-2',
        sabe_planchar=True,
        acepta_porcentaje_sueldo=True,
    )

    with flask_app.app_context():
        with patch('core.legacy_handlers.get_candidata_by_id', return_value=candidata_stub), \
             patch('core.legacy_handlers._get_candidata_by_fila_or_pk', return_value=candidata_stub), \
             patch('core.legacy_handlers.db.session.flush'), \
             patch('core.legacy_handlers.db.session.commit'):
            resp = client.post(
                '/buscar',
                data={
                    'guardar_edicion': '1',
                    'candidata_id': '33',
                    'telefono': '8099999999',
                },
                follow_redirects=False,
            )

    assert resp.status_code in (302, 303)

    with flask_app.app_context():
        log = StaffAuditLog.query.filter_by(action_type='CANDIDATA_EDIT').order_by(StaffAuditLog.id.desc()).first()
        assert log is not None
        assert isinstance(log.changes_json, dict)
        assert 'numero_telefono' in log.changes_json
        assert log.changes_json['numero_telefono']['from'] in ('<redacted>', '<hidden>') or log.changes_json['numero_telefono']['from'].startswith('***')
        assert log.changes_json['numero_telefono']['to'] in ('<redacted>', '<hidden>') or log.changes_json['numero_telefono']['to'].startswith('***')
        assert log.changes_json['numero_telefono']['from'] != '8092222222'
        assert log.changes_json['numero_telefono']['to'] != '8099999999'
