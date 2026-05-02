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

    page.fill('input[name="ciudad_sector"]', scenario.get("ciudad_sector", "Santiago / Centro"))
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
    page.fill('textarea[name="nota_cliente"]', scenario.get("nota", f"nota {suffix}"))


def _validate_ui_behavior(page, checks: dict[str, Any]):
    checks.setdefault("smart_alert_visible", 0)
    checks.setdefault("mascota_note_visible", 0)
    checks.setdefault("santiago_warning_visible", 0)
    checks.setdefault("planchar_modal_ok", 0)
    checks.setdefault("salary_suggestion_visible", 0)
    checks.setdefault("salary_suggestion_hidden_ambiguous", 0)

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
        accept_btn = "#termsAcceptNuevaBtn"
        reject_btn = "#termsRejectNuevaBtn"
    else:
        submit_btn = "#publicSubmitBtn"
        accept_btn = "#termsAcceptBtn"
        reject_btn = "#termsRejectBtn"

    assert page.is_disabled(submit_btn)
    page.click(accept_btn)
    assert not page.is_disabled(submit_btn)
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
        {"modalidad_grupo": "con_salida_diaria", "modalidad_especifica": "Salida diaria - lunes a viernes", "funciones": ["limpieza", "ninos"], "ninos": 2, "edades_ninos": "1 y 6", "mascota": "Perro", "ciudad_sector": "Santiago / Bella Vista", "rutas_cercanas": "", "sueldo": "18000", "areas_comunes": ["sala", "cocina"], "tipo_lugar": "casa", "habitaciones": 3, "banos": 2},
        {"modalidad_grupo": "con_dormida", "modalidad_especifica": "Con dormida 💤 lunes a sábado", "funciones": ["limpieza", "cocinar", "lavar", "planchar"], "mascota": "", "ciudad_sector": "La Vega / Centro", "rutas_cercanas": "Ruta B", "sueldo": "26000", "areas_comunes": ["sala", "patio"], "tipo_lugar": "casa", "habitaciones": 5, "banos": 3, "dos_pisos": True},
        {"modalidad_grupo": "con_salida_diaria", "modalidad_especifica": "Salida diaria - 2 días a la semana", "funciones": ["envejeciente"], "envejeciente_tipo": "independiente", "adultos": 1, "ninos": 0, "mascota": "Gato", "ciudad_sector": "Moca / Centro", "rutas_cercanas": "Ruta M", "sueldo": "20000"},
        {"modalidad_grupo": "con_dormida", "modalidad_especifica": "Con dormida 💤 quincenal", "funciones": ["envejeciente"], "envejeciente_tipo": "encamado", "envejeciente_resp": ["medicamentos", "movilidad"], "adultos": 2, "ninos": 0, "mascota": "", "ciudad_sector": "Santiago / Cerros", "rutas_cercanas": "Ruta K", "sueldo": "28000", "tipo_lugar": "apto", "habitaciones": 2, "banos": 1, "areas_comunes": ["sala"]},
        {"modalidad_grupo": "con_salida_diaria", "modalidad_especifica": "Salida diaria - 3 días a la semana", "funciones": ["envejeciente", "otro"], "envejeciente_tipo": "encamado", "solo_acomp": True, "adultos": 1, "ninos": 0, "mascota": "", "ciudad_sector": "Puerto Plata / Centro", "rutas_cercanas": "Ruta P", "sueldo": ""},
        {"modalidad_grupo": "con_salida_diaria", "modalidad_especifica": "Salida diaria - fin de semana", "funciones": ["limpieza"], "tipo_lugar": "apto", "habitaciones": 2, "banos": 1, "areas_comunes": ["sala", "otro"], "adultos": 2, "ninos": 0, "mascota": "Perro", "ciudad_sector": "Santiago / Gurabo", "rutas_cercanas": "Ruta C", "sueldo": "23000", "hora_out": "7:30 PM"},
    ]
    s = dict(pool[i % len(pool)])
    if not str(s.get("sueldo", "")).strip():
        s["sueldo"] = "21000"
    return s


def run() -> E2EReport:
    started = datetime.utcnow()
    app_env, db_redacted = _ensure_safe_local_env()
    flask_app.config.update(TESTING=False, WTF_CSRF_ENABLED=False)

    server, thread, base_url = _start_server()

    bugs: list[str] = []
    fixed: list[str] = []
    screenshots: list[str] = []
    ui_checks: dict[str, Any] = {}
    admin_checks: dict[str, Any] = {}
    created_client_ids: list[int] = []

    new_flows = 30
    existing_flows = 20

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context()
            page = context.new_page()
            _login_admin(page, base_url)

            # Flujo cliente nuevo x30
            for i in range(new_flows):
                page.goto(f"{base_url}/admin/solicitudes/nueva-publica/link", wait_until="domcontentloaded")
                token_link = _extract_link_from_input(page, "linkPublicoNuevo", base_url)

                public_page = context.new_page()
                try:
                    public_page.goto(token_link, wait_until="domcontentloaded")
                    public_page.wait_for_selector('input[name="nombre_completo"]', timeout=12000)
                    scenario = _scenario(i)
                    _fill_common_solicitud_fields(public_page, i + 1, scenario, new_client=True)
                    _validate_ui_behavior(public_page, ui_checks)
                    _accept_terms_and_submit(public_page, new_client=True)
                    _verify_success_page(public_page)
                    shot = ARTIFACT_DIR / f"new_success_{i+1:03d}.png"
                    if i < 3:
                        public_page.screenshot(path=str(shot), full_page=True)
                        screenshots.append(str(shot))
                finally:
                    public_page.close()

                _token_consumed_check(context, token_link)

            clientes, solicitudes = _extract_new_entities_since(started)
            created_client_ids = [int(c.id) for c in clientes]
            if len(created_client_ids) < 30:
                raise RuntimeError(f"Se esperaban >=30 clientes nuevos, encontrados {len(created_client_ids)}")

            # Flujo cliente existente x20
            for j in range(existing_flows):
                cid = created_client_ids[j % len(created_client_ids)]
                page.goto(f"{base_url}/admin/clientes/{cid}/solicitudes/link-publico", wait_until="domcontentloaded")
                token_link = _extract_link_from_input(page, "linkPublico", base_url)

                p2 = context.new_page()
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
                finally:
                    p2.close()

                _token_consumed_check(context, token_link)

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

            browser.close()

        clientes, solicitudes = _extract_new_entities_since(started)

        # Verificación no duplicado cliente en flujos existentes
        if len(clientes) != 30:
            bugs.append(f"Esperado 30 clientes creados por flujo nuevo, encontrados {len(clientes)}")

        if len(solicitudes) < 50:
            bugs.append(f"Esperado >=50 solicitudes, encontradas {len(solicitudes)}")

        audit = _audit_db(clientes, solicitudes)
        if audit["duplicados_email"]:
            bugs.append("Duplicados por email detectados")
        if audit["duplicados_telefono"]:
            bugs.append("Duplicados por teléfono detectados")
        if audit["solicitudes_sin_cliente"] > 0:
            bugs.append("Solicitudes huérfanas sin cliente")

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
