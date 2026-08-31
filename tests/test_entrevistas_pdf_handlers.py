from types import SimpleNamespace
from unittest.mock import patch

from flask import Response, url_for

from app import app as flask_app
from config_app import db
from models import Candidata, Entrevista, EntrevistaPregunta, EntrevistaReferencia, EntrevistaRespuesta
from core.services import pdf as pdf_service
from core.services.interview_references import collect_entrevista_reference_items, sync_entrevista_referencias_from_answers
from tests.t1_testkit import ensure_sqlite_compat_tables
from utils.timezone import utc_now_naive


def _login_secretaria(client):
    return client.post('/admin/login', data={'usuario': 'Karla', 'clave': '9989'}, follow_redirects=False)


def test_pdf_routes_apuntan_a_handler_nuevo_y_url_for_se_mantiene():
    endpoints = [
        'generar_pdf_entrevista_db',
        'generar_pdf_entrevista',
        'generar_pdf_entrevista_nueva_db',
        'generar_pdf_ultima_entrevista_candidata',
    ]
    for ep in endpoints:
        fn = flask_app.view_functions.get(ep)
        assert fn is not None
        assert fn.__module__ == 'core.handlers.entrevistas_pdf_handlers'

    with flask_app.test_request_context('/'):
        assert url_for('generar_pdf_entrevista_db', entrevista_id=7) == '/entrevistas/pdf/7'
        assert url_for('generar_pdf_entrevista', fila=9) == '/generar_pdf_entrevista?fila=9'
        assert url_for('generar_pdf_entrevista_nueva_db', entrevista_id=7) == '/entrevistas/pdf_nuevo/7'
        assert url_for('generar_pdf_ultima_entrevista_candidata', fila=9) == '/entrevistas/candidata/9/pdf'


def test_core_services_pdf_exporta_funciones_del_handler_nuevo():
    assert pdf_service.generar_pdf_entrevista_db.__module__ == 'core.handlers.entrevistas_pdf_handlers'
    assert pdf_service.generar_pdf_entrevista.__module__ == 'core.handlers.entrevistas_pdf_handlers'
    assert pdf_service.generar_pdf_ultima_entrevista_candidata.__module__ == 'core.handlers.entrevistas_pdf_handlers'


def test_generar_pdf_entrevista_400_y_404():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    resp_400 = client.get('/generar_pdf_entrevista', follow_redirects=False)
    assert resp_400.status_code == 400

    with flask_app.app_context():
        with patch('core.handlers.entrevistas_pdf_handlers.legacy_h._get_candidata_by_fila_or_pk', return_value=None):
            resp_404 = client.get('/generar_pdf_entrevista?fila=1', follow_redirects=False)
    assert resp_404.status_code == 404


def test_generar_pdf_entrevista_respuesta_pdf_binaria():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = SimpleNamespace(
        fila=1,
        entrevista='Pregunta: respuesta útil',
        referencias_laboral='Ref laboral',
        referencias_familiares='Ref familiar',
    )
    fake_pdf_resp = Response(b'%PDF-demo', mimetype='application/pdf')

    with flask_app.app_context():
        with patch('core.handlers.entrevistas_pdf_handlers.legacy_h._get_candidata_by_fila_or_pk', return_value=cand), \
             patch('core.handlers.entrevistas_pdf_handlers.send_file', return_value=fake_pdf_resp):
            resp = client.get('/generar_pdf_entrevista?fila=1', follow_redirects=False)

    assert resp.status_code == 200
    assert resp.mimetype == 'application/pdf'


