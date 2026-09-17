#!/usr/bin/env python3
"""Extreme browser suite for the public Domestica intake flow.

The default mode starts a short-lived local Flask server, matching the
controlled E2E infrastructure used by the project's realistic runner. Use
``--server-mode external`` to target an already-running local application.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import re
import socket
import sys
import threading
import time
import traceback
import urllib.parse
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
ARTIFACT_DIR = ROOT / "artifacts" / "e2e" / "public_intake_extreme" / RUN_ID
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
MARKER = f"E2E-EXTREME-{RUN_ID}"
BASE_URL_DEFAULT = "http://127.0.0.1:10000"
LOCAL_EXTERNAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
MODAL_TITLE = "Confirmación de las condiciones del empleo"
CONFIRM_LABEL = "Confirmo y acepto"
CASE_TIMEOUT_SECONDS = 60
NEW_PHONE_PREFIX = "82920"
EXISTING_PHONE_PREFIX = "82930"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("E2E_BASE_URL"))
    parser.add_argument("--server-mode", choices=("internal", "external"), default="internal")
    parser.add_argument("--limit", type=int, default=100, help="cantidad de casos; 1/5 sirven para smoke tests")
    parser.add_argument("--case-ids", help="IDs concretos separados por coma, por ejemplo E2E-002,E2E-004")
    parser.add_argument("--workers", type=int, default=int(os.getenv("E2E_WORKERS", "4")), help="workers de navegador; 1 ejecuta secuencialmente")
    parser.add_argument("--headed", action="store_true", help="abre Chromium visible en el Mac local")
    parser.add_argument("--slow-mo", type=int, default=0)
    return parser.parse_args()


def validate_external_base_url(base_url: str) -> str:
    """Allow external mode to target only a local HTTP(S) server."""
    parsed = urllib.parse.urlparse(base_url)
    try:
        hostname = (parsed.hostname or "").lower()
    except ValueError:
        hostname = ""
    if parsed.scheme.lower() not in {"http", "https"} or hostname not in LOCAL_EXTERNAL_HOSTS:
        raise RuntimeError(
            "External E2E base URL must target localhost/127.0.0.1/::1. "
            "Remote targets are blocked."
        )
    return base_url.rstrip("/")


def free_port() -> int:
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def start_internal_server(flask_app: Any) -> tuple[Any, threading.Thread, str]:
    port = free_port()
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
    server.shutdown()
    thread.join(timeout=3)
    raise RuntimeError("No se pudo levantar el servidor Flask interno para E2E")


def redact_db_url(value: str) -> str:
    return re.sub(r"//([^:@/]+)(?::[^@/]*)?@", r"//\1:***@", value)


def safe_database_check() -> tuple[str, Any]:
    """Validate both the configured environment and the effective SQLAlchemy URL."""
    raw_candidates = [os.getenv("DATABASE_URL"), os.getenv("DATABASE_URL_LOCAL")]
    env = (os.getenv("APP_ENV") or "").strip().lower()
    if env != "local":
        raise RuntimeError(f"SAFE DATABASE CHECK: FAIL (APP_ENV debe ser local; actual={env!r})")

    for raw in raw_candidates:
        if raw:
            lowered = raw.lower()
            parsed = urllib.parse.urlsplit(raw.replace("postgresql+psycopg2://", "postgresql://"))
            host = (parsed.hostname or "").lower()
            if host not in {"localhost", "127.0.0.1", "::1"}:
                raise RuntimeError("SAFE DATABASE CHECK: FAIL (hostname no local en la URL efectiva del entorno)")
            if "domestica_cibao_local" not in (parsed.path or ""):
                raise RuntimeError("SAFE DATABASE CHECK: FAIL (la base no contiene domestica_cibao_local)")
            if "render.com" in lowered or "mis_candidatas_db" in lowered:
                raise RuntimeError("SAFE DATABASE CHECK: FAIL (URL remota/producción detectada)")

    from app import app as flask_app  # imported only after the environment pre-check

    effective = str(flask_app.config.get("SQLALCHEMY_DATABASE_URI") or "")
    normalized = effective.replace("postgresql+psycopg2://", "postgresql://")
    parsed = urllib.parse.urlsplit(normalized)
    host = (parsed.hostname or "").lower()
    lowered = effective.lower()
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("SAFE DATABASE CHECK: FAIL (effective hostname no local)")
    if "domestica_cibao_local" not in (parsed.path or ""):
        raise RuntimeError("SAFE DATABASE CHECK: FAIL (effective DB no es domestica_cibao_local)")
    if "render.com" in lowered or "mis_candidatas_db" in lowered:
        raise RuntimeError("SAFE DATABASE CHECK: FAIL (effective DB remota/producción)")
    print("SAFE DATABASE CHECK: PASS")
    return effective, flask_app


@dataclass
class FormCatalog:
    values: dict[str, list[str]]
    options: dict[str, list[dict[str, str]]]


@dataclass
class CaseSpec:
    case_id: str
    purpose: str
    client_type: str
    viewport: tuple[int, int]
    valid: bool
    wrapper: bool = False
    block_cdn: bool = False
    block_local_bootstrap: bool = False
    interaction: str = "accept"
    invalidation: str = ""
    profile: int = 0
    expected_plan_slot: int = 0
    category: str = ""


def discover_catalog(page: Page) -> FormCatalog:
    payload = page.evaluate(
        """() => {
          const root = document.querySelector('form');
          if (!root) throw new Error('No se encontró el formulario público');
          const values = {};
          const options = {};
          root.querySelectorAll('input[type=checkbox], input[type=radio], select').forEach((el) => {
            const name = el.name || el.id;
            if (!name) return;
            if (el.tagName === 'SELECT') {
              options[name] = Array.from(el.options).map((o) => ({value: o.value, label: o.textContent.trim()}));
              return;
            }
            values[name] = values[name] || [];
            if (el.value && !values[name].includes(el.value)) values[name].push(el.value);
          });
          return {values, options};
        }"""
    )
    catalog = FormCatalog(values=payload["values"], options=payload["options"])
    validation_inventory = page.evaluate(
        """() => {
          const form = document.querySelector('form');
          const controls = Array.from(form.querySelectorAll('input, select, textarea')).map(el => ({
            name: el.name || '', id: el.id || '', type: el.type || el.tagName.toLowerCase(),
            required: !!el.required, pattern: el.getAttribute('pattern'),
            min: el.getAttribute('min'), max: el.getAttribute('max'),
            minlength: el.getAttribute('minlength'), maxlength: el.getAttribute('maxlength'),
            value: 'value' in el ? String(el.value || '') : '',
          }));
          return {
            controls,
            form_novalidate: !!form.noValidate,
            custom_rules: [
              {target: 'edad_requerida', mechanism: 'setCustomValidity', message: 'Selecciona una opción de edad.'},
              {target: 'funciones', mechanism: 'setCustomValidity', message: 'Selecciona al menos una función.'},
              {target: 'areas_comunes', mechanism: 'setCustomValidity', message: 'Selecciona al menos un área común.'},
              {target: 'ciudad_input_ui/sector_input_ui', mechanism: 'setCustomValidity', message: 'Completa la ciudad o el sector.'},
              {target: 'ninos/edades_ninos', mechanism: 'required condicionado', condition: 'funciones contiene ninos y ninos > 0'},
            ],
          };
        }"""
    )
    (ARTIFACT_DIR / "validation_inventory.json").write_text(
        json.dumps(validation_inventory, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    required_groups = ("edad_requerida", "funciones", "areas_comunes")
    for name in required_groups:
        if not catalog.values.get(name):
            raise RuntimeError(f"El formulario no expone valores reales para {name}")
    (ARTIFACT_DIR / "form_catalog.json").write_text(json.dumps(asdict(catalog), ensure_ascii=False, indent=2), encoding="utf-8")
    return catalog


def build_matrix() -> list[CaseSpec]:
    desktop = (1440, 900)
    laptop = (1280, 800)
    mobile = (390, 844)
    cases: list[CaseSpec] = []

    # 30 valid new-client combinations.
    for i in range(30):
        cases.append(CaseSpec(f"E2E-{len(cases)+1:03d}", "cliente nuevo válido con combinación real de campos", "nuevo", (desktop, laptop, mobile)[i % 3], True, profile=i, expected_plan_slot=i, category="cliente_nuevo_validos"))
    # 30 valid existing-client combinations.
    for i in range(30):
        cases.append(CaseSpec(f"E2E-{len(cases)+1:03d}", "cliente existente válido sin duplicar cliente", "existente", (desktop, laptop, mobile)[i % 3], True, profile=i + 30, expected_plan_slot=i, category="cliente_existente_validos"))
    invalidations = [
        "edad_requerida", "funciones", "areas_comunes", "terms", "ciudad", "sector", "telefono", "email",
        "sueldo", "modalidad", "horario", "adultos", "ninos", "edades_ninos", "tipo_lugar", "habitaciones",
        "banos", "pisos", "rutas_cercanas", "experiencia",
    ]
    for invalidation in invalidations:
        client_type = "nuevo" if len(cases) % 2 == 0 else "existente"
        cases.append(CaseSpec(f"E2E-{len(cases)+1:03d}", f"rechaza formulario incompleto o inválido: {invalidation}", client_type, laptop if len(cases) % 2 else desktop, False, invalidation=invalidation, profile=len(cases), category="validaciones_negativas"))
    negative_overrides = {
        "E2E-063": ("areas_comunes", "grupo de áreas comunes sin selección (custom validity)"),
        "E2E-067": ("edad_requerida", "grupo de edad sin selección (custom validity)"),
        "E2E-068": ("modalidad_especifica", "modalidad específica requerida sin selección"),
        "E2E-070": ("tipo_lugar", "tipo de lugar requerido con limpieza activa"),
        "E2E-073": ("funciones", "grupo de funciones sin selección (custom validity)"),
        "E2E-074": ("edades_ninos", "edades requeridas con niños activos"),
        "E2E-077": ("modalidad_especifica", "modalidad específica requerida sin selección"),
        "E2E-076": ("areas_comunes", "grupo de áreas comunes sin selección (custom validity)"),
        "E2E-078": ("sector_real", "sector visible requerido vacío"),
        "E2E-079": ("terms", "términos y política requeridos sin aceptación"),
    }
    for case in cases:
        if case.case_id in negative_overrides:
            case.invalidation, detail = negative_overrides[case.case_id]
            case.purpose = f"rechaza formulario: {detail}"
    interactions = [
        ("cancel", "nuevo", mobile), ("close_x", "existente", desktop), ("reopen", "nuevo", laptop),
        ("double_click", "existente", mobile), ("rapid_click", "nuevo", desktop),
        ("cancel", "existente", mobile), ("close_x", "nuevo", laptop), ("reopen", "existente", desktop),
        ("double_click", "nuevo", mobile), ("rapid_click", "existente", laptop),
    ]
    for interaction, client_type, viewport in interactions:
        cases.append(CaseSpec(f"E2E-{len(cases)+1:03d}", f"interacción modal: {interaction} ({client_type}, {viewport[0]}x{viewport[1]})", client_type, viewport, True, interaction=interaction, profile=len(cases), expected_plan_slot=len(cases), category="interaccion_modal"))
    resilience = [
        (True, False, "CDN bloqueado; fallback local"),
        (True, False, "CDN bloqueado con viewport móvil"),
        (True, True, "CDN y fallback local bloqueados; confirm nativo"),
        (True, True, "confirm nativo cancelado y reintento"),
        (True, True, "confirm nativo aceptado"),
        (False, False, "wrapper /solicitud/<codigo>/continuar"),
        (False, False, "wrapper con refresh previo"),
        (False, False, "wrapper móvil con scroll"),
        (True, False, "CDN bloqueado en cliente existente"),
        (False, False, "reapertura del enlace antes de plan"),
    ]
    for block_cdn, block_local, purpose in resilience:
        cases.append(CaseSpec(f"E2E-{len(cases)+1:03d}", purpose, "nuevo" if len(cases) % 2 == 0 else "existente", mobile if len(cases) % 3 == 0 else desktop, True, wrapper="wrapper" in purpose or "enlace" in purpose, block_cdn=block_cdn, block_local_bootstrap=block_local, interaction="native_cancel" if "cancelado" in purpose else "accept", profile=len(cases), expected_plan_slot=len(cases), category="resiliencia_navegacion"))
    validate_matrix(cases)
    return cases


def validate_matrix(cases: list[CaseSpec]) -> None:
    expected_ids = [f"E2E-{i:03d}" for i in range(1, 101)]
    actual_ids = [case.case_id for case in cases]
    counts = Counter(actual_ids)
    duplicates = sorted(case_id for case_id, count in counts.items() if count > 1)
    missing_ids = sorted(set(expected_ids) - set(actual_ids))
    extra_ids = sorted(set(actual_ids) - set(expected_ids))
    category_counts = dict(Counter(case.category for case in cases))
    diagnostic = {
        "count": len(cases),
        "first_id": actual_ids[0] if actual_ids else None,
        "last_id": actual_ids[-1] if actual_ids else None,
        "duplicates": duplicates,
        "missing_ids": missing_ids,
        "extra_ids": extra_ids,
        "category_counts": category_counts,
    }
    print("E2E MATRIX VALIDATION: " + json.dumps(diagnostic, ensure_ascii=False, sort_keys=True))
    expected_categories = {
        "cliente_nuevo_validos": 30,
        "cliente_existente_validos": 30,
        "validaciones_negativas": 20,
        "interaccion_modal": 10,
        "resiliencia_navegacion": 10,
    }
    invalid_without_target = sorted(
        case.case_id for case in cases
        if not case.valid and not case.invalidation
    )
    diagnostic["invalid_without_target"] = invalid_without_target
    if actual_ids != expected_ids or duplicates or missing_ids or extra_ids or category_counts != expected_categories or invalid_without_target:
        raise RuntimeError("Matriz E2E inválida: " + json.dumps(diagnostic, ensure_ascii=False, sort_keys=True))


def new_phone_for_case(case: CaseSpec) -> str:
    """Return a deterministic phone in the new-client namespace."""
    return f"{NEW_PHONE_PREFIX}{case.profile:05d}"


def existing_phone_for_index(index: int) -> str:
    """Return a deterministic phone in the existing-client namespace."""
    return f"{EXISTING_PHONE_PREFIX}{index:05d}"


def validate_fixture_matrix(cases: list[CaseSpec], existing_count: int = 0) -> dict[str, Any]:
    """Validate fixture namespaces before starting any browser worker."""
    new_cases = [case for case in cases if case.client_type == "nuevo"]
    new_phones = [new_phone_for_case(case) for case in new_cases]
    existing_phones = [existing_phone_for_index(index) for index in range(existing_count)]
    diagnostics = {
        "new_phone_count": len(new_phones),
        "existing_phone_count": len(existing_phones),
        "new_phone_duplicates": sorted(phone for phone, count in Counter(new_phones).items() if count > 1),
        "existing_phone_duplicates": sorted(phone for phone, count in Counter(existing_phones).items() if count > 1),
        "cross_namespace_collisions": sorted(set(new_phones) & set(existing_phones)),
        "invalid_without_target": sorted(case.case_id for case in cases if not case.valid and not case.invalidation),
    }
    (ARTIFACT_DIR / "matrix_validation.json").write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("E2E FIXTURE VALIDATION: " + json.dumps(diagnostics, ensure_ascii=False, sort_keys=True))
    if any(diagnostics[key] for key in (
        "new_phone_duplicates",
        "existing_phone_duplicates",
        "cross_namespace_collisions",
        "invalid_without_target",
    )):
        raise RuntimeError("Fixtures E2E inválidos: " + json.dumps(diagnostics, ensure_ascii=False, sort_keys=True))
    return diagnostics


def actual_value(catalog: FormCatalog, name: str, index: int = 0, fallback: str | None = None) -> str:
    vals = catalog.values.get(name) or []
    if vals:
        return vals[index % len(vals)]
    opts = [o["value"] for o in catalog.options.get(name, []) if o.get("value")]
    if opts:
        return opts[index % len(opts)]
    if fallback is not None:
        return fallback
    raise RuntimeError(f"No existe un valor real para {name}")


def first_select_value(catalog: FormCatalog, name: str, *, avoid_empty: bool = True) -> str:
    options = catalog.options.get(name) or []
    candidates = [o["value"] for o in options if not avoid_empty or o.get("value")]
    if not candidates:
        raise RuntimeError(f"No hay opciones reales para {name}")
    return candidates[0]


def locator_exists(page: Page, selector: str) -> bool:
    return page.locator(selector).count() > 0


class CaseTrace:
    """Short-lived per-case trace for the main browser-flow steps."""

    def __init__(self, case_id: str, case_dir: Path) -> None:
        self.case_id = case_id
        self.case_dir = case_dir
        self.case_dir.mkdir(parents=True, exist_ok=True)
        self.started = time.monotonic()
        self.last_step = ""
        self.events: list[dict[str, Any]] = []

    def step(self, label: str, operation: Any) -> Any:
        if time.monotonic() - self.started >= CASE_TIMEOUT_SECONDS:
            raise TimeoutError(f"{self.case_id}: timeout global de {CASE_TIMEOUT_SECONDS}s antes de {label}; último paso={self.last_step}")
        self.last_step = label
        self.events.append({"event": "START", "step": label, "at": round(time.monotonic() - self.started, 3)})
        print(f"[{self.case_id}] {label} START", flush=True)
        try:
            result = operation()
        except Exception as exc:
            self.events.append({"event": "FAIL", "step": label, "error": f"{type(exc).__name__}: {exc}"})
            raise RuntimeError(f"{self.case_id}: fallo en {label}: {exc}") from exc
        if time.monotonic() - self.started >= CASE_TIMEOUT_SECONDS:
            raise TimeoutError(f"{self.case_id}: timeout global de {CASE_TIMEOUT_SECONDS}s después de {label}")
        self.events.append({"event": "OK", "step": label, "at": round(time.monotonic() - self.started, 3)})
        print(f"[{self.case_id}] {label} OK", flush=True)
        return result

    def skip(self, label: str, reason: str) -> None:
        self.last_step = f"{label} SKIP: {reason}"
        self.events.append({"event": "SKIP", "step": label, "reason": reason, "at": round(time.monotonic() - self.started, 3)})
        print(f"[{self.case_id}] {label} SKIP: {reason}", flush=True)

    def close(self) -> None:
        return None


def traced_fill(trace: CaseTrace, page: Page, label: str, selector: str, value: str) -> None:
    trace.step(f"fill {label}", lambda: fill_if_present(page, selector, value))


def fill_if_present(page: Page, selector: str, value: str, *, force: bool = False) -> None:
    loc = page.locator(selector).first
    if not loc.count():
        return
    # Some values are mirrored into hidden inputs by the form.  Do not spend
    # the full Playwright fill timeout discovering that; update the DOM value
    # through the same input/change events used by the previous fallback.
    if not loc.is_visible():
        page.evaluate("([sel, value]) => { const e=document.querySelector(sel); if(e){e.value=value; e.dispatchEvent(new Event('input',{bubbles:true})); e.dispatchEvent(new Event('change',{bubbles:true}));} }", [selector, str(value)])
        return
    try:
        loc.fill(str(value), force=force, timeout=10000)
    except Exception:
        page.evaluate("([sel, value]) => { const e=document.querySelector(sel); if(e){e.value=value; e.dispatchEvent(new Event('input',{bubbles:true})); e.dispatchEvent(new Event('change',{bubbles:true}));} }", [selector, str(value)])


def check_value(page: Page, name: str, value: str) -> None:
    check_control(page, name, value)


DIRECT_LABEL_CONTROL_NAMES = frozenset({
    "modalidad_grupo",
    "habitaciones_selector",
    "banos_selector",
    "pisos_selector",
})


def check_control(page: Page, name: str, value: str, *, timeout: int = 8000) -> None:
    """Select a real checkbox/radio through the visual control when required."""
    loc = page.locator(f'input[name="{name}"][value="{value}"]').first
    if not loc.count():
        raise RuntimeError(f"No se encontró input real {name}={value!r}")
    label = page.locator(f'label:has(input[name="{name}"][value="{value}"])').first
    has_visual_label = label.count() and label.locator(":scope span").count() > 0
    use_direct_label = name in DIRECT_LABEL_CONTROL_NAMES and has_visual_label

    if use_direct_label:
        label.scroll_into_view_if_needed(timeout=timeout)
        label.click(timeout=timeout)
    elif not loc.is_visible() and label.count():
        label.scroll_into_view_if_needed(timeout=timeout)
        label.click(timeout=timeout)
    else:
        loc.scroll_into_view_if_needed(timeout=timeout)
        try:
            loc.check(timeout=timeout)
        except Exception as first_error:
            if not label.count():
                raise RuntimeError(
                    f"No se pudo seleccionar {name}={value!r}; input_id={loc.get_attribute('id')!r}, "
                    f"type={loc.get_attribute('type')!r}, no existe label asociado. Error: {first_error}"
                ) from first_error
            label.scroll_into_view_if_needed(timeout=timeout)
            label.click(timeout=timeout)
    if not loc.is_checked():
        raise RuntimeError(
            f"El control {name}={value!r} no quedó marcado; input_id={loc.get_attribute('id')!r}, "
            f"type={loc.get_attribute('type')!r}, visible={loc.is_visible()}"
        )
def select_dynamic_option(page: Page, group_value: str, *, timeout: int = 10000, trace: CaseTrace | None = None) -> dict[str, Any]:
    """Select modalidad_grupo, wait for the real dependent options, then choose one."""
    select_group = lambda: check_control(page, "modalidad_grupo", group_value, timeout=timeout)
    if trace:
        trace.step("modalidad_grupo", select_group)
    else:
        select_group()
    select = page.locator("#modalidad_especifica_select")
    if not select.count():
        raise RuntimeError("No se encontró #modalidad_especifica_select después de seleccionar modalidad_grupo")
    wait_options = lambda: page.wait_for_function(
        """() => Array.from(document.querySelectorAll('#modalidad_especifica_select option'))
        .some(option => String(option.value || '').trim() !== '')""", timeout=timeout)
    if trace:
        trace.step("modalidad_especifica WAIT", wait_options)
    else:
        wait_options()
    options = select.locator("option").evaluate_all(
        "options => options.map(option => ({value: option.value, label: (option.textContent || '').trim()}))"
    )
    real_options = [option for option in options if str(option.get("value") or "").strip()]
    if not real_options:
        raise RuntimeError(
            f"No se cargaron opciones reales para modalidad_especifica después de seleccionar "
            f"modalidad_grupo={group_value!r}. Opciones encontradas: {options!r}"
        )
    selected_value = str(real_options[0]["value"])
    if trace:
        trace.step("modalidad_especifica select", lambda: select.select_option(value=selected_value))
    else:
        select.select_option(value=selected_value)
    if select.input_value() != selected_value:
        raise RuntimeError(
            f"modalidad_especifica no conservó la selección {selected_value!r}; "
            f"valor actual={select.input_value()!r}, opciones={real_options!r}"
        )
    return {"group": group_value, "options": real_options, "selected": selected_value}


def choose_first_real(page: Page, catalog: FormCatalog, name: str, index: int = 0) -> str:
    value = actual_value(catalog, name, index)
    check_value(page, name, value)
    return value


def set_select(page: Page, catalog: FormCatalog, name: str, index: int = 0) -> str:
    value = first_select_value(catalog, name)
    page.locator(f'select[name="{name}"]').select_option(value=value)
    return value


def control_state(page: Page, selector: str) -> dict[str, Any]:
    """Read applicability without changing the page or forcing a control."""
    loc = page.locator(selector).first
    if not loc.count():
        return {"exists": False, "visible": False, "container_visible": False, "enabled": False, "required": False, "real_options": 0}
    return loc.evaluate(
        """el => {
          const container = el.closest('.public-field, .public-home-structure-wrap, [role="group"]') || el.parentElement;
          const label = el.id ? document.querySelector(`label[for="${el.id}"]`) : el.closest('label');
          const visible = !!(node => node && !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length))(el);
          const containerVisible = !!(node => node && !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length))(container);
          const labelVisible = !!(node => node && !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length))(label);
          const realOptions = el.tagName === 'SELECT'
            ? Array.from(el.options).filter(option => String(option.value || '').trim()).length
            : 0;
          return {
            exists: true,
            visible,
            container_visible: containerVisible,
            label_visible: labelVisible,
            enabled: !el.disabled,
            required: !!el.required,
            real_options: realOptions,
          };
        }"""
    )


def fill_conditional_text_if_active(
    page: Page,
    selector: str,
    value: str,
    label: str,
    trace: CaseTrace | None = None,
) -> bool:
    """Fill a dependent text control only when the real UI activates it."""
    state = control_state(page, selector)
    if not state["exists"]:
        if trace:
            trace.skip(label, "not present")
        return False
    if not state["container_visible"] or not state["enabled"]:
        if trace:
            trace.skip(label, "hidden/disabled")
        return False

    def fill_and_verify() -> str:
        fill_if_present(page, selector, value)
        actual = page.locator(selector).first.input_value()
        if actual != value:
            raise RuntimeError(f"{label} no conservó el valor {value!r}; valor actual={actual!r}")
        return actual

    if trace:
        trace.step(label, fill_and_verify)
    else:
        fill_and_verify()
    return True


def select_envejeciente_care_if_active(page: Page, trace: CaseTrace | None = None) -> bool:
    """Select the first real care option exposed by the active UI section."""
    selector = 'input[name="envejeciente_tipo_cuidado"]'
    state = control_state(page, selector)
    if not state["exists"]:
        if trace:
            trace.skip("envejeciente_tipo_cuidado", "not present")
        return False
    if not state["container_visible"] or not state["enabled"]:
        if trace:
            trace.skip("envejeciente_tipo_cuidado", "hidden/disabled")
        return False

    options = page.locator(selector).evaluate_all(
        "nodes => nodes.filter(node => !node.disabled && String(node.value || '').trim()).map(node => node.value)"
    )
    if not options:
        raise RuntimeError("envejeciente_tipo_cuidado visible/enabled pero sin opciones reales")
    selected_value = str(options[0])

    def select_and_verify() -> str:
        check_control(page, "envejeciente_tipo_cuidado", selected_value)
        actual = page.locator(f'{selector}:checked').first.input_value()
        if actual != selected_value:
            raise RuntimeError(
                f"envejeciente_tipo_cuidado no conservó {selected_value!r}; valor actual={actual!r}"
            )
        return actual

    if trace:
        trace.step("envejeciente_tipo_cuidado", select_and_verify)
    else:
        select_and_verify()
    return True


def dismiss_informational_modal_if_present(page: Page, trace: CaseTrace | None = None) -> None:
    """Close only the planchar informational modal; never touch final conditions."""
    modal = page.locator("#funciones_planchar_modal")
    if not modal.count() or not modal.is_visible():
        return

    def close_modal() -> dict[str, str]:
        title = modal.locator("#funciones_planchar_modal_title").inner_text()
        text = modal.locator("#funciones_planchar_modal_text").inner_text()
        print(f"[{trace.case_id if trace else 'E2E'}] modal informativo: {title}: {text[:240]}", flush=True)
        modal.locator("#funciones_planchar_continue").click(timeout=10000)
        page.wait_for_function(
            """() => {
              const modal = document.querySelector('#funciones_planchar_modal');
              const backdrop = document.querySelector('.public-planchar-modal-backdrop:not(.d-none)');
              return !!modal && modal.classList.contains('d-none') && !backdrop;
            }""",
            timeout=10000,
        )
        return {"title": title, "text": text}

    if trace:
        trace.step("modal informativo planchar", close_modal)
    else:
        close_modal()


def fill_valid_form(page: Page, catalog: FormCatalog, case: CaseSpec, client: dict[str, str] | None, trace: CaseTrace | None = None) -> dict[str, Any]:
    suffix = f"{MARKER}-{case.case_id}"
    def fill(label: str, selector: str, value: str) -> None:
        if trace:
            traced_fill(trace, page, label, selector, value)
        else:
            fill_if_present(page, selector, value)

    def action(label: str, operation: Any) -> Any:
        return trace.step(label, operation) if trace else operation()

    # CDN/bootstrap fallback cases test the final conditions modal. They do
    # not need the unrelated planchar flow. If missing CDN CSS leaves that
    # informational control visibly intercepting clicks, close it through its
    # real UI button; never hide or remove the overlay from the DOM.
    if case.block_cdn:
        dismiss_informational_modal_if_present(page, trace)

    if case.client_type == "nuevo":
        fill("nombre", 'input[name="nombre_completo"]', f"Cliente Nuevo {suffix}")
        fill("email", 'input[name="email_contacto"]', f"{RUN_ID.lower()}_{case.case_id.lower()}@example.com")
        fill("telefono", 'input[name="telefono_contacto"]', new_phone_for_case(case))
        fill("ciudad_cliente", 'input[name="ciudad_cliente"]', "Santiago")
        fill("sector_cliente", 'input[name="sector_cliente"]', f"Sector {suffix}")
    fill("ciudad", "#ciudad_input_ui", "Santiago")
    fill("sector", "#sector_input_ui", f"Sector {suffix}")
    fill("ciudad_sector", 'input[name="ciudad_sector"]', f"Santiago / Sector {suffix}")
    fill("rutas_cercanas", 'input[name="rutas_cercanas"]', f"Ruta E2E {case.profile}")
    modalidad_state = {"group": "", "options": [], "selected": ""}
    if locator_exists(page, 'input[name="modalidad_grupo"]'):
        modality = page.locator('input[name="modalidad_grupo"]').first.get_attribute("value")
        if modality:
            modalidad_state = select_dynamic_option(page, modality, trace=trace)
    fill("horarios días", 'input[name="horario_dias_trabajo"]', "Lunes a viernes")
    fill("horarios entrada", 'input[name="horario_hora_entrada"]', "8:00 AM")
    fill("horarios salida", 'input[name="horario_hora_salida"]', "5:00 PM")
    fill("horarios dormida entrada", 'input[name="horario_dormida_entrada"]', "Lunes 8:00 AM")
    fill("horarios dormida salida", 'input[name="horario_dormida_salida"]', "Sábado 12:00 PM")
    selected_age = action("edad", lambda: choose_first_real(page, catalog, "edad_requerida", case.profile))
    selected_age_value = str(selected_age)
    if str(selected_age).strip().lower() == "otro":
        fill("edad específica", 'input[name="edad_otro"]', "30 años")
        selected_age_value = "30 años"
    fill("experiencia", 'textarea[name="experiencia"]', f"Experiencia requerida {suffix}")
    functions = catalog.values.get("funciones") or []
    function_candidates = functions
    if case.case_id in {"E2E-091", "E2E-092", "E2E-093", "E2E-095", "E2E-099"}:
        # These cases validate CDN/bootstrap resilience, not planchar.
        non_planchar = [value for value in functions if "planchar" not in value.lower()]
        if non_planchar:
            function_candidates = non_planchar
    selected_functions = [function_candidates[case.profile % len(function_candidates)]]
    action("funciones", lambda: check_value(page, "funciones", selected_functions[0]))
    dismiss_informational_modal_if_present(page, trace)
    if case.profile % 3 == 0 and len(functions) > 1:
        selected_functions.append(function_candidates[(case.profile + 1) % len(function_candidates)])
        action("funciones adicional", lambda: check_value(page, "funciones", selected_functions[-1]))
        dismiss_informational_modal_if_present(page, trace)
    if "otro" in selected_functions:
        fill_conditional_text_if_active(
            page,
            'input[name="funciones_otro"]',
            "Apoyo adicional",
            "funciones_otro",
            trace,
        )
    else:
        if trace:
            trace.skip("funciones_otro", "funciones=otro no seleccionado")
    if "envejeciente" in selected_functions:
        select_envejeciente_care_if_active(page, trace)
    else:
        if trace:
            trace.skip("envejeciente_tipo_cuidado", "funciones=envejeciente no seleccionado")
    tipo_selector = 'select[name="tipo_lugar"]'
    tipo_state = control_state(page, tipo_selector)
    if not tipo_state["exists"]:
        if trace:
            trace.skip("tipo_lugar", "not present")
    elif not tipo_state["container_visible"] or not tipo_state["enabled"]:
        if trace:
            trace.skip("tipo_lugar", "hidden/disabled")
    elif not tipo_state["real_options"]:
        raise RuntimeError(f"tipo_lugar visible/enabled pero sin opciones reales: {tipo_state!r}")
    else:
        if trace:
            trace.step("tipo_lugar", lambda: set_select(page, catalog, "tipo_lugar", case.profile))
        else:
            set_select(page, catalog, "tipo_lugar", case.profile)
    for name, value in (("habitaciones_selector", "1"), ("banos_selector", "1"), ("pisos_selector", "1")):
        if locator_exists(page, f'input[name="{name}"]'):
            candidates = page.locator(f'input[name="{name}"]').all()
            if candidates:
                chosen = next((x for x in candidates if x.get_attribute("value") == value), candidates[0])
                chosen_value = chosen.get_attribute("value") or value
                state = control_state(page, f'input[name="{name}"][value="{chosen_value}"]')
                if not state["container_visible"] or not state["enabled"]:
                    if trace:
                        trace.skip(name, "hidden/disabled")
                else:
                    action(name, lambda: check_control(page, name, chosen_value))
    adults_state = control_state(page, 'input[name="adultos"]')
    if adults_state["container_visible"] and adults_state["enabled"]:
        fill("adultos", 'input[name="adultos"]', "2")
    elif trace:
        trace.skip("adultos", "hidden/disabled")
    children_state = control_state(page, 'input[name="ninos"]')
    if children_state["container_visible"] and children_state["enabled"]:
        fill("niños", 'input[name="ninos"]', "0")
    elif trace:
        trace.skip("niños", "hidden/disabled")
    fill("edades niños", 'input[name="edades_ninos"]', "")
    fill("mascotas", 'input[name="mascota"]', "")
    fill("salario", 'input[name="sueldo"]', "22000")
    areas = catalog.values.get("areas_comunes") or []
    selected_areas: list[str] = []
    candidate_areas = [areas[case.profile % len(areas)]]
    if case.profile % 4 == 0 and len(areas) > 1:
        candidate_areas.append(areas[(case.profile + 1) % len(areas)])
    for value in candidate_areas:
        area_state = control_state(page, f'input[name="areas_comunes"][value="{value}"]')
        if not area_state["container_visible"] or not area_state["enabled"]:
            if trace:
                trace.skip("áreas comunes", "hidden/disabled")
            continue
        selected_areas.append(value)
        action("áreas comunes", lambda value=value: check_value(page, "areas_comunes", value))
    fill("nota", 'textarea[name="nota_cliente"]', f"Nota de prueba {suffix}")
    terms = '#acepta_politica_nueva' if case.client_type == "nuevo" else '#acepta_politica'
    if locator_exists(page, terms):
        action("términos", lambda: page.locator(terms).check(timeout=10000))
        if not page.locator(terms).is_checked():
            raise RuntimeError(f"El checkbox de términos {terms} no quedó marcado")
    def value_of(selector: str) -> str:
        loc = page.locator(selector).first
        return loc.input_value() if loc.count() else ""

    return {
        "edad_requerida": [selected_age_value],
        "funciones": selected_functions,
        "funciones_otro": value_of('input[name="funciones_otro"]'),
        "areas_comunes": selected_areas,
        "modalidad_trabajo": value_of("#modalidad_trabajo_hidden"),
        "horario": value_of("#horario_hidden"),
        "sueldo": value_of('input[name="sueldo"]'),
        "adultos": value_of('input[name="adultos"]'),
        "ninos": value_of('input[name="ninos"]'),
        "tipo_lugar": value_of('select[name="tipo_lugar"]'),
        "email": value_of('input[name="email_contacto"]'),
        "modalidad": modalidad_state,
    }


def invalidate_form(page: Page, catalog: FormCatalog, invalidation: str, client: dict[str, str] | None) -> dict[str, Any]:
    fill_valid_form(page, catalog, CaseSpec("invalid", "", "nuevo" if client is None else "existente", (1440, 900), True), client)
    selectors = {
        "edad_requerida": 'input[name="edad_requerida"]', "funciones": 'input[name="funciones"]',
        "areas_comunes": 'input[name="areas_comunes"]', "terms": '#acepta_politica_nueva, #acepta_politica',
        "ciudad": '#ciudad_input_ui', "sector": '#sector_input_ui', "telefono": 'input[name="telefono_contacto"]',
        "email": 'input[name="email_contacto"]', "sueldo": 'input[name="sueldo"]', "modalidad": 'input[name="modalidad_grupo"]',
        "modalidad_especifica": '#modalidad_especifica_select',
        "horario": 'input[name="horario_dias_trabajo"], input[name="horario_dormida_entrada"]', "adultos": 'input[name="adultos"]',
        "ninos": 'input[name="ninos"]', "edades_ninos": 'input[name="edades_ninos"]', "tipo_lugar": 'select[name="tipo_lugar"]',
        "habitaciones": 'input[name="habitaciones_selector"]', "banos": 'input[name="banos_selector"]', "pisos": 'input[name="pisos_selector"]',
        "rutas_cercanas": 'input[name="rutas_cercanas"]', "experiencia": 'textarea[name="experiencia"]',
        "sector_real": '#sector_input_ui',
    }
    selector = selectors.get(invalidation)
    if not selector:
        raise RuntimeError(f"Invalidación no soportada: {invalidation}")
    if invalidation == "edades_ninos":
        check_value(page, "funciones", "ninos")
        fill_if_present(page, 'input[name="ninos"]', "2")
        target = page.locator(selector).first
        target.wait_for(state="visible", timeout=2000)
        target.fill("")
        target.dispatch_event("input")
        target.dispatch_event("change")
    elif invalidation == "tipo_lugar":
        if not page.locator('input[name="funciones"][value="limpieza"]').is_checked():
            check_value(page, "funciones", "limpieza")
        target = page.locator(selector).first
        target.wait_for(state="visible", timeout=2000)
        target.select_option(value="")
    elif invalidation in {"edad_requerida", "funciones", "areas_comunes", "terms", "modalidad", "habitaciones", "banos", "pisos"}:
        controls = page.locator(selector)
        for index in range(controls.count()):
            control = controls.nth(index)
            if not control.is_visible() or not control.is_enabled():
                continue
            if control.get_attribute("type") in {"checkbox", "radio"} and not control.is_checked():
                continue
            control_id = control.get_attribute("id")
            label = page.locator(f'label[for="{control_id}"]').first if control_id else page.locator(
                f'label:has({selector})'
            ).first
            if label.count():
                label.click(timeout=10000)
            else:
                control.click(timeout=10000)
    else:
        loc = page.locator(selector)
        target = None
        for index in range(loc.count()):
            candidate = loc.nth(index)
            if candidate.is_visible() and candidate.is_enabled():
                target = candidate
                break
        if target is None:
            raise RuntimeError(f"Invalidación {invalidation} no tiene control visible/enabled aplicable")
        if target.evaluate("el => el.tagName") == "SELECT":
            target.select_option(value="")
        else:
            target.fill("invalid" if invalidation in {"email", "telefono"} else "")
            target.dispatch_event("input")
            target.dispatch_event("change")
    return page.evaluate(
        """(target) => {
          const form = document.querySelector('form');
          const el = document.querySelector(target);
          return {
            target,
            exists: !!el,
            visible: !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
            enabled: !!el && !el.disabled,
            required: !!el && !!el.required,
            value: el && 'value' in el ? String(el.value || '') : '',
            checked: el && 'checked' in el ? !!el.checked : null,
            validation_message: el && 'validationMessage' in el ? String(el.validationMessage || '') : '',
            form_check_validity: !!form && form.checkValidity(),
            invalid_count: document.querySelectorAll(':invalid').length,
            custom_invalid_count: document.querySelectorAll('.is-invalid, .invalid-feedback:not(.d-none)').length,
          };
        }""",
        selector.split(",")[0].strip(),
    )


def url_for_case(base_url: str, token: str, case: CaseSpec, alias_code: str | None = None) -> str:
    if case.wrapper:
        if not alias_code:
            raise RuntimeError("El caso wrapper no tiene alias")
        return f"{base_url}/solicitud/{alias_code}/continuar"
    route = "nueva-publica" if case.client_type == "nuevo" else "publica"
    return f"{base_url}/clientes/solicitudes/{route}/{token}"


def capture_summary(page: Page) -> dict[str, Any]:
    return {"url": page.url, "title": page.title(), "text": re.sub(r"\s+", " ", page.locator("body").inner_text())[:2000]}


def save_failure(page: Page, context: BrowserContext, case: CaseSpec, payload: dict[str, Any], error: str) -> None:
    case_dir = ARTIFACT_DIR / "failures" / case.case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    payload["error"] = error
    payload["page"] = capture_summary(page)
    (case_dir / "case.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    try:
        page.screenshot(path=str(case_dir / "failure.png"), full_page=True)
    except Exception:
        pass
    try:
        context.tracing.stop(path=str(case_dir / "trace.zip"))
    except Exception:
        pass


def choose_real_plan(page: Page, slot: int) -> dict[str, str]:
    cards = page.locator('input[name="tipo_plan"]')
    if not cards.count():
        cards = page.locator('button:has-text("Elegir"), input[type="submit"]')
    if not cards.count():
        raise RuntimeError("La pantalla de planes no expone opciones reales")
    index = slot % cards.count()
    selected = cards.nth(index)
    plan_code = selected.get_attribute("value") or selected.get_attribute("data-plan") or str(index)
    card_text = selected.evaluate("e => e.closest('label')?.innerText || ''")
    selected.check(force=True) if selected.get_attribute("type") in {"radio", "checkbox"} else selected.click()
    submit = page.locator('button[type="submit"], input[type="submit"]').last
    if submit.count():
        submit.click()
    else:
        page.keyboard.press("Enter")
    return {"code": plan_code, "label": card_text, "card_text": card_text}


def verify_saved_data(flask_app: Any, case: CaseSpec, client: dict[str, str] | None, selected: dict[str, Any], plan: dict[str, str]) -> list[str]:
    """Compare the browser-selected values with the row persisted by the POST."""
    from models import Cliente, Solicitud

    mismatches: list[str] = []
    with flask_app.app_context():
        if case.client_type == "nuevo":
            email = str(selected.get("email") or "")
            cliente = Cliente.query.filter_by(email=email).order_by(Cliente.id.desc()).first()
        else:
            cliente = Cliente.query.get(int((client or {}).get("id") or 0))
        if cliente is None:
            return ["No se encontró el cliente persistido para comparar datos"]
        solicitud = Solicitud.query.filter_by(cliente_id=int(cliente.id)).order_by(Solicitud.id.desc()).first()
        if solicitud is None:
            return ["No se encontró la solicitud persistida para comparar datos"]

        for field in ("edad_requerida", "funciones", "areas_comunes"):
            expected = sorted(str(x) for x in (selected.get(field) or []))
            if field == "funciones" and "otro" in expected:
                custom_function = str(selected.get("funciones_otro") or "").strip()
                if custom_function:
                    expected = sorted(custom_function if value == "otro" else value for value in expected)
            actual = sorted(str(x) for x in (getattr(solicitud, field, None) or []))
            if expected != actual:
                mismatches.append(f"{field}: UI={expected!r}, DB={actual!r}")
        scalar_fields = ("modalidad_trabajo", "horario", "sueldo", "adultos", "ninos", "tipo_lugar")
        for field in scalar_fields:
            expected = str(selected.get(field) or "").strip()
            actual = str(getattr(solicitud, field, "") or "").strip()
            if field == "sueldo":
                expected = expected.replace(",", "")
                actual = actual.replace(",", "")
            if field == "ninos" and expected == "0" and actual == "":
                continue
            if expected and actual != expected:
                mismatches.append(f"{field}: UI={expected!r}, DB={actual!r}")
        plan_code = str(plan.get("code") or "").strip().lower()
        saved_plan = str(getattr(solicitud, "tipo_plan", "") or "").strip().lower()
        if plan_code and not plan_code.isdigit() and saved_plan != plan_code:
            mismatches.append(f"tipo_plan: UI={plan_code!r}, DB={saved_plan!r}")
    return mismatches


def run_case(browser: Browser, flask_app: Any, base_url: str, catalog: FormCatalog, case: CaseSpec, client: dict[str, str] | None, token: str, alias_code: str | None) -> dict[str, Any]:
    started = time.monotonic()
    started_at = utc_now()
    context = browser.new_context(viewport={"width": case.viewport[0], "height": case.viewport[1]})
    page = context.new_page()
    requests_log: list[dict[str, Any]] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    http_errors: list[dict[str, Any]] = []
    dialogs: list[str] = []
    post_count = 0
    event_seq = 0
    dialog_shown_seq: int | None = None
    dialog_accepted_seq: int | None = None
    post_events: list[dict[str, Any]] = []
    dialog_accept_next = {"value": case.interaction != "native_cancel"}
    case_dir = ARTIFACT_DIR / "failures" / case.case_id
    trace = CaseTrace(case.case_id, case_dir)
    page.set_default_timeout(10000)
    page.set_default_navigation_timeout(15000)

    def on_request(req: Any) -> None:
        nonlocal post_count, event_seq
        if req.method == "POST":
            event_seq += 1
            request_started_at = time.monotonic()
            requests_log.append({"method": req.method, "url": req.url})
            post_events.append({"seq": event_seq, "started_at": request_started_at, "url": req.url})
            if "/plan" not in urllib.parse.urlsplit(req.url).path:
                post_count += 1

    def on_console(msg: Any) -> None:
        if msg.type == "error":
            console_errors.append(msg.text)

    def on_page_error(error: Any) -> None:
        page_errors.append(str(error))

    def on_response(response: Any) -> None:
        if response.status >= 400:
            http_errors.append({"status": response.status, "url": response.url})

    def on_dialog(dialog: Any) -> None:
        nonlocal event_seq, dialog_shown_seq, dialog_accepted_seq
        event_seq += 1
        dialog_shown_seq = event_seq
        dialogs.append(dialog.message)
        accept = dialog_accept_next["value"] and case.interaction not in {"cancel", "close_x"}
        if accept:
            dialog.accept()
            event_seq += 1
            dialog_accepted_seq = event_seq
        else:
            dialog.dismiss()

    page.on("request", on_request)
    page.on("console", on_console)
    page.on("pageerror", on_page_error)
    page.on("response", on_response)
    page.on("dialog", on_dialog)
    if case.block_cdn:
        # The resilience target is the Bootstrap JS fallback. Keep CDN CSS
        # available so hidden informational markup remains visually hidden;
        # aborting styles creates an unrelated overlay before the test starts.
        page.route(
            "**://cdn.jsdelivr.net/**",
            lambda route: route.abort() if route.request.resource_type == "script" else route.continue_(),
        )
    if case.block_local_bootstrap:
        page.route("**/static/vendor/bootstrap/bootstrap.bundle.min.js", lambda route: route.abort())
    payload: dict[str, Any] = {"case": asdict(case), "requests": requests_log, "dialogs": dialogs}
    try:
        trace.step("formulario", lambda: context.tracing.start(screenshots=False, snapshots=False, sources=False))
        response = trace.step("GET formulario", lambda: page.goto(url_for_case(base_url, token, case, alias_code), wait_until="domcontentloaded", timeout=15000))
        if response is None or response.status >= 400:
            raise RuntimeError(f"GET inicial inesperado: {None if response is None else response.status}")
        trace.step("formulario disponible", lambda: page.wait_for_selector("form", timeout=10000))
        if case.valid:
            payload["selected"] = fill_valid_form(page, catalog, case, client, trace)
        else:
            payload["invalidation"] = invalidate_form(page, catalog, case.invalidation, client)
        submit_selector = "#publicSubmitNuevaBtn" if case.client_type == "nuevo" else "#publicSubmitBtn"
        submit = page.locator(submit_selector)
        if not submit.count():
            submit = page.locator('button[type="submit"]').last
        if not case.valid:
            invalidation_state = payload.get("invalidation") or {}
            negative_pre_submit = page.evaluate(
                """(targetSelector) => {
                  const form = document.querySelector('form');
                  const target = document.querySelector(targetSelector);
                  const submit = form && form.querySelector('button[type="submit"], input[type="submit"]');
                  return {
                    form_check_validity: !!form && form.checkValidity(),
                    submit_disabled: !!submit && !!submit.disabled,
                    validation_message: target && 'validationMessage' in target ? String(target.validationMessage || '') : '',
                    target_checked: target && 'checked' in target ? !!target.checked : null,
                    modal_visible: !!document.querySelector('#publicEmploymentConditionsModal, #publicEmploymentConditionsNuevaModal') &&
                      !!Array.from(document.querySelectorAll('#publicEmploymentConditionsModal, #publicEmploymentConditionsNuevaModal')).find(el => {
                        const style = window.getComputedStyle(el);
                        return style.display !== 'none' && style.visibility !== 'hidden';
                      }),
                  };
                }""",
                str(invalidation_state.get("target") or "form"),
            )
            payload["negative_pre_submit"] = negative_pre_submit
            payload["negative_fixture_valid"] = bool(
                not negative_pre_submit.get("form_check_validity")
                or negative_pre_submit.get("validation_message")
                or invalidation_state.get("custom_invalid_count", 0) > 0
            )
            if not payload["negative_fixture_valid"]:
                raise RuntimeError("Negative test fixture is not actually invalid")
            if negative_pre_submit.get("submit_disabled"):
                trace.skip("submit", "NEGATIVE PASS: submit disabled by valid client-side constraint")
            else:
                trace.step("submit", submit.click)
                trace.step("espera modal", lambda: page.wait_for_timeout(350))
        else:
            trace.step("submit", submit.click)
            trace.step("espera modal", lambda: page.wait_for_timeout(350))


        modal_visible = page.get_by_text(MODAL_TITLE, exact=True).count() > 0 and page.get_by_text(MODAL_TITLE, exact=True).first.is_visible()
        native_dialog = bool(dialogs)
        native_post_before_confirmation = bool(
            native_dialog
            and dialog_accepted_seq is not None
            and any(event["seq"] < dialog_accepted_seq for event in post_events if "/plan" not in urllib.parse.urlsplit(event["url"]).path)
        )
        post_count_before_confirmation = 1 if native_post_before_confirmation else 0
        payload.update({
            "modal_visible": modal_visible,
            "native_dialog": native_dialog,
            "post_count_before_confirmation": post_count_before_confirmation,
            "event_order": {
                "dialog_shown_seq": dialog_shown_seq,
                "dialog_accepted_seq": dialog_accepted_seq,
                "post_request_seq": [event["seq"] for event in post_events if "/plan" not in urllib.parse.urlsplit(event["url"]).path],
            },
        })
        if not case.valid:
            if modal_visible or native_dialog or post_count != 0:
                raise RuntimeError("Caso inválido abrió confirmación o hizo POST")
            if not page.locator(":invalid").count() and not page.locator(".is-invalid, .invalid-feedback").count():
                raise RuntimeError("Caso inválido no dejó indicación de validación")
            trace.step("tracing stop", context.tracing.stop)
            return {"ok": True, "result": "PASS", "started_at": started_at, "duration_ms": round((time.monotonic() - started) * 1000), **payload, "post_count": post_count, "final_url": page.url, "trace_events": trace.events, "console_errors": console_errors, "page_errors": page_errors, "http_errors": http_errors}

        if not modal_visible and not native_dialog:
            raise RuntimeError("Caso válido no mostró modal ni confirmación nativa")
        if (native_dialog and native_post_before_confirmation) or (not native_dialog and post_count != 0):
            raise RuntimeError("POST ocurrió antes de aceptar las condiciones")
        if modal_visible and MODAL_TITLE not in page.locator("body").inner_text():
            raise RuntimeError("Título del modal no coincide")
        if case.interaction in {"cancel", "close_x"}:
            if case.interaction == "cancel":
                trace.step("modal cancelar", lambda: page.get_by_role("button", name="Volver al formulario", exact=True).click())
            else:
                trace.step("modal cerrar", lambda: page.locator('[data-bs-dismiss="modal"]').first.click())
            trace.step("espera cierre modal", lambda: page.wait_for_timeout(200))
            if post_count != 0:
                raise RuntimeError("Cancelar/cerrar el modal produjo POST")
            trace.step("submit segundo intento", submit.click)
            trace.step("espera reapertura modal", lambda: page.wait_for_timeout(250))
            if not page.get_by_text(MODAL_TITLE, exact=True).first.is_visible():
                raise RuntimeError("El segundo intento no reabrió el modal")
            trace.step("modal confirmar", lambda: page.get_by_role("button", name=CONFIRM_LABEL, exact=True).click())
        elif case.interaction == "reopen":
            trace.step("modal volver formulario", lambda: page.get_by_role("button", name="Volver al formulario", exact=True).click())
            trace.step("submit reapertura", submit.click)
            trace.step("modal confirmar", lambda: page.get_by_role("button", name=CONFIRM_LABEL, exact=True).click())
        elif case.interaction in {"double_click", "rapid_click"}:
            trace.step("modal doble confirmación", lambda: page.get_by_role("button", name=CONFIRM_LABEL, exact=True).dblclick())
        elif case.interaction == "native_cancel":
            if post_count != 0:
                raise RuntimeError("Confirmación nativa cancelada pero hubo POST")
            dialog_accept_next["value"] = True
            trace.step("submit confirmación nativa", submit.click)
        elif modal_visible:
            trace.step("modal confirmar", lambda: page.get_by_role("button", name=CONFIRM_LABEL, exact=True).click())
        trace.step("espera POST", lambda: page.wait_for_timeout(500))
        if post_count != 1:
            raise RuntimeError(f"Se esperaba exactamente 1 POST tras confirmar; observado={post_count}")
        trace.step("navegación a plan", lambda: page.wait_for_url("**/plan", timeout=15000))
        if "/plan/resumen" in page.url:
            raise RuntimeError("El flujo saltó el selector de plan")
        plan = trace.step("selección de plan", lambda: choose_real_plan(page, case.expected_plan_slot))
        trace.step("navegación a resumen", lambda: page.wait_for_url("**/plan/resumen", timeout=15000))
        if "Solicitud recibida correctamente" not in page.locator("body").inner_text():
            raise RuntimeError("No apareció la pantalla final de solicitud recibida")
        summary_text = page.locator("body").inner_text()
        if plan.get("card_text") and not any(token in summary_text for token in plan["card_text"].split()[:3]):
            raise RuntimeError("El resumen no conserva la información visible del plan elegido")
        if "50% requerido" not in summary_text or "Saldo restante" not in summary_text:
            raise RuntimeError("El resumen no muestra abono y saldo del plan")
        mismatches = verify_saved_data(flask_app, case, client, payload.get("selected") or {}, plan)
        payload["db_mismatches"] = mismatches
        if mismatches:
            raise RuntimeError("Diferencias UI vs PostgreSQL: " + "; ".join(mismatches))
        payload["plan"] = plan
        trace.step("tracing stop", context.tracing.stop)
        return {"ok": True, "result": "PASS", "started_at": started_at, "duration_ms": round((time.monotonic() - started) * 1000), **payload, "post_count": post_count, "final_url": page.url, "trace_events": trace.events, "console_errors": console_errors, "page_errors": page_errors, "http_errors": http_errors}
    except Exception as exc:
        payload["last_step"] = trace.last_step
        payload["trace_events"] = trace.events
        save_failure(page, context, case, payload, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        return {"ok": False, "result": "FAIL", "started_at": started_at, "duration_ms": round((time.monotonic() - started) * 1000), **payload, "error": str(exc), "console_errors": console_errors, "page_errors": page_errors, "http_errors": http_errors, "post_count": post_count}
    finally:
        trace.close()
        try:
            context.close()
        except Exception:
            pass


def seed_existing_clients(flask_app: Any, run_id: str, count: int = 30) -> list[dict[str, str]]:
    from config_app import db
    from models import Cliente
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    clients: list[dict[str, str]] = []
    for i in range(count):
        email = f"{run_id.lower()}_existing_{i:03d}@example.com"
        client = Cliente(
            codigo=f"E2E{run_id[-8:]}{i:02d}"[:20],
            nombre_completo=f"{MARKER} EXISTENTE {i:03d}",
            email=email,
            telefono=existing_phone_for_index(i),
            ciudad="Santiago",
            sector=f"Sector {MARKER} {i:03d}",
            role="cliente",
            is_active=True,
            created_at=now,
            updated_at=now,
            fecha_registro=now,
            total_solicitudes=0,
        )
        db.session.add(client)
        clients.append({"id": "", "codigo": client.codigo, "email": email, "name": client.nombre_completo})
    db.session.commit()
    for item in clients:
        row = Cliente.query.filter_by(email=item["email"]).first()
        item["id"] = str(row.id)
    return clients


def cleanup(flask_app: Any) -> dict[str, Any]:
    from config_app import db
    from models import Cliente, PublicSolicitudClienteNuevoTokenUso, PublicSolicitudShareAlias, PublicSolicitudTokenUso, Solicitud
    report: dict[str, Any] = {"run_id": RUN_ID, "created": {}, "deleted": {}, "residuals": {}}
    with flask_app.app_context():
        clients = Cliente.query.filter(Cliente.email.like(f"{RUN_ID.lower()}_%@example.com")).all()
        client_ids = [int(c.id) for c in clients]
        requests_rows = Solicitud.query.filter(Solicitud.cliente_id.in_(client_ids)).all() if client_ids else []
        request_ids = [int(s.id) for s in requests_rows]
        new_tokens = PublicSolicitudClienteNuevoTokenUso.query.filter(PublicSolicitudClienteNuevoTokenUso.cliente_id.in_(client_ids)).all() if client_ids else []
        old_tokens = PublicSolicitudTokenUso.query.filter(PublicSolicitudTokenUso.cliente_id.in_(client_ids)).all() if client_ids else []
        aliases = PublicSolicitudShareAlias.query.filter(PublicSolicitudShareAlias.created_by == MARKER).all()
        report["created"] = {"clientes": len(clients), "solicitudes": len(requests_rows), "new_tokens": len(new_tokens), "existing_tokens": len(old_tokens), "aliases": len(aliases)}
        for row in new_tokens + old_tokens:
            db.session.delete(row)
        for row in requests_rows:
            db.session.delete(row)
        for row in aliases:
            db.session.delete(row)
        for row in clients:
            db.session.delete(row)
        db.session.commit()
        residual_clients = Cliente.query.filter(Cliente.email.like(f"{RUN_ID.lower()}_%@example.com")).count()
        residual_requests = Solicitud.query.filter(Solicitud.cliente_id.in_(client_ids)).count() if client_ids else 0
        report["deleted"] = report["created"].copy()
        report["residuals"] = {"clientes": int(residual_clients), "solicitudes": int(residual_requests)}
        report["ok"] = not any(report["residuals"].values())
    (ARTIFACT_DIR / "cleanup_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def prepare_case_jobs(flask_app: Any, cases: list[CaseSpec], existing_clients: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Create unique signed tokens/aliases before workers start.

    This phase is intentionally serial. It is the only phase that prepares
    shared DB-backed fixtures; browser cases themselves do not share tokens.
    """
    from clientes.routes import generar_token_publico_cliente, generar_token_publico_cliente_nuevo, create_public_share_alias
    from config_app import db
    from models import Cliente

    jobs: list[dict[str, Any]] = []
    existing_index = 0
    with flask_app.app_context():
        for case in cases:
            client = None
            if case.client_type == "existente":
                if existing_index >= len(existing_clients):
                    raise RuntimeError(
                        f"No hay fixture de cliente existente case-scoped para {case.case_id}; "
                        f"necesarios={existing_index + 1}, disponibles={len(existing_clients)}"
                    )
                client = existing_clients[existing_index]
                existing_index += 1
                client_row = Cliente.query.get(int(client["id"]))
                token = generar_token_publico_cliente(client_row)
            else:
                token = generar_token_publico_cliente_nuevo(created_by=MARKER)
            alias_code = None
            if case.wrapper:
                alias = create_public_share_alias(
                    token=token,
                    link_type="nuevo" if case.client_type == "nuevo" else "existente",
                    created_by=MARKER,
                )
                db.session.commit()
                alias_code = alias.code
            # Tokens, aliases and links are one-shot; every case owns its resource.
            resource_key = f"caso:{case.case_id}"
            jobs.append({"case": case, "client": client, "token": token, "alias_code": alias_code, "resource_key": resource_key})
    if existing_index != len(existing_clients):
        raise RuntimeError(
            f"Fixtures existentes no consumidos exactamente: usados={existing_index}, disponibles={len(existing_clients)}"
        )
    return jobs


