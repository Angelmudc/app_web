from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
from functools import wraps

from flask import (
    abort,
    current_app,
    g,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import or_

from config_app import cache, csrf
from clientes.routes import (
    generar_link_publico_compartible_cliente,
    generar_link_publico_compartible_cliente_nuevo,
)
from models import Cliente

from . import quick_form_sender_bp


COOKIE_NAME = "quick_form_sender"
SESSION_MAX_AGE = 30 * 24 * 60 * 60
_LOCAL_LOGIN_LOCK = threading.Lock()
_LOCAL_LOGIN_FAILURES: dict[str, tuple[int, float]] = {}


def _setting(name: str, default: str = "") -> str:
    return str(os.getenv(name) or current_app.config.get(name) or default).strip()


def _slug() -> str:
    return _setting("QUICK_FORM_SENDER_SLUG")


def _enabled() -> bool:
    return bool(
        _slug()
        and _setting("QUICK_FORM_SENDER_USERNAME")
        and _setting("QUICK_FORM_SENDER_PASSWORD_HASH")
    )


def _session_version() -> str:
    return _setting("QUICK_FORM_SENDER_SESSION_VERSION", "1")


def _serializer() -> URLSafeTimedSerializer:
    secret = str(current_app.config.get("SECRET_KEY") or "").strip()
    if not secret:
        raise RuntimeError("SECRET_KEY is required for quick form sender")
    return URLSafeTimedSerializer(secret, salt="quick-form-sender-v1")


def _issue_cookie(response, *, username: str) -> None:
    token = _serializer().dumps({"u": username[:120], "v": _session_version()})
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE,
        secure=bool(current_app.config.get("SESSION_COOKIE_SECURE")),
        httponly=True,
        samesite="Lax",
        path="/",
    )


def _clear_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def _authenticated_username() -> str | None:
    token = str(request.cookies.get(COOKIE_NAME) or "").strip()
    if not token or not _enabled():
        return None
    try:
        payload = _serializer().loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(payload, dict):
        return None
    username = str(payload.get("u") or "").strip()
    version = str(payload.get("v") or "").strip()
    expected = _setting("QUICK_FORM_SENDER_USERNAME")
    if not username or not hmac.compare_digest(username, expected):
        return None
    if not hmac.compare_digest(version, _session_version()):
        return None
    return username


def _client_ip() -> str:
    return (request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote_addr or "0.0.0.0")[:80]


def _login_rate_key(username: str) -> str:
    return hashlib.sha256(f"{_client_ip()}|{username.lower()}".encode()).hexdigest()


def _login_rate_limited(username: str) -> bool:
    window = 60.0
    limit = 5
    key = _login_rate_key(username)
    try:
        value = cache.get(f"quick-form-login:{key}")
        return int(value or 0) >= limit
    except Exception:
        with _LOCAL_LOGIN_LOCK:
            count, expires = _LOCAL_LOGIN_FAILURES.get(key, (0, 0.0))
            return expires > time.time() and count >= limit


def _record_login_failure(username: str) -> None:
    window = 60.0
    key = _login_rate_key(username)
    try:
        current = int(cache.get(f"quick-form-login:{key}") or 0) + 1
        cache.set(f"quick-form-login:{key}", current, timeout=int(window))
        return
    except Exception:
        with _LOCAL_LOGIN_LOCK:
            count, expires = _LOCAL_LOGIN_FAILURES.get(key, (0, 0.0))
            if expires <= time.time():
                count = 0
            _LOCAL_LOGIN_FAILURES[key] = (count + 1, time.time() + window)


def _valid_credentials(username: str, password: str) -> bool:
    configured_user = _setting("QUICK_FORM_SENDER_USERNAME")
    configured_hash = _setting("QUICK_FORM_SENDER_PASSWORD_HASH")
    try:
        from werkzeug.security import check_password_hash

        password_ok = check_password_hash(configured_hash, password)
    except Exception:
        password_ok = False
    return bool(
        hmac.compare_digest(username, configured_user)
        and password_ok
    )


