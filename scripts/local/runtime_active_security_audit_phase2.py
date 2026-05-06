#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import random
import re
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app
from config_app import db
from models import Cliente, PublicSolicitudClienteNuevoTokenUso, Solicitud
from clientes.routes import (
    generar_token_publico_cliente_nuevo,
)

CSRF_RE = re.compile(r'name="csrf_token"[^>]*value="([^"]+)"', re.I)


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

    def add(
        self,
        endpoint: str,
        test_case: str,
        expected: str = "",
        status_real: str = "",
        result: str = "",
        details: str = "",
        severity: str = "info",
    ):
        # Compatibilidad con llamadas legado: add(area, test, result, details, severity?)
        if expected in {"ok", "fail", "warn", "pass"} and not result and status_real.startswith("status="):
            result = expected
            details = status_real
            status_real = status_real.replace("status=", "", 1)
            expected = "n/a"
        self.findings.append(
            Finding(
                endpoint=endpoint,
                test_case=test_case,
                expected=expected,
                status_real=status_real,
                result=result,
                details=details,
                severity=severity,
            )
        )


def _require_local() -> str:
    env = (os.getenv("APP_ENV") or "").strip().lower()
    if env != "local":
        raise RuntimeError(f"Bloqueado: APP_ENV debe ser 'local'. actual={env!r}")
    with db.engine.connect() as conn:
        db_name = str(conn.execute(text("select current_database()")).scalar() or "")
    if db_name != "domestica_cibao_local":
        raise RuntimeError(f"Bloqueado: DB debe ser domestica_cibao_local. actual={db_name!r}")
    return db_name


def _csrf_from_html(html: str) -> str:
    m = CSRF_RE.search(html or "")
    return (m.group(1) if m else "").strip()


def _get_csrf_for_url(tc, url: str) -> str:
    page = tc.get(url, follow_redirects=False)
    return _csrf_from_html(page.get_data(as_text=True))