def validate_prepared_jobs(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """Reject shared one-shot resources before launching browser workers."""
    def duplicates(values: list[str]) -> list[str]:
        return sorted(value for value, count in Counter(values).items() if count > 1)

    resource_keys = [str(job.get("resource_key") or "") for job in jobs]
    tokens = [str(job.get("token") or "") for job in jobs]
    aliases = [str(job.get("alias_code") or "") for job in jobs if job.get("alias_code")]
    diagnostics = {
        "job_count": len(jobs),
        "resource_key_duplicates": duplicates(resource_keys),
        "token_duplicates": duplicates(tokens),
        "alias_duplicates": duplicates(aliases),
        "missing_tokens": [job["case"].case_id for job in jobs if not job.get("token")],
        "missing_case_resources": [job["case"].case_id for job in jobs if not job.get("resource_key")],
    }
    (ARTIFACT_DIR / "prepared_job_validation.json").write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("E2E PREPARED JOB VALIDATION: " + json.dumps(diagnostics, ensure_ascii=False, sort_keys=True))
    if any(diagnostics[key] for key in diagnostics if key != "job_count"):
        raise RuntimeError("Resources consumibles E2E compartidos o incompletos: " + json.dumps(diagnostics, ensure_ascii=False, sort_keys=True))
    return diagnostics


def run_worker_group(group: list[dict[str, Any]], flask_app: Any, base_url: str, catalog: FormCatalog, slow_mo: int, headed: bool) -> list[dict[str, Any]]:
    """One Playwright runtime/browser per worker; cases in a resource group are serial."""
    results: list[dict[str, Any]] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not headed, slow_mo=slow_mo, args=["--no-sandbox"])
            try:
                for job in group:
                    results.append(run_case(browser, flask_app, base_url, catalog, job["case"], job["client"], job["token"], job["alias_code"]))
            finally:
                browser.close()
    except Exception as exc:
        for job in group:
            results.append({
                "ok": False,
                "result": "FAIL",
                "case": asdict(job["case"]),
                "resource_key": job["resource_key"],
                "error": f"Worker/browser abortado: {exc}",
                "traceback": traceback.format_exc(),
            })
    return results


