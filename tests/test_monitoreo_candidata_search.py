# -*- coding: utf-8 -*-

from app import app as flask_app
from config_app import db
from models import StaffAuditLog, StaffUser
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
import admin.routes as admin_routes


def _login(client, usuario, clave):
    return client.post('/admin/login', data={'usuario': usuario, 'clave': clave}, follow_redirects=False)


def test_monitoreo_candidata_search_admin_only():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False

    client_admin = flask_app.test_client()
    client_sec = flask_app.test_client()

    with flask_app.app_context():
        sec = StaffUser.query.filter_by(username='Karla').first()
        if sec is None:
            sec = StaffUser(username='Karla', role='secretaria', is_active=True)
            sec.set_password('9989')
            db.session.add(sec)
            db.session.commit()
        assert sec is not None

        db.session.add(
            StaffAuditLog(
                created_at=datetime.utcnow(),
                actor_user_id=sec.id,
                actor_role='secretaria',
                action_type='CANDIDATA_EDIT',
                entity_type='candidata',
                entity_id='901',
                summary='edit candidata',
                metadata_json={},
                success=True,
            )
        )
        db.session.commit()

    assert _login(client_sec, 'Karla', '9989').status_code in (302, 303)
    denied = client_sec.get('/admin/monitoreo/candidatas?q=Ana', follow_redirects=False)
    assert denied.status_code == 403

    assert _login(client_admin, 'Cruz', '8998').status_code in (302, 303)
    fake_rows = [
        SimpleNamespace(fila=901, codigo='SEA-901', nombre_completo='Ana Search', cedula='001-1234567-1', estado='lista_para_trabajar'),
        SimpleNamespace(fila=902, codigo='SEB-902', nombre_completo='Berta Search', cedula='001-9876543-1', estado='trabajando'),
    ]
    query_mock = SimpleNamespace(
        filter=lambda *args, **kwargs: SimpleNamespace(
            order_by=lambda *a, **k: SimpleNamespace(limit=lambda *la, **lk: SimpleNamespace(all=lambda: fake_rows))
        )
    )
    fake_model = SimpleNamespace(
        query=query_mock,
        nombre_completo=SimpleNamespace(ilike=lambda *_: None),
        cedula=SimpleNamespace(ilike=lambda *_: None),
        codigo=SimpleNamespace(ilike=lambda *_: None),
        fila=SimpleNamespace(desc=lambda: None),
        cedula_norm_digits=SimpleNamespace(ilike=lambda *_: None),
    )
    with patch.object(admin_routes, 'Candidata', fake_model), \
         patch.object(admin_routes, 'cast', lambda *args, **kwargs: SimpleNamespace(ilike=lambda *_: None)):
        ok = client_admin.get('/admin/monitoreo/candidatas?q=Ana Search', follow_redirects=False)
    assert ok.status_code == 200
    body = ok.data.decode('utf-8', errors='ignore')
    assert 'Ana Search' in body
    assert 'Ver historial' in body
