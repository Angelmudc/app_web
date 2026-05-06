#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import socket
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from playwright.sync_api import sync_playwright
from sqlalchemy import func
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app as flask_app
from config_app import db
from models import Cliente, Solicitud


ADMIN_USER = "owner_test"
ADMIN_PASS = "admin123"

ARTIFACT_DIR = Path("/tmp/e2e_public_intake")
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
RUN_TAG = str(int(time.time()))


@dataclass
class E2EReport:
    started_at: str
    finished_at: str
    base_url: str
    app_env: str
    db_url_redacted: str
    clientes_created: int
    solicitudes_created: int
    flujos_cliente_nuevo: int
    flujos_cliente_existente: int
    ui_checks: dict[str, Any]
    admin_checks: dict[str, Any]
    runtime_scope: dict[str, Any]
    db_audit: dict[str, Any]
    bugs_encontrados: list[str]
    bugs_corregidos: list[str]
    tests_ejecutados: list[str]
    screenshots: list[str]
    conclusion: str


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    return int(port)


def _ensure_safe_local_env() -> tuple[str, str]:
    app_env = (os.getenv("APP_ENV") or "").strip().lower()
    if app_env != "local":
        raise RuntimeError(f"APP_ENV debe ser 'local'. Actual: {app_env!r}")

    db_url = str(flask_app.config.get("SQLALCHEMY_DATABASE_URI") or "")
    if "domestica_cibao_local" not in db_url:
        raise RuntimeError("BD no segura para esta corrida. Debe apuntar a domestica_cibao_local.")
    if "render.com" in db_url or "mis_candidatas_db" in db_url:
        raise RuntimeError("Bloqueado: parece DB remota/producción.")

    redacted = re.sub(r"//([^:@]+):[^@]+@", r"//\\1:***@", db_url)
    return app_env, redacted


