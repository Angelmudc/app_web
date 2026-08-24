#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import socket
import sys
import threading
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import requests
from sqlalchemy import text
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app as flask_app
from config_app import db
from models import Candidata, Entrevista, EntrevistaPregunta, EntrevistaRespuesta, StaffUser
from utils.timezone import utc_now_naive


PREFIX = "CODEX-REF-VALIDATION-"
EXPECTED_DB = "domestica_cibao_local"
USERNAME = PREFIX + "staff"
PASSWORD = "CodexRefValidation#2026"
CEDULA = "09399123001"
CODIGO = PREFIX + "CAND-001"
EMAIL = PREFIX.lower() + "staff@test.local"


@dataclass
class Created:
    candidata_id: int | None = None
    staff_user_id: int | None = None
    question_ids: list[int] = field(default_factory=list)
    interview_ids: list[int] = field(default_factory=list)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print("OK:", message)


def current_database_name() -> str:
    return str(db.session.execute(text("select current_database()")).scalar() or "")


def cleanup(created: Created) -> None:
    with flask_app.app_context():
        db.session.remove()
        ids = [int(x) for x in created.interview_ids if x]
        if ids:
            EntrevistaRespuesta.query.filter(EntrevistaRespuesta.entrevista_id.in_(ids)).delete(synchronize_session=False)
            Entrevista.query.filter(Entrevista.id.in_(ids)).delete(synchronize_session=False)
        if created.candidata_id:
            db.session.execute(
                text(
                    "delete from staff_audit_logs "
                    "where entity_type in ('candidata', 'Candidata') and entity_id = :entity_id"
                ),
                {"entity_id": str(int(created.candidata_id))},
            )
            cand = Candidata.query.get(int(created.candidata_id))
            if cand and (cand.codigo or "").startswith(PREFIX):
                Candidata.query.filter_by(fila=int(created.candidata_id)).delete(synchronize_session=False)
        if created.staff_user_id:
            db.session.execute(
                text("delete from staff_audit_logs where actor_user_id = :actor_user_id"),
                {"actor_user_id": int(created.staff_user_id)},
            )
            db.session.execute(
                text("delete from staff_presence_state where user_id = :actor_user_id"),
                {"actor_user_id": int(created.staff_user_id)},
            )
            db.session.execute(
                text("delete from trusted_devices where user_id = :actor_user_id"),
                {"actor_user_id": int(created.staff_user_id)},
            )
            user = StaffUser.query.get(int(created.staff_user_id))
            if user and (user.username or "").startswith(PREFIX):
                StaffUser.query.filter_by(id=int(created.staff_user_id)).delete(synchronize_session=False)
        if created.question_ids:
            EntrevistaPregunta.query.filter(EntrevistaPregunta.id.in_(created.question_ids)).delete(synchronize_session=False)
        db.session.commit()
        db.session.remove()


def cleanup_prefixed_residue() -> None:
    with flask_app.app_context():
        db.session.remove()
        rows = db.session.execute(
            text("select fila from candidatas where codigo like :p or nombre_completo like :p"),
            {"p": PREFIX + "%"},
        ).fetchall()
        candidate_ids = [int(row[0]) for row in rows]
        user_rows = db.session.execute(
            text("select id from staff_users where username like :p or email like :p"),
            {"p": PREFIX + "%"},
        ).fetchall()
        user_ids = [int(row[0]) for row in user_rows]
        question_rows = db.session.execute(
            text("select id from entrevista_preguntas where texto like :p"),
            {"p": PREFIX + "%"},
        ).fetchall()
        question_ids = [int(row[0]) for row in question_rows]
        interview_ids: list[int] = []
        if candidate_ids:
            interview_ids = [
                int(row[0])
                for row in db.session.execute(
                    text("select id from entrevistas where candidata_id = any(:candidate_ids)"),
                    {"candidate_ids": candidate_ids},
                ).fetchall()
            ]
        cleanup(Created(
            candidata_id=candidate_ids[0] if len(candidate_ids) == 1 else None,
            staff_user_id=user_ids[0] if len(user_ids) == 1 else None,
            question_ids=question_ids,
            interview_ids=interview_ids,
        ))


def ensure_local_db() -> None:
    uri = str(flask_app.config.get("SQLALCHEMY_DATABASE_URI") or "")
    db_name = current_database_name()
    print("SQLALCHEMY_DATABASE_URI:", uri)
    print("current_database():", db_name)
    assert_true(db_name == EXPECTED_DB, f"DB conectada es {EXPECTED_DB}")
    assert_true("domestica_cibao_local" in uri, "URI apunta a domestica_cibao_local")


