# -*- coding: utf-8 -*-
import os
import csv
from pathlib import Path
from datetime import timedelta
import click

from flask import session, request, redirect, url_for, render_template, jsonify, flash
from flask_login import current_user
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_wtf.csrf import CSRFError

from config_app import create_app, db, csrf, cache
from models import Cliente
from utils.client_contact_norm import norm_email, nullable_norm_phone_rd
from core.routes import candidatas_bp, procesos_bp, entrevistas_bp, archivos_bp


app = create_app()


app.jinja_env.globals['has_endpoint'] = lambda name: name in app.view_functions


def url_for_safe(endpoint: str, **values):
    return url_for(endpoint, **values) if endpoint in app.view_functions else None


app.jinja_env.globals['url_for_safe'] = url_for_safe


def _effective_phone_norm(telefono_norm_row, telefono_raw) -> str:
    # Si telefono_norm ya viene poblado pero es placeholder inválido, se ignora (None).
    ph = nullable_norm_phone_rd(telefono_norm_row)
    if ph:
        return ph
    # Fallback al teléfono original cuando telefono_norm no existe o quedó sucio.
    return nullable_norm_phone_rd(telefono_raw) or ""


@app.cli.command("audit-clientes-duplicados")
def audit_clientes_duplicados():
    """
    Auditoría de duplicados históricos por email_norm/telefono_norm.
    No modifica datos.
    """
    rows = Cliente.query.with_entities(
        Cliente.id,
        Cliente.nombre_completo,
        Cliente.email,
        Cliente.email_norm,
        Cliente.telefono,
        Cliente.telefono_norm,
        Cliente.fecha_registro,
    ).order_by(Cliente.id.asc()).all()

    by_email = {}
    by_phone = {}
    for rid, nombre, email, email_norm_row, telefono, telefono_norm_row, fecha_registro in rows:
        em = (email_norm_row or norm_email(email or "")).strip()
        ph = _effective_phone_norm(telefono_norm_row, telefono)
        row = {
            "id": int(rid or 0),
            "nombre": str(nombre or ""),
            "email": str(email or ""),
            "email_norm": em,
            "telefono": str(telefono or ""),
            "telefono_norm": ph,
            "fecha_registro": str(fecha_registro or ""),
        }
        if em:
            by_email.setdefault(em, []).append(row)
        if ph:
            by_phone.setdefault(ph, []).append(row)

    dup_email = {k: v for k, v in by_email.items() if len(v) > 1}
    dup_phone = {k: v for k, v in by_phone.items() if len(v) > 1}

    print("=== AUDITORIA DUPLICADOS CLIENTES ===")
    print(f"Total clientes: {len(rows)}")
    print(f"Duplicados por email_norm (grupos): {len(dup_email)}")
    print(f"Duplicados por telefono_norm (grupos): {len(dup_phone)}")

    if dup_email:
        print("\n--- DUPLICADOS POR EMAIL_NORM ---")
        for key in sorted(dup_email.keys()):
            print(f"\nemail_norm={key}")
            for item in sorted(dup_email[key], key=lambda x: x["id"]):
                print(
                    f"  id={item['id']} nombre={item['nombre']} "
                    f"email={item['email']} telefono={item['telefono']} fecha={item['fecha_registro']}"
                )

    if dup_phone:
        print("\n--- DUPLICADOS POR TELEFONO_NORM ---")
        for key in sorted(dup_phone.keys()):
            print(f"\ntelefono_norm={key}")
            for item in sorted(dup_phone[key], key=lambda x: x["id"]):
                print(
                    f"  id={item['id']} nombre={item['nombre']} "
                    f"email={item['email']} telefono={item['telefono']} fecha={item['fecha_registro']}"
                )

    if not dup_email and not dup_phone:
        print("\nSin duplicados historicos detectados por campos normalizados.")