def test_pdf_referencias_entrevista_prioriza_referencias_explicitas_sobre_respuestas_estructuradas():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False

    with flask_app.app_context():
        ensure_sqlite_compat_tables([Candidata, Entrevista, EntrevistaPregunta, EntrevistaReferencia, EntrevistaRespuesta], reset=False)
        db.session.query(EntrevistaRespuesta).delete(synchronize_session=False)
        db.session.query(EntrevistaReferencia).delete(synchronize_session=False)
        db.session.query(EntrevistaPregunta).filter(EntrevistaPregunta.clave.like('domestica.%')).delete(synchronize_session=False)
        db.session.query(Entrevista).filter(Entrevista.candidata_id == 991101).delete(synchronize_session=False)
        db.session.query(Candidata).filter(Candidata.fila == 991101).delete(synchronize_session=False)
        cand = Candidata(
            fila=991101,
            nombre_completo='Ana PDF',
            referencias_laboral='CAND-LAB',
            referencias_familiares='CAND-FAM',
        )
        db.session.add(cand)
        db.session.add_all([
            EntrevistaPregunta(clave='domestica.referencia_laboral', texto='Referencia laboral mencionada', tipo='texto', orden=1, activa=True),
            EntrevistaPregunta(clave='domestica.referencia_familiar', texto='Referencia familiar mencionada', tipo='texto', orden=2, activa=True),
        ])
        db.session.flush()
        entrevista = Entrevista(candidata_id=991101, tipo='domestica', estado='completa', creada_en=utc_now_naive())
        db.session.add(entrevista)
        db.session.flush()
        qlab = EntrevistaPregunta.query.filter_by(clave='domestica.referencia_laboral').first()
        qfam = EntrevistaPregunta.query.filter_by(clave='domestica.referencia_familiar').first()
        db.session.add_all([
            EntrevistaReferencia(
                entrevista_id=entrevista.id,
                tipo='laboral',
                texto='LAB-EXPLICITA',
                datos_json={'texto': 'LAB-EXPLICITA', 'origen': 'explicita'},
                creada_en=utc_now_naive(),
            ),
            EntrevistaReferencia(
                entrevista_id=entrevista.id,
                tipo='familiar',
                texto='FAM-EXPLICITA',
                datos_json={'texto': 'FAM-EXPLICITA', 'origen': 'explicita'},
                creada_en=utc_now_naive(),
            ),
            EntrevistaRespuesta(entrevista_id=entrevista.id, pregunta_id=qlab.id, respuesta='INT-LAB', creada_en=utc_now_naive()),
            EntrevistaRespuesta(entrevista_id=entrevista.id, pregunta_id=qfam.id, respuesta='INT-FAM', creada_en=utc_now_naive()),
        ])
        db.session.commit()
        collected = collect_entrevista_reference_items(entrevista)

    assert [(item["tipo"], item["respuesta"]) for item in collected] == [('laboral', 'LAB-EXPLICITA'), ('familiar', 'FAM-EXPLICITA')]


def test_pdf_referencias_entrevista_fallback_historico_usa_respuestas_estructuradas_si_no_hay_explicitas():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False

    with flask_app.app_context():
        ensure_sqlite_compat_tables([Candidata, Entrevista, EntrevistaPregunta, EntrevistaReferencia, EntrevistaRespuesta], reset=False)
        db.session.query(EntrevistaRespuesta).delete(synchronize_session=False)
        db.session.query(EntrevistaReferencia).delete(synchronize_session=False)
        db.session.query(EntrevistaPregunta).filter(EntrevistaPregunta.clave.like('domestica.%')).delete(synchronize_session=False)
        db.session.query(Entrevista).filter(Entrevista.candidata_id == 991102).delete(synchronize_session=False)
        db.session.query(Candidata).filter(Candidata.fila == 991102).delete(synchronize_session=False)
        cand = Candidata(
            fila=991102,
            nombre_completo='Ana PDF Fallback',
            referencias_laboral='CAND-LAB',
            referencias_familiares='CAND-FAM',
        )
        db.session.add(cand)
        db.session.add(
            EntrevistaPregunta(
                clave='domestica.referencia_laboral',
                texto='Referencia laboral mencionada',
                tipo='texto',
                orden=1,
                activa=True,
            )
        )
        entrevista = Entrevista(candidata_id=991102, tipo='domestica', estado='completa', creada_en=utc_now_naive())
        db.session.add(entrevista)
        db.session.flush()
        pregunta = EntrevistaPregunta.query.filter_by(clave='domestica.referencia_laboral').first()
        db.session.add(
            EntrevistaRespuesta(
                entrevista_id=entrevista.id,
                pregunta_id=pregunta.id,
                respuesta='Cinco años en casa de familia.',
                creada_en=utc_now_naive(),
            )
        )
        db.session.commit()
        collected = collect_entrevista_reference_items(entrevista)

    assert [(item["tipo"], item["respuesta"]) for item in collected] == [('laboral', 'Cinco años en casa de familia.')]


