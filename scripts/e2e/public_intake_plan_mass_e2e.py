#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import socket
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import requests
from werkzeug.serving import make_server
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["APP_ENV"] = "local"
os.environ["ADMIN_LEGACY_ENABLED"] = "1"
os.environ["TRUST_XFF"] = "1"

from app import app as flask_app
from config_app import db
from models import (
    Cliente,
    PagoSolicitud,
    PublicSolicitudClienteNuevoTokenUso,
    PublicSolicitudTokenUso,
    Solicitud,
    StaffUser,
)
from services.payment_ledger import get_payment_summary
from services.payment_rules import get_plan_label, get_plan_price, get_required_deposit, normalize_plan
from services.phone_identity_service import normalize_phone_to_e164
from clientes.routes import (
    generar_token_publico_cliente,
    generar_token_publico_cliente_nuevo,
)


RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
ARTIFACT_DIR = ROOT / "artifacts" / "e2e" / "public_intake_plan_mass" / RUN_ID
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
REPORT_PATH = ARTIFACT_DIR / "report.json"
BASE_TIMESTAMP_ISO = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
PLANS = ("basico", "premium", "vip")
VALID_PLAN_SET = set(PLANS)
NEW_FLOW_TOTAL = 50
EXISTING_FLOW_TOTAL = 50
TOTAL_CASES = NEW_FLOW_TOTAL + EXISTING_FLOW_TOTAL
ADMIN_USER = "mass_e2e_owner"
ADMIN_PASS = "admin12345"
ADMIN_IP = "10.0.0.1"
PLAN_BUTTON_LABELS = {
    "basico": "Elegir Básico",
    "premium": "Elegir Premium",
    "vip": "Elegir VIP",
}


@dataclass
class CaseSpec:
    index: int
    flow: str
    plan: str
    phone: str
    phone_kind: str
    token_label: str
    existing_cliente_id: int | None = None
    existing_codigo: str = ""
    existing_nombre: str = ""
    existing_email: str = ""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run_seed_tail() -> int:
    digits = re.sub(r"\D+", "", RUN_ID)
    return int(digits[-6:] or "123456")


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    return int(port)


