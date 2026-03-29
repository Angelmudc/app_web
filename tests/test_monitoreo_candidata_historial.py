# -*- coding: utf-8 -*-

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
from config_app import db
from models import StaffAuditLog, StaffUser


def _login(client, usuario, clave):
    return client.post('/admin/login', data={'usuario': usuario, 'clave': clave}, follow_redirects=False)


def _async_headers():
    return {
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-Admin-Async": "1",
    }


def test_monitoreo_candidata_historial_and_filters():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        sec = StaffUser.query.filter_by(username='Karla').first()
        if sec is None:
            sec = StaffUser(username='Karla', role='secretaria', is_active=True)
            sec.set_password('9989')
            db.session.add(sec)
            db.session.commit()
        assert sec is not None
        cand_id = 777

        rows = [
            StaffAuditLog(
                created_at=datetime.utcnow(),
                actor_user_id=sec.id,
                actor_role='secretaria',
                action_type='CANDIDATA_EDIT',
                entity_type='candidata',
                entity_id=str(cand_id),
                summary='edito formulario',
                metadata_json={'telefono': '8099999999'},
                changes_json={
                    'nombre_completo': {'from': 'A', 'to': 'B'},
                    'referencias_familiares_detalle': {'from': 'Ana', 'to': 'Maria'},
                },
                success=True,
            ),
            StaffAuditLog(
                created_at=datetime.utcnow(),
                actor_user_id=sec.id,
                actor_role='secretaria',
                action_type='CANDIDATA_UPLOAD_DOCS',
                entity_type='candidata',
                entity_id=str(cand_id),
                summary='subio docs',
                metadata_json={'source': 'subir_fotos'},
                success=True,
            ),
            StaffAuditLog(
                created_at=datetime.utcnow(),
                actor_user_id=sec.id,
                actor_role='secretaria',
                action_type='MATCHING_SEND',
                entity_type='candidata',
                entity_id=str(cand_id),
                summary='envio al cliente',
                metadata_json={'solicitud_id': 99},
                success=False,
            ),
        ]
        db.session.add_all(rows)
        db.session.commit()

    assert _login(client, 'Cruz', '8998').status_code in (302, 303)

    cand_stub = SimpleNamespace(fila=cand_id, codigo='HIS-777', cedula='001-0000000-7', nombre_completo='Karla Hist', estado='lista_para_trabajar')
    with patch('admin.routes._resolve_candidata_from_entity_id', return_value=cand_stub):
        resp = client.get(f'/admin/monitoreo/candidatas/{cand_id}', follow_redirects=False)
    assert resp.status_code == 200
    body = resp.data.decode('utf-8', errors='ignore')
    assert 'Karla' in body
    assert 'CANDIDATA_EDIT' in body
    assert 'Referencias familiares: modificadas' in body
    assert '8099999999' not in body
    assert 'id="monitoreoCandidataHistorialAsyncRegion"' in body
    assert 'data-admin-async-link' in body
    assert 'data-async-target="#monitoreoCandidataHistorialAsyncRegion"' in body

    with patch('admin.routes._resolve_candidata_from_entity_id', return_value=cand_stub):
        docs = client.get(f'/admin/monitoreo/candidatas/{cand_id}?filter=docs', follow_redirects=False)
    assert docs.status_code == 200
    docs_body = docs.data.decode('utf-8', errors='ignore')
    assert 'CANDIDATA_UPLOAD_DOCS' in docs_body
    assert 'CANDIDATA_EDIT' not in docs_body

    with patch('admin.routes._resolve_candidata_from_entity_id', return_value=cand_stub):
        fails = client.get(f'/admin/monitoreo/candidatas/{cand_id}?filter=fallos', follow_redirects=False)
    assert fails.status_code == 200
    fails_body = fails.data.decode('utf-8', errors='ignore')
    assert 'MATCHING_SEND' in fails_body


def test_monitoreo_candidata_historial_get_async_devuelve_region():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    with flask_app.app_context():
        sec = StaffUser.query.filter_by(username='Karla').first()
        assert sec is not None
        cand_id = 778

        rows = [
            StaffAuditLog(
                created_at=datetime.utcnow(),
                actor_user_id=sec.id,
                actor_role='secretaria',
                action_type='CANDIDATA_EDIT',
                entity_type='candidata',
                entity_id=str(cand_id),
                summary='edito formulario',
                success=True,
            ),
            StaffAuditLog(
                created_at=datetime.utcnow(),
                actor_user_id=sec.id,
                actor_role='secretaria',
                action_type='CANDIDATA_UPLOAD_DOCS',
                entity_type='candidata',
                entity_id=str(cand_id),
                summary='subio docs',
                success=True,
            ),
        ]
        db.session.add_all(rows)
        db.session.commit()

    assert _login(client, 'Cruz', '8998').status_code in (302, 303)

    cand_stub = SimpleNamespace(fila=778, codigo='HIS-778', cedula='001-0000000-8', nombre_completo='Karla Hist 2', estado='lista_para_trabajar')
    with patch('admin.routes._resolve_candidata_from_entity_id', return_value=cand_stub):
        resp = client.get(
            '/admin/monitoreo/candidatas/778?filter=docs',
            headers=_async_headers(),
            follow_redirects=False,
        )

    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get('success') is True
    assert payload.get('update_target') == '#monitoreoCandidataHistorialAsyncRegion'
    assert '/admin/monitoreo/candidatas/778' in (payload.get('redirect_url') or '')
    assert 'filter=docs' in (payload.get('redirect_url') or '')
    html = payload.get('replace_html') or ''
    assert 'CANDIDATA_UPLOAD_DOCS' in html
    assert 'CANDIDATA_EDIT' not in html
    assert 'data-active-filter="docs"' in html
    assert 'data-initial-last-id="' in html
    assert 'data-admin-async-link' in html
    assert 'data-async-target="#monitoreoCandidataHistorialAsyncRegion"' in html


def test_monitoreo_candidata_live_sync_hooks_present_for_async_region_replace():
    js_path = Path(flask_app.root_path) / 'static' / 'js' / 'monitoreo_candidata_live.js'
    content = js_path.read_text(encoding='utf-8')
    assert "admin:content-updated" in content
    assert "#monitoreoCandidataHistorialAsyncRegion" in content
    assert "data-candidata-historial-state" in content
    assert "activeFilter = nextFilter" in content
    assert "lastId = nextLastId" in content
    assert "if (paused)" in content
    assert "stopSSE();" in content
    assert "stopPolling();" in content
