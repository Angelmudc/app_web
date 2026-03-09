# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.exc import OperationalError

from app import app as flask_app
from utils.robust_save import execute_robust_save


class _DummySession:
    def __init__(self):
        self.commit_calls = 0
        self.rollback_calls = 0

    def flush(self):
        return None

    def commit(self):
        self.commit_calls += 1
        return None

    def rollback(self):
        self.rollback_calls += 1
        return None


def _login_secretaria(client):
    return client.post('/admin/login', data={'usuario': 'Karla', 'clave': '9989'}, follow_redirects=False)


def test_form_save_normal_success():
    sess = _DummySession()
    state = {'saved': False}

    def _persist(_attempt: int):
        state['saved'] = True

    result = execute_robust_save(
        session=sess,
        persist_fn=_persist,
        verify_fn=lambda: bool(state['saved']),
    )

    assert result.ok is True
    assert result.attempts == 1
    assert sess.commit_calls == 1


def test_form_save_retry_on_transient_commit_error():
    class _RetrySession(_DummySession):
        def commit(self):
            self.commit_calls += 1
            if self.commit_calls == 1:
                raise OperationalError('select 1', {}, Exception('transient'))
            return None

    sess = _RetrySession()

    result = execute_robust_save(
        session=sess,
        persist_fn=lambda _attempt: None,
        verify_fn=lambda: True,
    )

    assert result.ok is True
    assert result.attempts == 2
    assert sess.commit_calls == 2
    assert sess.rollback_calls == 1


def test_form_save_rollbacks_when_error_happens():
    class _FailSession(_DummySession):
        def commit(self):
            self.commit_calls += 1
            raise RuntimeError('boom')

    sess = _FailSession()
    result = execute_robust_save(
        session=sess,
        persist_fn=lambda _attempt: None,
        verify_fn=lambda: True,
        max_retries=0,
    )

    assert result.ok is False
    assert sess.rollback_calls == 1


def test_form_save_post_commit_verification_required():
    sess = _DummySession()
    result = execute_robust_save(
        session=sess,
        persist_fn=lambda _attempt: None,
        verify_fn=lambda: False,
        max_retries=0,
    )

    assert result.ok is False
    assert 'verificar' in (result.error_message or '').lower()


def test_form_save_failure_is_visible_to_user():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    assert _login_secretaria(client).status_code in (302, 303)

    cand = SimpleNamespace(
        fila=91,
        estado='lista_para_trabajar',
        nombre_completo='Demo',
        fecha_cambio_estado=None,
        usuario_cambio_estado=None,
        nota_descalificacion=None,
    )

    class _CandidataQuery:
        def filter_by(self, **kwargs):
            return self

        def first_or_404(self):
            return cand

        def first(self):
            return cand

    with flask_app.app_context():
        with patch('admin.routes.Candidata.query', _CandidataQuery()), \
             patch('admin.routes._execute_form_save', return_value=SimpleNamespace(ok=False, attempts=3, error_message='forced')):
            resp = client.post(
                '/admin/candidatas/91/marcar_trabajando',
                data={'next': '/admin/solicitudes'},
                follow_redirects=False,
            )

    assert resp.status_code in (302, 303)
    with client.session_transaction() as sess:
        flashes = list(sess.get('_flashes', []))
    assert any('No se pudo guardar correctamente. Intente nuevamente.' in msg for _cat, msg in flashes)