def _start_server() -> tuple[Any, threading.Thread, str]:
    port = _free_port()
    server = make_server("127.0.0.1", port, flask_app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"

    deadline = time.time() + 12
    while time.time() < deadline:
        try:
            r = requests.get(f"{base_url}/health", timeout=0.8)
            if r.status_code in (200, 404):
                return server, thread, base_url
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("No se pudo levantar servidor local para E2E.")


def _login_admin(page, base_url: str):
    page.goto(f"{base_url}/admin/login", wait_until="domcontentloaded")
    page.fill('input[name="usuario"]', ADMIN_USER)
    page.fill('input[name="clave"]', ADMIN_PASS)
    page.click('button[type="submit"]')
    page.wait_for_url("**/admin/**", timeout=15000)


def _extract_link_from_input(page, input_id: str, base_url: str) -> str:
    link = (page.input_value(f"#{input_id}") or "").strip()
    if not link.startswith("http"):
        raise RuntimeError(f"No se pudo extraer link público ({input_id}).")
    parsed = urllib.parse.urlsplit(link)
    local_base = urllib.parse.urlsplit(base_url)
    local_url = urllib.parse.urlunsplit((local_base.scheme, local_base.netloc, parsed.path, parsed.query, parsed.fragment))
    # El link corto /solicitud/<code> tiene landing intermedia; el formulario real vive en /continuar.
    if re.match(r"^/solicitud/[^/]+/?$", parsed.path or ""):
        local_url = local_url.rstrip("/") + "/continuar"
    return local_url


def _check_visible(page, selector: str) -> bool:
    return bool(page.evaluate("(sel)=>{const el=document.querySelector(sel); if(!el) return false; return !el.classList.contains('d-none') && el.getAttribute('aria-hidden')!=='true';}", selector))

def _fill_if_editable(page, selector: str, value: str):
    page.evaluate(
        """(payload) => {
          const el = document.querySelector(payload.sel);
          if (!el) return;
          const style = window.getComputedStyle(el);
          const hidden = (style.display === 'none') || (style.visibility === 'hidden') || (el.offsetParent === null);
          if (el.disabled || hidden) return;
          el.value = String(payload.value ?? '');
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
        }""",
        {"sel": selector, "value": value},
    )


def _set_modalidad_and_horario(page, scenario: dict[str, Any]):
    page.locator(f'input[name="modalidad_grupo"][value="{scenario["modalidad_grupo"]}"]').set_checked(True, force=True)
    page.select_option("#modalidad_especifica_select", value=scenario["modalidad_especifica"])

    if scenario["modalidad_grupo"] == "con_salida_diaria":
        page.fill("#horario_dias_trabajo", scenario.get("horario_dias", "Lunes a viernes"))
        page.fill("#horario_hora_entrada", scenario.get("hora_in", "8:00 AM"))
        page.fill("#horario_hora_salida", scenario.get("hora_out", "5:00 PM"))
    else:
        page.fill("#horario_dormida_entrada", scenario.get("dormida_in", "Lunes 8:00 AM"))
        page.fill("#horario_dormida_salida", scenario.get("dormida_out", "Sabado 12:00 PM"))


def _fill_common_solicitud_fields(page, i: int, scenario: dict[str, Any], *, new_client: bool):
    suffix = f"E2E-{i:03d}"

    if new_client:
        page.fill('input[name="nombre_completo"]', f"Cliente Local {suffix}")
        page.fill('input[name="email_contacto"]', f"e2e_local_{RUN_TAG}_{i:03d}@example.com")
        phone_tail = f"{(int(RUN_TAG[-4:]) + i) % 1000000:06d}"
        page.fill('input[name="telefono_contacto"]', f"829{phone_tail}")
        page.fill('input[name="ciudad_cliente"]', scenario.get("ciudad_cliente", "Santiago"))
        page.fill('input[name="sector_cliente"]', f"Sector {suffix}")

    page.fill("#ciudad_input_ui", scenario.get("ciudad", "Santiago"))
    page.fill("#sector_input_ui", scenario.get("sector", "Centro"))
    page.fill('input[name="rutas_cercanas"]', scenario.get("rutas_cercanas", "Ruta K"))
    _set_modalidad_and_horario(page, scenario)

    for v in scenario.get("edad_requerida", ["26-35"]):
        page.check(f'input[name="edad_requerida"][value="{v}"]')

    page.fill('textarea[name="experiencia"]', scenario.get("experiencia", f"Experiencia requerida {suffix}"))

    for v in scenario.get("funciones", ["limpieza"]):
        page.check(f'input[name="funciones"][value="{v}"]')

    if "otro" in scenario.get("funciones", []):
        page.fill('input[name="funciones_otro"]', "acompanamiento")

    if "envejeciente" in scenario.get("funciones", []):
        page.check(f'input[name="envejeciente_tipo_cuidado"][value="{scenario.get("envejeciente_tipo", "independiente")}"]')
        if scenario.get("envejeciente_tipo") == "encamado":
            if scenario.get("solo_acomp", False):
                page.check('input[name="envejeciente_solo_acompanamiento"]')
            else:
                for r in scenario.get("envejeciente_resp", ["medicamentos"]):
                    page.check(f'input[name="envejeciente_responsabilidades"][value="{r}"]')

    if "limpieza" in scenario.get("funciones", []):
        tl = scenario.get("tipo_lugar", "casa")
        page.select_option('select[name="tipo_lugar"]', value=tl)
        if tl == "otro":
            page.fill('input[name="tipo_lugar_otro"]', "villa")
        hab_val = str(scenario.get("habitaciones", 3))
        ban_val = str(scenario.get("banos", 2))
        page.evaluate(
            """(payload) => {
              function setSegmented(name, otherInputId, raw) {
                const wanted = String(raw || '').trim();
                const radios = Array.from(document.querySelectorAll(`input[type="radio"][name="${name}"]`));
                if (!radios.length) return;
                const direct = radios.find((r) => String(r.value || '').trim() === wanted);
                if (direct) {
                  direct.checked = true;
                  direct.dispatchEvent(new Event('change', { bubbles: true }));
                  return;
                }
                const other = radios.find((r) => String(r.value || '').trim().toLowerCase() === 'otro');
                if (other) {
                  other.checked = true;
                  other.dispatchEvent(new Event('change', { bubbles: true }));
                  const otherInput = document.getElementById(otherInputId);
                  if (otherInput) {
                    otherInput.value = wanted;
                    otherInput.dispatchEvent(new Event('input', { bubbles: true }));
                    otherInput.dispatchEvent(new Event('change', { bubbles: true }));
                  }
                }
              }
              setSegmented('habitaciones_selector', 'habitaciones_otro_input', payload.hab);
              setSegmented('banos_selector', 'banos_otro_input', payload.ban);
            }""",
            {"hab": hab_val, "ban": ban_val},
        )
        pisos_val = "2" if scenario.get("dos_pisos", False) else "1"
        page.evaluate(
            """(pisos) => {
              const target = document.querySelector(`input[type="radio"][name="pisos_selector"][value="${pisos}"]`);
              if (!target) return;
              target.checked = true;
              target.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            pisos_val,
        )
        for area in scenario.get("areas_comunes", ["sala"]):
            page.evaluate(
                """(val) => {
                  const el = document.querySelector(`input[type="checkbox"][name="areas_comunes"][value="${val}"]`);
                  if (!el) return;
                  el.checked = true;
                  el.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                area,
            )
        if "otro" in scenario.get("areas_comunes", []):
            page.fill('input[name="area_otro"]', "balcon")

    _fill_if_editable(page, 'input[name="adultos"]', str(scenario.get("adultos", 2)))
    _fill_if_editable(page, 'input[name="ninos"]', str(scenario.get("ninos", 0)))
    if int(scenario.get("ninos", 0)) > 0:
        _fill_if_editable(page, 'input[name="edades_ninos"]', scenario.get("edades_ninos", "2 y 6"))

    page.fill('input[name="mascota"]', scenario.get("mascota", ""))
    page.fill('input[name="sueldo"]', str(scenario.get("sueldo", "22000")))
    pasaje_mode = str(scenario.get("pasaje_mode", "incluido") or "incluido").strip()
    if pasaje_mode not in {"incluido", "aparte", "otro"}:
        pasaje_mode = "incluido"
    page.evaluate(
        """(mode) => {
          const el = document.querySelector(`input[name="pasaje_mode"][value="${mode}"]`);
          if (!el) return;
          el.checked = true;
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
        }""",
        pasaje_mode,
    )
    if pasaje_mode == "otro":
        _fill_if_editable(page, 'input[name="pasaje_otro_text"]', scenario.get("pasaje_otro_text", "aporte parcial"))
    page.fill('textarea[name="nota_cliente"]', scenario.get("nota", f"nota {suffix}"))


def _validate_ui_behavior(page, checks: dict[str, Any]):
    checks.setdefault("smart_alert_visible", 0)
    checks.setdefault("mascota_note_visible", 0)
    checks.setdefault("santiago_warning_visible", 0)
    checks.setdefault("planchar_modal_ok", 0)
    checks.setdefault("salary_suggestion_visible", 0)
    checks.setdefault("salary_suggestion_hidden_ambiguous", 0)
    checks.setdefault("salary_suggestion_not_present", 0)

    if _check_visible(page, "#wrap_ninos_limpieza_smart_alert"):
        checks["smart_alert_visible"] += 1
    if _check_visible(page, "#wrap_mascota_secondary_note"):
        checks["mascota_note_visible"] += 1
    if _check_visible(page, "#wrap_santiago_sin_ruta_notice"):
        checks["santiago_warning_visible"] += 1

    try:
        if page.is_checked('input[name="funciones"][value="planchar"]'):
            page.wait_for_selector("#funciones_planchar_modal:not(.d-none)", timeout=5000)
            page.click("#funciones_planchar_continue")
            checks["planchar_modal_ok"] += 1
    except Exception:
        pass

    try:
        page.wait_for_timeout(500)
        if _check_visible(page, "#salarySuggestionBox"):
            checks["salary_suggestion_visible"] += 1
        if page.locator("#salarySuggestionBox").count() == 0:
            checks["salary_suggestion_not_present"] += 1
    except Exception:
        pass


def _accept_terms_and_submit(page, *, new_client: bool):
    try:
        if _check_visible(page, "#funciones_planchar_modal:not(.d-none)"):
            page.click("#funciones_planchar_continue")
            page.wait_for_timeout(200)
    except Exception:
        pass

    if new_client:
        submit_btn = "#publicSubmitNuevaBtn"
        terms_checkbox = "#acepta_politica_nueva"
        reject_btn = "#termsRejectNuevaBtn"
    else:
        submit_btn = "#publicSubmitBtn"
        terms_checkbox = "#acepta_politica"
        reject_btn = "#termsRejectBtn"

    assert page.is_disabled(submit_btn)
    page.check(terms_checkbox, force=True)
    page.wait_for_timeout(120)
    assert not page.is_disabled(submit_btn)
    if page.locator(reject_btn).count() > 0:
        assert not page.is_visible(reject_btn)

    page.click(submit_btn)


def _verify_success_page(page):
    page.wait_for_load_state("networkidle", timeout=15000)
    txt = (page.text_content("body") or "").lower()
    success_markers = [
        "solicitud enviada",
        "recibimos tu solicitud",
        "solicitud registrada",
        "solicitud recibida correctamente",
        "nueva solicitud registrada correctamente",
        "enlace ya utilizado",
    ]
    if any(m in txt for m in success_markers):
        return
    if "revisa los campos marcados" in txt or "debes aceptar los términos" in txt:
        errs = page.evaluate(
            """() => Array.from(document.querySelectorAll('.invalid-feedback'))
            .map(n => (n.textContent || '').trim()).filter(Boolean)"""
        )
        raise RuntimeError(f"Envío no exitoso: validaciones={errs}")
    raise RuntimeError("No se detectó pantalla de éxito tras enviar formulario público.")


def _token_consumed_check(context, token_link: str):
    p = context.new_page()
    try:
        r = p.goto(token_link, wait_until="domcontentloaded", timeout=12000)
        status = r.status if r else 0
        body = (p.text_content("body") or "").lower()
        if status not in (410, 200):
            raise RuntimeError(f"Token no en estado esperado tras uso. status={status}")
        if status == 200 and "solicitud enviada" in body:
            p.goto(token_link, wait_until="domcontentloaded", timeout=12000)
            body = (p.text_content("body") or "").lower()
        if "ya fue utilizado" not in body and "enlace ya utilizado" not in body and "expir" not in body:
            raise RuntimeError("No se confirmó consumo de token en la UI.")
    finally:
        p.close()


def _extract_new_entities_since(ts_start: datetime) -> tuple[list[Cliente], list[Solicitud]]:
    with flask_app.app_context():
        clientes = (
            Cliente.query
            .filter(Cliente.email.like(f"e2e_local_{RUN_TAG}_%@example.com"))
            .order_by(Cliente.id.asc())
            .all()
        )
        solicitudes = (
            Solicitud.query
            .join(Cliente, Cliente.id == Solicitud.cliente_id)
            .filter(Cliente.email.like(f"e2e_local_{RUN_TAG}_%@example.com"))
            .order_by(Solicitud.id.asc())
            .all()
        )
        return clientes, solicitudes


def _audit_db(clientes: list[Cliente], solicitudes: list[Solicitud]) -> dict[str, Any]:
    cliente_ids = [int(c.id) for c in clientes]
    with flask_app.app_context():
        dups_email = (
            db.session.query(Cliente.email, func.count(Cliente.id))
            .filter(Cliente.id.in_(cliente_ids))
            .group_by(Cliente.email)
            .having(func.count(Cliente.id) > 1)
            .all()
        ) if cliente_ids else []

        dups_tel = (
            db.session.query(Cliente.telefono, func.count(Cliente.id))
            .filter(Cliente.id.in_(cliente_ids))
            .group_by(Cliente.telefono)
            .having(func.count(Cliente.id) > 1)
            .all()
        ) if cliente_ids else []

        sin_cliente = sum(1 for s in solicitudes if not getattr(s, "cliente_id", None))
        terms_ok = sum(1 for s in solicitudes if bool(getattr(s, "terms_accepted", False)))
        source_counts: dict[str, int] = {}
        review_counts: dict[str, int] = {}
        sueldo_missing = 0
        notas_dirty = 0

        for s in solicitudes:
            src = str(getattr(s, "public_form_source", "") or "")
            rev = str(getattr(s, "review_status", "") or "")
            source_counts[src] = source_counts.get(src, 0) + 1
            review_counts[rev] = review_counts.get(rev, 0) + 1
            if not str(getattr(s, "sueldo", "") or "").strip():
                sueldo_missing += 1
            nota = str(getattr(s, "nota_cliente", "") or "")
            if "  " in nota:
                notas_dirty += 1

        return {
            "clientes": len(clientes),
            "solicitudes": len(solicitudes),
            "duplicados_email": [(e, int(c)) for e, c in dups_email],
            "duplicados_telefono": [(t, int(c)) for t, c in dups_tel],
            "solicitudes_sin_cliente": sin_cliente,
            "terms_accepted_true": terms_ok,
            "public_form_source": source_counts,
            "review_status": review_counts,
            "sueldo_missing": sueldo_missing,
            "notas_dirty": notas_dirty,
        }


def _scenario(i: int) -> dict[str, Any]:
    pool = [
        {"tag":"ninera_pequenos", "modalidad_grupo":"con_salida_diaria", "modalidad_especifica":"Salida diaria - lunes a viernes", "funciones":["ninos"], "ninos":2, "edades_ninos":"1 y 4", "adultos":2, "ciudad":"Santiago", "sector":"Bella Vista", "rutas_cercanas":"Ruta K", "sueldo":"19000", "pasaje_mode":"aparte"},
        {"tag":"envejeciente_indep", "modalidad_grupo":"con_salida_diaria", "modalidad_especifica":"Salida diaria - 2 días a la semana", "funciones":["envejeciente"], "envejeciente_tipo":"independiente", "adultos":1, "ciudad":"Moca", "sector":"Centro", "rutas_cercanas":"Ruta M", "sueldo":"20000", "pasaje_mode":"incluido"},
        {"tag":"limpieza_apto", "modalidad_grupo":"con_salida_diaria", "modalidad_especifica":"Salida diaria - 3 días a la semana", "funciones":["limpieza"], "tipo_lugar":"apto", "habitaciones":1, "banos":1, "areas_comunes":["sala"], "ciudad":"Santiago", "sector":"Los Jardines", "rutas_cercanas":"Ruta A", "sueldo":"16000", "pasaje_mode":"incluido"},
        {"tag":"ninos_limpieza", "modalidad_grupo":"con_salida_diaria", "modalidad_especifica":"Salida diaria - lunes a viernes", "funciones":["limpieza","ninos"], "ninos":2, "edades_ninos":"2 y 6", "tipo_lugar":"casa", "habitaciones":3, "banos":2, "areas_comunes":["sala","cocina"], "ciudad":"Santiago", "sector":"Cerros", "rutas_cercanas":"", "sueldo":"21000", "pasaje_mode":"aparte"},
        {"tag":"adolescentes", "modalidad_grupo":"con_salida_diaria", "modalidad_especifica":"Salida diaria - lunes a viernes", "funciones":["ninos","cocinar"], "ninos":2, "edades_ninos":"13 y 16", "adultos":2, "ciudad":"La Vega", "sector":"Centro", "rutas_cercanas":"Ruta B", "sueldo":"18000", "pasaje_mode":"incluido"},
        {"tag":"casa_grande", "modalidad_grupo":"con_dormida", "modalidad_especifica":"Con dormida 💤 lunes a sábado", "funciones":["limpieza","cocinar","lavar","planchar"], "tipo_lugar":"casa", "habitaciones":6, "banos":4, "dos_pisos":True, "areas_comunes":["sala","patio","cocina"], "ciudad":"Santiago", "sector":"Las Colinas", "rutas_cercanas":"Ruta C", "sueldo":"30000", "pasaje_mode":"aparte"},
        {"tag":"apto_pequeno", "modalidad_grupo":"con_salida_diaria", "modalidad_especifica":"Salida diaria - 1 día a la semana", "funciones":["limpieza"], "tipo_lugar":"apto", "habitaciones":1, "banos":1, "areas_comunes":["sala"], "ciudad":"Santiago", "sector":"Pekín", "rutas_cercanas":"Ruta D", "sueldo":"9000", "pasaje_mode":"incluido"},
        {"tag":"fin_semana", "modalidad_grupo":"con_salida_diaria", "modalidad_especifica":"Salida diaria - fin de semana", "funciones":["limpieza"], "tipo_lugar":"casa", "habitaciones":2, "banos":1, "areas_comunes":["sala","otro"], "ciudad":"Puerto Plata", "sector":"Centro", "rutas_cercanas":"Ruta P", "sueldo":"13000", "pasaje_mode":"otro", "pasaje_otro_text":"mitad y mitad"},
        {"tag":"dormida_quincenal", "modalidad_grupo":"con_dormida", "modalidad_especifica":"Con dormida 💤 quincenal", "funciones":["envejeciente"], "envejeciente_tipo":"encamado", "envejeciente_resp":["medicamentos","movilidad"], "adultos":1, "ciudad":"Santiago", "sector":"Gurabo", "rutas_cercanas":"Ruta K", "sueldo":"28000", "pasaje_mode":"aparte"},
        {"tag":"otro_funcion", "modalidad_grupo":"con_salida_diaria", "modalidad_especifica":"Salida diaria - lunes a sábado", "funciones":["envejeciente","otro"], "envejeciente_tipo":"encamado", "solo_acomp":True, "adultos":1, "ciudad":"San Francisco", "sector":"Centro", "rutas_cercanas":"Ruta S", "sueldo":"22000", "pasaje_mode":"incluido"},
        {"tag":"horario_otro", "modalidad_grupo":"con_salida_diaria", "modalidad_especifica":"Salida diaria otro", "hora_in":"9:00 AM", "hora_out":"7:00 PM", "horario_dias":"Martes a sábado", "funciones":["limpieza","cocinar"], "tipo_lugar":"casa", "habitaciones":3, "banos":2, "ciudad":"Santiago", "sector":"Ensanche", "rutas_cercanas":"Ruta T", "sueldo":"23000", "pasaje_mode":"aparte"},
        {"tag":"ninera_dormida", "modalidad_grupo":"con_dormida", "modalidad_especifica":"Con dormida 💤 lunes a viernes", "funciones":["ninos"], "ninos":1, "edades_ninos":"3", "ciudad":"Santiago", "sector":"Villa Olga", "rutas_cercanas":"Ruta A", "sueldo":"26000", "pasaje_mode":"incluido"},
    ]
    s = dict(pool[i % len(pool)])
    if not str(s.get("sueldo", "")).strip():
        s["sueldo"] = "21000"
    return s


def _path(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).path or "").lower()
    except Exception:
        return ""


def _is_admin_live_stream_url(url: str) -> bool:
    return "/admin/live/invalidation/stream" in _path(url)


def _is_external_runtime_noise(url: str) -> bool:
    p = _path(url)
    return (
        _is_admin_live_stream_url(url)
        or p.startswith("/admin/live/")
        or p.startswith("/clientes/live/")
        or p.startswith("/secretarias/live/")
        or "/realtime/" in p
        or p.endswith("/stream")
    )


def run() -> E2EReport:
    started = datetime.utcnow()
    app_env, db_redacted = _ensure_safe_local_env()
    flask_app.config.update(
        TESTING=False,
        WTF_CSRF_ENABLED=False,
        SALARY_SUGGESTION_ENABLED=False,
        ADMIN_LIVE_SSE_ENABLED=False,
        CLIENTES_LIVE_SSE_ENABLED=False,
    )

    server, thread, base_url = _start_server()

    bugs: list[str] = []
    fixed: list[str] = []
    screenshots: list[str] = []
    ui_checks: dict[str, Any] = {}
    admin_checks: dict[str, Any] = {}
    created_client_ids: list[int] = []

    new_flows = 20
    existing_flows = 8

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context()
            runtime = {
                "salary_endpoint_calls": 0,
                "submit_success": 0,
                "token_consumed_ok": True,
                "request_failed": [],
                "responses_4xx_5xx": [],
                "js_errors": [],
                "console_errors": [],
                "public_form_critical_errors": [],
                "external_runtime_noise": [],
                "admin_live_stream_failures": [],
            }

            def _wire_runtime_monitor(pg, scope: str):
                def on_request(req):
                    if "/clientes/api/sueldo-sugerido" in (req.url or ""):
                        runtime["salary_endpoint_calls"] += 1

                def on_request_failed(req):
                    item = {"scope": scope, "url": req.url, "method": req.method, "failure": str(req.failure)}
                    runtime["request_failed"].append(item)
                    if _is_admin_live_stream_url(req.url or ""):
                        runtime["admin_live_stream_failures"].append(item)
                    if _is_external_runtime_noise(req.url or ""):
                        runtime["external_runtime_noise"].append(item)
                    elif scope == "public_form":
                        runtime["public_form_critical_errors"].append({"type": "request_failed", **item})

                def on_page_error(err):
                    msg = str(err)
                    item = {"scope": scope, "error": msg}
                    runtime["js_errors"].append(item)
                    if scope == "public_form":
                        runtime["public_form_critical_errors"].append({"type": "js_error", **item})
                    else:
                        runtime["external_runtime_noise"].append({"type": "js_error", **item})

                def on_console(msg):
                    if msg.type != "error":
                        return
                    item = {"scope": scope, "error": msg.text}
                    runtime["console_errors"].append(item)
                    if _is_external_runtime_noise(msg.text or "") or scope != "public_form":
                        runtime["external_runtime_noise"].append({"type": "console_error", **item})
                    else:
                        runtime["public_form_critical_errors"].append({"type": "console_error", **item})

                def on_response(resp):
                    status = int(resp.status)
                    if status < 400:
                        return
                    item = {"scope": scope, "url": resp.url, "status": status}
                    runtime["responses_4xx_5xx"].append(item)
                    if _is_external_runtime_noise(resp.url or ""):
                        runtime["external_runtime_noise"].append({"type": "http_error", **item})
                    elif scope == "public_form":
                        runtime["public_form_critical_errors"].append({"type": "http_error", **item})

                pg.on("request", on_request)
                pg.on("requestfailed", on_request_failed)
                pg.on("pageerror", on_page_error)
                pg.on("console", on_console)
                pg.on("response", on_response)

            page = context.new_page()
            _wire_runtime_monitor(page, "admin_runtime")
            _login_admin(page, base_url)

            # Flujo cliente nuevo
            for i in range(new_flows):
                page.goto(f"{base_url}/admin/solicitudes/nueva-publica/link", wait_until="domcontentloaded")
                token_link = _extract_link_from_input(page, "linkPublicoNuevo", base_url)

                public_page = context.new_page()
                _wire_runtime_monitor(public_page, "public_form")
                try:
                    public_page.goto(token_link, wait_until="domcontentloaded")
                    public_page.wait_for_selector('input[name="nombre_completo"]', timeout=12000)
                    scenario = _scenario(i)
                    _fill_common_solicitud_fields(public_page, i + 1, scenario, new_client=True)
                    _validate_ui_behavior(public_page, ui_checks)
                    _accept_terms_and_submit(public_page, new_client=True)
                    _verify_success_page(public_page)
                    runtime["submit_success"] += 1
                    shot = ARTIFACT_DIR / f"new_success_{i+1:03d}.png"
                    if i < 3:
                        public_page.screenshot(path=str(shot), full_page=True)
                        screenshots.append(str(shot))
                finally:
                    public_page.close()

                try:
                    _token_consumed_check(context, token_link)
                except Exception as e:
                    runtime["token_consumed_ok"] = False
                    runtime["public_form_critical_errors"].append({"type": "token_not_consumed", "error": str(e), "url": token_link})
                    raise

            clientes, solicitudes = _extract_new_entities_since(started)
            created_client_ids = [int(c.id) for c in clientes]
            if len(created_client_ids) < 20:
                raise RuntimeError(f"Se esperaban >=20 clientes nuevos, encontrados {len(created_client_ids)}")

            # Flujo cliente existente
            for j in range(existing_flows):
                cid = created_client_ids[j % len(created_client_ids)]
                page.goto(f"{base_url}/admin/clientes/{cid}/solicitudes/link-publico", wait_until="domcontentloaded")
                token_link = _extract_link_from_input(page, "linkPublico", base_url)

                p2 = context.new_page()
                _wire_runtime_monitor(p2, "public_form")
                try:
                    p2.goto(token_link, wait_until="domcontentloaded")
                    sc = _scenario(j + 100)
                    _fill_common_solicitud_fields(p2, 1000 + j + 1, sc, new_client=False)
                    # Ambiguo: sueldo sugerido no debe mostrarse con funciones no estructuradas
                    if "otro" in sc.get("funciones", []):
                        p2.wait_for_timeout(400)
                        if not _check_visible(p2, "#salarySuggestionBox"):
                            ui_checks["salary_suggestion_hidden_ambiguous"] = ui_checks.get("salary_suggestion_hidden_ambiguous", 0) + 1
                    _accept_terms_and_submit(p2, new_client=False)
                    _verify_success_page(p2)
                    runtime["submit_success"] += 1
                finally:
                    p2.close()

                try:
                    _token_consumed_check(context, token_link)
                except Exception as e:
                    runtime["token_consumed_ok"] = False
                    runtime["public_form_critical_errors"].append({"type": "token_not_consumed", "error": str(e), "url": token_link})
                    raise

            # Admin checks
            page.goto(f"{base_url}/admin/clientes", wait_until="domcontentloaded")
            page.fill('input[name="q"]', "e2e_local_")
            page.press('input[name="q"]', "Enter")
            page.wait_for_timeout(900)
            admin_checks["clientes_list_busca"] = "e2e_local_" in ((page.text_content("body") or "").lower())

            page.goto(f"{base_url}/admin/solicitudes/publicas/nuevas", wait_until="domcontentloaded")
            body = (page.text_content("body") or "")
            admin_checks["bandeja_nuevas_visible"] = "Solicitudes nuevas por revisar" in body
            admin_checks["solicitudes_hoy_visible"] = "Solicitudes de hoy" in body

            rev_btn = page.locator(
                'form[action*="/review-status"] button:has-text("Marcar revisado"), '
                'form[action*="/review-status"] button:has-text("Revisado")'
            ).first
            if rev_btn.count() > 0:
                rev_btn.click()
                page.wait_for_timeout(600)
                admin_checks["marcar_revisado_ok"] = True
            else:
                admin_checks["marcar_revisado_ok"] = False

            # Delete checks: with solicitudes should block
            page.goto(f"{base_url}/admin/clientes/{created_client_ids[0]}", wait_until="domcontentloaded")
            page.click('[data-bs-target="#deleteClienteModal"]')
            page.fill('#delete_cliente_confirm_input', 'ELIMINAR')
            page.click('#deleteClienteModal form button[type="submit"]')
            page.wait_for_timeout(800)
            txt_after = (page.text_content("body") or "").lower()
            admin_checks["cliente_con_solicitudes_no_elimina"] = ("no puede eliminar" in txt_after) or ("información asociada" in txt_after)
            page.goto(f"{base_url}/admin/solicitudes/copiar", wait_until="domcontentloaded")
            admin_checks["copiar_publicar_view_ok"] = "copiar solicitudes" in (page.text_content("body") or "").lower()

            page.goto(f"{base_url}/secretarias/solicitudes/buscar", wait_until="domcontentloaded")
            body_secretarias = (page.text_content("body") or "").lower()
            admin_checks["secretarias_buscador_ok"] = ("buscar solicitudes" in body_secretarias) or ("solicitudes" in body_secretarias)

            admin_checks["salary_endpoint_calls"] = int(runtime["salary_endpoint_calls"])
            admin_checks["http_4xx_5xx_count"] = len(runtime["responses_4xx_5xx"])
            admin_checks["js_errors_count"] = len(runtime["js_errors"])
            admin_checks["console_errors_count"] = len(runtime["console_errors"])
            admin_checks["request_failed_count"] = len(runtime["request_failed"])
            admin_checks["public_form_critical_errors"] = len(runtime["public_form_critical_errors"])
            admin_checks["external_runtime_noise"] = len(runtime["external_runtime_noise"])
            admin_checks["admin_live_stream_failures"] = len(runtime["admin_live_stream_failures"])
            admin_checks["submit_success"] = int(runtime["submit_success"])
            admin_checks["token_consumed_ok"] = bool(runtime["token_consumed_ok"])

            browser.close()

        clientes, solicitudes = _extract_new_entities_since(started)

        # Verificación no duplicado cliente en flujos existentes
        if len(clientes) != 20:
            bugs.append(f"Esperado 20 clientes creados por flujo nuevo, encontrados {len(clientes)}")

        if len(solicitudes) < 28:
            bugs.append(f"Esperado >=28 solicitudes, encontradas {len(solicitudes)}")

        audit = _audit_db(clientes, solicitudes)
        if audit["duplicados_email"]:
            bugs.append("Duplicados por email detectados")
        if audit["duplicados_telefono"]:
            bugs.append("Duplicados por teléfono detectados")
        if audit["solicitudes_sin_cliente"] > 0:
            bugs.append("Solicitudes huérfanas sin cliente")
        if int(admin_checks.get("salary_endpoint_calls", 0)) > 0:
            bugs.append("Detectadas llamadas automáticas al endpoint de sueldo sugerido")
        if int(admin_checks.get("public_form_critical_errors", 0)) > 0:
            bugs.append("Detectados errores críticos del formulario público")
        if not bool(admin_checks.get("token_consumed_ok", False)):
            bugs.append("Token no consumido correctamente en al menos un flujo")
        if int(admin_checks.get("submit_success", 0)) != (new_flows + existing_flows):
            bugs.append("Cantidad de submit exitosos menor al total esperado")

        conclusion = "Luz verde para uso real controlado"
        if bugs:
            conclusion = "No listo todavía"

        return E2EReport(
            started_at=started.isoformat() + "Z",
            finished_at=datetime.utcnow().isoformat() + "Z",
            base_url=base_url,
            app_env=app_env,
            db_url_redacted=db_redacted,
            clientes_created=len(clientes),
            solicitudes_created=len(solicitudes),
            flujos_cliente_nuevo=new_flows,
            flujos_cliente_existente=existing_flows,
            ui_checks=ui_checks,
            admin_checks=admin_checks,
            runtime_scope={
                "public_form_critical_errors": runtime["public_form_critical_errors"],
                "external_runtime_noise": runtime["external_runtime_noise"],
                "admin_live_stream_failures": runtime["admin_live_stream_failures"],
                "responses_4xx_5xx": runtime["responses_4xx_5xx"],
                "request_failed": runtime["request_failed"],
                "js_errors": runtime["js_errors"],
                "console_errors": runtime["console_errors"],
            },
            db_audit=audit,
            bugs_encontrados=bugs,
            bugs_corregidos=fixed,
            tests_ejecutados=["scripts/e2e/public_intake_realistic_e2e.py"],
            screenshots=screenshots,
            conclusion=conclusion,
        )
    finally:
        try:
            server.shutdown()
        except Exception:
            pass
        thread.join(timeout=3)


def main() -> int:
    report = run()
    out_path = ARTIFACT_DIR / f"report_{int(time.time())}.json"
    out_path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    print(f"\nReporte guardado en: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