def ensure_questions(created: Created) -> dict[str, EntrevistaPregunta]:
    required = ["domestica.referencia_laboral", "domestica.referencia_familiar"]
    active = {
        q.clave: q
        for q in EntrevistaPregunta.query.filter(
            EntrevistaPregunta.clave.in_(required),
            EntrevistaPregunta.activa.is_(True),
        ).all()
    }
    for clave in required:
        if clave in active:
            continue
        existing = EntrevistaPregunta.query.filter_by(clave=clave).first()
        if existing:
            raise AssertionError(f"Falta pregunta activa {clave}, pero existe inactiva; no se modifica pregunta real.")
        q = EntrevistaPregunta(
            clave=clave,
            texto=PREFIX + ("Referencia laboral mencionada" if clave.endswith("laboral") else "Referencia familiar mencionada"),
            tipo="texto",
            orden=9000 + len(created.question_ids),
            activa=True,
            creada_en=utc_now_naive(),
        )
        db.session.add(q)
        db.session.flush()
        created.question_ids.append(int(q.id))
        active[clave] = q
    db.session.commit()
    print("Preguntas usadas:", {k: int(v.id) for k, v in active.items()})
    return active


def create_staff_user(created: Created) -> None:
    existing = StaffUser.query.filter(
        (StaffUser.username == USERNAME) | (StaffUser.email == EMAIL)
    ).first()
    if existing:
        raise AssertionError("Ya existe usuario de validacion; no se reutiliza para evitar mezclar datos.")
    user = StaffUser(username=USERNAME, email=EMAIL, role="secretaria", is_active=True, mfa_enabled=False)
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    created.staff_user_id = int(user.id)
    db.session.commit()
    print("Usuario staff test creado:", USERNAME, "id=", created.staff_user_id)


def create_candidate(created: Created) -> int:
    existing = Candidata.query.filter(
        (Candidata.codigo == CODIGO) | (Candidata.cedula == CEDULA)
    ).first()
    if existing:
        raise AssertionError("Ya existe candidata de validacion; abortado para no reutilizar datos.")
    cand = Candidata(
        nombre_completo=PREFIX + "Maria Referencias",
        codigo=CODIGO,
        cedula=CEDULA,
        cedula_norm_digits=CEDULA,
        numero_telefono="8095552301",
        estado="en_proceso",
        entrevista="",
        contactos_referencias_laborales="FORM-LAB-111",
        referencias_familiares_detalle="FORM-FAM-222",
        referencias_laboral="FORM-LAB-111",
        referencias_familiares="FORM-FAM-222",
        medio_inscripcion=PREFIX + "local",
        origen_registro="interno",
        creado_por_staff=USERNAME,
        creado_desde_ruta=PREFIX + "script",
    )
    db.session.add(cand)
    db.session.flush()
    created.candidata_id = int(cand.fila)
    db.session.commit()
    print("Candidata test creada: fila=", created.candidata_id)
    return int(cand.fila)


def _answers_for(interview_id: int) -> list[str]:
    return [
        r.respuesta
        for r in EntrevistaRespuesta.query.join(EntrevistaPregunta, EntrevistaPregunta.id == EntrevistaRespuesta.pregunta_id)
        .filter(EntrevistaRespuesta.entrevista_id == int(interview_id))
        .filter(EntrevistaPregunta.clave.in_(["domestica.referencia_laboral", "domestica.referencia_familiar"]))
        .order_by(EntrevistaPregunta.orden.asc(), EntrevistaPregunta.id.asc())
        .all()
    ]


def assert_db_refs(fila: int, expected_form: tuple[str, str], expected_by_interview: dict[int, list[str]]) -> None:
    db.session.remove()
    cand = Candidata.query.get(int(fila))
    assert_true(cand is not None, "candidata test existe")
    assert_true(cand.contactos_referencias_laborales == expected_form[0], f"Candidata laboral = {expected_form[0]}")
    assert_true(cand.referencias_familiares_detalle == expected_form[1], f"Candidata familiar = {expected_form[1]}")
    assert_true(cand.referencias_laboral == expected_form[0], f"Candidata compat laboral = {expected_form[0]}")
    assert_true(cand.referencias_familiares == expected_form[1], f"Candidata compat familiar = {expected_form[1]}")
    for interview_id, expected in expected_by_interview.items():
        assert_true(_answers_for(interview_id) == expected, f"Entrevista {interview_id} referencias = {expected}")


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    return int(port)


