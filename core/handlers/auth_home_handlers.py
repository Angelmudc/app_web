# -*- coding: utf-8 -*-

import os

from core import legacy_handlers as legacy


def robots_txt():
    static_folder = legacy.current_app.static_folder or os.path.join(legacy.current_app.root_path, "static")
    return legacy.send_from_directory(static_folder, "robots.txt")


def home():
    pending = legacy.session.get("_staff_mfa_pending")
    if isinstance(pending, dict) and pending.get("staff_user_id"):
        try:
            sid = int(pending.get("staff_user_id") or 0)
        except Exception:
            sid = 0
        pending_user = legacy.StaffUser.query.get(sid) if sid > 0 else None
        if pending_user is not None and bool(pending_user.mfa_enabled) and bool(pending_user.get_mfa_secret()):
            return legacy.redirect(legacy.url_for("admin.mfa_verify"))
        return legacy.redirect(legacy.url_for("admin.mfa_setup"))

    if 'usuario' not in legacy.session:
        return legacy.redirect(legacy.url_for('login'))

    role = legacy._normalize_staff_role_loose(legacy.session.get("role"))
    if role not in ("owner", "admin", "secretaria"):
        return legacy.redirect(legacy.url_for('login'))

    if legacy.mfa_enforced_for_staff(testing=bool(legacy.current_app.config.get("TESTING"))) and legacy.staff_role_requires_mfa(role):
        if not bool(legacy.session.get("mfa_verified")):
            return legacy.redirect(legacy.url_for("admin.login"))

    if not bool(legacy.session.get("is_admin_session")):
        return legacy.redirect(legacy.url_for('login'))
    # Evita UTC si tu app es local/DR; suficiente con fecha local del servidor
    return legacy.render_template(
        'home.html',
        usuario=legacy.session['usuario'],
        current_year=legacy.rd_today().year
    )