def quick_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _enabled():
            abort(404)
        username = _authenticated_username()
        if not username:
            return redirect(url_for("quick_form_sender.login", slug=_slug()))
        g.quick_form_sender_username = username
        return view(*args, **kwargs)

    return wrapped


@quick_form_sender_bp.before_request
def _require_configured_slug():
    if not _enabled() or not hmac.compare_digest(str(request.view_args.get("slug") or ""), _slug()):
        abort(404)


@quick_form_sender_bp.after_request
def _renew_quick_cookie(response):
    username = getattr(g, "quick_form_sender_username", None)
    if username:
        _issue_cookie(response, username=username)
    return response


@quick_form_sender_bp.route("/m/<slug>/", methods=["GET"])
def home(slug: str):
    username = _authenticated_username()
    if not username:
        return redirect(url_for("quick_form_sender.login", slug=slug))
    g.quick_form_sender_username = username
    return render_template("quick_form_sender/home.html", slug=slug)


@quick_form_sender_bp.route("/m/<slug>/login", methods=["GET", "POST"])
def login(slug: str):
    if _authenticated_username():
        return redirect(url_for("quick_form_sender.home", slug=slug))
    error = None
    if request.method == "POST":
        if current_app.config.get("WTF_CSRF_ENABLED", True):
            csrf.protect()
        username = str(request.form.get("username") or "").strip()
        password = str(request.form.get("password") or "")
        limited = _login_rate_limited(username)
        if limited or not _valid_credentials(username, password):
            _record_login_failure(username)
            error = "No pudimos validar tus credenciales."
        else:
            response = make_response(redirect(url_for("quick_form_sender.home", slug=slug)))
            _issue_cookie(response, username=username)
            return response
    return render_template("quick_form_sender/login.html", slug=slug, error=error)


@quick_form_sender_bp.post("/m/<slug>/logout")
@quick_login_required
def logout(slug: str):
    if current_app.config.get("WTF_CSRF_ENABLED", True):
        csrf.protect()
    response = make_response(redirect(url_for("quick_form_sender.login", slug=slug)))
    _clear_cookie(response)
    g.pop("quick_form_sender_username", None)
    return response


@quick_form_sender_bp.route("/m/<slug>/existing", methods=["GET"])
@quick_login_required
def existing(slug: str):
    query = str(request.args.get("q") or "").strip()
    clients = []
    if query:
        pattern = f"%{query}%"
        clients = (
            Cliente.query
            .filter(
                or_(
                    Cliente.nombre_completo.ilike(pattern),
                    Cliente.telefono.ilike(pattern),
                    Cliente.email.ilike(pattern),
                )
            )
            .order_by(Cliente.nombre_completo.asc())
            .limit(20)
            .all()
        )
    return render_template("quick_form_sender/existing.html", slug=slug, query=query, clients=clients)


@quick_form_sender_bp.post("/m/<slug>/existing/generate")
@quick_login_required
def existing_generate(slug: str):
    client_id = request.form.get("client_id", type=int)
    client = Cliente.query.filter_by(id=client_id).first() if client_id else None
    if client is None or not bool(getattr(client, "is_active", True)):
        return render_template(
            "quick_form_sender/existing.html",
            slug=slug,
            query="",
            clients=[],
            error="No encontramos ese cliente.",
        ), 404
    link = generar_link_publico_compartible_cliente(
        client,
        created_by=f"quick_form_sender:{g.quick_form_sender_username}",
    )
    return render_template(
        "quick_form_sender/result.html",
        slug=slug,
        title="Formulario listo",
        subtitle=f"Cliente existente: {client.nombre_completo}",
        link=link,
        back_endpoint="quick_form_sender.existing",
    )


@quick_form_sender_bp.route("/m/<slug>/new", methods=["GET", "POST"])
@quick_login_required
def new(slug: str):
    if request.method == "POST":
        link = generar_link_publico_compartible_cliente_nuevo(
            created_by=f"quick_form_sender:{g.quick_form_sender_username}",
        )
        return render_template(
            "quick_form_sender/result.html",
            slug=slug,
            title="Formulario listo",
            subtitle="Cliente nuevo",
            link=link,
            back_endpoint="quick_form_sender.new",
        )
    return render_template("quick_form_sender/new.html", slug=slug)