def _collect_clientes_duplicados_rows():
    rows = Cliente.query.with_entities(
        Cliente.id,
        Cliente.nombre_completo,
        Cliente.email,
        Cliente.email_norm,
        Cliente.telefono,
        Cliente.telefono_norm,
        Cliente.fecha_registro,
    ).order_by(Cliente.id.asc()).all()

    by_email = {}
    by_phone = {}
    for rid, nombre, email, email_norm_row, telefono, telefono_norm_row, fecha_registro in rows:
        em = (email_norm_row or norm_email(email or "")).strip()
        ph = _effective_phone_norm(telefono_norm_row, telefono)
        base = {
            "cliente_id": int(rid or 0),
            "nombre": str(nombre or ""),
            "email": str(email or ""),
            "email_norm": em,
            "telefono": str(telefono or ""),
            "telefono_norm": ph,
            "fecha_creacion": str(fecha_registro or ""),
        }
        if em:
            by_email.setdefault(em, []).append(base)
        if ph:
            by_phone.setdefault(ph, []).append(base)

    out = []
    for key, items in by_email.items():
        if len(items) <= 1:
            continue
        for item in sorted(items, key=lambda x: x["cliente_id"]):
            row = dict(item)
            row["campo_duplicado"] = "email_norm"
            row["valor_duplicado"] = key
            out.append(row)
    for key, items in by_phone.items():
        if len(items) <= 1:
            continue
        for item in sorted(items, key=lambda x: x["cliente_id"]):
            row = dict(item)
            row["campo_duplicado"] = "telefono_norm"
            row["valor_duplicado"] = key
            out.append(row)
    return out


@app.cli.command("export-clientes-duplicados")
@click.option("--output", "output_path", default="instance/exports/clientes_duplicados.csv", show_default=True)
def export_clientes_duplicados(output_path: str):
    """
    Exporta duplicados históricos de clientes a CSV (solo lectura).
    """
    headers = [
        "cliente_id",
        "nombre",
        "email",
        "email_norm",
        "telefono",
        "telefono_norm",
        "fecha_creacion",
        "campo_duplicado",
        "valor_duplicado",
    ]
    output = Path(str(output_path or "").strip() or "instance/exports/clientes_duplicados.csv")
    output.parent.mkdir(parents=True, exist_ok=True)
    dup_rows = _collect_clientes_duplicados_rows()
    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        for row in dup_rows:
            writer.writerow(row)
    print(f"CSV generado: {output}")
    print(f"Filas exportadas: {len(dup_rows)}")
    if not dup_rows:
        print("No se detectaron duplicados; CSV contiene solo encabezados.")


@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    wants_json = False
    try:
        accept = (request.headers.get("Accept") or "").lower()
        xrw = (request.headers.get("X-Requested-With") or "").lower()
        async_hdr = (request.headers.get("X-Admin-Async") or "").lower()
        wants_json = bool(request.is_json or ("application/json" in accept) or (xrw == "xmlhttprequest") or (async_hdr in {"1", "true", "yes"}))
    except Exception:
        wants_json = False

    if wants_json:
        login_url = None
        try:
            if (request.path or "").startswith("/admin/"):
                login_url = url_for("admin.login", next=(request.full_path or request.path))
            elif (request.path or "").startswith("/clientes/"):
                login_url = url_for("clientes.login", next=(request.full_path or request.path))
        except Exception:
            login_url = None

        payload = {
            "success": False,
            "ok": False,
            "error_code": "csrf",
            "category": "danger",
            "message": "La sesión de seguridad expiró. Recarga la página e intenta de nuevo.",
            "redirect_url": login_url,
            "errors": [{"field": "csrf_token", "message": "Token CSRF inválido o vencido."}],
        }
        return jsonify(payload), 400

    path = (request.path or '').strip()
    if path.startswith('/clientes/solicitudes/publica/') or path.startswith('/clientes/f/'):
        return render_template(
            'clientes/public_link_invalid.html',
            reason_key='csrf',
            status_code=400,
        ), 400
    if path.startswith('/clientes/solicitudes/nueva-publica/') or path.startswith('/clientes/n/'):
        return render_template(
            'clientes/public_link_invalid.html',
            reason_key='csrf',
            status_code=400,
        ), 400
    if path.startswith('/solicitud/'):
        return render_template(
            'clientes/public_link_invalid.html',
            reason_key='csrf',
            status_code=400,
        ), 400
    if path.startswith('/contratos/f/'):
        return render_template(
            'contratos/public_invalid.html',
            reason_key='csrf',
        ), 400
    if path == '/clientes/solicitudes/nueva-publica':
        return redirect(url_for('clientes.solicitud_publica_nueva'))
    flash("Tu sesión de seguridad expiró. Recarga la página e intenta de nuevo.", "warning")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        referrer = (request.referrer or "").strip()
        if referrer:
            try:
                host_url = (request.host_url or "").rstrip("/")
                if referrer.startswith(host_url + "/") or referrer == host_url:
                    return redirect(referrer)
            except Exception:
                pass
        return redirect(request.url)
    return render_template("errors/403.html"), 400