def test_sync_entrevista_referencias_crea_actualiza_y_borra_sin_duplicar():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False

    with flask_app.app_context():
        ensure_sqlite_compat_tables([Candidata, Entrevista, EntrevistaPregunta, EntrevistaReferencia, EntrevistaRespuesta], reset=False)
        db.session.query(EntrevistaRespuesta).delete(synchronize_session=False)
        db.session.query(EntrevistaReferencia).delete(synchronize_session=False)
        db.session.query(EntrevistaPregunta).filter(EntrevistaPregunta.clave.like('domestica.%')).delete(synchronize_session=False)
        db.session.query(Entrevista).filter(Entrevista.candidata_id == 991103).delete(synchronize_session=False)
        db.session.query(Candidata).filter(Candidata.fila == 991103).delete(synchronize_session=False)
        cand = Candidata(fila=991103, nombre_completo='Ana Sync')
        db.session.add(cand)
        db.session.add_all([
            EntrevistaPregunta(clave='domestica.referencia_laboral', texto='Referencia laboral', tipo='texto', orden=1, activa=True),
            EntrevistaPregunta(clave='domestica.referencia_familiar', texto='Referencia familiar', tipo='texto', orden=2, activa=True),
        ])
        entrevista = Entrevista(candidata_id=991103, tipo='domestica', estado='completa', creada_en=utc_now_naive())
        db.session.add(entrevista)
        db.session.flush()
        preguntas = EntrevistaPregunta.query.order_by(EntrevistaPregunta.orden.asc()).all()

        sync_entrevista_referencias_from_answers(
            session=db.session,
            entrevista=entrevista,
            preguntas=preguntas,
            respuestas_payload={int(preguntas[0].id): 'LAB-1', int(preguntas[1].id): 'FAM-1'},
        )
        db.session.flush()
        sync_entrevista_referencias_from_answers(
            session=db.session,
            entrevista=entrevista,
            preguntas=preguntas,
            respuestas_payload={int(preguntas[0].id): 'LAB-2'},
        )
        db.session.commit()
        rows = EntrevistaReferencia.query.filter_by(entrevista_id=entrevista.id).order_by(EntrevistaReferencia.tipo.asc()).all()

    assert [(row.tipo, row.texto) for row in rows] == [('laboral', 'LAB-2')]


def test_pdf_nuevo_alias_delega_en_pdf_db():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    with flask_app.app_context():
        with patch('core.handlers.entrevistas_pdf_handlers.generar_pdf_entrevista_db', return_value=('ok', 200)) as db_pdf:
            resp = client.get('/entrevistas/pdf_nuevo/7', follow_redirects=False)

    assert resp.status_code == 200
    db_pdf.assert_called_once_with(7)


def test_pdf_ultima_entrevista_redirect_y_404():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    class _QueryFirst:
        def filter(self, *_a, **_k):
            return self

        def order_by(self, *_a, **_k):
            return self

        def first(self):
            return SimpleNamespace(id=12)

    class _QueryNone(_QueryFirst):
        def first(self):
            return None

    with flask_app.app_context():
        with patch('core.handlers.entrevistas_pdf_handlers.legacy_h.db.session.query', return_value=_QueryFirst()):
            resp_redirect = client.get('/entrevistas/candidata/1/pdf', follow_redirects=False)
    assert resp_redirect.status_code in (302, 303)
    assert '/entrevistas/pdf/12' in (resp_redirect.location or '')

    with flask_app.app_context():
        with patch('core.handlers.entrevistas_pdf_handlers.legacy_h.db.session.query', return_value=_QueryNone()):
            resp_404 = client.get('/entrevistas/candidata/1/pdf', follow_redirects=False)
    assert resp_404.status_code == 404