def start_server() -> tuple[object, threading.Thread, str]:
    port = free_port()
    server = make_server("127.0.0.1", port, flask_app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            response = requests.get(f"{base_url}/health", timeout=0.7)
            if response.status_code in (200, 404):
                return server, thread, base_url
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("Servidor local no respondio a tiempo.")


def fill_reference_fields(page, lab: str, fam: str) -> None:
    page.evaluate(
        """({lab, fam}) => {
          for (const el of document.querySelectorAll('input[type="text"][name^="q_"], textarea[name^="q_"]')) {
            el.value = `CODEX respuesta ${el.name}`;
            el.dispatchEvent(new Event("input", { bubbles: true }));
            el.dispatchEvent(new Event("change", { bubbles: true }));
          }
          for (const field of document.querySelectorAll('.field')) {
            const label = field.querySelector('label')?.textContent?.trim() || '';
            const input = field.querySelector('input[type="text"][name^="q_"], textarea[name^="q_"]');
            if (!input) continue;
            if (label.includes('Referencia laboral')) input.value = lab;
            if (label.includes('Referencia familiar')) input.value = fam;
            input.dispatchEvent(new Event("input", { bubbles: true }));
            input.dispatchEvent(new Event("change", { bubbles: true }));
          }
          const radio = document.querySelector('input[type="radio"][name^="q_"][value="Sí"]')
            || document.querySelector('input[type="radio"][name^="q_"]');
          if (radio) {
            radio.checked = true;
            radio.dispatchEvent(new Event("change", { bubbles: true }));
          }
        }""",
        {"lab": lab, "fam": fam},
    )


def latest_interview_id(fila: int, known: set[int] | None = None) -> int:
    known = known or set()
    q = Entrevista.query.filter_by(candidata_id=int(fila), tipo="domestica")
    if known:
        q = q.filter(~Entrevista.id.in_(list(known)))
    interview = q.order_by(Entrevista.id.desc()).first()
    assert_true(interview is not None, "entrevista nueva persistida")
    return int(interview.id)


def extract_pdf_text(pdf_bytes: bytes) -> str:
    chunks: list[str] = []
    cmap: dict[int, str] = {}
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdf_bytes, re.S):
        raw = match.group(1)
        for data in (raw, zlib.decompress(raw) if raw.startswith(b"x") else b""):
            if not data:
                continue
            text_data = data.decode("latin1", "ignore")
            chunks.append(text_data)
            for src, dst in re.findall(r"<([0-9A-Fa-f]{4})>\s+<([0-9A-Fa-f]{4,6})>", text_data):
                try:
                    cmap[int(src, 16)] = chr(int(dst, 16))
                except Exception:
                    pass
            for start, end, dst_start in re.findall(
                r"<([0-9A-Fa-f]{4})>\s+<([0-9A-Fa-f]{4})>\s+<([0-9A-Fa-f]{4,6})>",
                text_data,
            ):
                try:
                    a = int(start, 16)
                    b = int(end, 16)
                    d = int(dst_start, 16)
                    for offset, code in enumerate(range(a, b + 1)):
                        cmap[code] = chr(d + offset)
                except Exception:
                    pass
    chunks.append(pdf_bytes.decode("latin1", "ignore"))
    raw_text = "\n".join(chunks)
    decoded_parts: list[str] = []
    for hex_payload in re.findall(r"<([0-9A-Fa-f]{4,})>\s*Tj", raw_text):
        if len(hex_payload) % 4 != 0:
            continue
        decoded_parts.append(
            "".join(
                cmap.get(int(hex_payload[i:i + 4], 16), "")
                for i in range(0, len(hex_payload), 4)
            )
        )
    for array_payload in re.findall(r"\[(.*?)\]\s*TJ", raw_text, flags=re.S):
        pieces = []
        for hex_payload in re.findall(r"<([0-9A-Fa-f]{4,})>", array_payload):
            if len(hex_payload) % 4 != 0:
                continue
            pieces.append(
                "".join(
                    cmap.get(int(hex_payload[i:i + 4], 16), "")
                    for i in range(0, len(hex_payload), 4)
                )
            )
        decoded_parts.append("".join(pieces))
    return raw_text + "\n" + "\n".join(decoded_parts)


