#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stress local para /registro/registro_publico con 50 escenarios.
Uso:
  APP_ENV=local venv/bin/python scripts/local_registro_publico_stress.py
"""

import os
import argparse
import hashlib
from collections import Counter
from datetime import datetime, timezone
from typing import Dict, List, Optional
from unittest.mock import patch

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stress local para /registro/registro_publico")
    parser.add_argument(
        "--stress-prefix",
        dest="stress_prefix",
        default=None,
        help="Prefijo de campaña (ej: MAY05, QA_TEAM, LOADTEST1). Alternativa a STRESS_PREFIX.",
    )
    parser.add_argument(
        "--run-id",
        dest="run_id",
        default=None,
        help="Id opcional de corrida para aislar IDs entre runs con el mismo prefix.",
    )
    return parser.parse_args()


def _sanitize_prefix(raw: Optional[str]) -> str:
    value = (raw or "").strip().upper()
    safe = "".join(ch if (ch.isalnum() or ch in {"_", "-"}) else "_" for ch in value)
    safe = safe.strip("_-")
    return safe[:24]


def _fallback_prefix(now: Optional[datetime] = None) -> str:
    dt = now or datetime.now(timezone.utc)
    return dt.strftime("RUN%Y%m%d%H%M%S")


def _resolve_prefix(cli_prefix: Optional[str], env_prefix: Optional[str], now: Optional[datetime] = None) -> str:
    for raw in (cli_prefix, env_prefix):
        normalized = _sanitize_prefix(raw)
        if normalized:
            return normalized
    return _fallback_prefix(now=now)


def _resolve_run_id(cli_run_id: Optional[str], now: Optional[datetime] = None) -> str:
    raw = _sanitize_prefix(cli_run_id)
    if raw:
        return raw
    dt = now or datetime.now(timezone.utc)
    return dt.strftime("T%H%M%S")


def _marker(prefix: str, i: int) -> str:
    return f"{prefix}-{i:04d}"


def _digits_from_hash(seed: str, size: int) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    out = []
    idx = 0
    while len(out) < size:
        chunk = digest[idx % len(digest)]
        out.append(str(int(chunk, 16) % 10))
        idx += 1
    return "".join(out)


def _cedula_from_index(namespace: str, i: int) -> str:
    digits11 = _digits_from_hash(f"cedula:{namespace}:{i}", 11)
    if digits11 == "00000000000":
        digits11 = "00100000001"
    return f"{digits11[:3]}-{digits11[3:10]}-{digits11[10:]}"


def _telefono_from_index(namespace: str, i: int) -> str:
    suffix = _digits_from_hash(f"phone:{namespace}:{i}", 7)
    return f"809-{suffix[:3]}-{suffix[3:]}"


def _base_payload(i: int, *, prefix: str, namespace: str) -> Dict[str, object]:
    mk = _marker(prefix, i)
    return {
        "nombre_completo": f"STRESSLOC {mk} Candidata Perez",
        "edad": "29",
        "numero_telefono": _telefono_from_index(namespace, i),
        "direccion_completa": f"Santiago Centro Calle {i} #12, Residencial Prueba ({mk})",
        "modalidad_trabajo_preferida": "Salida diaria",
        "rutas_cercanas": "Centro, Tamboril",
        "empleo_anterior": f"Limpieza y cocina [{mk}]",
        "anos_experiencia": "3 años o más",
        "areas_experiencia": ["Limpieza", "Cocina"],
        "sabe_planchar": "si",
        "contactos_referencias_laborales": f"Ref Lab {mk} - 8091231234",
        "referencias_familiares_detalle": f"Ref Fam {mk} - 8099990000",
        "acepta_porcentaje_sueldo": "1",
        "cedula": _cedula_from_index(namespace, i),
    }


def _build_scenarios(*, prefix: str, namespace: str) -> List[dict]:
    out = []
    for i in range(1, 51):
        payload = _base_payload(i, prefix=prefix, namespace=namespace)
        expected_valid = True
        label = "normal"

        if i == 2:
            payload["nombre_completo"] = "STRESSLOC Maria Fernanda de los Angeles Rodriguez Hernandez"
            label = "nombre_largo"
        elif i == 3:
            payload["nombre_completo"] = "STRESSLOC Ana Maria De La Cruz Peña"
            label = "apellido_compuesto_acentos"
        elif i == 4:
            payload["numero_telefono"] = "(809) 123 4567"
            label = "telefono_parentesis_espacios"
        elif i == 5:
            payload["cedula"] = "001-2345678-9"
            label = "cedula_guiones"
        elif i == 6:
            payload["direccion_completa"] = "Santiago " + ("Avenida " * 20)
            label = "direccion_larga"
        elif i == 7:
            payload["empleo_anterior"] = "Limpieza."
            label = "experiencia_corta"
        elif i == 8:
            payload["empleo_anterior"] = ("Experiencia en limpieza, cocina, planchado y cuidado. " * 20).strip()
            label = "experiencia_larga"
        elif i == 9:
            payload["empleo_anterior"] = "Limpieza, cocina básica, niños y envejecientes."
            payload["motivacion_trabajo"] = "Me interesa trabajar con responsabilidad y respeto."
            label = "acentos"
        elif i == 10:
            payload["motivacion_trabajo"] = "Me adapto: orden, puntualidad; compromiso."
            label = "caracteres_especiales_normales"
        elif i == 11:
            payload["disponibilidad_inicio"] = ""
            payload["sueldo_esperado"] = ""
            payload["motivacion_trabajo"] = ""
            label = "opcionales_vacios"
        elif i == 12:
            payload["areas_experiencia"] = ["Limpieza", "Cocina", "Niñera", "Cuidado de ancianos"]
            label = "seleccion_multiple"
        elif i == 13:
            payload["areas_experiencia"] = ["Limpieza", "Otro: labores mixtas"]
            label = "respuesta_otro"
        elif i == 14:
            payload["motivation"] = "campo_extra_manual"
            label = "post_manual_extra_field"
        elif i == 15:
            payload["empleo_anterior"] = "Me gusta trabajar 😊"
            label = "emoji_texto"
        elif i == 16:
            payload["empleo_anterior"] = "<script>alert(1)</script> limpieza"
            label = "html_script"
        elif i == 17:
            payload["motivacion_trabajo"] = "Dijo: \"sí\" y luego 'no'"
            label = "comillas_simples_dobles"
        elif i == 18:
            payload["contactos_referencias_laborales"] = "Ref1\n8091111111\nRef2\n8092222222"
            label = "saltos_linea"
        elif i == 19:
            payload["empleo_anterior"] = "X" * 5000
            label = "texto_extremadamente_largo"
        elif i == 20:
            payload["numero_telefono"] = "80912"
            expected_valid = False
            label = "telefono_invalido"
        elif i == 21:
            payload["cedula"] = "001-12"
            expected_valid = False
            label = "cedula_invalida"
        elif i == 22:
            payload["cedula"] = _cedula_from_index(namespace, 1)
            expected_valid = False
            label = "cedula_duplicada"
        elif i == 23:
            payload["nombre_completo"] = "Ana"
            expected_valid = False
            label = "nombre_corto"
        elif i == 24:
            payload["edad"] = "10"
            expected_valid = False
            label = "edad_fuera_rango"
        elif i == 25:
            payload["edad"] = "abc"
            expected_valid = False
            label = "edad_no_numerica"
        elif i == 26:
            payload["sabe_planchar"] = "quizas"
            expected_valid = False
            label = "planchar_invalido"
        elif i == 27:
            payload["acepta_porcentaje_sueldo"] = "x"
            expected_valid = False
            label = "acepta_porcentaje_invalido"
        elif i == 28:
            payload["nombre_completo"] = "STRESSLOC Ñandú Élite Gómez"
            label = "unicode_acentos"
        elif i == 29:
            payload["numero_telefono"] = "809 333 4444"
            label = "telefono_espaciado"
        elif i == 30:
            payload["numero_telefono"] = "809.333.4444"
            label = "telefono_puntos"
        elif i == 31:
            payload["rutas_cercanas"] = "Centro<script>alert(1)</script>"
            label = "script_en_ruta"
        elif i == 32:
            payload["direccion_completa"] = "\"Calle 10\" #5, Apto 'B'"
            label = "direccion_comillas"
        elif i == 33:
            payload["contactos_referencias_laborales"] = "<b>Ref</b> 8091111111"
            label = "html_referencias"
        elif i == 34:
            payload["referencias_familiares_detalle"] = "Ref1 - 8091111111\nRef2 - 8092222222"
            label = "familiares_multilinea"
        elif i == 35:
            payload["motivacion_trabajo"] = "Quiero estabilidad económica y crecimiento."
            label = "motivacion_normal"
        elif i == 36:
            payload["areas_experiencia"] = []
            label = "areas_vacia_permitida"
        elif i == 37:
            payload["modalidad_trabajo_preferida"] = ""
            expected_valid = False
            label = "modalidad_vacia"
        elif i == 38:
            payload["contactos_referencias_laborales"] = ""
            expected_valid = False
            label = "referencia_laboral_vacia"
        elif i == 39:
            payload["referencias_familiares_detalle"] = ""
            expected_valid = False
            label = "referencia_familiar_vacia"
        elif i == 40:
            payload["direccion_completa"] = ""
            expected_valid = False
            label = "direccion_vacia"
        elif i == 41:
            payload["empleo_anterior"] = ""
            expected_valid = False
            label = "empleo_anterior_vacio"
        elif i == 42:
            payload["numero_telefono"] = "809-000-0000"
            label = "telefono_borde"
        elif i == 43:
            payload["cedula"] = "000-0000000-0"
            label = "cedula_borde"
        elif i == 44:
            payload["motivacion_trabajo"] = "linea1\nlinea2\nlinea3"
            label = "motivacion_multilinea"
        elif i == 45:
            payload["empleo_anterior"] = "Prueba con símbolos #, %, &, /, ()"
            label = "simbolos_normales"
        elif i == 46:
            payload["rutas_cercanas"] = "Ruta K ; DROP TABLE candidatas;"
            label = "sql_like_text"
        elif i == 47:
            payload["nombre_completo"] = "STRESSLOC Candidata Apostrofo O'Connor"
            label = "apostrofo_nombre"
        elif i == 48:
            payload["empleo_anterior"] = "Limpieza\tcocina\tplanchado"
            label = "tabs_en_texto"
        elif i == 49:
            payload["nombre_completo"] = "STRESSLOC Candidate HTML <b>Bold</b>"
            label = "html_en_nombre"
        elif i == 50:
            payload["cedula"] = _cedula_from_index(namespace, 49)
            expected_valid = False
            label = "doble_submit_simulado"

        out.append({"idx": i, "label": label, "payload": payload, "expected_valid": expected_valid})
    return out


def run():
    args = _parse_args()
    if (os.getenv("APP_ENV") or "").strip().lower() != "local":
        raise SystemExit("ERROR: Este script solo corre con APP_ENV=local")

    from app import app as flask_app  # noqa: E402
    from models import Candidata  # noqa: E402

    start_wall = datetime.now(timezone.utc)
    prefix = _resolve_prefix(args.stress_prefix, os.getenv("STRESS_PREFIX"))
    run_id = _resolve_run_id(args.run_id)
    namespace = f"{prefix}:{run_id}"

    flask_app.config["WTF_CSRF_ENABLED"] = False
    flask_app.config["TESTING"] = False

    started_at = datetime.now(timezone.utc)
    scenarios = _build_scenarios(prefix=prefix, namespace=namespace)
    client = flask_app.test_client()
    result_rows = []
    errors_500 = 0
    tracebacks = 0

    with patch("registro.routes.hit_rate_limit", return_value=False), \
         patch("registro.routes.enforce_business_limit", return_value=(False, 1)), \
         patch("registro.routes.enforce_min_human_interval", return_value=(False, 1)):
        for s in scenarios:
            resp = client.post("/registro/registro_publico/", data=s["payload"], follow_redirects=False)
            body = resp.get_data(as_text=True)
            status = resp.status_code
            redirected_gracias = (resp.headers.get("Location", "").endswith("/registro/registro_publico/gracias/"))
            if status >= 500:
                errors_500 += 1
            if "Traceback" in body:
                tracebacks += 1
            result_rows.append(
                {
                    "idx": s["idx"],
                    "label": s["label"],
                    "expected_valid": s["expected_valid"],
                    "status": status,
                    "ok": bool(status in (302, 303) and redirected_gracias),
                    "location": resp.headers.get("Location", ""),
                }
            )

        # refresh después de enviar y GET normal
        refresh_resp = client.get("/registro/registro_publico/gracias/")
        form_after_resp = client.get("/registro/registro_publico/")

        # doble submit real del mismo payload
        ds_payload = _base_payload(500, prefix=prefix, namespace=namespace)
        first = client.post("/registro/registro_publico/", data=ds_payload, follow_redirects=False)
        second = client.post("/registro/registro_publico/", data=ds_payload, follow_redirects=False)

    with flask_app.app_context():
        inserted = (
            Candidata.query.filter(Candidata.nombre_completo.like(f"STRESSLOC {prefix}-%"))
            .filter(Candidata.marca_temporal >= started_at.replace(tzinfo=None))
            .order_by(Candidata.fila.desc())
            .all()
        )
        inserted_ids = [r.fila for r in inserted]
        script_hits = [
            r.fila for r in inserted
            if any(
                "<script" in (v or "").lower()
                for v in [
                    r.direccion_completa,
                    r.rutas_cercanas,
                    r.empleo_anterior,
                    r.contactos_referencias_laborales,
                    r.referencias_familiares_detalle,
                    r.sueldo_esperado,
                    r.motivacion_trabajo,
                ]
            )
        ]
        cedulas = [r.cedula for r in inserted]
        ced_dup_count = len(cedulas) - len(set(cedulas))

        samples = []
        for r in inserted[:5]:
            samples.append(
                {
                    "fila": r.fila,
                    "nombre": r.nombre_completo,
                    "cedula": r.cedula,
                    "telefono": r.numero_telefono,
                    "origen": r.origen_registro,
                    "ruta": r.creado_desde_ruta,
                    "codigo": r.codigo,
                }
            )

    expected_valid = sum(1 for r in result_rows if r["expected_valid"])
    expected_invalid = len(result_rows) - expected_valid
    accepted = sum(1 for r in result_rows if r["ok"])
    rejected = len(result_rows) - accepted
    mismatch = [
        r for r in result_rows
        if (r["expected_valid"] and not r["ok"]) or ((not r["expected_valid"]) and r["ok"])
    ]
    status_counter = Counter([r["status"] for r in result_rows])
    elapsed_seconds = (datetime.now(timezone.utc) - start_wall).total_seconds()

    print("=== LOCAL STRESS REGISTRO PUBLICO ===")
    print(f"PREFIX_USADO={prefix}")
    print(f"RUN_ID={run_id}")
    print(f"NAMESPACE={namespace}")
    print(f"APP_ENV={os.getenv('APP_ENV')}")
    print(f"DB_URI={flask_app.config.get('SQLALCHEMY_DATABASE_URI')}")
    print(f"Escenarios ejecutados={len(result_rows)}")
    print(f"Esperados validos={expected_valid} | esperados invalidos={expected_invalid}")
    print(f"Aceptados={accepted} | rechazados={rejected}")
    print(f"Distribucion status={dict(status_counter)}")
    print(f"Mismatches validacion={len(mismatch)}")
    print(f"Errores 500={errors_500} | tracebacks_en_html={tracebacks}")
    print(f"Refresh gracias status={refresh_resp.status_code} | GET formulario post-submit status={form_after_resp.status_code}")
    print(f"Doble submit: first={first.status_code} second={second.status_code}")
    print(f"Insertados detectados (prefijo STRESSLOC {prefix}-)={len(inserted_ids)}")
    print(f"Duplicados de cedula en insertados={ced_dup_count}")
    print(f"Campos con '<script' persistido={len(script_hits)}")
    print(f"Tiempo total (s)={elapsed_seconds:.2f}")
    print("Muestra de 5 registros:")
    for s in samples:
        print(s)
    if mismatch:
        print("Ejemplos mismatch:")
        for row in mismatch[:10]:
            print(row)

    og_resp = client.get("/registro/registro_publico/")
    og_html = og_resp.get_data(as_text=True)
    og_checks = [
        'property="og:title" content="Registro para empleo | Doméstica del Cibao A&amp;D"',
        'property="og:description" content="Regístrate de forma segura para aplicar a oportunidades de empleo doméstico."',
        'name="twitter:card" content="summary_large_image"',
    ]
    og_ok = all(t in og_html for t in og_checks)
    print(f"OG/Twitter render check={'OK' if og_ok else 'FAIL'}")


if __name__ == "__main__":
    run()