@app.before_request
def force_session_expire():
    # Mantiene la sesión no permanente (como en la versión monolítica)
    session.permanent = False


IS_PROD = (
    (os.getenv("FLASK_ENV", "").strip().lower() == "production")
    or (os.getenv("ENV", "").strip().lower() == "production")
)

app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
app.config.setdefault("SESSION_COOKIE_SECURE", bool(IS_PROD))
app.config.setdefault("REMEMBER_COOKIE_HTTPONLY", True)
app.config.setdefault("REMEMBER_COOKIE_SAMESITE", "Lax")
app.config.setdefault("REMEMBER_COOKIE_SECURE", bool(IS_PROD))
app.config.setdefault("PERMANENT_SESSION_LIFETIME", timedelta(hours=8))
app.config.setdefault("SESSION_PERMANENT", False)

# Refuerzo explícito para despliegues detrás de Render/Cloudflare
if not isinstance(app.wsgi_app, ProxyFix):
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)


@app.after_request
def security_headers(resp):
    try:
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        resp.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if IS_PROD and request.is_secure:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    except Exception:
        pass
    return resp


def _is_authenticated_any() -> bool:
    try:
        if bool(getattr(current_user, "is_authenticated", False)):
            return True
    except Exception:
        pass
    return bool(session.get("usuario") and session.get("role"))


def _normalize_staff_role_loose(role_raw) -> str:
    role = str(role_raw or "").strip().lower()
    if role in ("owner", "admin", "secretaria"):
        return role
    if role in ("secretary", "secre", "secretaría"):
        return "secretaria"
    return ""


def _is_admin_any() -> bool:
    try:
        if bool(getattr(current_user, "is_authenticated", False)):
            role = _normalize_staff_role_loose(
                getattr(current_user, "role", None) or getattr(current_user, "rol", None) or ""
            )
            if role in ("owner", "admin") or bool(getattr(current_user, "is_admin", False)):
                return True
    except Exception:
        pass
    return (_normalize_staff_role_loose(session.get("role")) in ("owner", "admin"))


def _is_staff_any() -> bool:
    try:
        if bool(getattr(current_user, "is_authenticated", False)):
            role = _normalize_staff_role_loose(
                getattr(current_user, "role", None) or getattr(current_user, "rol", None) or ""
            )
            if role in ("owner", "admin", "secretaria"):
                return True
    except Exception:
        pass
    return (_normalize_staff_role_loose(session.get("role")) in ("owner", "admin", "secretaria"))


@app.before_request
def _protect_sensitive_routes():
    path = (request.path or "").strip()

    if not path or path.startswith("/static/"):
        return None

    # Nunca interceptar logins/logout/health
    if path in {"/login", "/logout", "/admin/login", "/clientes/login", "/health", "/healthz", "/ping"}:
        return None
    if path.startswith("/admin/mfa/"):
        return None
    if path == "/clientes/reset-password":
        return None
    if path.startswith("/clientes/solicitudes/publica/"):
        return None
    if path.startswith("/clientes/f/"):
        return None
    if path.startswith("/clientes/solicitudes/nueva-publica"):
        return None
    if path.startswith("/clientes/n/"):
        return None

    if path.startswith("/admin/"):
        if not _is_staff_any():
            return redirect(url_for("admin.login", next=request.full_path or path))
        return None

    if path.startswith("/clientes/"):
        if not _is_authenticated_any():
            return redirect(url_for("clientes.login", next=request.full_path or path))
        return None

    sensitive_prefixes = (
        "/gestionar_archivos",
        "/subir_fotos",
        "/report",
        "/pagos",
        "/editar",
    )
    if any(path.startswith(p) for p in sensitive_prefixes):
        if not _is_authenticated_any():
            return redirect(url_for("login", next=request.full_path or path))
    return None