def execute_jobs(jobs: list[dict[str, Any]], flask_app: Any, base_url: str, catalog: FormCatalog, workers: int, slow_mo: int, headed: bool) -> tuple[list[dict[str, Any]], float]:
    """Run independent resource groups concurrently, preserving per-resource order."""
    groups_by_resource: dict[str, list[dict[str, Any]]] = {}
    for job in jobs:
        groups_by_resource.setdefault(job["resource_key"], []).append(job)
    groups = list(groups_by_resource.values())
    worker_count = max(1, min(int(workers), len(groups) or 1))
    started = time.monotonic()
    results: list[dict[str, Any]] = []
    if worker_count == 1:
        # Keep the smoke test in the main thread so the real traceback is visible.
        for group in groups:
            results.extend(run_worker_group(group, flask_app, base_url, catalog, slow_mo, headed))
        results.sort(key=lambda item: str((item.get("case") or {}).get("case_id") or ""))
        return results, time.monotonic() - started
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="public-intake-e2e") as pool:
        futures = [pool.submit(run_worker_group, group, flask_app, base_url, catalog, slow_mo, headed) for group in groups]
        for future in as_completed(futures):
            results.extend(future.result())
    results.sort(key=lambda item: str((item.get("case") or {}).get("case_id") or ""))
    elapsed = time.monotonic() - started
    return results, elapsed


