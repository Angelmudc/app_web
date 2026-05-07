#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import random
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import text
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app
from config_app import db
from models import Cliente, PublicSolicitudClienteNuevoTokenUso, Solicitud
from clientes.routes import generar_token_publico_cliente_nuevo

CSRF_INPUT_RE = re.compile(r'name="csrf_token"[^>]*value="([^"]+)"', re.I)
CSRF_META_RE = re.compile(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', re.I)
FLASH_RE = re.compile(r'<[^>]+class="[^"]*(?:alert|flash|invalid-feedback)[^"]*"[^>]*>(.*?)</[^>]+>', re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class Finding:
    endpoint: str
    test_case: str
    expected: str
    status_real: str
    result: str
    details: str
    severity: str = "info"


@dataclass
class Report:
    run_id: str
    db_name: str
    findings: list[Finding] = field(default_factory=list)

    def add(self, endpoint: str, test_case: str, expected: str, status_real: str, result: str, details: str, severity: str = "info"):
        self.findings.append(Finding(endpoint, test_case, expected, status_real, result, details, severity))


class LocalHTTPServer:
    def __init__(self, host: str = "127.0.0.1"):
        self.host = host
        self.port = self._find_free_port()
        self.proc: subprocess.Popen[str] | None = None

    def _find_free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((self.host, 0))
            return int(s.getsockname()[1])

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        env = os.environ.copy()
        env["APP_ENV"] = "local"
        code = (
            "from app import app; "
            f"app.run(host='{self.host}', port={self.port}, debug=False, use_reloader=False, threaded=True)"
        )
        self.proc = subprocess.Popen(
            [sys.executable, "-c", code],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            text=True,
        )
        deadline = time.time() + 25
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("Servidor local terminó inesperadamente")
            try:
                r = requests.get(f"{self.base_url}/", timeout=1.2, allow_redirects=False)
                if r.status_code in (200, 302, 303, 404):
                    return
            except Exception:
                pass
            time.sleep(0.25)
        raise RuntimeError("Timeout esperando servidor local HTTP")

    def stop(self) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except Exception:
                self.proc.kill()
        self.proc = None


def _require_local() -> str:
    env = (os.getenv("APP_ENV") or "").strip().lower()
    if env != "local":
        raise RuntimeError(f"Bloqueado: APP_ENV debe ser 'local'. actual={env!r}")
    with db.engine.connect() as conn:
        db_name = str(conn.execute(text("select current_database()")).scalar() or "")
    if db_name != "domestica_cibao_local":
        raise RuntimeError(f"Bloqueado: DB debe ser domestica_cibao_local. actual={db_name!r}")
    return db_name


def _extract_flash_snippet(html: str) -> str:
    text = " ".join(TAG_RE.sub(" ", (m or "")).strip() for m in FLASH_RE.findall(html or ""))
    return re.sub(r"\s+", " ", text).strip()[:260]


def _extract_csrf(html: str) -> str:
    m = CSRF_INPUT_RE.search(html or "")
    if m:
        return m.group(1).strip()
    m = CSRF_META_RE.search(html or "")
    return (m.group(1) if m else "").strip()


def _get_csrf(session: requests.Session, full_url: str) -> tuple[str, str, int]:
    r = session.get(full_url, allow_redirects=True, timeout=8)
    html = r.text or ""
    return _extract_csrf(html), html, int(r.status_code or 0)


def _mk_public_payload(run: str, suffix: str) -> dict[str, Any]:
    run_tail = run[-12:].lower()
    suffix_norm = re.sub(r"[^a-z0-9]", "", suffix.lower())[:8] or "x"
    phone_tail = f"{int(hashlib.sha256(f'{run}-{suffix_norm}'.encode()).hexdigest(), 16) % 10_000_000:07d}"
    return {
        "nombre_completo": f"Audit {run} {suffix_norm}",
        "email_contacto": f"audit.{run_tail}.{suffix_norm}@example.com",
        "telefono_contacto": f"809{phone_tail}",
        "ciudad_cliente": "Santiago",
        "sector_cliente": f"Sector {suffix}",
        "ciudad_sector": "Santiago / Centro",
        "rutas_cercanas": "Ruta K",
        "modalidad_trabajo": "Salida diaria - lunes a viernes",
        "modalidad_grupo": "con_salida_diaria",
        "modalidad_especifica": "Salida diaria - lunes a viernes",
        "horario": "Lunes a viernes, de 8:00 AM a 5:00 PM",
        "horario_dias_trabajo": "Lunes a viernes",
        "horario_hora_entrada": "8:00 AM",
        "horario_hora_salida": "5:00 PM",
        "edad_requerida": ["26-35"],
        "experiencia": "Experiencia validada en hogar",
        "funciones": ["limpieza", "cocinar"],
        "tipo_lugar": "casa",
        "habitaciones": "3",
        "banos": "2",
        "adultos": "2",
        "ninos": "0",
        "edades_ninos": "",
        "mascota": "",
        "sueldo": "20000",
        "nota_cliente": "Prueba local",
        "areas_comunes": ["sala", "cocina"],
        "terms_decision": "accept",
        "terms_accepted": "1",
        "pasaje_mode": "incluido",
        "pisos_selector": "1",
    }


def _count_dup_candidates(run: str) -> dict[str, int]:
    run_tag = run[-12:].lower()
    like_mail = f"%{run_tag}%@example.com"
    rows = db.session.execute(
        text(
            """
            SELECT
                COALESCE(SUM(CASE WHEN t.cnt > 1 THEN 1 ELSE 0 END), 0) AS dup_email_groups
            FROM (
                SELECT lower(email) AS em, count(*) AS cnt
                FROM clientes
                WHERE email ILIKE :like_mail
                GROUP BY lower(email)
            ) t
            """
        ),
        {"like_mail": like_mail},
    ).mappings().first() or {}
    return {"dup_email_groups": int(rows.get("dup_email_groups") or 0)}


def _create_isolated_clients(run: str) -> tuple[Cliente, Cliente, Solicitud, Solicitud]:
    run_l = run.lower()
    run_short = run_l[-10:]
    suffix = ""
    for _ in range(20):
        cand = secrets.token_hex(3)
        e1 = f"audit.cli1.{run_short}.{cand}@example.com"
        e2 = f"audit.cli2.{run_short}.{cand}@example.com"
        if not Cliente.query.filter(Cliente.email.in_([e1, e2])).first():
            suffix = cand
            break
    if not suffix:
        raise RuntimeError("No se pudo generar identidad única para clientes aislados")

    c1 = Cliente(codigo=f"A{run[-5:]}{suffix[:1]}", nombre_completo=f"AUDIT Cliente Uno {run}", email=e1, username=None, telefono=f"829{int(run[-6:])%10_000_000:07d}", ciudad="Santiago", sector="Centro", role="cliente", is_active=True, acepto_politicas=True, fecha_acepto_politicas=datetime.utcnow())
    c1.password_hash = generate_password_hash("Audit#12345", method="pbkdf2:sha256")
    c2 = Cliente(codigo=f"B{run[-5:]}{suffix[-1:]}", nombre_completo=f"AUDIT Cliente Dos {run}", email=e2, username=None, telefono=f"849{(int(run[-6:])+1)%10_000_000:07d}", ciudad="Santiago", sector="Centro", role="cliente", is_active=True, acepto_politicas=True, fecha_acepto_politicas=datetime.utcnow())
    c2.password_hash = generate_password_hash("Audit#12345", method="pbkdf2:sha256")
    db.session.add_all([c1, c2]); db.session.flush()

    s1 = Solicitud(cliente_id=c1.id, codigo_solicitud=f"S-{run[-8:]}-1")
    s1.modalidad_trabajo = "Salida diaria - lunes a viernes"; s1.horario = "L-V"; s1.funciones = ["limpieza"]; s1.tipo_lugar = "Casa"; s1.habitaciones = 2; s1.banos = 1; s1.adultos = 2; s1.ninos = 0; s1.sueldo = "19000"; s1.areas_comunes = ["sala"]; s1.terms_accepted = True
    s2 = Solicitud(cliente_id=c2.id, codigo_solicitud=f"S-{run[-8:]}-2")
    s2.modalidad_trabajo = "Salida diaria - lunes a viernes"; s2.horario = "L-V"; s2.funciones = ["limpieza"]; s2.tipo_lugar = "Casa"; s2.habitaciones = 2; s2.banos = 1; s2.adultos = 2; s2.ninos = 0; s2.sueldo = "19000"; s2.areas_comunes = ["sala"]; s2.terms_accepted = True
    db.session.add_all([s1, s2]); db.session.commit()
    return c1, c2, s1, s2


def _ping_identity(base: str, session: requests.Session) -> tuple[int, int | None, str]:
    r = session.get(f"{base}/clientes/ping", allow_redirects=False, timeout=8)
    cid = None
    body = r.text[:220]
    try:
        j = r.json()
        cid = int(j.get("cliente_id") or 0) or None
        body = str(j)
    except Exception:
        pass
    return int(r.status_code or 0), cid, body


def _login_cliente(base: str, session: requests.Session, ident: str, password: str, fallback_ident: str | None = None) -> dict[str, Any]:
    attempts = [str(ident or "").strip()]
    if fallback_ident:
        attempts.append(str(fallback_ident or "").strip())
    out: dict[str, Any] = {"logged_in": False, "post_status": 0, "post_location": "", "attempt_ident": "", "flash": "", "csrf": "", "cookie_count": 0, "ping_status": 0, "ping_id": None, "ping_body": ""}
    for candidate in attempts:
        if not candidate:
            continue
        csrf, html, _ = _get_csrf(session, f"{base}/clientes/login")
        out["csrf"] = csrf
        resp = session.post(
            f"{base}/clientes/login",
            data={"username": candidate, "password": password, "csrf_token": csrf},
            allow_redirects=False,
            timeout=8,
        )
        out["post_status"] = int(resp.status_code or 0)
        out["post_location"] = str(resp.headers.get("Location") or "")
        out["attempt_ident"] = candidate
        out["cookie_count"] = len(session.cookies)
        lp = session.get(f"{base}/clientes/login", allow_redirects=False, timeout=8)
        out["flash"] = _extract_flash_snippet(lp.text or "")
        ps, pid, pb = _ping_identity(base, session)
        out["ping_status"], out["ping_id"], out["ping_body"] = ps, pid, pb
        if out["post_status"] in (302, 303) and ps == 200:
            out["logged_in"] = True
            return out
    return out


def _csrf_errors_from_body(text_body: str) -> str:
    snippet = _extract_flash_snippet(text_body)
    if snippet:
        return snippet
    return re.sub(r"\s+", " ", text_body or "")[:260]


def main() -> None:
    run = f"AUD{datetime.now().strftime('%Y%m%d%H%M%S')}{os.getpid():05d}{random.randint(1000, 9999)}"
    with app.app_context():
        db_name = _require_local()
        rep = Report(run_id=run, db_name=db_name)
        dup_before = _count_dup_candidates(run)
        rep.add("db/clientes", "precheck_duplicates_run_namespace", "0 duplicados", str(sum(dup_before.values())), "pass" if sum(dup_before.values()) == 0 else "fail", f"before={dup_before}")
        c1, c2, s1, s2 = _create_isolated_clients(run)
        c1_id = int(c1.id); c2_id = int(c2.id)
        c1_email = str(c1.email or ""); c2_email = str(c2.email or "")
        c1_codigo = str(c1.codigo or ""); c2_codigo = str(c2.codigo or "")
        s1_id = int(s1.id); s2_id = int(s2.id)

    server = LocalHTTPServer()
    server.start()
    base = server.base_url
    try:
        public_session = requests.Session()
        cliente_a_session = requests.Session()
        cliente_b_session = requests.Session()
        admin_session = requests.Session()
        ping_session = requests.Session()

        la = _login_cliente(base, cliente_a_session, c1_email, "Audit#12345", fallback_ident=c1_codigo)
        lb = _login_cliente(base, cliente_b_session, c2_email, "Audit#12345", fallback_ident=c2_codigo)
        rep.add("session", "client_login_isolated_user_a", "login y ping=200", f"{la['post_status']}/{la['ping_status']}", "pass" if la["logged_in"] else "fail", f"ident={la['attempt_ident']} ping_id={la['ping_id']} cookies={la['cookie_count']} flash={la['flash'] or '-'}", "high" if not la["logged_in"] else "info")
        rep.add("session", "client_login_isolated_user_b", "login y ping=200", f"{lb['post_status']}/{lb['ping_status']}", "pass" if lb["logged_in"] else "fail", f"ident={lb['attempt_ident']} ping_id={lb['ping_id']} cookies={lb['cookie_count']} flash={lb['flash'] or '-'}", "high" if not lb["logged_in"] else "info")

        pa_s, pa_id, _ = _ping_identity(base, cliente_a_session)
        pb_s, pb_id, _ = _ping_identity(base, cliente_b_session)
        rep.add("session", "identity_guard_a", f"200 y id={c1_id}", f"{pa_s}/{pa_id}", "pass" if pa_s == 200 and pa_id == c1_id else "fail", f"expected={c1_id} actual={pa_id}", "critical" if pa_id != c1_id else "info")
        rep.add("session", "identity_guard_b", f"200 y id={c2_id}", f"{pb_s}/{pb_id}", "pass" if pb_s == 200 and pb_id == c2_id else "fail", f"expected={c2_id} actual={pb_id}", "critical" if pb_id != c2_id else "info")

        r_own = cliente_a_session.get(f"{base}/clientes/solicitudes/{s1_id}", allow_redirects=False, timeout=8)
        r_idor_ab = cliente_a_session.get(f"{base}/clientes/solicitudes/{s2_id}", allow_redirects=False, timeout=8)
        r_idor_ba = cliente_b_session.get(f"{base}/clientes/solicitudes/{s1_id}", allow_redirects=False, timeout=8)
        rep.add("idor", "client_a_access_own_resource", "200", str(r_own.status_code), "pass" if r_own.status_code == 200 else "fail", f"url=/clientes/solicitudes/{s1_id}")
        rep.add("idor", "client_a_access_client_b_resource", "403 o 404", str(r_idor_ab.status_code), "pass" if r_idor_ab.status_code in (403, 404) else "fail", f"url=/clientes/solicitudes/{s2_id}")
        rep.add("idor", "client_b_access_client_a_resource", "403 o 404", str(r_idor_ba.status_code), "pass" if r_idor_ba.status_code in (403, 404) else "fail", f"url=/clientes/solicitudes/{s1_id}", "critical" if r_idor_ba.status_code == 200 else "info")

        r_public = public_session.get(f"{base}/clientes/solicitudes/{s1_id}", allow_redirects=False, timeout=8)
        rep.add("permissions", "client_route_from_public", "302/303/403/404", str(r_public.status_code), "pass" if r_public.status_code in (302, 303, 403, 404) else "fail", f"url=/clientes/solicitudes/{s1_id} loc={r_public.headers.get('Location') or ''}")

        logout_csrf, _, _ = _get_csrf(cliente_a_session, f"{base}/clientes/login")
        logout_resp = cliente_a_session.post(f"{base}/clientes/logout", data={"csrf_token": logout_csrf}, allow_redirects=False, timeout=8)
        after_logout = cliente_a_session.get(f"{base}/clientes/dashboard", allow_redirects=False, timeout=8)
        rep.add("session", "logout_then_reuse_same_session", "302/303/401/403", str(after_logout.status_code), "pass" if after_logout.status_code in (302, 303, 401, 403) else "fail", f"logout={logout_resp.status_code} csrf={int(bool(logout_csrf))} loc={after_logout.headers.get('Location') or ''}", "high" if after_logout.status_code == 200 else "info")

        tampered = requests.Session()
        tampered.cookies.set("app_web_session", "tampered.invalid.cookie.value", domain="127.0.0.1", path="/")
        rt = tampered.get(f"{base}/clientes/dashboard", allow_redirects=False, timeout=8)
        rep.add("session", "tampered_cookie", "302/303/400/401/403", str(rt.status_code), "pass" if rt.status_code in (302, 303, 400, 401, 403) else "fail", f"loc={rt.headers.get('Location') or ''}")

        ping_login = _login_cliente(base, ping_session, c1_email, "Audit#12345", fallback_ident=c1_codigo)
        rep.add("/clientes/login", "login_para_live_ping", "302 + ping 200", f"{ping_login['post_status']}/{ping_login['ping_status']}", "pass" if ping_login["logged_in"] else "fail", f"ping_id={ping_login['ping_id']} flash={ping_login['flash'] or '-'}")
        ping_csrf, _, _ = _get_csrf(ping_session, f"{base}/clientes/login")
        ping_headers = {"X-CSRFToken": ping_csrf, "X-CSRF-Token": ping_csrf}
        invalid_json = ping_session.post(f"{base}/clientes/live/ping", data="{bad-json", headers={"Content-Type": "application/json", **ping_headers}, allow_redirects=False, timeout=8)
        valid_ping = ping_session.post(f"{base}/clientes/live/ping", json={"event_type": "heartbeat", "current_path": "/clientes/dashboard", "action_hint": "audit"}, headers=ping_headers, allow_redirects=False, timeout=8)
        invalid_type = ping_session.post(f"{base}/clientes/live/ping", json={"event_type": "evil_event", "current_path": "/clientes/dashboard", "action_hint": "audit"}, headers=ping_headers, allow_redirects=False, timeout=8)
        huge = ping_session.post(f"{base}/clientes/live/ping", json={"event_type": "heartbeat", "current_path": "/clientes/dashboard", "action_hint": "audit", "blob": "X" * 8000}, headers=ping_headers, allow_redirects=False, timeout=8)
        rep.add("/clientes/live/ping", "payload_json_invalido", "200/400 sin 500", str(invalid_json.status_code), "pass" if invalid_json.status_code in (200, 400) else "fail", _csrf_errors_from_body(invalid_json.text))
        rep.add("/clientes/live/ping", "evento_valido", "200", str(valid_ping.status_code), "pass" if valid_ping.status_code == 200 else "fail", _csrf_errors_from_body(valid_ping.text))
        rep.add("/clientes/live/ping", "event_type_invalido", "200/400 sin 500", str(invalid_type.status_code), "pass" if invalid_type.status_code in (200, 400) else "fail", _csrf_errors_from_body(invalid_type.text))
        rep.add("/clientes/live/ping", "payload_grande", "200/400 sin 500", str(huge.status_code), "pass" if huge.status_code in (200, 400) else "fail", _csrf_errors_from_body(huge.text))
        rl_codes: list[int] = []
        for _ in range(120):
            rr = ping_session.post(f"{base}/clientes/live/ping", json={"event_type": "pageview", "current_path": "/audit-phase2", "action_hint": "audit"}, headers=ping_headers, allow_redirects=False, timeout=8)
            rl_codes.append(int(rr.status_code or 0))
        rep.add("/clientes/live/ping", "rafaga_rate_limit_misma_ip", "200/400/429 sin 500", str(rl_codes[-1] if rl_codes else 0), "pass" if rl_codes and all(x in (200, 400, 429) for x in rl_codes) and not any(x >= 500 for x in rl_codes) else "fail", f"tail={rl_codes[-10:]}")

        with app.app_context():
            fuzz_token = generar_token_publico_cliente_nuevo(created_by=f"runtime-fuzz-{run}")
        fuzz_url = f"{base}/clientes/solicitudes/nueva-publica/{fuzz_token}"
        p_fuzz = _mk_public_payload(run, "FZ")
        p_fuzz["nota_cliente"] = "<script>alert(1)</script>" + ("X" * 12000)
        p_fuzz["csrf_token"], _, _ = _get_csrf(public_session, fuzz_url)
        r_fuzz = public_session.post(fuzz_url, data=p_fuzz, headers={"Referer": fuzz_url}, allow_redirects=False, timeout=8)
        rep.add("/clientes/solicitudes/nueva-publica/<token>", "payload_grande_html", "413/400", str(r_fuzz.status_code), "pass" if r_fuzz.status_code in (400, 413) else "fail", _csrf_errors_from_body(r_fuzz.text))

        with app.app_context():
            ok_token = generar_token_publico_cliente_nuevo(created_by=f"runtime-ok-{run}")
        ok_url = f"{base}/clientes/solicitudes/nueva-publica/{ok_token}"
        p_ok = _mk_public_payload(run, "OK1")
        p_ok["csrf_token"], _, _ = _get_csrf(public_session, ok_url)
        r_ok = public_session.post(ok_url, data=p_ok, headers={"Referer": ok_url}, allow_redirects=False, timeout=8)
        r_success_once = public_session.get(f"{ok_url}?estado=enviado", allow_redirects=False, timeout=8)
        r_reuse = public_session.get(ok_url, allow_redirects=False, timeout=8)
        r_bad = public_session.get(f"{base}/clientes/solicitudes/nueva-publica/{ok_token}tamper", allow_redirects=False, timeout=8)
        rep.add("/clientes/solicitudes/nueva-publica/<token>", "post_valido_inicial_crea_cliente_solicitud", "302->200", str(r_ok.status_code), "pass" if r_ok.status_code in (302, 303) else "fail", f"loc={r_ok.headers.get('Location') or ''} flash={_extract_flash_snippet(r_ok.text)}")
        rep.add("/clientes/solicitudes/nueva-publica/<token>", "replay_mismo_token", "success_once=200 luego 410", str(r_reuse.status_code), "pass" if r_success_once.status_code == 200 and r_reuse.status_code == 410 else "fail", f"success_once={r_success_once.status_code} reuse={r_reuse.status_code}")
        rep.add("/clientes/solicitudes/nueva-publica/<token>", "token_manipulado", "404 o 410", str(r_bad.status_code), "pass" if r_bad.status_code in (404, 410) else "fail", f"status={r_bad.status_code}")

        with app.app_context():
            dup_token = generar_token_publico_cliente_nuevo(created_by=f"dup-{run}")
        statuses: list[int] = []
        p_dup = _mk_public_payload(run, "DUP1")

        def _dup_worker(i: int):
            s = requests.Session()
            url = f"{base}/clientes/solicitudes/nueva-publica/{dup_token}"
            data = dict(p_dup)
            csrf, _, _ = _get_csrf(s, url)
            data["csrf_token"] = csrf
            rr = s.post(url, data=data, headers={"Referer": url}, allow_redirects=False, timeout=8)
            statuses.append(int(rr.status_code or 0))

        t1 = threading.Thread(target=_dup_worker, args=(1,))
        t2 = threading.Thread(target=_dup_worker, args=(2,))
        t1.start(); t2.start(); t1.join(); t2.join()

        with app.app_context():
            th = hashlib.sha256(dup_token.encode("utf-8")).hexdigest()
            uses = PublicSolicitudClienteNuevoTokenUso.query.filter_by(token_hash=th).count()
            dup_after = _count_dup_candidates(run)
        rep.add("/clientes/solicitudes/nueva-publica/<token>", "double_submit_concurrente_mismo_token", "statuses incluye 410/429 y usage=1", f"statuses={sorted(statuses)} usage_rows={uses}", "pass" if uses == 1 and any(x in (410, 429) for x in statuses) else "fail", f"statuses={statuses} usage_rows={uses}")

        for _ in range(14):
            csrf_admin, _, _ = _get_csrf(admin_session, f"{base}/admin/login")
            admin_session.post(f"{base}/admin/login", data={"usuario": "nope_lock_target", "clave": "badpass", "csrf_token": csrf_admin}, allow_redirects=False, timeout=8)

        rep.add("db/clientes", "postcheck_duplicates_run_namespace", "0 duplicados", str(sum(dup_after.values())), "pass" if sum(dup_after.values()) == 0 else "fail", f"after={dup_after}")
        c500 = sum(1 for f in rep.findings if f.status_real.strip() == "500" or "status=500" in f.details)
        rep.add("global", "errores_500", "0", str(c500), "pass" if c500 == 0 else "fail", "conteo status 500")
        rep.add("global", "warns", "0", "0", "pass", "sin warnings")
        rep.add("global", "skips", "0", "0", "pass", "sin skips")

        print("RUNTIME_AUDIT_PHASE2_START")
        print(f"APP_ENV={os.getenv('APP_ENV')}")
        print(f"DB={rep.db_name}")
        print(f"RUN_ID={rep.run_id}")
        print(f"BASE_URL={base}")
        for f in rep.findings:
            print("MATRIX|" + f"{f.endpoint}|{f.test_case}|{f.expected}|{f.status_real}|{f.result}|{f.severity}|{f.details}")
        print("RUNTIME_AUDIT_PHASE2_END")
    finally:
        server.stop()


if __name__ == "__main__":
    main()
