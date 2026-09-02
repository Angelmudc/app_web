# -*- coding: utf-8 -*-

import os

from sqlalchemy import event
from werkzeug.security import generate_password_hash

from app import app as flask_app
from config_app import db
from models import Cliente, StaffUser


def _new_cliente(*, codigo: str, nombre: str, email: str, telefono: str) -> Cliente:
    row = Cliente(
        codigo=codigo,
        nombre_completo=nombre,
        email=email,
        telefono=telefono,
        username=f"user_{codigo.lower().replace('-', '_')}",
        password_hash=generate_password_hash("cliente12345", method="pbkdf2:sha256"),
        is_active=True,
        role="cliente",
        total_solicitudes=0,
    )
    db.session.add(row)
    return row


def test_admin_clientes_search_phone_digits_matches_formatted_phone():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"

    with flask_app.app_context():
        Cliente.__table__.drop(bind=db.engine, checkfirst=True)
        StaffUser.__table__.drop(bind=db.engine, checkfirst=True)
        StaffUser.__table__.create(bind=db.engine, checkfirst=True)
        Cliente.__table__.create(bind=db.engine, checkfirst=True)

        owner = StaffUser(
            username="owner_test",
            email="owner_test@test.local",
            role="owner",
            is_active=True,
            mfa_enabled=False,
        )
        owner.password_hash = generate_password_hash("admin12345", method="pbkdf2:sha256")
        db.session.add(owner)

        _new_cliente(
            codigo="CL-001",
            nombre="Cliente Uno",
            email="cliente1@test.local",
            telefono="809-555-0001",
        )
        _new_cliente(
            codigo="CL-002",
            nombre="Cliente Dos",
            email="cliente2@test.local",
            telefono="809-555-0002",
        )
        db.session.commit()

    client = flask_app.test_client()
    login = client.post("/admin/login", data={"usuario": "owner_test", "clave": "admin12345"}, follow_redirects=False)
    assert login.status_code in (302, 303)

    resp = client.get("/admin/clientes?q=8095550002&page=1&per_page=25", follow_redirects=False)
    assert resp.status_code == 200
    html = (resp.get_data(as_text=True) or "")
    assert "Cliente Dos" in html
    assert "Cliente Uno" not in html


def test_admin_clientes_search_does_not_reload_each_row():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"

    with flask_app.app_context():
        Cliente.__table__.drop(bind=db.engine, checkfirst=True)
        StaffUser.__table__.drop(bind=db.engine, checkfirst=True)
        StaffUser.__table__.create(bind=db.engine, checkfirst=True)
        Cliente.__table__.create(bind=db.engine, checkfirst=True)

        owner = StaffUser(
            username="owner_test_search",
            email="owner_test_search@test.local",
            role="owner",
            is_active=True,
            mfa_enabled=False,
        )
        owner.password_hash = generate_password_hash("admin12345", method="pbkdf2:sha256")
        db.session.add(owner)

        for idx in range(1, 6):
            _new_cliente(
                codigo=f"PERF-{idx:03d}",
                nombre=f"Cliente Perf {idx}",
                email=f"cliente_perf_{idx}@test.local",
                telefono=f"809-555-1{idx:03d}",
            )
        db.session.commit()

    client = flask_app.test_client()
    login = client.post("/admin/login", data={"usuario": "owner_test_search", "clave": "admin12345"}, follow_redirects=False)
    assert login.status_code in (302, 303)

    clientes_statements: list[str] = []

    def _before_cursor_execute(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "from clientes" in statement.lower():
            clientes_statements.append(statement)

    with flask_app.app_context():
        event.listen(db.engine, "before_cursor_execute", _before_cursor_execute)
        try:
            resp = client.get("/admin/clientes?q=PERF&page=1&per_page=10", follow_redirects=False)
        finally:
            event.remove(db.engine, "before_cursor_execute", _before_cursor_execute)

    assert resp.status_code == 200
    html = (resp.get_data(as_text=True) or "")
    assert "Cliente Perf 1" in html
    assert "Cliente Perf 5" in html
    assert len(clientes_statements) <= 2


def test_admin_clientes_without_query_does_not_auto_list_clients():
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    os.environ["ADMIN_LEGACY_ENABLED"] = "1"

    with flask_app.app_context():
        Cliente.__table__.drop(bind=db.engine, checkfirst=True)
        StaffUser.__table__.drop(bind=db.engine, checkfirst=True)
        StaffUser.__table__.create(bind=db.engine, checkfirst=True)
        Cliente.__table__.create(bind=db.engine, checkfirst=True)

        owner = StaffUser(
            username="owner_test_empty",
            email="owner_test_empty@test.local",
            role="owner",
            is_active=True,
            mfa_enabled=False,
        )
        owner.password_hash = generate_password_hash("admin12345", method="pbkdf2:sha256")
        db.session.add(owner)

        _new_cliente(
            codigo="CL-100",
            nombre="Cliente Cien",
            email="cliente100@test.local",
            telefono="809-555-0100",
        )
        db.session.commit()

    client = flask_app.test_client()
    login = client.post("/admin/login", data={"usuario": "owner_test_empty", "clave": "admin12345"}, follow_redirects=False)
    assert login.status_code in (302, 303)

    resp = client.get("/admin/clientes", follow_redirects=False)
    assert resp.status_code == 200
    html = (resp.get_data(as_text=True) or "")
    assert "Busca un cliente por nombre, teléfono o cédula." in html
    assert "Cliente Cien" not in html
