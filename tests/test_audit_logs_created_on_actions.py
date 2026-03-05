# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import patch

from app import app as flask_app
from config_app import db
from models import StaffAuditLog, StaffUser


def _login(client, usuario, clave):
    return client.post('/admin/login', data={'usuario': usuario, 'clave': clave}, follow_redirects=False)


def _clear_logs():
    with flask_app.app_context():
        db.session.query(StaffAuditLog).delete()
        db.session.commit()


def test_audit_logs_created_on_actions():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()

    _clear_logs()
    resp_login = _login(client, 'Cruz', '8998')
    assert resp_login.status_code in (302, 303)

    with flask_app.app_context():
        actor = StaffUser.query.filter_by(username='Cruz').first()
        assert actor is not None
        actor_id = actor.id

    candidata_stub = SimpleNamespace(
        fila=15,
        nombre_completo='Ana Original',
        edad='28',
        numero_telefono='8090000000',
        direccion_completa='Santiago',
        modalidad_trabajo_preferida='con dormida',
        rutas_cercanas='Centro',
        empleo_anterior='',
        anos_experiencia='2',
        areas_experiencia='limpieza',
        contactos_referencias_laborales='',
        referencias_familiares_detalle='',
        cedula='001-0000000-1',
        sabe_planchar=False,
        acepta_porcentaje_sueldo=False,
    )

    with flask_app.app_context():
        with patch('core.legacy_handlers.get_candidata_by_id', return_value=candidata_stub), \
             patch('core.legacy_handlers.db.session.commit'):
            resp_edit = client.post(
                '/buscar',
                data={
                    'guardar_edicion': '1',
                    'candidata_id': '15',
                    'nombre': 'Ana Editada',
                    'telefono': '8091111111',
                },
                follow_redirects=False,
            )
    assert resp_edit.status_code in (302, 303)

    solicitud_stub = SimpleNamespace(id=10, codigo_solicitud='SOL-10')
    candidata_match = SimpleNamespace(fila=101, nombre_completo='Candidata Match', estado='lista_para_trabajar')

    class _SolicitudQuery:
        def filter_by(self, **kwargs):
            return self

        def first_or_404(self):
            return solicitud_stub

    class _CandidataQuery:
        def filter(self, *args, **kwargs):
            return self

        def all(self):
            return [candidata_match]

        def filter_by(self, **kwargs):
            return self

        def first(self):
            return candidata_match

    class _SolicitudCandidataQuery:
        def filter_by(self, **kwargs):
            return self

        def first(self):
            return None

    with flask_app.app_context():
        with patch('admin.routes.Solicitud.query', _SolicitudQuery()), \
             patch('admin.routes.Candidata.query', _CandidataQuery()), \
             patch('admin.routes.SolicitudCandidata.query', _SolicitudCandidataQuery()), \
             patch('admin.routes.rank_candidates', return_value=[{'candidate': candidata_match, 'score': 88, 'breakdown_snapshot': {}}]), \
             patch('admin.routes._matching_candidate_flags', return_value=(set(), set())), \
             patch('admin.routes.candidata_esta_descalificada', return_value=False), \
             patch('admin.routes.candidata_is_ready_to_send', return_value=(True, [])), \
             patch('admin.routes._upsert_cliente_notificacion_candidatas'), \
             patch('admin.routes.db.session.commit'):
            resp_send = client.post('/admin/matching/solicitudes/10/enviar', data={'candidata_ids': ['101']}, follow_redirects=False)
    assert resp_send.status_code in (302, 303)

    cand_desc = SimpleNamespace(
        fila=88,
        nombre_completo='Desc',
        estado='lista_para_trabajar',
        nota_descalificacion=None,
        fecha_cambio_estado=None,
        usuario_cambio_estado=None,
    )

    class _CandidataQueryOne:
        def filter_by(self, **kwargs):
            return self

        def first_or_404(self):
            return cand_desc

    with flask_app.app_context():
        with patch('admin.routes.Candidata.query', _CandidataQueryOne()), \
             patch('admin.routes.db.session.commit'):
            resp_desc = client.post('/admin/candidatas/88/descalificar', data={'motivo': 'No cumple perfil'}, follow_redirects=False)
    assert resp_desc.status_code in (302, 303)

    with flask_app.app_context():
        edit_log = StaffAuditLog.query.filter_by(action_type='CANDIDATA_EDIT').order_by(StaffAuditLog.id.desc()).first()
        send_log = StaffAuditLog.query.filter_by(action_type='MATCHING_SEND').order_by(StaffAuditLog.id.desc()).first()
        send_log_solicitud = (
            StaffAuditLog.query
            .filter_by(action_type='MATCHING_SEND', entity_type='Solicitud')
            .order_by(StaffAuditLog.id.desc())
            .first()
        )
        desc_log = StaffAuditLog.query.filter_by(action_type='CANDIDATA_DESCALIFICAR').order_by(StaffAuditLog.id.desc()).first()

        assert edit_log is not None
        assert send_log is not None
        assert desc_log is not None

        assert int(edit_log.actor_user_id) == int(actor_id)
        assert int(send_log.actor_user_id) == int(actor_id)
        assert int(desc_log.actor_user_id) == int(actor_id)

        assert (edit_log.entity_type or '').lower() == 'candidata'
        assert str(edit_log.entity_id) == '15'

        assert send_log_solicitud is not None
        assert send_log_solicitud.entity_type == 'Solicitud'
        assert str(send_log_solicitud.entity_id) == '10'

        assert desc_log.entity_type == 'Candidata'
        assert str(desc_log.entity_id) == '88'
