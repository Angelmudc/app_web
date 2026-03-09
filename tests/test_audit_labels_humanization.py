# -*- coding: utf-8 -*-

from datetime import datetime

from app import app as flask_app
from config_app import db
from models import StaffAuditLog, StaffUser
from utils.audit_labels import humanize_audit_field, humanize_change


def _login(client, usuario, clave):
    return client.post('/admin/login', data={'usuario': usuario, 'clave': clave}, follow_redirects=False)


def test_audit_labels_mapping_and_fallback():
    assert humanize_audit_field('anos_experiencia') == 'Años de experiencia'
    assert humanize_audit_field('referencias_familiares_detalle') == 'Referencias familiares'
    assert humanize_audit_field('ruta_cercana_1') == 'Ruta cercana'
    assert humanize_audit_field('acepta_porcentaje_sueldo') == '¿Acepta porcentaje del sueldo?'
    assert humanize_audit_field('campo_no_mapeado_demo') == 'Campo no mapeado demo'


def test_monitoreo_logs_json_humanizes_candidate_edit_changes():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False

    client = flask_app.test_client()
    assert _login(client, 'Cruz', '8998').status_code in (302, 303)

    with flask_app.app_context():
        admin = StaffUser.query.filter_by(username='Cruz').first()
        assert admin is not None
        db.session.add(
            StaffAuditLog(
                created_at=datetime.utcnow(),
                actor_user_id=admin.id,
                actor_role='admin',
                action_type='CANDIDATA_EDIT',
                entity_type='candidata',
                entity_id='909',
                summary='edicion multiple',
                metadata_json={},
                changes_json={
                    'anos_experiencia': {'from': '2', 'to': '5'},
                    'cedula': {'from': '001-0000000-1', 'to': '001-0000000-9'},
                    'referencias_familiares_detalle': {'from': 'ana', 'to': 'maria'},
                },
                success=True,
            )
        )
        db.session.commit()

    resp = client.get('/admin/monitoreo/logs.json?action_type=CANDIDATA_EDIT&limit=20', follow_redirects=False)
    assert resp.status_code == 200
    data = resp.get_json() or {}
    items = data.get('items') or []
    assert items

    item = next((x for x in reversed(items) if str(x.get('entity_id')) == '909'), None)
    assert item is not None

    assert str(item.get('action_human') or '').startswith('Edito candidata')
    changes_human = item.get('changes_human') or []
    labels = {str(c.get('label')) for c in changes_human}
    assert 'Años de experiencia' in labels
    assert 'Cedula' in labels
    assert 'Referencias familiares' in labels

    cedula_change = next((c for c in changes_human if c.get('label') == 'Cedula'), {})
    assert cedula_change.get('to') == 'actualizado'
    assert bool(cedula_change.get('sensitive')) is True


def test_sensitive_change_masks_values():
    row = humanize_change('numero_telefono', '8090000000', '8099999999')
    assert row.get('sensitive') is True
    assert row.get('from') == 'dato protegido'
    assert row.get('to') == 'actualizado'