def _start_server() -> tuple[Any, threading.Thread, str]:
    port = _free_port()
    server = make_server("127.0.0.1", port, flask_app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"

    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            response = requests.get(f"{base_url}/health", timeout=0.8)
            if response.status_code in (200, 404):
                return server, thread, base_url
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("No se pudo levantar el servidor local para la corrida E2E.")


def _ensure_safe_local_env() -> str:
    app_env = (os.getenv("APP_ENV") or "").strip().lower()
    if app_env != "local":
        raise RuntimeError(f"APP_ENV debe ser 'local'. Actual: {app_env!r}")
    db_url = str(flask_app.config.get("SQLALCHEMY_DATABASE_URI") or "")
    if "domestica_cibao_local" not in db_url:
        raise RuntimeError("La corrida E2E masiva debe apuntar a domestica_cibao_local.")
    if "render.com" in db_url or "mis_candidatas_db" in db_url:
        raise RuntimeError("Bloqueado: parece una base remota o de producción.")
    return db_url


def _ensure_admin_user() -> None:
    user = StaffUser.query.filter_by(username=ADMIN_USER).first()
    changed = False
    if user is None:
        user = StaffUser(
            username=ADMIN_USER,
            email=f"{ADMIN_USER}@example.com",
            role="owner",
            is_active=True,
            mfa_enabled=False,
        )
        db.session.add(user)
        changed = True
    if (getattr(user, "role", "") or "").strip().lower() != "owner":
        user.role = "owner"
        changed = True
    if not bool(getattr(user, "is_active", False)):
        user.is_active = True
        changed = True
    if not bool(getattr(user, "password_hash", "")) or not user.check_password(ADMIN_PASS):
        user.password_hash = generate_password_hash(ADMIN_PASS, method="pbkdf2:sha256")
        changed = True
    if changed:
        db.session.commit()


def _seed_existing_clients() -> list[Cliente]:
    seed_tail = _run_seed_tail()
    special_phones = [
        (f"+34 612 34{seed_tail % 100:02d} 78", "international"),
        (f"0034 612 34{(seed_tail + 1) % 100:02d} 79", "international"),
        (f"+52 55 12{(seed_tail + 2) % 100:02d} 5678", "international"),
        (f"{(seed_tail % 9000000) + 1000000}", "invalid"),
        (f"{(seed_tail % 90000000) + 10000000}", "invalid"),
        (f"{(seed_tail % 900000000) + 100000000}", "invalid"),
    ]
    created: list[Cliente] = []
    now_ref = datetime.utcnow()

    for i in range(EXISTING_FLOW_TOTAL):
        phone, kind = special_phones[i] if i < len(special_phones) else (f"829{((seed_tail + i) % 10000000):07d}", "valid")
        suffix = f"EX-{i + 1:03d}"
        email = f"mass_existing_{RUN_ID.lower()}_{i + 1:03d}@example.com"
        codigo = f"E2ECLI{RUN_ID[-6:]}{i + 1:03d}"
        cliente = Cliente(
            codigo=codigo,
            nombre_completo=f"Cliente Existente {suffix}",
            email=email,
            email_norm=email,
            telefono=phone,
            telefono_norm=re.sub(r"\D+", "", phone) or None,
            ciudad="Santiago",
            sector=f"Sector Existente {i + 1:03d}",
            role="cliente",
            is_active=True,
            created_at=now_ref,
            updated_at=now_ref,
            fecha_registro=now_ref,
            fecha_ultima_actividad=now_ref,
            total_solicitudes=0,
        )
        db.session.add(cliente)
        created.append(cliente)
        if kind not in {"international", "invalid", "valid"}:
            raise RuntimeError("Clasificación de teléfono inesperada.")

    db.session.commit()
    return created


def _build_cases(existing_clients: list[Cliente]) -> list[CaseSpec]:
    cases: list[CaseSpec] = []
    seed_tail = _run_seed_tail()
    new_special_phones = [
        (f"+34 600 10{seed_tail % 100:02d} 01", "international"),
        (f"0034 600 10{(seed_tail + 1) % 100:02d} 02", "international"),
        (f"+52 55 10{(seed_tail + 2) % 100:02d} 0003", "international"),
        (f"{(seed_tail % 9000000) + 2000000}", "invalid"),
        (f"{(seed_tail % 90000000) + 20000000}", "invalid"),
        (f"{(seed_tail % 900000000) + 200000000}", "invalid"),
    ]

    for i in range(NEW_FLOW_TOTAL):
        phone, kind = new_special_phones[i] if i < len(new_special_phones) else (f"849{((seed_tail + 500 + i) % 10000000):07d}", "valid")
        cases.append(
            CaseSpec(
                index=i + 1,
                flow="cliente_nuevo",
                plan=PLANS[i % len(PLANS)],
                phone=phone,
                phone_kind=kind,
                token_label=f"nuevo-{i + 1:03d}",
            )
        )

    for i, cliente in enumerate(existing_clients):
        phone_digits = str(getattr(cliente, "telefono", "") or "")
        kind = "valid"
        if phone_digits.startswith(("+34", "0034", "+52")):
            kind = "international"
        if phone_digits in {"1234567", "1111111111", "0000000000"}:
            kind = "invalid"
        cases.append(
            CaseSpec(
                index=NEW_FLOW_TOTAL + i + 1,
                flow="cliente_existente",
                plan=PLANS[i % len(PLANS)],
                phone=str(cliente.telefono or ""),
                phone_kind=kind,
                token_label=f"existente-{i + 1:03d}",
                existing_cliente_id=int(cliente.id),
                existing_codigo=str(cliente.codigo or ""),
                existing_nombre=str(cliente.nombre_completo or ""),
                existing_email=str(cliente.email or ""),
            )
        )
    return cases


def _admin_login(base_url: str) -> requests.Session:
    sess = requests.Session()
    response = sess.post(
        f"{base_url}/admin/login",
        data={"usuario": ADMIN_USER, "clave": ADMIN_PASS},
        headers={"X-Forwarded-For": ADMIN_IP},
        timeout=20,
        allow_redirects=False,
    )
    if response.status_code not in (302, 303):
        raise RuntimeError(f"Login admin falló. status={response.status_code}")
    return sess


def _expected_whatsapp_state(phone: str) -> tuple[bool, str]:
    normalized = normalize_phone_to_e164(phone, default_country="DO")
    if not normalized:
        return False, ""
    return True, normalized.lstrip("+")


def _plan_option_selected(html: str, plan: str) -> bool:
    pattern = rf'<option[^>]*value="{re.escape(plan)}"[^>]*selected'
    return bool(re.search(pattern, html, flags=re.IGNORECASE))


def _contains_amount(html: str, amount: Decimal) -> bool:
    amount_txt = f"{amount.quantize(Decimal('0.01')):,.2f}"
    return amount_txt in html


def _screenshot_failure_path(case: CaseSpec, phase: str) -> Path:
    safe_phase = re.sub(r"[^a-z0-9_-]+", "_", str(phase or "").strip().lower()) or "unknown"
    return ARTIFACT_DIR / f"failure_{case.index:03d}_{case.flow}_{safe_phase}.png"


def _sample_public_screenshot_path(case: CaseSpec) -> Path:
    return ARTIFACT_DIR / f"sample_{case.flow}_{case.index:03d}.png"


def _base_form_payload(case: CaseSpec) -> list[tuple[str, str]]:
    flow_label = "nuevo" if case.flow == "cliente_nuevo" else "existente"
    suffix = f"{flow_label} {case.index:03d}"
    payload: list[tuple[str, str]] = [
        ("ciudad_sector", f"Santiago / Sector {suffix}"),
        ("rutas_cercanas", f"Ruta {case.index:03d}"),
        ("modalidad_trabajo", "Con salida diaria - Lunes a Viernes"),
        ("experiencia", f"Experiencia E2E {suffix}"),
        ("horario", "Lunes a viernes, de 8:00 AM a 5:00 PM"),
        ("tipo_lugar", "casa"),
        ("habitaciones", "2"),
        ("banos", "1"),
        ("adultos", "2"),
        ("ninos", "0"),
        ("mascota", ""),
        ("sueldo", "18000"),
        ("modalidad_grupo", "con_salida_diaria"),
        ("modalidad_especifica", "sd_l_v"),
        ("horario_dias_trabajo", "Lunes a viernes"),
        ("horario_hora_entrada", "8:00 AM"),
        ("horario_hora_salida", "5:00 PM"),
        ("pasaje_mode", "incluido"),
        ("pisos_selector", "1"),
        ("nota_cliente", f"Nota E2E {suffix}"),
        ("tipo_plan", case.plan),
        ("terms_accepted", "1"),
        ("terms_decision", "accept"),
        ("terms_accepted_at", BASE_TIMESTAMP_ISO),
    ]
    payload.extend([("edad_requerida", "26-35")])
    payload.extend([("funciones", "limpieza")])
    payload.extend([("areas_comunes", "sala")])
    return payload


def _new_client_payload(case: CaseSpec) -> list[tuple[str, str]]:
    payload = _base_form_payload(case)
    payload.extend(
        [
            ("nombre_completo", f"Cliente Nuevo {case.index:03d}"),
            ("email_contacto", f"mass_new_{RUN_ID.lower()}_{case.index:03d}@example.com"),
            ("telefono_contacto", case.phone),
            ("ciudad_cliente", "Santiago"),
            ("sector_cliente", f"Sector Nuevo {case.index:03d}"),
            ("hp", ""),
        ]
    )
    return payload


def _existing_client_payload(case: CaseSpec) -> list[tuple[str, str]]:
    payload = _base_form_payload(case)
    payload.extend(
        [
            ("token", ""),
            ("codigo_cliente", case.existing_codigo),
            ("nombre_cliente", case.existing_nombre),
            ("email_cliente", case.existing_email),
            ("hp", ""),
        ]
    )
    return payload


def _fetch_counts() -> dict[str, int]:
    return {
        "clientes": int(Cliente.query.count() or 0),
        "solicitudes": int(Solicitud.query.count() or 0),
        "pagos_abono": int(PagoSolicitud.query.filter(PagoSolicitud.tipo_pago == "abono", PagoSolicitud.anulado_at.is_(None)).count() or 0),
        "token_new": int(PublicSolicitudClienteNuevoTokenUso.query.count() or 0),
        "token_existing": int(PublicSolicitudTokenUso.query.count() or 0),
    }


def _serialize_case_failure(case: CaseSpec, phase: str, message: str, *, status_code: int | None = None) -> dict[str, Any]:
    return {
        "case_index": case.index,
        "flow": case.flow,
        "plan": case.plan,
        "phone": case.phone,
        "phone_kind": case.phone_kind,
        "phase": phase,
        "status_code": status_code,
        "message": message,
    }


def _extract_invalid_feedback(html: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", raw).strip()
        for raw in re.findall(r'<div class="invalid-feedback[^"]*">(.*?)</div>', html, flags=re.S)
        if re.sub(r"\s+", " ", raw).strip()
    ]


def _capture_page_screenshot(page: Any, path: Path) -> str | None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception:
        return None


def _set_checked(page: Any, selector: str) -> None:
    locator = page.locator(selector)
    locator.scroll_into_view_if_needed()
    try:
        locator.set_checked(True, force=True)
        if locator.is_checked():
            return
    except Exception:
        pass
    locator.evaluate(
        """(el) => {
            el.checked = true;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('click', { bubbles: true }));
        }"""
    )


def _set_input_value(page: Any, selector: str, value: str) -> None:
    page.locator(selector).evaluate(
        """(el, value) => {
            el.value = value;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }""",
        value,
    )


def _fill_public_form(page: Any, case: CaseSpec) -> None:
    if case.flow == "cliente_nuevo":
        page.fill('input[name="nombre_completo"]', f"Cliente Nuevo {case.index:03d}")
        page.fill('input[name="email_contacto"]', f"mass_new_{RUN_ID.lower()}_{case.index:03d}@example.com")
        page.fill('input[name="telefono_contacto"]', case.phone)
        page.fill('input[name="ciudad_cliente"]', "Santiago")
        page.fill('input[name="sector_cliente"]', f"Sector Nuevo {case.index:03d}")

    page.fill("#ciudad_input_ui", "Santiago")
    page.fill("#sector_input_ui", f"Sector {'Nuevo' if case.flow == 'cliente_nuevo' else 'Existente'} {case.index:03d}")
    _set_input_value(page, 'input[name="ciudad_sector"]', f"Santiago (Sector {'Nuevo' if case.flow == 'cliente_nuevo' else 'Existente'} {case.index:03d})")
    page.fill('input[name="rutas_cercanas"]', f"Ruta {case.index:03d}")
    _set_checked(page, 'input[name="modalidad_grupo"][value="con_salida_diaria"]')
    page.select_option('select[name="modalidad_especifica"]', label="Salida diaria - lunes a viernes")
    page.fill('input[name="horario_dias_trabajo"]', "Lunes a viernes")
    page.fill('input[name="horario_hora_entrada"]', "8:00 AM")
    page.fill('input[name="horario_hora_salida"]', "5:00 PM")
    _set_input_value(page, "#modalidad_trabajo_hidden", "Salida diaria - lunes a viernes")
    _set_input_value(page, "#horario_hidden", "Lunes a viernes, de 8:00 AM a 5:00 PM")
    _set_checked(page, 'input[name="edad_requerida"][value="26-35"]')
    page.fill('textarea[name="experiencia"]', f"Experiencia E2E {'nuevo' if case.flow == 'cliente_nuevo' else 'existente'} {case.index:03d}")
    _set_checked(page, 'input[name="funciones"][value="limpieza"]')
    page.select_option('select[name="tipo_lugar"]', "casa")
    _set_input_value(page, "#habitaciones_hidden", "2")
    _set_input_value(page, "#banos_hidden", "1")
    page.locator('label:has(input[name="habitaciones_selector"][value="2"])').click(force=True)
    page.locator('label:has(input[name="banos_selector"][value="1"])').click(force=True)
    page.locator('label:has(input[name="pisos_selector"][value="1"])').click(force=True)
    page.fill('input[name="adultos"]', "2")
    page.fill('input[name="ninos"]', "0")
    page.fill('input[name="sueldo"]', "18000")
    _set_checked(page, 'input[name="areas_comunes"][value="sala"]')
    page.fill('textarea[name="nota_cliente"]', f"Nota E2E {'nuevo' if case.flow == 'cliente_nuevo' else 'existente'} {case.index:03d}")


def _run_public_browser_flow(browser: Any, public_url: str, case: CaseSpec) -> dict[str, Any]:
    context = browser.new_context(viewport={"width": 1440, "height": 2200})
    page = context.new_page()
    failure_shots: list[str] = []
    sample_shot: str | None = None

    try:
        first_response = page.goto(public_url, wait_until="domcontentloaded")
        if first_response is None or first_response.status != 200:
            shot = _capture_page_screenshot(page, _screenshot_failure_path(case, "public_get"))
            if shot:
                failure_shots.append(shot)
            return {
                "ok": False,
                "phase": "public_get",
                "status_code": None if first_response is None else int(first_response.status),
                "message": "El formulario público no abrió en navegador.",
                "failure_screenshots": failure_shots,
            }

        _fill_public_form(page, case)
        terms_checkbox = "#acepta_politica_nueva" if case.flow == "cliente_nuevo" else "#acepta_politica"
        submit_button = "#publicSubmitNuevaBtn" if case.flow == "cliente_nuevo" else "#publicSubmitBtn"
        page.check(terms_checkbox)

        with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
            page.click(submit_button)

        if "/plan" not in page.url or "/plan/resumen" in page.url:
            shot = _capture_page_screenshot(page, _screenshot_failure_path(case, "redirect_plan"))
            if shot:
                failure_shots.append(shot)
            return {
                "ok": False,
                "phase": "redirect_plan",
                "message": f"Tras enviar formulario no llegó a pantalla de planes. url={page.url}",
                "failure_screenshots": failure_shots,
            }

        page.wait_for_selector("text=Elige el plan ideal para tu solicitud", timeout=12000)

        page.goto(public_url, wait_until="domcontentloaded")
        page.wait_for_url("**/plan", timeout=12000)
        if "/plan/resumen" in page.url:
            shot = _capture_page_screenshot(page, _screenshot_failure_path(case, "resume_before_plan"))
            if shot:
                failure_shots.append(shot)
            return {
                "ok": False,
                "phase": "resume_before_plan",
                "message": "Volver al link antes de elegir plan no reanudó la selección; cayó en resumen.",
                "failure_screenshots": failure_shots,
            }

        button_text = PLAN_BUTTON_LABELS[case.plan]
        with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
            page.get_by_role("button", name=button_text, exact=True).click()

        if "/plan/resumen" not in page.url:
            shot = _capture_page_screenshot(page, _screenshot_failure_path(case, "plan_click"))
            if shot:
                failure_shots.append(shot)
            return {
                "ok": False,
                "phase": "plan_click",
                "message": f"El click real en {button_text!r} no llevó al resumen final. url={page.url}",
                "failure_screenshots": failure_shots,
            }

        expected_price = get_plan_price(case.plan)
        expected_deposit = get_required_deposit(case.plan)
        expected_balance = (expected_price - expected_deposit).quantize(Decimal("0.01"))
        html = page.content()
        if "Solicitud recibida correctamente" not in html:
            shot = _capture_page_screenshot(page, _screenshot_failure_path(case, "summary_marker"))
            if shot:
                failure_shots.append(shot)
            return {
                "ok": False,
                "phase": "summary_marker",
                "message": "No apareció el resumen final esperado.",
                "failure_screenshots": failure_shots,
            }
        if get_plan_label(case.plan) not in html:
            shot = _capture_page_screenshot(page, _screenshot_failure_path(case, "summary_plan"))
            if shot:
                failure_shots.append(shot)
            return {
                "ok": False,
                "phase": "summary_plan",
                "message": "El resumen final no muestra el plan correcto.",
                "failure_screenshots": failure_shots,
            }
        if not _contains_amount(html, expected_price):
            shot = _capture_page_screenshot(page, _screenshot_failure_path(case, "summary_total"))
            if shot:
                failure_shots.append(shot)
            return {
                "ok": False,
                "phase": "summary_total",
                "message": "El resumen final no muestra el precio total correcto.",
                "failure_screenshots": failure_shots,
            }
        if not _contains_amount(html, expected_deposit):
            shot = _capture_page_screenshot(page, _screenshot_failure_path(case, "summary_deposit"))
            if shot:
                failure_shots.append(shot)
            return {
                "ok": False,
                "phase": "summary_deposit",
                "message": "El resumen final no muestra el 50% requerido correcto.",
                "failure_screenshots": failure_shots,
            }
        if not _contains_amount(html, expected_balance):
            shot = _capture_page_screenshot(page, _screenshot_failure_path(case, "summary_balance"))
            if shot:
                failure_shots.append(shot)
            return {
                "ok": False,
                "phase": "summary_balance",
                "message": "El resumen final no muestra el saldo restante correcto.",
                "failure_screenshots": failure_shots,
            }

        if case.index in {1, NEW_FLOW_TOTAL + 1}:
            sample_shot = _capture_page_screenshot(page, _sample_public_screenshot_path(case))

        second_response = page.goto(public_url, wait_until="domcontentloaded")
        second_status = None if second_response is None else int(second_response.status)
        used_html = page.content().lower()
        if second_status not in (200, 410):
            shot = _capture_page_screenshot(page, _screenshot_failure_path(case, "token_reuse_after_plan"))
            if shot:
                failure_shots.append(shot)
            return {
                "ok": False,
                "phase": "token_reuse_after_plan",
                "status_code": second_status,
                "message": "Volver al link después de elegir plan devolvió estado inesperado.",
                "failure_screenshots": failure_shots,
            }
        if "utilizado" not in used_html and "used" not in used_html and "expir" not in used_html:
            shot = _capture_page_screenshot(page, _screenshot_failure_path(case, "token_reuse_after_plan"))
            if shot:
                failure_shots.append(shot)
            return {
                "ok": False,
                "phase": "token_reuse_after_plan",
                "status_code": second_status,
                "message": "Volver al link después de elegir plan no mostró token consumido.",
                "failure_screenshots": failure_shots,
            }

        return {
            "ok": True,
            "sample_screenshot": sample_shot,
            "failure_screenshots": failure_shots,
        }
    except Exception as exc:
        shot = _capture_page_screenshot(page, _screenshot_failure_path(case, "unexpected_ui_error"))
        if shot:
            failure_shots.append(shot)
        return {
            "ok": False,
            "phase": "unexpected_ui_error",
            "message": f"Excepción no controlada en navegador: {exc}",
            "failure_screenshots": failure_shots,
        }
    finally:
        context.close()


def _run_single_case(base_url: str, browser: Any, admin_session: requests.Session, case: CaseSpec) -> dict[str, Any]:
    started = _utcnow_iso()
    ip = f"10.20.{case.index // 255}.{case.index % 255 or 1}"
    token = ""

    with flask_app.app_context():
        before_counts = _fetch_counts()
        if case.flow == "cliente_nuevo":
            token = generar_token_publico_cliente_nuevo(created_by="mass-e2e")
            public_url = f"{base_url}/clientes/solicitudes/nueva-publica/{token}"
        else:
            cliente = Cliente.query.get(int(case.existing_cliente_id or 0))
            if cliente is None:
                return {
                    "ok": False,
                    "failure": _serialize_case_failure(case, "seed", "Cliente existente no encontrado."),
                }
            token = generar_token_publico_cliente(cliente)
            public_url = f"{base_url}/clientes/solicitudes/publica/{token}"
    public_browser_result = _run_public_browser_flow(browser, public_url, case)
    if not public_browser_result.get("ok"):
        failure = _serialize_case_failure(
            case,
            str(public_browser_result.get("phase") or "public_ui"),
            str(public_browser_result.get("message") or "Fallo de UI en flujo público."),
            status_code=public_browser_result.get("status_code"),
        )
        failure["screenshots"] = list(public_browser_result.get("failure_screenshots") or [])
        return {"ok": False, "failure": failure}

    with flask_app.app_context():
        after_counts = _fetch_counts()
        cliente: Cliente | None = None
        solicitud: Solicitud | None = None
        token_consumido = False
        token_reason = ""

        if case.flow == "cliente_nuevo":
            if (after_counts["clientes"] - before_counts["clientes"]) != 1:
                return {
                    "ok": False,
                    "failure": _serialize_case_failure(case, "db_create_cliente", "El flujo nuevo no creó exactamente un cliente."),
                }
            cliente = (
                Cliente.query
                .filter_by(email=f"mass_new_{RUN_ID.lower()}_{case.index:03d}@example.com")
                .order_by(Cliente.id.desc())
                .first()
            )
            if cliente is None:
                return {
                    "ok": False,
                    "failure": _serialize_case_failure(case, "db_lookup_cliente", "No se encontró el cliente nuevo creado."),
                }
            solicitud = (
                Solicitud.query
                .filter_by(cliente_id=int(cliente.id))
                .order_by(Solicitud.id.desc())
                .first()
            )
            token_row = (
                PublicSolicitudClienteNuevoTokenUso.query
                .filter_by(solicitud_id=int(getattr(solicitud, "id", 0) or 0))
                .order_by(PublicSolicitudClienteNuevoTokenUso.id.desc())
                .first()
            )
            token_consumido = token_row is not None
            token_reason = str(getattr(token_row, "consumption_reason", "") or "")
        else:
            cliente = Cliente.query.get(int(case.existing_cliente_id or 0))
            if cliente is None:
                return {
                    "ok": False,
                    "failure": _serialize_case_failure(case, "db_lookup_existing_cliente", "No se encontró el cliente existente tras el submit."),
                }
            if (after_counts["clientes"] - before_counts["clientes"]) != 0:
                return {
                    "ok": False,
                    "failure": _serialize_case_failure(case, "db_duplicate_cliente", "El flujo existente creó un cliente duplicado."),
                }
            solicitud = (
                Solicitud.query
                .filter_by(cliente_id=int(cliente.id))
                .order_by(Solicitud.id.desc())
                .first()
            )
            token_row = (
                PublicSolicitudTokenUso.query
                .filter_by(cliente_id=int(cliente.id), solicitud_id=int(getattr(solicitud, "id", 0) or 0))
                .order_by(PublicSolicitudTokenUso.id.desc())
                .first()
            )
            token_consumido = token_row is not None
            token_reason = str(getattr(token_row, "consumption_reason", "") or "")

        if solicitud is None:
            return {
                "ok": False,
                "failure": _serialize_case_failure(case, "db_lookup_solicitud", "No se encontró la solicitud creada."),
            }
        if (after_counts["solicitudes"] - before_counts["solicitudes"]) != 1:
            return {
                "ok": False,
                "failure": _serialize_case_failure(case, "db_duplicate_solicitud", "El submit no creó exactamente una solicitud."),
            }
        if not token_consumido:
            return {
                "ok": False,
                "failure": _serialize_case_failure(case, "token", "No se detectó consumo de token."),
            }
        if token_reason != "plan_selected":
            return {
                "ok": False,
                "failure": _serialize_case_failure(case, "token_state", f"Estado final inesperado del token: {token_reason!r}."),
            }

        normalized_plan = normalize_plan(getattr(solicitud, "tipo_plan", None))
        if normalized_plan != case.plan:
            return {
                "ok": False,
                "failure": _serialize_case_failure(case, "tipo_plan", f"tipo_plan guardado={normalized_plan!r}, esperado={case.plan!r}."),
            }
        if int(getattr(solicitud, "cliente_id", 0) or 0) != int(getattr(cliente, "id", 0) or 0):
            return {
                "ok": False,
                "failure": _serialize_case_failure(case, "cliente_id", "La solicitud quedó sin cliente correcto."),
            }

        untouched_cycle_fields = (
            getattr(solicitud, "payment_cycle_plan", None) is None
            and getattr(solicitud, "payment_cycle_precio_total", None) is None
            and getattr(solicitud, "payment_cycle_abono_requerido", None) is None
            and getattr(solicitud, "payment_cycle_opened_at", None) is None
            and getattr(solicitud, "payment_cycle_closed_at", None) is None
            and getattr(solicitud, "payment_cycle_motivo_apertura", None) is None
        )
        if not untouched_cycle_fields:
            return {
                "ok": False,
                "failure": _serialize_case_failure(case, "payment_cycle_pre", "Se tocaron payment_cycle_* antes de Gestionar Plan."),
            }
        if (after_counts["pagos_abono"] - before_counts["pagos_abono"]) != 0:
            return {
                "ok": False,
                "failure": _serialize_case_failure(case, "pago_pre_plan", "Elegir plan en flujo público registró un pago antes de Gestionar Plan."),
            }

        duplicate_email = int(
            db.session.query(db.func.count(Cliente.id))
            .filter(Cliente.email == str(getattr(cliente, "email", "") or ""))
            .scalar()
            or 0
        )
        duplicate_phone = int(
            db.session.query(db.func.count(Cliente.id))
            .filter(Cliente.telefono == str(getattr(cliente, "telefono", "") or ""))
            .scalar()
            or 0
        )
        if duplicate_email != 1:
            return {
                "ok": False,
                "failure": _serialize_case_failure(case, "duplicate_email", f"Email duplicado detectado: count={duplicate_email}."),
            }
        if case.phone_kind != "invalid" and duplicate_phone != 1:
            return {
                "ok": False,
                "failure": _serialize_case_failure(case, "duplicate_phone", f"Teléfono duplicado detectado: count={duplicate_phone}."),
            }

        cliente_id = int(cliente.id)
        solicitud_id = int(solicitud.id)
        solicitud_codigo = str(solicitud.codigo_solicitud or "")

    plan_url = f"{base_url}/admin/clientes/{cliente_id}/solicitudes/{solicitud_id}/plan"
    manage_get = admin_session.get(plan_url, headers={"X-Forwarded-For": ADMIN_IP}, timeout=20)
    if manage_get.status_code != 200:
        return {
            "ok": False,
            "failure": _serialize_case_failure(case, "gestionar_plan_get", "No abrió Gestionar Plan.", status_code=manage_get.status_code),
        }
    if "/admin/login" in str(getattr(manage_get, "url", "") or ""):
        return {
            "ok": False,
            "failure": _serialize_case_failure(case, "gestionar_plan_auth", "La sesión admin no sobrevivió hasta Gestionar Plan."),
        }
    if not _plan_option_selected(manage_get.text, case.plan):
        return {
            "ok": False,
            "failure": _serialize_case_failure(case, "gestionar_plan_prefill", "Gestionar Plan no tomó el plan guardado en la solicitud."),
        }

    expected_price = get_plan_price(case.plan)
    expected_deposit = get_required_deposit(case.plan)
    manage_post = admin_session.post(
        plan_url,
        data={
            "tipo_plan": case.plan,
            "abono": f"{expected_deposit:.2f}",
            "plan_action": "update",
        },
        headers={"X-Forwarded-For": ADMIN_IP},
        timeout=30,
        allow_redirects=True,
    )
    if manage_post.status_code != 200:
        return {
            "ok": False,
            "failure": _serialize_case_failure(case, "gestionar_plan_post", "Gestionar Plan no guardó correctamente.", status_code=manage_post.status_code),
        }
    if "show_whatsapp_cta=1" not in str(manage_post.url):
        return {
            "ok": False,
            "failure": _serialize_case_failure(case, "gestionar_plan_redirect", "Gestionar Plan no activó el CTA de WhatsApp en el redirect final."),
        }

    expected_wa_enabled, expected_digits = _expected_whatsapp_state(case.phone)
    final_html = manage_post.text
    if expected_wa_enabled:
        if "data-testid=\"gestionar-plan-whatsapp-link\"" not in final_html:
            return {
                "ok": False,
                "failure": _serialize_case_failure(case, "whatsapp_cta", "Faltó el CTA de WhatsApp para un teléfono válido."),
            }
        if expected_digits not in final_html:
            return {
                "ok": False,
                "failure": _serialize_case_failure(case, "whatsapp_cta_phone", "El CTA de WhatsApp no contiene el teléfono esperado."),
            }
        if not _contains_amount(final_html, expected_deposit):
            return {
                "ok": False,
                "failure": _serialize_case_failure(case, "whatsapp_cta_amount", "El CTA de WhatsApp no contiene el monto correcto del 50%."),
            }
    else:
        if "WhatsApp no disponible" not in final_html:
            return {
                "ok": False,
                "failure": _serialize_case_failure(case, "whatsapp_cta_invalid", "El CTA no quedó bloqueado para teléfono inválido."),
            }

    with flask_app.app_context():
        solicitud = Solicitud.query.get(int(solicitud_id))
        if solicitud is None:
            return {
                "ok": False,
                "failure": _serialize_case_failure(case, "db_post_plan", "No se encontró la solicitud tras Gestionar Plan."),
            }
        summary = get_payment_summary(solicitud)
        abonos = (
            PagoSolicitud.query
            .filter(
                PagoSolicitud.solicitud_id == int(solicitud_id),
                PagoSolicitud.ciclo_numero == int(summary["numero_ciclo"]),
                PagoSolicitud.tipo_pago == "abono",
                PagoSolicitud.anulado_at.is_(None),
            )
            .order_by(PagoSolicitud.id.asc())
            .all()
        )
        if normalize_plan(getattr(solicitud, "payment_cycle_plan", None)) != case.plan:
            return {
                "ok": False,
                "failure": _serialize_case_failure(case, "payment_cycle_plan", "payment_cycle_plan no coincide con el plan esperado."),
            }
        if Decimal(str(getattr(solicitud, "payment_cycle_precio_total", 0) or 0)) != expected_price:
            return {
                "ok": False,
                "failure": _serialize_case_failure(case, "payment_cycle_total", "payment_cycle_precio_total no coincide con el precio esperado."),
            }
        if Decimal(str(getattr(solicitud, "payment_cycle_abono_requerido", 0) or 0)) != expected_deposit:
            return {
                "ok": False,
                "failure": _serialize_case_failure(case, "payment_cycle_abono", "payment_cycle_abono_requerido no coincide con el 50% esperado."),
            }
        if len(abonos) != 1:
            return {
                "ok": False,
                "failure": _serialize_case_failure(case, "abono_count", f"Se esperaban 1 abono activo y se encontraron {len(abonos)}."),
            }
        monto_abono = Decimal(str(getattr(abonos[0], "monto", 0) or 0))
        if monto_abono != expected_deposit:
            return {
                "ok": False,
                "failure": _serialize_case_failure(case, "abono_monto", "El monto del abono no coincide con el 50% esperado."),
            }
        if str(getattr(solicitud, "estado", "") or "").strip().lower() != "activa":
            return {
                "ok": False,
                "failure": _serialize_case_failure(case, "estado_final", f"Estado final inesperado: {solicitud.estado!r}."),
            }
        if str(getattr(solicitud, "payment_cycle_estado", "") or "").strip().lower() != "parcial":
            return {
                "ok": False,
                "failure": _serialize_case_failure(case, "cycle_state", f"payment_cycle_estado inesperado: {solicitud.payment_cycle_estado!r}."),
            }
        if Decimal(str(summary["abono_requerido"])) != expected_deposit:
            return {
                "ok": False,
                "failure": _serialize_case_failure(case, "summary_abono", "get_payment_summary devolvió un abono requerido incorrecto."),
            }
        if Decimal(str(summary["precio_plan"])) != expected_price:
            return {
                "ok": False,
                "failure": _serialize_case_failure(case, "summary_total", "get_payment_summary devolvió un precio incorrecto."),
            }

    return {
        "ok": True,
        "result": {
            "case_index": case.index,
            "flow": case.flow,
            "plan": case.plan,
            "phone": case.phone,
            "phone_kind": case.phone_kind,
            "cliente_id": cliente_id,
            "solicitud_id": solicitud_id,
            "solicitud_codigo": solicitud_codigo,
            "token_consumido": True,
            "whatsapp_cta_enabled": expected_wa_enabled,
            "expected_price": f"{expected_price:.2f}",
            "expected_deposit": f"{expected_deposit:.2f}",
            "started_at": started,
            "finished_at": _utcnow_iso(),
            "sample_screenshot": public_browser_result.get("sample_screenshot"),
        },
    }


def _aggregate_report(
    case_results: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    *,
    base_url: str,
    started_at: str,
    seeded_existing_ids: list[int],
) -> dict[str, Any]:
    success_rows = [row["result"] for row in case_results if row.get("ok")]
    sample_screenshots = [str(row.get("sample_screenshot")) for row in success_rows if row.get("sample_screenshot")]
    failure_screenshots = [
        str(path)
        for item in failures
        for path in list(item.get("screenshots") or [])
        if path
    ]
    solicitud_ids = [int(row["solicitud_id"]) for row in success_rows]
    created_client_ids = [int(row["cliente_id"]) for row in success_rows if row["flow"] == "cliente_nuevo"]
    relevant_client_ids = sorted(set(created_client_ids + [int(x) for x in seeded_existing_ids]))
    with flask_app.app_context():
        plan_counts = {
            plan: int(
                Solicitud.query
                .filter(Solicitud.id.in_(solicitud_ids), Solicitud.tipo_plan == plan)
                .count()
                or 0
            )
            for plan in PLANS
        } if solicitud_ids else {plan: 0 for plan in PLANS}
        abonos_total = int(
            PagoSolicitud.query
            .filter(
                PagoSolicitud.solicitud_id.in_(solicitud_ids) if solicitud_ids else db.text("1=0"),
                PagoSolicitud.tipo_pago == "abono",
                PagoSolicitud.anulado_at.is_(None),
            )
            .count()
            or 0
        ) if solicitud_ids else 0
        tokens_consumidos = int(
            (
                PublicSolicitudTokenUso.query
                .filter(PublicSolicitudTokenUso.solicitud_id.in_(solicitud_ids) if solicitud_ids else db.text("1=0"))
                .count()
                or 0
            )
            + (
                PublicSolicitudClienteNuevoTokenUso.query
                .filter(PublicSolicitudClienteNuevoTokenUso.solicitud_id.in_(solicitud_ids) if solicitud_ids else db.text("1=0"))
                .count()
                or 0
            )
        )
        solicitudes_sin_cliente = int(
            Solicitud.query
            .filter(Solicitud.id.in_(solicitud_ids) if solicitud_ids else db.text("1=0"), Solicitud.cliente_id.is_(None))
            .count()
            or 0
        ) if solicitud_ids else 0
        solicitudes_sin_plan = int(
            Solicitud.query
            .filter(
                Solicitud.id.in_(solicitud_ids) if solicitud_ids else db.text("1=0"),
                (Solicitud.tipo_plan.is_(None))
                | (Solicitud.tipo_plan == "")
                | (~db.func.lower(db.cast(Solicitud.tipo_plan, db.String)).in_(tuple(VALID_PLAN_SET)))
            )
            .count()
            or 0
        ) if solicitud_ids else 0
        pagos_duplicados_details = []
        for solicitud_id, total in (
            db.session.query(PagoSolicitud.solicitud_id, db.func.count(PagoSolicitud.id))
            .filter(
                PagoSolicitud.solicitud_id.in_(solicitud_ids) if solicitud_ids else db.text("1=0"),
                PagoSolicitud.tipo_pago == "abono",
                PagoSolicitud.anulado_at.is_(None),
            )
            .group_by(PagoSolicitud.solicitud_id, PagoSolicitud.ciclo_numero)
            .having(db.func.count(PagoSolicitud.id) > 1)
            .all()
        ):
            pagos_duplicados_details.append({"solicitud_id": int(solicitud_id), "count": int(total)})
        duplicate_emails = [
            {"email": str(email or ""), "count": int(total)}
            for email, total in (
                db.session.query(Cliente.email, db.func.count(Cliente.id))
                .filter(Cliente.id.in_(relevant_client_ids) if relevant_client_ids else db.text("1=0"))
                .group_by(Cliente.email)
                .having(db.func.count(Cliente.id) > 1)
                .all()
            )
        ]
        duplicate_phones = [
            {"telefono": str(phone or ""), "count": int(total)}
            for phone, total in (
                db.session.query(Cliente.telefono, db.func.count(Cliente.id))
                .filter(Cliente.id.in_(relevant_client_ids) if relevant_client_ids else db.text("1=0"))
                .group_by(Cliente.telefono)
                .having(db.func.count(Cliente.id) > 1)
                .all()
            )
            if str(phone or "").strip()
        ]

    http_errors = [item for item in failures if item.get("status_code")]
    validation_errors = [item for item in failures if item.get("phase") in {"public_success", "tipo_plan", "payment_cycle_pre"}]
    flow_counts = {
        "cliente_nuevo": sum(1 for row in success_rows if row["flow"] == "cliente_nuevo"),
        "cliente_existente": sum(1 for row in success_rows if row["flow"] == "cliente_existente"),
    }
    amount_errors = [item for item in failures if item.get("phase") in {"payment_cycle_total", "payment_cycle_abono", "abono_monto", "summary_abono", "summary_total", "whatsapp_cta_amount"}]
    plan_errors = [item for item in failures if item.get("phase") in {"tipo_plan", "gestionar_plan_prefill", "payment_cycle_plan"}]
    cta_errors = [item for item in failures if item.get("phase", "").startswith("whatsapp_cta")]

    report = {
        "run_id": RUN_ID,
        "started_at": started_at,
        "finished_at": _utcnow_iso(),
        "base_url": base_url,
        "report_path": str(REPORT_PATH),
        "total_intentos": TOTAL_CASES,
        "intentos_ejecutados": len(case_results),
        "solicitudes_creadas": len(success_rows),
        "clientes_creados": flow_counts["cliente_nuevo"],
        "solicitudes_por_plan": plan_counts,
        "abonos_registrados": abonos_total,
        "errores_http": http_errors,
        "errores_validacion": validation_errors,
        "tokens_consumidos": tokens_consumidos,
        "duplicados_email": duplicate_emails,
        "duplicados_telefono": duplicate_phones,
        "solicitudes_sin_cliente": solicitudes_sin_cliente,
        "solicitudes_sin_plan": solicitudes_sin_plan,
        "pagos_duplicados": pagos_duplicados_details,
        "monto_incorrecto": amount_errors,
        "plan_incorrecto_en_gestionar_plan": plan_errors,
        "cta_whatsapp_incorrecto": cta_errors,
        "conteo_por_flujo": flow_counts,
        "conteo_por_plan_en_exitos": {
            plan: sum(1 for row in success_rows if row["plan"] == plan)
            for plan in PLANS
        },
        "resultados_exitosos": success_rows,
        "fallos": failures,
        "screenshots_fallos": failure_screenshots,
        "screenshots_muestra": sample_screenshots,
        "all_passed": len(success_rows) == TOTAL_CASES and not failures,
    }
    return report


def main() -> int:
    started_at = _utcnow_iso()
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError("Playwright no está disponible; este runner requiere navegador real.") from exc

    with flask_app.app_context():
        _ensure_safe_local_env()
        _ensure_admin_user()
        existing_clients = _seed_existing_clients()
        cases = _build_cases(existing_clients)
        seeded_existing_ids = [int(c.id) for c in existing_clients]

    server, thread, base_url = _start_server()
    failures: list[dict[str, Any]] = []
    case_results: list[dict[str, Any]] = []

    try:
        admin_session = _admin_login(base_url)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                for case in cases:
                    result = _run_single_case(base_url, browser, admin_session, case)
                    case_results.append(result)
                    if result.get("ok"):
                        print(f"[OK] case={case.index:03d} flow={case.flow} plan={case.plan}")
                    else:
                        failure = result["failure"]
                        failures.append(failure)
                        print(
                            f"[FAIL] case={case.index:03d} flow={case.flow} plan={case.plan} "
                            f"phase={failure.get('phase')} status={failure.get('status_code')} msg={failure.get('message')}"
                        )
                        break
            finally:
                browser.close()

        report = _aggregate_report(
            case_results,
            failures,
            base_url=base_url,
            started_at=started_at,
            seeded_existing_ids=seeded_existing_ids,
        )
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")

        print(f"Reporte JSON: {REPORT_PATH}")
        print(
            json.dumps(
                {
                    "all_passed": report["all_passed"],
                    "solicitudes_creadas": report["solicitudes_creadas"],
                    "clientes_creados": report["clientes_creados"],
                    "conteo_por_flujo": report["conteo_por_flujo"],
                    "conteo_por_plan": report["conteo_por_plan_en_exitos"],
                    "abonos_registrados": report["abonos_registrados"],
                    "fallos": len(report["fallos"]),
                },
                ensure_ascii=True,
                indent=2,
            )
        )
        return 0 if report["all_passed"] else 1
    finally:
        server.shutdown()
        thread.join(timeout=3)


if __name__ == "__main__":
    raise SystemExit(main())