def login():
    mensaje = ""

    if legacy.request.method == 'POST':
        # (Opcional pero recomendado) Honeypot: si lo llenan, es bot
        # Si tu login.html tiene input hidden name="website", deja esto:
        if (legacy.request.form.get("website") or "").strip():
            return "", 400

        usuario_raw = (legacy.request.form.get('usuario') or '').strip()[:64]
        clave_input_raw = (legacy.request.form.get('clave') or '')
        clave_input_trimmed = clave_input_raw.strip()
        clave = clave_input_trimmed[:128]

        # ✅ Normaliza para llaves internas (bloqueos) y consistencia
        usuario_norm = usuario_raw.lower().strip()

        # 🔒 Bloqueo por intentos (tu bloqueo por usuario+IP)
        if legacy._is_locked(usuario_norm):
            return legacy.render_template(
                'login.html',
                mensaje=f"Demasiados intentos. Bloqueado por {legacy.LOGIN_LOCK_MINUTOS} minutos."
            ), 429

        # 1) Primero intentar StaffUser (BD) por username o email (case-insensitive)
        staff_user = None
        staff_lookup_error = False
        try:
            staff_user = legacy.StaffUser.query.filter(
                legacy.or_(
                    legacy.func.lower(legacy.StaffUser.username) == usuario_norm,
                    legacy.func.lower(legacy.StaffUser.email) == usuario_norm,
                )
            ).first()
        except Exception:
            staff_lookup_error = True
            try:
                legacy.current_app.logger.exception("LOGIN_DB_LOOKUP_ERROR /login usuario=%s", usuario_norm)
            except Exception:
                pass
            staff_user = None

        staff_ok = False
        staff_password_ok = False
        if staff_user and bool(getattr(staff_user, "is_active", True)):
            role = legacy._normalize_staff_role_loose(getattr(staff_user, "role", "") or "")
            if role in ("owner", "admin", "secretaria"):
                try:
                    staff_password_ok = bool(staff_user.check_password(clave))
                    staff_ok = bool(staff_password_ok)
                except Exception:
                    staff_password_ok = False
                    staff_ok = False

        breakglass_ok = False
        if not staff_ok and legacy.is_breakglass_enabled() and usuario_norm == legacy.breakglass_username().strip().lower():
            ip = legacy.get_request_ip()
            ua = legacy.request.headers.get("User-Agent") or ""
            if legacy.breakglass_allowed_ip(ip) and legacy.check_breakglass_password(clave):
                breakglass_ok = True
                legacy.log_breakglass_attempt(True, ip, ua)
            else:
                legacy.log_breakglass_attempt(False, ip, ua)

        if legacy._login_debug_enabled():
            try:
                legacy.current_app.logger.warning(
                    "LOGIN_DEBUG_LEGACY %s",
                    legacy.json.dumps(
                        {
                            "route": "/login",
                            "usuario_input": usuario_raw,
                            "usuario_norm": usuario_norm,
                            "has_clave_field": bool("clave" in legacy.request.form),
                            "form_keys": sorted(list(legacy.request.form.keys()))[:40],
                            "clave_value_count": int(len(legacy.request.form.getlist("clave"))),
                            "password_len_raw": int(len(clave_input_raw)),
                            "password_len_after_strip": int(len(clave_input_trimmed)),
                            "password_len_final": int(len(clave)),
                            "password_empty": bool(not clave),
                            "password_had_outer_spaces": bool(clave_input_raw != clave_input_trimmed),
                            "password_truncated_128": bool(len(clave_input_trimmed) > 128),
                            "locked": bool(legacy._is_locked(usuario_norm)),
                            "staff_lookup_error": bool(staff_lookup_error),
                            "staff_found": bool(staff_user),
                            "staff_id": int(staff_user.id) if isinstance(staff_user, legacy.StaffUser) else None,
                            "staff_role": (getattr(staff_user, "role", None) if staff_user else None),
                            "staff_active": bool(getattr(staff_user, "is_active", False)) if staff_user else False,
                            "staff_password_ok": bool(staff_password_ok),
                            "breakglass_ok": bool(breakglass_ok),
                            "auth_ok": bool(staff_ok or breakglass_ok),
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                )
            except Exception:
                pass

        if staff_lookup_error:
            return legacy.render_template(
                'login.html',
                mensaje="Error temporal validando credenciales. Intenta de nuevo."
            ), 503

        if staff_ok:
            # ✅ Login correcto: limpia intentos (los tuyos) + limpia lock global (IP+endpoint+usuario)
            legacy._reset_fail(usuario_norm)

            # ✅ Limpia lock del security_layer con IP real (Render) si existe helper
            try:
                clear_fn = legacy.current_app.extensions.get("clear_login_attempts")
                if callable(clear_fn):
                    ip = legacy._client_ip()
                    # Tu security_layer usa username lower para keys
                    clear_fn(ip, "/login", usuario_norm)
            except Exception:
                pass

            # Si tú también tienes tu helper legacy, lo dejamos (no rompe nada)
            try:
                legacy._clear_security_layer_lock("/login", usuario_norm)
            except Exception:
                pass

            if legacy._staff_mfa_required_for_user(staff_user):
                nxt = (legacy.request.args.get("next") or legacy.request.form.get("next") or "").strip()
                next_url = nxt if legacy._is_safe_next(nxt) else legacy.url_for("home")
                legacy.session.clear()
                legacy.session.permanent = False
                legacy.session_begin_mfa_pending(
                    legacy.session,
                    staff_user_id=int(staff_user.id),
                    username=(staff_user.username or usuario_raw),
                    role=legacy._normalize_staff_role_loose(staff_user.role) or "secretaria",
                    next_url=next_url,
                    source="legacy",
                )
                if not (bool(staff_user.mfa_enabled) and bool(staff_user.get_mfa_secret())):
                    legacy.session[legacy.MFA_SETUP_SECRET_SESSION_KEY] = legacy.generate_mfa_secret()
                    legacy.session.modified = True
                    return legacy.redirect(legacy.url_for("admin.mfa_setup"))
                return legacy.redirect(legacy.url_for("admin.mfa_verify"))

            # 🔒 Regenerar sesión completamente al autenticar
            legacy.session.clear()
            legacy.session.permanent = False
            legacy.login_user(staff_user, remember=False)
            legacy.session['usuario'] = (staff_user.username or usuario_raw)
            legacy.session['role'] = legacy._normalize_staff_role_loose(staff_user.role) or "secretaria"
            legacy.session['is_staff'] = True
            legacy.session['is_admin_session'] = True
            legacy.session['mfa_verified'] = True
            legacy.session['logged_at'] = legacy.utc_now_naive().isoformat(timespec='seconds')
            legacy.clear_breakglass_session(legacy.session)
            legacy.session.modified = True

            # Auditoría de último login para StaffUser (incluye emergency admin activado).
            try:
                staff_user.last_login_at = legacy.utc_now_naive()
                staff_user.last_login_ip = legacy._client_ip()
                legacy.db.session.commit()
            except Exception:
                legacy.db.session.rollback()

            if legacy._login_debug_enabled():
                try:
                    legacy.current_app.logger.warning(
                        "LOGIN_DEBUG_LEGACY_SESSION %s",
                        legacy.json.dumps(
                            {
                                "route": "/login",
                                "usuario_session": legacy.session.get("usuario"),
                                "role_session": legacy.session.get("role"),
                                "is_admin_session": bool(legacy.session.get("is_admin_session")),
                            },
                            ensure_ascii=False,
                            default=str,
                        ),
                    )
                except Exception:
                    pass
            return legacy.safe_redirect_next('home')

        if breakglass_ok:
            legacy._reset_fail(usuario_norm)
            try:
                clear_fn = legacy.current_app.extensions.get("clear_login_attempts")
                if callable(clear_fn):
                    ip = legacy._client_ip()
                    clear_fn(ip, "/login", usuario_norm)
            except Exception:
                pass
            try:
                legacy._clear_security_layer_lock("/login", usuario_norm)
            except Exception:
                pass

            legacy.session.clear()
            legacy.session.permanent = False
            legacy.login_user(legacy.build_breakglass_user(), remember=False)
            legacy.set_breakglass_session(legacy.session)
            legacy.session['mfa_verified'] = True
            legacy.session['logged_at'] = legacy.utc_now_naive().isoformat(timespec='seconds')
            legacy.session.modified = True
            return legacy.safe_redirect_next('home')

        # ❌ Login incorrecto: registra intento
        n = legacy._register_fail(usuario_norm)

        if legacy._is_locked(usuario_norm):
            return legacy.render_template(
                'login.html',
                mensaje=f"Demasiados intentos. Bloqueado por {legacy.LOGIN_LOCK_MINUTOS} minutos."
            ), 429

        restantes = max(0, legacy.LOGIN_MAX_INTENTOS - n)
        mensaje = "Credenciales incorrectas."

    return legacy.render_template('login.html', mensaje=mensaje)


def logout():
    try:
        legacy.logout_user()
    except Exception:
        pass
    legacy.session.clear()
    return legacy.safe_redirect_next('login')