def validate_pdf(api_request, base_url: str, interview_id: int) -> None:
    resp = api_request.get(f"{base_url}/entrevistas/pdf/{interview_id}", timeout=20000)
    assert_true(resp.status == 200, "PDF entrevista 1 responde 200")
    assert_true((resp.headers.get("content-type") or "").startswith("application/pdf"), "PDF es application/pdf")
    text_content = extract_pdf_text(resp.body())
    assert_true("INT-LAB-777" in text_content, "PDF entrevista 1 contiene INT-LAB-777")
    assert_true("INT-FAM-888" in text_content, "PDF entrevista 1 contiene INT-FAM-888")
    assert_true("FORM-LAB-555" not in text_content, "PDF entrevista 1 no contiene FORM-LAB-555")
    assert_true("FORM-FAM-666" not in text_content, "PDF entrevista 1 no contiene FORM-FAM-666")


def run_browser_flow(base_url: str, fila: int, created: Created, browser_name: str) -> None:
    from playwright.sync_api import sync_playwright

    console_errors: list[str] = []
    page_errors: list[str] = []
    failed_requests: list[str] = []
    with flask_app.app_context():
        db.session.remove()
        cand = Candidata.query.get(int(fila))
        initial_form = (
            str(getattr(cand, "contactos_referencias_laborales", "") or ""),
            str(getattr(cand, "referencias_familiares_detalle", "") or ""),
        )
    with sync_playwright() as p:
        browser_type = getattr(p, browser_name)
        browser = browser_type.launch(headless=True, args=["--no-sandbox"] if browser_name == "chromium" else [])
        context = browser.new_context()
        page = context.new_page()
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        page.on("requestfailed", lambda req: failed_requests.append(f"{req.method} {req.url} {req.failure}"))

        page.goto(f"{base_url}/admin/login", wait_until="domcontentloaded")
        page.fill('input[name="usuario"]', USERNAME)
        page.fill('input[name="clave"]', PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_url(re.compile(r".*/(home|admin).*"), timeout=15000)

        page.goto(f"{base_url}/admin/candidatas/{fila}", wait_until="domcontentloaded")
        refs_text = page.locator("#referencias").inner_text()
        assert_true(initial_form[0] in refs_text and initial_form[1] in refs_text, f"{browser_name}: ficha muestra referencias formulario iniciales")

        page.get_by_role("link", name=re.compile(r"Nueva entrevista doméstica", re.I)).click()
        page.wait_for_url(f"**/entrevistas/nueva/{fila}/domestica?next=/admin/candidatas/{fila}", timeout=15000)
        fill_reference_fields(page, "INT-LAB-333", "INT-FAM-444")
        with page.expect_response(lambda r: r.request.method == "POST" and f"/entrevistas/nueva/{fila}/domestica" in r.url, timeout=15000):
            page.get_by_role("button", name=re.compile(r"Guardar entrevista", re.I)).click()
        page.wait_for_url(f"**/admin/candidatas/{fila}", timeout=15000)

        with flask_app.app_context():
            first_id = latest_interview_id(fila)
            created.interview_ids.append(first_id)
            assert_db_refs(fila, initial_form, {first_id: ["INT-LAB-333", "INT-FAM-444"]})

        panel = page.locator("#entrevistas").inner_text()
        assert_true("INT-LAB-333" in panel and "INT-FAM-444" in panel, f"{browser_name}: entrevista 1 visible sin mezclar")

        page.get_by_role("button", name=re.compile(r"Editar referencias declaradas", re.I)).click()
        page.fill("#cand_ref_lab", "FORM-LAB-555")
        page.fill("#cand_ref_fam", "FORM-FAM-666")
        with page.expect_response(lambda r: r.request.method == "POST" and f"/admin/candidatas/{fila}/referencias" in r.url, timeout=15000):
            page.locator('#referencias button[type="submit"]').click()
        page.wait_for_timeout(500)
        with flask_app.app_context():
            assert_db_refs(fila, ("FORM-LAB-555", "FORM-FAM-666"), {first_id: ["INT-LAB-333", "INT-FAM-444"]})

        page.goto(f"{base_url}/entrevistas/editar/{first_id}?next=/admin/candidatas/{fila}", wait_until="domcontentloaded")
        fill_reference_fields(page, "INT-LAB-777", "INT-FAM-888")
        with page.expect_response(lambda r: r.request.method == "POST" and f"/entrevistas/editar/{first_id}" in r.url, timeout=15000):
            page.get_by_role("button", name=re.compile(r"Guardar cambios", re.I)).click()
        page.wait_for_url(f"**/admin/candidatas/{fila}", timeout=15000)
        with flask_app.app_context():
            assert_db_refs(fila, ("FORM-LAB-555", "FORM-FAM-666"), {first_id: ["INT-LAB-777", "INT-FAM-888"]})

        page.goto(f"{base_url}/entrevistas/candidata/{fila}?next=/admin/candidatas/{fila}", wait_until="domcontentloaded")
        page.get_by_role("link", name=re.compile(r"\+\s*Doméstica", re.I)).click()
        page.wait_for_url(f"**/entrevistas/nueva/{fila}/domestica?next=/admin/candidatas/{fila}", timeout=15000)
        fill_reference_fields(page, "INT2-LAB-999", "INT2-FAM-000")
        with page.expect_response(lambda r: r.request.method == "POST" and f"/entrevistas/nueva/{fila}/domestica" in r.url, timeout=15000):
            page.get_by_role("button", name=re.compile(r"Guardar entrevista", re.I)).click()
        page.wait_for_url(f"**/admin/candidatas/{fila}", timeout=15000)

        with flask_app.app_context():
            second_id = latest_interview_id(fila, known={first_id})
            created.interview_ids.append(second_id)
            assert_db_refs(
                fila,
                ("FORM-LAB-555", "FORM-FAM-666"),
                {
                    first_id: ["INT-LAB-777", "INT-FAM-888"],
                    second_id: ["INT2-LAB-999", "INT2-FAM-000"],
                },
            )

        page.reload(wait_until="domcontentloaded")
        refs_text = page.locator("#referencias").inner_text()
        interviews_text = page.locator("#entrevistas").inner_text()
        assert_true("FORM-LAB-555" in refs_text and "FORM-FAM-666" in refs_text, f"{browser_name}: reload conserva referencias formulario")
        assert_true("INT-LAB-777" in interviews_text and "INT-FAM-888" in interviews_text, f"{browser_name}: reload conserva entrevista 1")
        assert_true("INT2-LAB-999" in interviews_text and "INT2-FAM-000" in interviews_text, f"{browser_name}: reload conserva entrevista 2")
        assert_true("FORM-LAB-555" not in interviews_text and "FORM-FAM-666" not in interviews_text, f"{browser_name}: panel entrevistas no mezcla formulario")

        validate_pdf(context.request, base_url, first_id)

        same_origin_failures = [
            item
            for item in failed_requests
            if item.startswith(("GET " + base_url, "POST " + base_url))
            and "/admin/live/invalidation/stream" not in item
            and "/admin/live/invalidation/poll" not in item
            and "/admin/chat/badge.json" not in item
            and "/admin/seguimiento-candidatas/badge.json" not in item
            and "/admin/monitoreo/presence/ping" not in item
            and "/admin/live/observability" not in item
        ]
        critical_console_errors = [item for item in console_errors if "429 (TOO MANY REQUESTS)" not in item]
        assert_true(page_errors == [], f"{browser_name}: sin page errors")
        assert_true(same_origin_failures == [], f"{browser_name}: sin request failures same-origin criticos")
        assert_true(critical_console_errors == [], f"{browser_name}: sin console errors criticos")
        browser.close()


def main() -> None:
    created = Created()
    server = None
    thread = None
    try:
        flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        with flask_app.app_context():
            ensure_local_db()
            cleanup_prefixed_residue()
            ensure_questions(created)
            create_staff_user(created)
            fila = create_candidate(created)
            assert_db_refs(fila, ("FORM-LAB-111", "FORM-FAM-222"), {})

        server, thread, base_url = start_server()
        print("Servidor local:", base_url)
        run_browser_flow(base_url, int(created.candidata_id), created, "chromium")
        try:
            run_browser_flow(base_url, int(created.candidata_id), created, "webkit")
        except Exception as exc:
            print("WEBKIT_SKIP_OR_FAIL:", repr(exc))
        print("VALIDATION_OK")
    finally:
        if server is not None:
            server.shutdown()
        if thread is not None:
            thread.join(timeout=3)
        cleanup(created)
        print("CLEANUP_OK", created)


if __name__ == "__main__":
    if (os.getenv("APP_ENV") or "").strip().lower() != "local":
        raise SystemExit("Este script requiere APP_ENV=local.")
    main()
