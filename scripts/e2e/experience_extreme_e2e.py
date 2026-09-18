#!/usr/bin/env python3
"""Browser + PostgreSQL E2E matrix focused on Solicitud.experiencia."""

from __future__ import annotations

import json
import hashlib
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

if (os.getenv("APP_ENV") or "").strip().lower() != "local":
    raise RuntimeError("EXPERIENCE E2E requires APP_ENV=local")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from werkzeug.security import generate_password_hash
from playwright.sync_api import sync_playwright

from scripts.e2e import public_intake_extreme_e2e as base
from config_app import db
from models import Cliente, Solicitud, StaffAuditLog, StaffPresenceState, StaffUser, TrustedDevice
from clientes.routes import generar_token_publico_cliente, generar_token_publico_cliente_nuevo
from utils.experiencia_solicitud import EXPERIENCIA_CLOSED_VALUES

# config_app imports dotenv before the local-only safety check.  Prevent a
# DATABASE_URL loaded from .env from being mistaken for the effective local
# database selected by APP_ENV=local.
os.environ.pop("DATABASE_URL", None)

RUN_ID = base.RUN_ID
ARTIFACT_DIR = ROOT / "artifacts" / "e2e" / "experience_extreme" / RUN_ID
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
ADMIN_USER = f"e2e_exp_admin_{RUN_ID.lower()}"
ADMIN_PASS = "admin12345"
ADMIN_EMAIL = f"{ADMIN_USER}@example.com"
EXPERIENCE = list(EXPERIENCIA_CLOSED_VALUES)
ACTIVE_PHASE = "SMOKE"


def fixture_identity(phase: str, case_id: str, client_type: str) -> dict[str, str]:
    key = f"{RUN_ID}:{phase}:{case_id}:{client_type}".encode("utf-8")
    digest = hashlib.sha256(key).hexdigest()
    prefix = "8294" if client_type == "nuevo" else "8295"
    return {
        "email": f"{RUN_ID.lower()}_{phase.lower()}_{case_id.lower()}@example.com",
        "phone": f"{prefix}{int(digest[:6], 16) % 1_000_000:06d}",
    }


def validate_fixture_identities(specs: list[tuple[str, str, str]]) -> dict[str, object]:
    identities = [fixture_identity(*spec) for spec in specs]
    emails = [item["email"] for item in identities]
    phones = [item["phone"] for item in identities]
    diagnostics = {
        "fixture_count": len(specs),
        "duplicate_emails": sorted(email for email, count in Counter(emails).items() if count > 1),
        "duplicate_phones": sorted(phone for phone, count in Counter(phones).items() if count > 1),
        "missing_identities": [spec for spec, identity in zip(specs, identities) if not identity["email"] or not identity["phone"]],
    }
    (ARTIFACT_DIR / "fixture_identity_validation.json").write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if any(diagnostics[key] for key in ("duplicate_emails", "duplicate_phones", "missing_identities")):
        raise RuntimeError("Duplicate fixture identity detected before E2E execution")
    print("FIXTURE IDENTITY VALIDATION: " + json.dumps(diagnostics, ensure_ascii=False, sort_keys=True), flush=True)
    return diagnostics


def seed_unique_existing_client(flask_app, phase: str, case_id: str) -> dict[str, str]:
    identity = fixture_identity(phase, case_id, "existente")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with flask_app.app_context():
        client = Cliente(
            codigo=f"E2EEXP{RUN_ID[-8:]}{hashlib.sha256(case_id.encode()).hexdigest()[:4]}",
            nombre_completo=f"{base.MARKER} {phase} {case_id}",
            email=identity["email"],
            telefono=identity["phone"],
            ciudad="Santiago",
            sector=f"Sector {case_id}",
            role="cliente",
            is_active=True,
            created_at=now,
            updated_at=now,
            fecha_registro=now,
            total_solicitudes=0,
        )
        db.session.add(client)
        db.session.commit()
        return {"id": str(client.id), "email": identity["email"], "phone": identity["phone"], "name": client.nombre_completo}


