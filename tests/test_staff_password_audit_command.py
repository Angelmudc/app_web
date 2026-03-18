# -*- coding: utf-8 -*-

import uuid

from app import app as flask_app
from config_app import db
from models import StaffUser


def test_audit_staff_passwords_reports_suspicious_hashes():
    flask_app.config["TESTING"] = True

    suffix = uuid.uuid4().hex[:8]
    username = f"audit_staff_{suffix}"

    with flask_app.app_context():
        row = StaffUser(
            username=username,
            role="secretaria",
            is_active=True,
            password_hash="plain-text-password",
        )
        db.session.add(row)
        db.session.commit()

    runner = flask_app.test_cli_runner()
    result = runner.invoke(args=["audit-staff-passwords"])

    assert result.exit_code == 0
    assert "staff_total=" in result.output
    assert "suspicious_hashes=" in result.output
    assert username in result.output
    assert "not_kdf_format" in result.output
