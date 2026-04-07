# -*- coding: utf-8 -*-
import base64
import hashlib

import pytest
from cryptography.fernet import Fernet

from models import StaffUser


def _legacy_key_from_flask_secret(flask_secret: str) -> str:
    digest = hashlib.sha256((flask_secret or "").encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8")


def test_get_mfa_secret_reads_encrypted_secret_with_explicit_key(monkeypatch):
    key = Fernet.generate_key().decode("utf-8")
    monkeypatch.setenv("STAFF_MFA_ENCRYPTION_KEY", key)
    monkeypatch.setenv("FLASK_SECRET_KEY", "legacy-should-not-be-used")

    token = Fernet(key.encode("utf-8")).encrypt(b"JBSWY3DPEHPK3PXP").decode("utf-8")
    user = StaffUser(username="cipher_explicit", password_hash="x", role="admin", is_active=True)
    user.mfa_secret = f"enc:{token}"

    assert user.get_mfa_secret() == "JBSWY3DPEHPK3PXP"


def test_get_mfa_secret_falls_back_to_legacy_flask_key_when_needed(monkeypatch):
    monkeypatch.delenv("STAFF_MFA_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("FLASK_SECRET_KEY", "legacy-flask-secret")

    legacy_key = _legacy_key_from_flask_secret("legacy-flask-secret")
    token = Fernet(legacy_key.encode("utf-8")).encrypt(b"JBSWY3DPEHPK3PXP").decode("utf-8")
    user = StaffUser(username="cipher_legacy", password_hash="x", role="admin", is_active=True)
    user.mfa_secret = f"enc:{token}"

    assert user.get_mfa_secret() == "JBSWY3DPEHPK3PXP"


def test_set_mfa_secret_fails_without_explicit_key(monkeypatch):
    monkeypatch.delenv("STAFF_MFA_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("STAFF_MFA_ALLOW_PLAINTEXT_SECRET", raising=False)
    monkeypatch.setenv("FLASK_SECRET_KEY", "legacy-only-not-allowed-for-write")

    user = StaffUser(username="cipher_write_fail", password_hash="x", role="admin", is_active=True)
    with pytest.raises(RuntimeError) as exc:
        user.set_mfa_secret("JBSWY3DPEHPK3PXP")

    assert (
        str(exc.value)
        == "STAFF_MFA_ENCRYPTION_KEY is required to write MFA secrets. "
        "Legacy FLASK_SECRET_KEY fallback is read-only."
    )


def test_set_mfa_secret_writes_with_explicit_key(monkeypatch):
    key = Fernet.generate_key().decode("utf-8")
    monkeypatch.setenv("STAFF_MFA_ENCRYPTION_KEY", key)
    monkeypatch.setenv("FLASK_SECRET_KEY", "legacy-does-not-matter")

    user = StaffUser(username="cipher_write_ok", password_hash="x", role="admin", is_active=True)
    user.set_mfa_secret("JBSWY3DPEHPK3PXP")

    assert str(user.mfa_secret or "").startswith("enc:")
    assert user.get_mfa_secret() == "JBSWY3DPEHPK3PXP"
