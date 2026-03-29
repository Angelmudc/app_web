from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from typing import Optional
from urllib.parse import quote


MFA_PENDING_SESSION_KEY = "_staff_mfa_pending"
MFA_SETUP_SECRET_SESSION_KEY = "_staff_mfa_setup_secret"

MFA_DIGITS = 6
MFA_PERIOD_SECONDS = 30
MFA_VALID_WINDOW = 1
MFA_ROLE_SET = {"owner", "admin", "secretaria"}


def _is_true(raw: str, *, default: bool = False) -> bool:
    val = (raw or "").strip().lower()
    if not val:
        return default
    return val in {"1", "true", "yes", "on"}


def mfa_enforced_for_staff(*, testing: bool = False) -> bool:
    # MFA obligatorio por defecto fuera de tests.
    required = _is_true(os.getenv("STAFF_MFA_REQUIRED", "1"), default=True)
    if testing:
        if not required:
            return False
        return _is_true(os.getenv("STAFF_MFA_ENFORCE_IN_TESTS", "0"), default=False)

    run_env = (os.getenv("APP_ENV", os.getenv("FLASK_ENV", "development")) or "").strip().lower()
    is_prod = run_env in {"prod", "production"}
    if not required:
        # Fail-closed en producción: ignora desactivación accidental.
        if is_prod and not _is_true(os.getenv("STAFF_MFA_ALLOW_DISABLE_IN_PROD", "0"), default=False):
            return True
        return False
    return True


def staff_role_requires_mfa(role: str) -> bool:
    return (role or "").strip().lower() in MFA_ROLE_SET


def _normalize_base32(secret: str) -> str:
    cleaned = "".join(ch for ch in str(secret or "").strip().upper() if ch.isalnum())
    if not cleaned:
        return ""
    pad = len(cleaned) % 8
    if pad:
        cleaned += "=" * (8 - pad)
    return cleaned


def generate_mfa_secret() -> str:
    # 160 bits => estándar para TOTP.
    raw = os.urandom(20)
    return base64.b32encode(raw).decode("ascii").replace("=", "")


def _hotp(secret: str, counter: int, *, digits: int = MFA_DIGITS) -> str:
    key = base64.b32decode(_normalize_base32(secret), casefold=True)
    msg = int(counter).to_bytes(8, byteorder="big", signed=False)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    dbc = int.from_bytes(digest[offset:offset + 4], byteorder="big") & 0x7FFFFFFF
    token = dbc % (10 ** digits)
    return str(token).zfill(digits)


def _token_counter(now_ts: Optional[float] = None, *, period: int = MFA_PERIOD_SECONDS) -> int:
    current = time.time() if now_ts is None else float(now_ts)
    return int(current // int(period))


def generate_totp_token(secret: str, *, now_ts: Optional[float] = None) -> str:
    return _hotp(secret, _token_counter(now_ts))


def verify_totp_code(
    secret: str,
    code: str,
    *,
    last_used_counter: Optional[int] = None,
    now_ts: Optional[float] = None,
    valid_window: int = MFA_VALID_WINDOW,
) -> tuple[bool, Optional[int], str]:
    candidate = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(candidate) != MFA_DIGITS:
        return False, None, "bad_format"

    base_counter = _token_counter(now_ts)
    for delta in range(-int(valid_window), int(valid_window) + 1):
        counter = base_counter + delta
        if counter < 0:
            continue
        expected = _hotp(secret, counter)
        if hmac.compare_digest(expected, candidate):
            if last_used_counter is not None and int(counter) <= int(last_used_counter):
                return False, None, "reused"
            return True, int(counter), "ok"
    return False, None, "invalid"


def provisioning_uri(secret: str, *, username: str, issuer: str) -> str:
    issuer_clean = (issuer or "App").strip() or "App"
    user_clean = (username or "staff").strip() or "staff"
    label = f"{issuer_clean}:{user_clean}"
    return (
        f"otpauth://totp/{quote(label)}"
        f"?secret={quote(secret)}"
        f"&issuer={quote(issuer_clean)}"
        f"&algorithm=SHA1&digits={MFA_DIGITS}&period={MFA_PERIOD_SECONDS}"
    )


def mfa_issuer_name() -> str:
    return (os.getenv("STAFF_MFA_ISSUER") or "Domestica del Cibao A&D").strip() or "Domestica del Cibao A&D"


def generate_qr_png_data_uri(payload: str) -> Optional[str]:
    # Dependencia opcional: si no existe qrcode, se permite setup manual.
    try:
        import io
        import qrcode

        img = qrcode.make(payload)
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception:
        return None


def session_begin_mfa_pending(sess, *, staff_user_id: int, username: str, role: str, next_url: str, source: str) -> None:
    sess[MFA_PENDING_SESSION_KEY] = {
        "staff_user_id": int(staff_user_id),
        "username": (username or "").strip()[:80],
        "role": (role or "").strip().lower()[:20],
        "next_url": (next_url or "").strip()[:1024],
        "source": (source or "admin").strip().lower()[:20],
        "started_at": int(time.time()),
    }
    sess.pop(MFA_SETUP_SECRET_SESSION_KEY, None)
    sess["mfa_verified"] = False
    sess.modified = True


def session_get_mfa_pending(sess) -> dict:
    data = sess.get(MFA_PENDING_SESSION_KEY)
    return dict(data) if isinstance(data, dict) else {}


def session_clear_mfa_pending(sess) -> None:
    sess.pop(MFA_PENDING_SESSION_KEY, None)
    sess.pop(MFA_SETUP_SECRET_SESSION_KEY, None)
    sess.modified = True