def _mk_public_payload(run: str, suffix: str) -> dict[str, Any]:
    run_tail = run[-12:].lower()
    suffix_norm = re.sub(r"[^a-z0-9]", "", suffix.lower())[:8] or "x"
    phone_tail = f"{int(hashlib.sha256(f'{run}-{suffix_norm}'.encode()).hexdigest(), 16) % 10_000_000:07d}"
    return {
        "nombre_completo": f"AUDIT_{run}_{suffix_norm}",
        "email_contacto": f"audit.{run_tail}.{suffix_norm}@example.com",
        "telefono_contacto": f"809{phone_tail}",
        "ciudad_cliente": "Santiago",
        "sector_cliente": f"Sector {suffix}",
        "ciudad_sector": "Santiago / Centro",
        "rutas_cercanas": "Ruta K",
        "modalidad_trabajo": "Salida diaria - lunes a viernes",
        "modalidad_grupo": "con_salida_diaria",
        "modalidad_especifica": "l-v",
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
    like_mail = f"%{run_tag}%"
    like_name = f"%AUDIT_{run}%"
    like_phone = "809%"
    rows = db.session.execute(
        text(
            """
            SELECT
                COALESCE(SUM(CASE WHEN email ILIKE :like_mail THEN 1 ELSE 0 END), 0) AS by_email,
                COALESCE(SUM(CASE WHEN nombre_completo ILIKE :like_name THEN 1 ELSE 0 END), 0) AS by_name,
                COALESCE(SUM(CASE WHEN telefono LIKE :like_phone AND nombre_completo ILIKE :like_name THEN 1 ELSE 0 END), 0) AS by_phone_name
            FROM clientes
            """
        ),
        {"like_mail": like_mail, "like_name": like_name, "like_phone": like_phone},
    ).mappings().first() or {}
    return {
        "by_email": int(rows.get("by_email") or 0),
        "by_name": int(rows.get("by_name") or 0),
        "by_phone_name": int(rows.get("by_phone_name") or 0),
    }


def _create_isolated_clients(run: str) -> tuple[Cliente, Cliente, Solicitud, Solicitud]:
    run_l = run.lower()
    c1 = Cliente(
        codigo=f"A{run[-6:]}1",
        nombre_completo=f"AUDIT Cliente Uno {run}",
        email=f"audit.cli1.{run_l}@example.com",
        username=f"audit_cli1_{run_l[-8:]}",
        telefono=f"829{int(run[-6:])%10_000_000:07d}",
        ciudad="Santiago",
        sector="Centro",
        role="cliente",
        is_active=True,
        acepto_politicas=True,
        fecha_acepto_politicas=datetime.utcnow(),
    )
    c1.password_hash = generate_password_hash("Audit#12345", method="pbkdf2:sha256")

    c2 = Cliente(
        codigo=f"B{run[-6:]}2",
        nombre_completo=f"AUDIT Cliente Dos {run}",
        email=f"audit.cli2.{run_l}@example.com",
        username=f"audit_cli2_{run_l[-8:]}",
        telefono=f"849{(int(run[-6:])+1)%10_000_000:07d}",
        ciudad="Santiago",
        sector="Centro",
        role="cliente",
        is_active=True,
        acepto_politicas=True,
        fecha_acepto_politicas=datetime.utcnow(),
    )
    c2.password_hash = generate_password_hash("Audit#12345", method="pbkdf2:sha256")
    db.session.add_all([c1, c2])
    db.session.flush()

    s1 = Solicitud(cliente_id=c1.id, codigo_solicitud=f"S-{run[-8:]}-1")
    s1.modalidad_trabajo = "Salida diaria - lunes a viernes"
    s1.horario = "L-V"
    s1.funciones = ["limpieza"]
    s1.tipo_lugar = "Casa"
    s1.habitaciones = 2
    s1.banos = 1
    s1.adultos = 2
    s1.ninos = 0
    s1.sueldo = "19000"
    s1.areas_comunes = ["sala"]
    s1.terms_accepted = True

    s2 = Solicitud(cliente_id=c2.id, codigo_solicitud=f"S-{run[-8:]}-2")
    s2.modalidad_trabajo = "Salida diaria - lunes a viernes"
    s2.horario = "L-V"
    s2.funciones = ["limpieza"]
    s2.tipo_lugar = "Casa"
    s2.habitaciones = 2
    s2.banos = 1
    s2.adultos = 2
    s2.ninos = 0
    s2.sueldo = "19000"
    s2.areas_comunes = ["sala"]
    s2.terms_accepted = True

    db.session.add_all([s1, s2])
    db.session.commit()
    return c1, c2, s1, s2


def _client_login(tc, username: str, password: str) -> int:
    page = tc.get("/clientes/login")
    token = _csrf_from_html(page.get_data(as_text=True))
    resp = tc.post(
        "/clientes/login",
        data={"username": username, "password": password, "csrf_token": token},
        follow_redirects=False,
    )
    return resp.status_code


def _admin_login_bad_loop(ip: str, n: int = 12) -> list[int]:
    codes = []
    tc = app.test_client()
    page = tc.get("/admin/login", environ_overrides={"REMOTE_ADDR": ip})
    token = _csrf_from_html(page.get_data(as_text=True))
    for i in range(n):
        r = tc.post(
            "/admin/login",
            data={"usuario": "nope_lock_target", "clave": "badpass", "csrf_token": token},
            follow_redirects=False,
            environ_overrides={"REMOTE_ADDR": ip},
        )
        codes.append(r.status_code)
    return codes


def main() -> None:
    run = f"AUD{datetime.now().strftime('%Y%m%d%H%M%S')}{os.getpid():05d}{random.randint(1000, 9999)}"
    with app.app_context():
        db_name = _require_local()
        app.config["TESTING"] = False
        rep = Report(run_id=run, db_name=db_name)
        expected = {
            "ok_200": "200",
            "replay_410": "410",
            "rl_429": "429",
            "payload_large": "413|400",
            "invalid_type_400": "400",
        }

        dup_before = _count_dup_candidates(run)
        dup_before_total = sum(dup_before.values())
        rep.add(
            endpoint="db/clientes",
            test_case="precheck_duplicates_run_namespace",
            expected="0 duplicados",
            status_real=str(dup_before_total),
            result="pass" if dup_before_total == 0 else "fail",
            details=f"before={dup_before}",
            severity="high" if dup_before_total else "info",
        )

        c1, c2, s1, s2 = _create_isolated_clients(run)

        # 1) Formularios públicos / fuzz
        tc = app.test_client()
        token_fuzz = generar_token_publico_cliente_nuevo(created_by=f"runtime-fuzz-{run}")
        payload_fuzz = _mk_public_payload(run, "FZ")
        payload_fuzz["nota_cliente"] = "<script>alert(1)</script>" + ("X" * 12000)
        fuzz_url = f"/clientes/solicitudes/nueva-publica/{token_fuzz}"
        payload_fuzz["csrf_token"] = _get_csrf_for_url(tc, fuzz_url)
        r_pub = tc.post(fuzz_url, data=payload_fuzz, follow_redirects=False, headers={"Referer": f"http://localhost{fuzz_url}"})
        rep.add(
            endpoint="/clientes/solicitudes/nueva-publica/<token>",
            test_case="payload_grande_html",
            expected=expected["payload_large"],
            status_real=str(r_pub.status_code),
            result="pass" if r_pub.status_code in (400, 413) else "fail",
            details=f"status={r_pub.status_code}",
            severity="high" if r_pub.status_code >= 500 else "info",
        )

        # token replay / manipulación
        token_new = generar_token_publico_cliente_nuevo(created_by=f"runtime-ok-{run}")
        payload_ok = _mk_public_payload(run, "OK1")
        ok_url = f"/clientes/solicitudes/nueva-publica/{token_new}"
        payload_ok["csrf_token"] = _get_csrf_for_url(tc, ok_url)
        r_ok = tc.post(ok_url, data=payload_ok, follow_redirects=False, headers={"Referer": f"http://localhost{ok_url}"})
        r_reuse = tc.get(f"/clientes/solicitudes/nueva-publica/{token_new}", follow_redirects=False)
        bad_token = token_new[:-1] + ("A" if token_new[-1] != "A" else "B")
        r_bad = tc.get(f"/clientes/solicitudes/nueva-publica/{bad_token}", follow_redirects=False)
        rep.add(
            endpoint="/clientes/solicitudes/nueva-publica/<token>",
            test_case="post_valido_inicial_crea_cliente_solicitud",
            expected="302->200 y 1 cliente+1 solicitud",
            status_real=str(r_ok.status_code),
            result="pass" if r_ok.status_code in (302, 303) else "fail",
            details=f"status={r_ok.status_code}",
            severity="high" if r_ok.status_code >= 500 else "info",
        )
        rep.add(
            endpoint="/clientes/solicitudes/nueva-publica/<token>",
            test_case="replay_mismo_token",
            expected=expected["replay_410"],
            status_real=str(r_reuse.status_code),
            result="pass" if r_reuse.status_code == 410 else "fail",
            details=f"status={r_reuse.status_code}",
            severity="high" if r_reuse.status_code >= 500 else "info",
        )
        rep.add(
            endpoint="/clientes/solicitudes/nueva-publica/<token>",
            test_case="token_manipulado",
            expected="404|410",
            status_real=str(r_bad.status_code),
            result="pass" if r_bad.status_code in (404, 410) else "fail",
            details=f"status={r_bad.status_code}",
            severity="info",
        )

        # double submit concurrente del mismo token
        token_dup = generar_token_publico_cliente_nuevo(created_by=f"dup-{run}")
        pdup = _mk_public_payload(run, "DUP1")
        statuses: list[int] = []

        def _w(ip_suffix: int):
            c = app.test_client()
            dup_url = f"/clientes/solicitudes/nueva-publica/{token_dup}"
            pdup_local = dict(pdup)
            pdup_local["csrf_token"] = _get_csrf_for_url(c, dup_url)
            rr = c.post(
                dup_url,
                data=pdup_local,
                follow_redirects=False,
                headers={"Referer": f"http://localhost{dup_url}"},
                environ_overrides={"REMOTE_ADDR": f"127.0.8.{ip_suffix}"},
            )
            statuses.append(rr.status_code)

        t1 = threading.Thread(target=_w, args=(1,))
        t2 = threading.Thread(target=_w, args=(2,))
        t1.start(); t2.start(); t1.join(); t2.join()
        th = hashlib.sha256(token_dup.encode("utf-8")).hexdigest()
        uses = PublicSolicitudClienteNuevoTokenUso.query.filter_by(token_hash=th).count()
        rep.add(
            endpoint="/clientes/solicitudes/nueva-publica/<token>",
            test_case="double_submit_concurrente_mismo_token",
            expected="solo 1 creacion + segundo bloqueado",
            status_real=f"statuses={sorted(statuses)} usage_rows={uses}",
            result="pass" if uses == 1 and any(x in (410, 429) for x in statuses) else "fail",
            details=f"statuses={statuses} usage_rows={uses}",
            severity="high" if uses != 1 else "info",
        )

        # 2) IDOR / permisos
        cli1 = app.test_client()
        st_login = _client_login(cli1, c1.username, "Audit#12345")
        rep.add("session", "client_login_isolated_user", "ok" if st_login in (302, 303) else "fail", f"status={st_login}", "high" if st_login not in (302, 303) else "info")

        r_own = cli1.get(f"/clientes/solicitudes/{s1.id}", follow_redirects=True)
        r_idor = cli1.get(f"/clientes/solicitudes/{s2.id}", follow_redirects=True)
        rep.add("idor", "client_access_own_resource", "ok" if r_own.status_code == 200 else "warn", f"status={r_own.status_code}")
        rep.add("idor", "client_access_other_client_resource", "ok" if r_idor.status_code in (403, 404) else "fail", f"status={r_idor.status_code}", "critical" if r_idor.status_code == 200 else "info")

        r_admin_from_client = cli1.get("/admin/solicitudes", follow_redirects=False)
        rep.add("permissions", "admin_route_from_client_session", "ok" if r_admin_from_client.status_code in (302, 303, 401, 403) else "fail", f"status={r_admin_from_client.status_code}", "high" if r_admin_from_client.status_code == 200 else "info")

        pub = app.test_client()
        r_client_from_public = pub.get(f"/clientes/solicitudes/{s1.id}", follow_redirects=False)
        rep.add("permissions", "client_route_from_public", "ok" if r_client_from_public.status_code in (302, 303, 401) else "warn", f"status={r_client_from_public.status_code}")

        # 3) Sesión / cookie alterada / logout reuse
        logout_resp = cli1.get("/clientes/logout", follow_redirects=False)
        after_logout = cli1.get("/clientes/dashboard", follow_redirects=False)
        rep.add("session", "logout_then_reuse_same_session", "ok" if after_logout.status_code in (302, 303, 401) else "fail", f"logout={logout_resp.status_code} after={after_logout.status_code}", "high" if after_logout.status_code == 200 else "info")

        tampered = app.test_client()
        tampered.set_cookie("session", "tampered.invalid.cookie.value")
        r_tampered = tampered.get("/clientes/dashboard", follow_redirects=False)
        rep.add("session", "tampered_cookie", "ok" if r_tampered.status_code in (302, 303, 400, 401) else "warn", f"status={r_tampered.status_code}")

        # 4) /live/ping abuso y payload inválido
        ping_client = app.test_client()
        csrf_page = ping_client.get("/clientes/login")
        ping_csrf = _csrf_from_html(csrf_page.get_data(as_text=True))
        ping_headers = {"X-CSRFToken": ping_csrf, "Referer": "http://localhost/clientes/login"} if ping_csrf else {"Referer": "http://localhost/"}
        invalid_json = ping_client.post("/live/ping", data="{bad-json", content_type="application/json", headers=ping_headers, follow_redirects=False)
        rep.add(
            endpoint="/live/ping",
            test_case="payload_json_invalido",
            expected="400",
            status_real=str(invalid_json.status_code),
            result="pass" if invalid_json.status_code == 400 else "fail",
            details=f"status={invalid_json.status_code}",
        )

        huge = ping_client.post(
            "/live/ping",
            json={"event_type": "heartbeat", "current_path": "/", "blob": "X" * 8000},
            headers=ping_headers,
            follow_redirects=False,
        )
        rep.add(
            endpoint="/live/ping",
            test_case="payload_grande",
            expected=expected["payload_large"],
            status_real=str(huge.status_code),
            result="pass" if huge.status_code in (400, 413) else "fail",
            details=f"status={huge.status_code}",
        )

        valid_ping = ping_client.post(
            "/live/ping",
            json={"event_type": "heartbeat", "current_path": "/", "page_title": "Audit"},
            headers=ping_headers,
            follow_redirects=False,
            environ_overrides={"REMOTE_ADDR": "127.55.55.55"},
        )
        rep.add(
            endpoint="/live/ping",
            test_case="evento_valido",
            expected=expected["ok_200"],
            status_real=str(valid_ping.status_code),
            result="pass" if valid_ping.status_code == 200 else "fail",
            details=f"status={valid_ping.status_code}",
        )

        invalid_type = ping_client.post(
            "/live/ping",
            json={"event_type": "evil_event", "current_path": "/"},
            headers=ping_headers,
            follow_redirects=False,
            environ_overrides={"REMOTE_ADDR": "127.56.56.56"},
        )
        rep.add(
            endpoint="/live/ping",
            test_case="event_type_invalido",
            expected=expected["invalid_type_400"],
            status_real=str(invalid_type.status_code),
            result="pass" if invalid_type.status_code == 400 else "fail",
            details=f"status={invalid_type.status_code}",
        )

        rl_codes = []
        for i in range(120):
            rr = ping_client.post(
                "/live/ping",
                json={"event_type": "pageview", "current_path": "/audit-phase2"},
                headers=ping_headers,
                follow_redirects=False,
                environ_overrides={"REMOTE_ADDR": "127.88.88.88"},
            )
            rl_codes.append(rr.status_code)
        has_429 = any(x == 429 for x in rl_codes)
        rep.add(
            endpoint="/live/ping",
            test_case="rafaga_rate_limit_misma_ip",
            expected=expected["rl_429"],
            status_real="429" if has_429 else str(rl_codes[-1] if rl_codes else "none"),
            result="pass" if has_429 else "fail",
            details=f"sample_tail={rl_codes[-10:]}",
            severity="medium" if not has_429 else "info",
        )

        # 5) admin login brute force
        brute_codes = _admin_login_bad_loop(ip="127.77.77.77", n=14)
        rep.add("auth", "admin_bruteforce_bad_credentials", "ok" if any(c == 429 for c in brute_codes) else "warn", f"codes={brute_codes}")

        # salida compacta
        dup_after = _count_dup_candidates(run)
        dup_after_total = sum(dup_after.values())
        rep.add(
            endpoint="db/clientes",
            test_case="postcheck_duplicates_run_namespace",
            expected="0 duplicados",
            status_real=str(dup_after_total),
            result="pass" if dup_after_total == 0 else "fail",
            details=f"after={dup_after}",
            severity="high" if dup_after_total else "info",
        )
        c500 = sum(1 for f in rep.findings if f.status_real.strip() == "500" or "status=500" in f.details)
        rep.add(
            endpoint="global",
            test_case="errores_500",
            expected="0",
            status_real=str(c500),
            result="pass" if c500 == 0 else "fail",
            details="conteo status 500 en esta corrida",
            severity="high" if c500 else "info",
        )

        print("RUNTIME_AUDIT_PHASE2_START")
        print(f"APP_ENV={os.getenv('APP_ENV')}")
        print(f"DB={rep.db_name}")
        print(f"RUN_ID={rep.run_id}")
        for f in rep.findings:
            print(
                "MATRIX|"
                f"{f.endpoint}|{f.test_case}|{f.expected}|{f.status_real}|{f.result}|{f.severity}|{f.details}"
            )
        print("RUNTIME_AUDIT_PHASE2_END")


if __name__ == "__main__":
    main()