def historical_fixture_plan() -> list[tuple[str, str, str]]:
    plan = [
        ("SMOKE", "E2E-EXP-039", "10 años trabajando en casas de familia"),
        ("SMOKE", "E2E-EXP-048", "Experiencia inicial"),
        ("SMOKE", "E2E-EXP-050", "Experiencia inicial"),
        ("INTERMEDIA", "E2E-EXP-INT-009", "10 años trabajando en casas de familia"),
        ("INTERMEDIA", "E2E-EXP-INT-010", "Cuidaba una señora mayor y también cocinaba"),
        ("INTERMEDIA", "E2E-EXP-INT-011", "10 años trabajando en casas de familia"),
        ("INTERMEDIA", "E2E-EXP-INT-012", EXPERIENCE[0]),
        ("INTERMEDIA", "E2E-EXP-INT-013", "Experiencia inicial"),
        ("INTERMEDIA", "E2E-EXP-INT-014", "Experiencia inicial"),
    ]
    full_initials = [
        *EXPERIENCE,
        "10 años trabajando en casas de familia",
        "Cuidaba una señora mayor y también cocinaba",
        "Atención de niños, preparación de alimentos y acompañamiento",
        "  Experiencia con espacios internos, puntuación.  ",
    ] + ["Experiencia inicial"] * 8
    for index, value in enumerate(full_initials):
        case_id = f"E2E-EXP-{index + 33:03d}"
        plan.append(("COMPLETA", case_id, value))
    return plan