def write_reports(cases: list[dict[str, Any]], db_url: str, base_url: str, cleanup_report: dict[str, Any] | None, *, elapsed_seconds: float, workers: int) -> None:
    passed = sum(1 for c in cases if c.get("ok"))
    failed = len(cases) - passed
    critical = sum(1 for c in cases if c.get("post_count", c.get("post_count_before_confirmation", 0)) > 1)
    total = len(cases)
    case_elapsed = sorted(float(c.get("duration_ms", 0)) / 1000 for c in cases)

    def percentile(values: list[float], percentile_value: float) -> float:
        if not values:
            return 0.0
        if len(values) == 1:
            return values[0]
        rank = (len(values) - 1) * percentile_value
        lower = int(rank)
        upper = min(lower + 1, len(values) - 1)
        fraction = rank - lower
        return values[lower] + (values[upper] - values[lower]) * fraction

    fastest = min(cases, key=lambda c: c.get("duration_ms", 0), default={})
    slowest = max(cases, key=lambda c: c.get("duration_ms", 0), default={})
    case_timing = {
        "average_seconds_per_case": round(sum(case_elapsed) / len(case_elapsed), 3) if case_elapsed else 0,
        "p50_seconds_per_case": round(percentile(case_elapsed, 0.50), 3),
        "p95_seconds_per_case": round(percentile(case_elapsed, 0.95), 3),
        "fastest": {
            "case_id": (fastest.get("case") or {}).get("case_id"),
            "seconds": round(float(fastest.get("duration_ms", 0)) / 1000, 3),
        },
        "slowest": {
            "case_id": (slowest.get("case") or {}).get("case_id"),
            "seconds": round(float(slowest.get("duration_ms", 0)) / 1000, 3),
        },
    }
    report = {
        "run_id": RUN_ID, "started_at": cases[0].get("started_at") if cases else utc_now(), "finished_at": utc_now(),
        "base_url": base_url, "database": redact_db_url(db_url), "total": total, "pass": passed, "fail": failed,
        "skipped": 100 - total, "severity": {"critical": critical, "high": failed - critical, "medium": 0, "low": 0},
        "timing": {
            "total_seconds": round(elapsed_seconds, 3),
            "average_seconds_per_case": case_timing["average_seconds_per_case"],
            "wall_clock_seconds": round(elapsed_seconds, 3),
            "case_elapsed_average_seconds": case_timing["average_seconds_per_case"],
            "case_elapsed_p50_seconds": case_timing["p50_seconds_per_case"],
            "case_elapsed_p95_seconds": case_timing["p95_seconds_per_case"],
            "fastest_case": case_timing["fastest"],
            "slowest_case": case_timing["slowest"],
            "cases_per_minute": round(total / (elapsed_seconds / 60), 2) if elapsed_seconds > 0 else 0,
            "workers": workers,
        },
        "go_no_go": "GO" if len(cases) == 100 and failed == 0 and not critical and cleanup_report and cleanup_report.get("ok") else "NO-GO",
        "cases": cases, "cleanup": cleanup_report,
    }
    (ARTIFACT_DIR / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (ARTIFACT_DIR / "cases.json").write_text(json.dumps([c.get("case") for c in cases], ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"# Public intake extreme E2E {RUN_ID}", "", f"TOTAL: {total}", f"PASS: {passed}",
        f"FAIL: {failed}", f"SKIPPED: {100-total}", f"GO / NO-GO: {report['go_no_go']}",
        f"WALL CLOCK SECONDS: {report['timing']['wall_clock_seconds']:.3f}",
        f"AVERAGE CASE ELAPSED SECONDS: {report['timing']['case_elapsed_average_seconds']:.3f}",
        f"P50 CASE ELAPSED SECONDS: {report['timing']['case_elapsed_p50_seconds']:.3f}",
        f"P95 CASE ELAPSED SECONDS: {report['timing']['case_elapsed_p95_seconds']:.3f}",
        f"FASTEST CASE: {report['timing']['fastest_case']['case_id']} ({report['timing']['fastest_case']['seconds']:.3f}s)",
        f"SLOWEST CASE: {report['timing']['slowest_case']['case_id']} ({report['timing']['slowest_case']['seconds']:.3f}s)",
        f"CASES/MINUTE: {report['timing']['cases_per_minute']:.2f}", f"WORKERS: {workers}", "",
    ]
    (ARTIFACT_DIR / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    if args.workers < 1:
        raise RuntimeError("--workers debe ser >= 1")
    base_url = (args.base_url or BASE_URL_DEFAULT).rstrip("/")
    if args.server_mode == "external":
        base_url = validate_external_base_url(base_url)
    db_url, flask_app = safe_database_check()
    all_cases = build_matrix()
    cases_by_id = {case.case_id: case for case in all_cases}
    if args.case_ids:
        requested_ids = [value.strip() for value in args.case_ids.split(",") if value.strip()]
        unknown_ids = [case_id for case_id in requested_ids if case_id not in cases_by_id]
        if unknown_ids:
            raise RuntimeError(f"--case-ids contiene IDs desconocidos: {unknown_ids!r}")
        if len(set(requested_ids)) != len(requested_ids):
            raise RuntimeError("--case-ids contiene IDs duplicados")
        cases = [cases_by_id[case_id] for case_id in requested_ids]
    elif args.limit < 1 or args.limit > len(all_cases):
        raise RuntimeError(f"--limit debe estar entre 1 y {len(all_cases)}")
    else:
        cases = all_cases[:args.limit]
    existing_case_count = sum(1 for case in cases if case.client_type == "existente")
    validate_fixture_matrix(cases, existing_count=existing_case_count)
    server = None
    thread = None
    if args.server_mode == "internal":
        flask_app.config.update(
            TESTING=False,
            WTF_CSRF_ENABLED=False,
            SALARY_SUGGESTION_ENABLED=False,
            ADMIN_LIVE_SSE_ENABLED=False,
            CLIENTES_LIVE_SSE_ENABLED=False,
        )
    suite_started = time.monotonic()
    (ARTIFACT_DIR / "cases.json").write_text(json.dumps([asdict(c) for c in cases], ensure_ascii=False, indent=2), encoding="utf-8")
    existing_clients: list[dict[str, str]] = []
    results: list[dict[str, Any]] = []
    cleanup_report: dict[str, Any] | None = None
    elapsed_seconds = 0.0
    try:
        if args.server_mode == "internal":
            server, thread, base_url = start_internal_server(flask_app)
        if existing_case_count:
            with flask_app.app_context():
                existing_clients = seed_existing_clients(flask_app, RUN_ID, existing_case_count)
        jobs = prepare_case_jobs(flask_app, cases, existing_clients)
        validate_prepared_jobs(jobs)
        from clientes.routes import generar_token_publico_cliente_nuevo
        with sync_playwright() as playwright:
            # One short-lived probe browser discovers the real form values;
            # workers then own and reuse one browser each.
            probe_browser = playwright.chromium.launch(headless=not args.headed, slow_mo=args.slow_mo, args=["--no-sandbox"])
            try:
                with flask_app.app_context():
                    probe_token = generar_token_publico_cliente_nuevo(created_by=f"{MARKER}-PROBE")
                probe_context = probe_browser.new_context(viewport={"width": 1440, "height": 900})
                probe = probe_context.new_page()
                probe.goto(f"{base_url}/clientes/solicitudes/nueva-publica/{probe_token}", wait_until="domcontentloaded", timeout=30000)
                catalog = discover_catalog(probe)
                probe_context.close()
            finally:
                probe_browser.close()
        results, _case_elapsed = execute_jobs(jobs, flask_app, base_url, catalog, args.workers, args.slow_mo, args.headed)
    except Exception as exc:
        results.append({"ok": False, "result": "FAIL", "error": f"Suite abortada antes de completar casos: {exc}", "traceback": traceback.format_exc()})
    finally:
        elapsed_seconds = time.monotonic() - suite_started
        try:
            cleanup_report = cleanup(flask_app)
        except Exception as exc:
            cleanup_report = {"ok": False, "error": str(exc), "traceback": traceback.format_exc()}
            (ARTIFACT_DIR / "cleanup_report.json").write_text(json.dumps(cleanup_report, ensure_ascii=False, indent=2), encoding="utf-8")
        write_reports(results, db_url, base_url, cleanup_report, elapsed_seconds=elapsed_seconds, workers=args.workers)
        if server is not None:
            try:
                server.shutdown()
            finally:
                if thread is not None:
                    thread.join(timeout=3)
    print(f"Artifacts: {ARTIFACT_DIR}")
    return 0 if len(results) == len(cases) and all(r.get("ok") for r in results) and cleanup_report and cleanup_report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