def _register_endpoint_aliases(app_obj, blueprint_name: str):
    """Crea alias legacy sin prefijo para conservar url_for('endpoint') existentes."""
    prefix = f"{blueprint_name}."
    rules = list(app_obj.url_map.iter_rules())

    for rule in rules:
        if not rule.endpoint.startswith(prefix):
            continue

        bare_endpoint = rule.endpoint[len(prefix):]
        if bare_endpoint in app_obj.url_map._rules_by_endpoint:
            continue

        view_func = app_obj.view_functions.get(rule.endpoint)
        if view_func is None:
            continue

        methods = sorted(m for m in rule.methods if m not in {"HEAD", "OPTIONS"})
        app_obj.add_url_rule(
            rule.rule,
            endpoint=bare_endpoint,
            view_func=view_func,
            methods=methods,
            defaults=rule.defaults,
            strict_slashes=rule.strict_slashes,
        )


def _register_endpoint_alias(app_obj, source_endpoint: str, alias_endpoint: str):
    """Crea alias explícito entre endpoints, incluyendo endpoints legacy con namespace."""
    if alias_endpoint in app_obj.url_map._rules_by_endpoint:
        return

    source_rules = list(app_obj.url_map._rules_by_endpoint.get(source_endpoint, []))
    if not source_rules:
        return

    view_func = app_obj.view_functions.get(source_endpoint)
    if view_func is None:
        return

    for rule in source_rules:
        methods = sorted(m for m in rule.methods if m not in {"HEAD", "OPTIONS"})
        app_obj.add_url_rule(
            rule.rule,
            endpoint=alias_endpoint,
            view_func=view_func,
            methods=methods,
            defaults=rule.defaults,
            strict_slashes=rule.strict_slashes,
        )


# Blueprints internos modularizados
app.register_blueprint(candidatas_bp)
app.register_blueprint(procesos_bp)
app.register_blueprint(entrevistas_bp)
app.register_blueprint(archivos_bp)

# Compatibilidad de endpoint names preexistentes
for bp_name in ("candidatas_routes", "procesos_routes", "entrevistas_routes", "archivos_routes"):
    _register_endpoint_aliases(app, bp_name)

# Compatibilidad legacy explícita para templates/flujo de archivos
_register_endpoint_alias(app, "subir_fotos", "subir_fotos.subir_fotos")
_register_endpoint_alias(app, "ver_imagen", "subir_fotos.ver_imagen")


# Login routing por blueprint (mismo comportamiento esperado)
try:
    lm = app.extensions.get("login_manager")
    if lm is not None:
        lm.login_view = "login"
        if not getattr(lm, "blueprint_login_views", None):
            lm.blueprint_login_views = {}
        lm.blueprint_login_views["clientes"] = "clientes.login"
        lm.blueprint_login_views["admin"] = "admin.login"

        @lm.unauthorized_handler
        def _unauthorized_callback():
            bp = (request.blueprint or "").strip()
            next_url = request.full_path if request.full_path else request.path
            wants_json = False
            try:
                accept = (request.headers.get("Accept") or "").lower()
                xrw = (request.headers.get("X-Requested-With") or "").lower()
                async_hdr = (request.headers.get("X-Admin-Async") or "").lower()
                wants_json = bool(
                    request.is_json
                    or ("application/json" in accept)
                    or (xrw == "xmlhttprequest")
                    or (async_hdr in {"1", "true", "yes"})
                )
            except Exception:
                wants_json = False
            if bp == "clientes":
                return redirect(url_for("clientes.login", next=next_url))
            if bp == "admin":
                if wants_json:
                    login_url = url_for("admin.login", next=next_url)
                    return jsonify({
                        "success": False,
                        "ok": False,
                        "category": "warning",
                        "message": "Tu sesión expiró. Inicia sesión nuevamente.",
                        "error_code": "session_expired",
                        "redirect_url": login_url,
                    }), 401
                return redirect(url_for("admin.login", next=next_url))
            return redirect(url_for("login", next=next_url))
except Exception:
    pass


if __name__ == '__main__':
    debug_mode = (
        (os.getenv("FLASK_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"})
        and (os.getenv("APP_ENV", "").strip().lower() not in {"prod", "production"})
    )
    app.run(debug=debug_mode, host='0.0.0.0', port=10000)