def validate_historical_fixture_plan(plan: list[tuple[str, str, str]]) -> None:
    keys = [(phase, case_id) for phase, case_id, _ in plan]
    originals = {(phase, case_id): value for phase, case_id, value in plan}
    duplicate_keys = [key for key, count in Counter(keys).items() if count > 1]
    missing_originals = [key for key, value in originals.items() if not value]
    if duplicate_keys or missing_originals:
        raise RuntimeError("Mutable historical fixture shared between E2E cases")
    print(json.dumps({
        "historical_fixture_count": len(plan),
        "duplicate_case_keys": duplicate_keys,
        "missing_original_experiences": missing_originals,
    }, ensure_ascii=False, sort_keys=True), flush=True)
    (ARTIFACT_DIR / "historical_fixture_validation.json").write_text(
        json.dumps({"plan": plan, "duplicate_case_keys": duplicate_keys, "missing_original_experiences": missing_originals}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def seed_admin_fixtures(flask_app, plan: list[tuple[str, str, str]]) -> dict:
    validate_historical_fixture_plan(plan)
    with flask_app.app_context():
        staff = StaffUser(username=ADMIN_USER, email=ADMIN_EMAIL, role="admin", is_active=True, mfa_enabled=False)
        staff.password_hash = generate_password_hash(ADMIN_PASS, method="pbkdf2:sha256")
        db.session.add(staff)
        db.session.flush()
        fixtures = {}
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for phase, case_id, original in plan:
            identity = fixture_identity(phase, case_id, "admin")
            client = Cliente(
                codigo=f"E2EEXP{RUN_ID[-8:]}{hashlib.sha256(f'{phase}:{case_id}'.encode()).hexdigest()[:4]}",
                nombre_completo=f"{base.MARKER} ADMIN {phase} {case_id}",
                email=identity["email"], telefono=identity["phone"], ciudad="Santiago", sector=f"Sector {case_id}",
                role="cliente", is_active=True, total_solicitudes=1,
                created_at=now, updated_at=now, fecha_registro=now,
            )
            db.session.add(client)
            db.session.flush()
            row = Solicitud(
                cliente_id=client.id, codigo_solicitud=f"E2EEXP-{RUN_ID[-8:]}-{hashlib.sha256(f'{phase}:{case_id}'.encode()).hexdigest()[:8]}",
                ciudad_sector="Santiago / Centro", rutas_cercanas="Ruta K", modalidad_trabajo="Con dormida",
                experiencia=original, horario="L-V 8:00 a 17:00", tipo_lugar="casa", habitaciones=2, banos=1,
                adultos=2, ninos=0, funciones=["limpieza"], edad_requerida=["26-35"], areas_comunes=["sala"], sueldo="22000",
            )
            db.session.add(row)
            db.session.flush()
            fixtures[(phase, case_id)] = {
                "staff_id": int(staff.id), "client_id": int(client.id), "solicitud_id": int(row.id),
                "original_experiencia": original,
            }
        db.session.commit()
        solicitation_ids = [fixture["solicitud_id"] for fixture in fixtures.values()]
        if len(solicitation_ids) != len(set(solicitation_ids)):
            raise RuntimeError("Mutable historical fixture shared between E2E cases")
        return {"staff_id": int(staff.id), "fixtures": fixtures}


def select_experience_value(page, selection: str) -> None:
    select = page.locator('select[name="experiencia"]')
    if not select.count():
        raise RuntimeError("No existe el selector único de experiencia")
    visual = page.locator('input[name="experiencia_visual"]')
    clicked = False
    for index in range(visual.count()):
        radio = visual.nth(index)
        if radio.get_attribute("value") == selection:
            if not radio.is_visible() or not radio.is_enabled():
                raise RuntimeError(f"Radio visual de experiencia no interactuable: {selection}")
            radio.check()
            if not radio.is_checked():
                raise RuntimeError(f"Radio visual de experiencia no quedó marcado: {selection}")
            clicked = True
            break
    if not clicked:
        select.select_option(value=selection)
    if select.input_value() != selection:
        raise RuntimeError(f"Experiencia no sincronizada: esperado={selection!r}, actual={select.input_value()!r}")


def set_experience(page, selection: str, other: str = "") -> None:
    select_experience_value(page, selection)
    field = page.locator('textarea[name="experiencia_otro"]')
    if selection == "otro":
        if not field.is_visible():
            raise RuntimeError("experiencia_otro no se mostró para Otro")
        field.fill(other)
        if field.input_value() != other:
            raise RuntimeError("experiencia_otro no conservó el texto")
    elif field.count() and field.is_visible():
        raise RuntimeError("experiencia_otro quedó visible para una opción cerrada")


_AUXILIARY_INVALIDATION_PATH = "/admin/live/invalidation/stream"


def _is_expected_auxiliary_429(url: str, status: int | None = None) -> bool:
    parsed = urlsplit(url)
    return (
        (status is None or status == 429)
        and parsed.path == _AUXILIARY_INVALIDATION_PATH
    )


def classify_http_response(url: str, status: int) -> tuple[str, dict]:
    event = {"url": url, "status": status, "pathname": urlsplit(url).path}
    if status == 429 and event["pathname"] == _AUXILIARY_INVALIDATION_PATH:
        return "expected_auxiliary_429", event
    if 400 <= status <= 599:
        return "functional_http_error", event
    return "ok", event


def is_browser_resource_console_message(message: str) -> bool:
    return bool(re.match(r"^Failed to load resource: the server responded with a status of \d+", message))


def attach_diagnostics(page, diagnostics: dict) -> None:
    expected_auxiliary_429 = diagnostics["expected_auxiliary_429"]
    functional_http_errors = diagnostics["functional_http_errors"]
    browser_resource_console_messages = diagnostics["browser_resource_console_messages"]
    functional_console_errors = diagnostics["functional_console_errors"]

    def on_response(response) -> None:
        category, event = classify_http_response(response.url, response.status)
        if category == "expected_auxiliary_429":
            expected_auxiliary_429.append(event)
        elif category == "functional_http_error":
            functional_http_errors.append(event)

    def on_console(message) -> None:
        if message.type != "error":
            return
        if is_browser_resource_console_message(message.text):
            browser_resource_console_messages.append(message.text)
            return
        functional_console_errors.append(message.text)

    page.context.on("response", on_response)
    page.on("console", on_console)
    page.on("pageerror", lambda error: diagnostics["page_errors"].append(str(error)))

def assert_no_errors(diagnostics: dict) -> None:
    if diagnostics["functional_http_errors"] or diagnostics["functional_console_errors"] or diagnostics["page_errors"]:
        raise RuntimeError(
            "Errores funcionales: "
            f"http={diagnostics['functional_http_errors']!r}; "
            f"console={diagnostics['functional_console_errors']!r}; "
            f"page={diagnostics['page_errors']!r}"
        )


def validate_http_classifier() -> None:
    auxiliary_category, _ = classify_http_response(
        "http://127.0.0.1:5000/admin/live/invalidation/stream?probe=1", 429
    )
    functional_category, _ = classify_http_response(
        "http://127.0.0.1:5000/clientes/solicitudes/publica", 429
    )
    if auxiliary_category != "expected_auxiliary_429" or functional_category != "functional_http_error":
        raise RuntimeError("HTTP classifier check failed")


def admin_login(page, base_url: str) -> None:
    response = page.goto(f"{base_url}/admin/login", wait_until="domcontentloaded", timeout=20000)
    if not response or response.status >= 400:
        raise RuntimeError(f"GET admin login inválido: {None if not response else response.status}")
    page.locator('input[name="usuario"]').fill(ADMIN_USER)
    page.locator('input[name="clave"]').fill(ADMIN_PASS)
    page.locator('button[type="submit"]').click()
    page.wait_for_load_state("domcontentloaded")
    if "/admin/login" in page.url:
        raise RuntimeError("Login admin no creó sesión")


def prepare_admin_valid_state(page) -> None:
    """Complete the UI-controlled composite fields before an admin POST."""
    specific = page.locator("#modalidad_especifica_select")
    if specific.count() and specific.is_visible():
        values = [option for option in specific.locator("option").all() if option.get_attribute("value")]
        if values:
            specific.select_option(value=values[0].get_attribute("value"))
    for selector, value in (
        ("#ciudad_input_ui", "Santiago"),
        ("#sector_input_ui", "Centro"),
        ("#horario_dormida_entrada", "Lunes 8:00 AM"),
        ("#horario_dormida_salida", "Sábado 12:00 PM"),
    ):
        field = page.locator(selector)
        if field.count() and field.is_visible():
            field.fill(value)


def admin_history_case(flask_app, base_url: str, fixture: dict, *,
                       selection: str | None = None, other: str = "",
                       legacy: str | None = None, xss: bool = False) -> dict:
    """Validate the admin edit UI and the persisted TEXT value for one row."""
    diagnostics = {
        "expected_auxiliary_429": [],
        "browser_resource_console_messages": [],
        "functional_http_errors": [],
        "functional_console_errors": [],
        "page_errors": [],
    }
    posts: list[str] = []
    row_id = fixture["solicitud_id"]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        attach_diagnostics(page, diagnostics)
        page.on("request", lambda req: posts.append(req.url) if req.method == "POST" else None)
        try:
            admin_login(page, base_url)
            # La redirección histórica del login apunta a /admin/solicitudes,
            # que no es una ruta de edición y responde 404 en este checkout.
            # No pertenece al caso; desde aquí se audita únicamente la página
            # objetivo y sus recursos.
            for values in diagnostics.values():
                values.clear()
            posts.clear()
            edit_url = f"{base_url}/admin/clientes/{fixture['client_id']}/solicitudes/{row_id}/editar"
            response = page.goto(edit_url, wait_until="domcontentloaded", timeout=20000)
            if not response or response.status >= 400:
                raise RuntimeError(f"GET edición inválido: {None if not response else response.status}")
            select = page.locator('select[name="experiencia"]')
            other_field = page.locator('textarea[name="experiencia_otro"]')
            if not select.count() or not select.is_enabled():
                raise RuntimeError("Selector experiencia ausente/deshabilitado en admin")
            observed = select.input_value()
            if observed == "otro":
                if not other_field.is_visible() or other_field.input_value() != fixture["original_experiencia"]:
                    raise RuntimeError("Histórico Otro no se precargó con el texto exacto")
            if legacy is not None:
                # Compatibility path: submit the real rendered form with an old
                # arbitrary value. LegacyExperienceSelectField maps it to Otro.
                select_experience_value(page, "Cocina")
                prepare_admin_valid_state(page)
                page.locator('button[type="submit"]').last.click()
                page.wait_for_timeout(350)
                page.goto(edit_url, wait_until="domcontentloaded", timeout=20000)
                prepare_admin_valid_state(page)
                response_info = page.evaluate(
                    """async ({url, legacy}) => {
                        const form = document.querySelector('form#solicitud-form');
                        const data = new FormData(form);
                        data.set('experiencia', legacy);
                        data.delete('experiencia_otro');
                        const response = await fetch(url, {
                            method: 'POST', body: data, credentials: 'same-origin',
                            redirect: 'manual'
                        });
                        return {status: response.status, body: await response.text()};
                    }""",
                    {"url": edit_url, "legacy": legacy},
                )
                if response_info["status"] >= 400:
                    raise RuntimeError(f"POST legacy rechazado: {response_info['status']}")
                legacy_response_body = response_info["body"][:1600]
                (ARTIFACT_DIR / "E2E-EXP-048-response.html").write_text(
                    response_info["body"], encoding="utf-8"
                )
                expected = legacy
            else:
                if selection is not None:
                    select_experience_value(page, selection)
                    if selection == "otro":
                        other_field.fill(other)
                        if other_field.input_value() != other:
                            raise RuntimeError("Texto Otro no quedó aplicado")
                prepare_admin_valid_state(page)
                page.locator('button[type="submit"]').last.click()
                page.wait_for_timeout(400)
                expected = other if selection == "otro" else (
                    selection or fixture["original_experiencia"]
                )
            with flask_app.app_context():
                row = Solicitud.query.get(row_id)
                if not row or row.experiencia != expected:
                    detail = f"UI/DB experiencia={None if not row else row.experiencia!r}, esperado={expected!r}"
                    if legacy is not None:
                        detail += f"; respuesta={legacy_response_body!r}"
                    raise RuntimeError(detail)
            assert_no_errors(diagnostics)
            if xss:
                if page.evaluate("() => Boolean(window.__exp_xss_test)"):
                    raise RuntimeError("Se ejecutó un marcador XSS inesperado")
                if "<script>" in page.locator("body").inner_text().lower():
                    raise RuntimeError("Texto XSS no escapado en la UI")
            result = {"ok": True, "post_count": 1 if (selection is not None or legacy is not None) else 0,
                      "url": page.url, "expected": expected,
                      **diagnostics}
            if legacy is not None:
                result["legacy_response_body"] = legacy_response_body
            return result
        finally:
            context.close()
            browser.close()


def public_case(flask_app, base_url: str, *, case_id: str, client_type: str, experience: str, other: str = "", cancel=False, invalid=False, double_submit=False) -> dict:
    from clientes.routes import generar_token_publico_cliente, generar_token_publico_cliente_nuevo
    phase = ACTIVE_PHASE
    identity = fixture_identity(phase, case_id, client_type)
    with flask_app.app_context():
        if client_type == "nuevo":
            token = generar_token_publico_cliente_nuevo(created_by=base.MARKER)
            client = None
        else:
            client = seed_unique_existing_client(flask_app, phase, case_id)
            token = generar_token_publico_cliente(Cliente.query.get(int(client["id"])))
    route = "nueva-publica" if client_type == "nuevo" else "publica"
    url = f"{base_url}/clientes/solicitudes/{route}/{token}"
    diagnostics = {
        "expected_auxiliary_429": [],
        "browser_resource_console_messages": [],
        "functional_http_errors": [],
        "functional_console_errors": [],
        "page_errors": [],
    }
    posts: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        attach_diagnostics(page, diagnostics)
        page.on("request", lambda req: posts.append(req.url) if req.method == "POST" else None)
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=20000)
            if not response or response.status >= 400:
                raise RuntimeError(f"GET público inválido: {None if not response else response.status}")
            catalog = base.discover_catalog(page)
            case = base.CaseSpec(
                case_id,
                "experience",
                client_type,
                (1440, 900),
                True,
                profile=1 if experience == "otro" else 0,
            )
            base.fill_valid_form(page, catalog, case, client)
            if client_type == "nuevo":
                page.locator('input[name="email_contacto"]').fill(identity["email"])
                page.locator('input[name="telefono_contacto"]').fill(identity["phone"])
            set_experience(page, experience, other)
            if invalid:
                select_experience_value(page, "otro")
                page.locator('textarea[name="experiencia_otro"]').fill("")
                assert page.locator("form").evaluate("form => !form.checkValidity()")
                assert not posts
            else:
                submit = page.locator('button[type="submit"]').last
                submit.click()
                page.wait_for_timeout(300)
                modal = page.get_by_text(base.MODAL_TITLE, exact=True)
                if not modal.count() or not modal.first.is_visible():
                    raise RuntimeError("No apareció el modal final de condiciones")
                if cancel:
                    page.get_by_role("button", name="Volver al formulario", exact=True).click()
                    page.wait_for_function(
                        "() => { const el = document.querySelector('.employment-conditions-modal'); "
                        "return !el || getComputedStyle(el).display === 'none' || el.getAttribute('aria-hidden') === 'true'; }",
                        timeout=5000,
                    )
                    if posts:
                        raise RuntimeError("Cancelar produjo POST")
                    submit.click()
                    page.wait_for_function(
                        "() => { const el = document.querySelector('.employment-conditions-modal'); "
                        "return el && getComputedStyle(el).display !== 'none' && el.getAttribute('aria-hidden') !== 'true'; }",
                        timeout=5000,
                    )
                    page.wait_for_timeout(300)
                if double_submit:
                    base.confirm_conditions_modal(page, double=True)
                else:
                    base.confirm_conditions_modal(page)
                page.wait_for_timeout(500)
                if len([url for url in posts if "/plan" not in url]) != 1:
                    raise RuntimeError(f"POST esperado=1 observado={posts!r}")
                page.wait_for_url("**/plan", timeout=15000)
                base.choose_real_plan(page, 0)
                page.wait_for_url("**/plan/resumen", timeout=15000)
            assert_no_errors(diagnostics)
            if invalid:
                result = {"ok": True, "post_count": 0, "url": page.url,
                          **diagnostics}
            else:
                with flask_app.app_context():
                    if client_type == "nuevo":
                        row_client = Cliente.query.filter_by(email=identity["email"]).first()
                    else:
                        row_client = Cliente.query.get(int(client["id"]))
                    row = Solicitud.query.filter_by(cliente_id=row_client.id).order_by(Solicitud.id.desc()).first()
                    expected = other if experience == "otro" else experience
                    if row.experiencia != expected:
                        raise RuntimeError(f"DB experiencia={row.experiencia!r}, expected={expected!r}")
                result = {"ok": True, "post_count": 1, "db_experience": expected, "url": page.url,
                          **diagnostics}
            return result
        finally:
            context.close()
            browser.close()


def main() -> int:
    global ACTIVE_PHASE
    validate_http_classifier()
    print("HTTP CLASSIFIER CHECK: PASS", flush=True)
    db_url, flask_app = base.safe_database_check()
    server = thread = None
    admin_fixture = None
    report = {
        "run_id": RUN_ID,
        "cases": [],
        "cleanup": None,
        "http_classifier_check": "PASS",
    }
    fixture_specs = [
        ("SMOKE", "E2E-EXP-001", "nuevo"),
        ("SMOKE", "E2E-EXP-007", "nuevo"),
        ("INTERMEDIA", "E2E-EXP-INT-001", "nuevo"),
        ("INTERMEDIA", "E2E-EXP-INT-002", "nuevo"),
        ("INTERMEDIA", "E2E-EXP-INT-003", "existente"),
        ("INTERMEDIA", "E2E-EXP-INT-004", "existente"),
        ("INTERMEDIA", "E2E-EXP-INT-005", "nuevo"),
        ("INTERMEDIA", "E2E-EXP-INT-006", "existente"),
        ("INTERMEDIA", "E2E-EXP-INT-007", "nuevo"),
        ("INTERMEDIA", "E2E-EXP-INT-008", "nuevo"),
        ("INTERMEDIA", "E2E-EXP-INT-015", "existente"),
    ]
    fixture_specs.extend(
        [("COMPLETA", f"E2E-EXP-{index:03d}", "nuevo") for index in range(1, 19)]
        + [("COMPLETA", f"E2E-EXP-{index:03d}", "existente") for index in range(19, 33)]
    )
    report["fixture_identity_validation"] = validate_fixture_identities(fixture_specs)
    try:
        server, thread, base_url = base.start_internal_server(flask_app)
        admin_fixture = seed_admin_fixtures(flask_app, historical_fixture_plan())
        def history(phase, case_id, **kwargs):
            return lambda: admin_history_case(
                flask_app, base_url, admin_fixture["fixtures"][(phase, case_id)], **kwargs
            )
        smoke = [
            ("E2E-EXP-001", lambda: public_case(flask_app, base_url, case_id="E2E-EXP-001", client_type="nuevo", experience="Doméstica completa")),
            ("E2E-EXP-007", lambda: public_case(flask_app, base_url, case_id="E2E-EXP-007", client_type="nuevo", experience="otro", other="Experiencia en limpieza profunda")),
            ("E2E-EXP-039", history("SMOKE", "E2E-EXP-039")),
            ("E2E-EXP-048", history("SMOKE", "E2E-EXP-048", legacy="Experiencia legacy enviada por cliente")),
            ("E2E-EXP-050", history("SMOKE", "E2E-EXP-050", selection="otro", other="<script>window.__exp_xss_test=true</script>", xss=True)),
        ]
        only = (os.getenv("EXPERIENCE_ONLY") or "").strip()
        only_intermediate = (os.getenv("EXPERIENCE_INTERMEDIATE_ONLY") or "").strip()
        only_admin = [value.strip() for value in (os.getenv("EXPERIENCE_ADMIN_ONLY") or "").split(",") if value.strip()]
        only_final = (os.getenv("EXPERIENCE_FINAL_ONLY") or "").strip().lower() in {"1", "true", "yes"}
        if only:
            smoke = [(case_id, fn) for case_id, fn in smoke if case_id == only]
            if not smoke:
                raise RuntimeError(f"Caso no disponible en smoke: {only}")
        elif only_intermediate or only_admin or only_final:
            smoke = []
        ACTIVE_PHASE = "SMOKE"
        for case_id, fn in smoke:
            started = time.monotonic()
            try:
                result = fn()
                report["cases"].append({"id": case_id, "status": "PASS", "elapsed": round(time.monotonic() - started, 3), **result})
                print(f"[{case_id}] PASS", flush=True)
            except Exception as exc:
                report["cases"].append({"id": case_id, "status": "FAIL", "elapsed": round(time.monotonic() - started, 3), "error": str(exc)})
                print(f"[{case_id}] FAIL: {exc}", flush=True)
                raise
        report["summary"] = {"pass": len(smoke), "fail": 0, "phase": "smoke"}
        print(f"SMOKE {len(smoke)}/{len(smoke)} PASS", flush=True)
        if only:
            return 0

        def run_cases(label, specs):
            phase = {"label": label, "pass": 0, "fail": 0, "cases": []}
            for case_id, fn in specs:
                started = time.monotonic()
                try:
                    result = fn()
                    phase["pass"] += 1
                    phase["cases"].append({"id": case_id, "status": "PASS", "elapsed": round(time.monotonic() - started, 3), **result})
                    print(f"[{case_id}] PASS", flush=True)
                except Exception as exc:
                    phase["fail"] += 1
                    phase["cases"].append({"id": case_id, "status": "FAIL", "elapsed": round(time.monotonic() - started, 3), "error": str(exc)})
                    print(f"[{case_id}] FAIL: {exc}", flush=True)
                    report.setdefault("phases", []).append(phase)
                    raise
            report.setdefault("phases", []).append(phase)
            print(f"{label}: {phase['pass']}/{len(specs)} PASS", flush=True)
            return phase

        closed = list(EXPERIENCE)
        intermediate = [
            ("E2E-EXP-INT-001", lambda: public_case(flask_app, base_url, case_id="E2E-EXP-INT-001", client_type="nuevo", experience=closed[0])),
            ("E2E-EXP-INT-002", lambda: public_case(flask_app, base_url, case_id="E2E-EXP-INT-002", client_type="nuevo", experience=closed[1])),
            ("E2E-EXP-INT-003", lambda: public_case(flask_app, base_url, case_id="E2E-EXP-INT-003", client_type="existente", experience=closed[2])),
            ("E2E-EXP-INT-004", lambda: public_case(flask_app, base_url, case_id="E2E-EXP-INT-004", client_type="existente", experience=closed[3])),
            ("E2E-EXP-INT-005", lambda: public_case(flask_app, base_url, case_id="E2E-EXP-INT-005", client_type="nuevo", experience=closed[4])),
            ("E2E-EXP-INT-006", lambda: public_case(flask_app, base_url, case_id="E2E-EXP-INT-006", client_type="existente", experience=closed[5])),
            ("E2E-EXP-INT-007", lambda: public_case(flask_app, base_url, case_id="E2E-EXP-INT-007", client_type="nuevo", experience="otro", other="Cocina y limpieza profunda")),
            ("E2E-EXP-INT-008", lambda: public_case(flask_app, base_url, case_id="E2E-EXP-INT-008", client_type="nuevo", experience="otro", invalid=True)),
            ("E2E-EXP-INT-009", history("INTERMEDIA", "E2E-EXP-INT-009")),
            ("E2E-EXP-INT-010", history("INTERMEDIA", "E2E-EXP-INT-010")),
            ("E2E-EXP-INT-011", history("INTERMEDIA", "E2E-EXP-INT-011", selection=closed[0])),
            ("E2E-EXP-INT-012", history("INTERMEDIA", "E2E-EXP-INT-012", selection="otro", other="Acompañamiento especializado")),
            ("E2E-EXP-INT-013", history("INTERMEDIA", "E2E-EXP-INT-013", legacy="Experiencia legacy intermedia")),
            ("E2E-EXP-INT-014", history("INTERMEDIA", "E2E-EXP-INT-014", selection="otro", other="<script>window.__exp_xss_test=true</script>", xss=True)),
            ("E2E-EXP-INT-015", lambda: public_case(flask_app, base_url, case_id="E2E-EXP-INT-015", client_type="existente", experience=closed[0], cancel=True, double_submit=True)),
        ]
        if only_intermediate:
            intermediate = [(case_id, fn) for case_id, fn in intermediate if case_id == only_intermediate]
            if not intermediate:
                raise RuntimeError(f"Caso intermedio no disponible: {only_intermediate}")
        if not only_final:
            ACTIVE_PHASE = "INTERMEDIA"
            if not only_admin:
                run_cases("INTERMEDIA", intermediate)
        if only_intermediate:
            return 0

        full = []
        for index in range(18):
            exp = closed[index % len(closed)]
            full.append((f"E2E-EXP-{index+1:03d}", lambda index=index, exp=exp: public_case(flask_app, base_url, case_id=f"E2E-EXP-{index+1:03d}", client_type="nuevo", experience=exp, double_submit=index == 10)))
        for index in range(14):
            exp = closed[(index + 2) % len(closed)]
            full.append((f"E2E-EXP-{index+19:03d}", lambda index=index, exp=exp: public_case(flask_app, base_url, case_id=f"E2E-EXP-{index+19:03d}", client_type="existente", experience=exp, cancel=index == 5)))
        admin_specs = [
            *[(f"E2E-EXP-{index:03d}", history("COMPLETA", f"E2E-EXP-{index:03d}")) for index in range(33, 48)],
            ("E2E-EXP-048", history("COMPLETA", "E2E-EXP-048", legacy="Experiencia legacy de matriz")),
            ("E2E-EXP-049", history("COMPLETA", "E2E-EXP-049", selection="otro", other="Unicode: atención y años ñáéíóú")),
            ("E2E-EXP-050", history("COMPLETA", "E2E-EXP-050", selection="otro", other="<script>window.__exp_xss_test=true</script>", xss=True)),
        ]
        if only_admin:
            requested = set(only_admin)
            missing = [case_id for case_id in only_admin if case_id not in {case_id for case_id, _ in admin_specs}]
            if missing:
                raise RuntimeError(f"Casos admin no disponibles: {', '.join(missing)}")
            run_cases("ADMIN_ONLY", [(case_id, fn) for case_id, fn in admin_specs if case_id in requested])
            return 0
        full.extend(admin_specs)
        ACTIVE_PHASE = "COMPLETA"
        run_cases("COMPLETA", full)
        return 0
    finally:
        try:
            with flask_app.app_context():
                staff_id = (admin_fixture or {}).get("staff_id") if admin_fixture else None
                if staff_id:
                    # StaffAuditLog is intentionally immutable through the ORM.
                    # Null only the exact E2E actor references, then remove the
                    # exact local staff fixture through SQLAlchemy Core.
                    db.session.execute(
                        StaffAuditLog.__table__.update()
                        .where(StaffAuditLog.actor_user_id == int(staff_id))
                        .values(actor_user_id=None)
                    )
                    db.session.execute(
                        StaffPresenceState.__table__.delete().where(StaffPresenceState.user_id == int(staff_id))
                    )
                    db.session.execute(
                        TrustedDevice.__table__.delete().where(TrustedDevice.user_id == int(staff_id))
                    )
                    db.session.execute(StaffUser.__table__.delete().where(StaffUser.id == int(staff_id)))
                    db.session.commit()
        except Exception as exc:
            report["cleanup_staff_error"] = str(exc)
        try:
            report["cleanup"] = base.cleanup(flask_app)
        except Exception as exc:
            report["cleanup"] = {"ok": False, "error": str(exc)}
        (ARTIFACT_DIR / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        if server is not None:
            server.shutdown()
        if thread is not None:
            thread.join(timeout=3)


if __name__ == "__main__":
    raise SystemExit(main())
