# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import hashlib
import io
import os
import random
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app as flask_app
from config_app import db
from models import (
    Candidata,
    CandidataWeb,
    CatalogoPrivado,
    Entrevista,
    EntrevistaPregunta,
    EntrevistaRespuesta,
)
from utils.timezone import utc_now_naive

SEED_PREFIX = "QA-TIENDA-"
SEED_TOTAL = 100
DEFAULT_TOKEN = "qa-tienda-local-demo"

CITIES = [
    "Santiago",
    "Santo Domingo",
    "La Vega",
    "Moca",
    "San Francisco de Macorís",
    "Puerto Plata",
    "Bonao",
    "La Romana",
    "Higüey",
    "San Pedro de Macorís",
    "Baní",
    "Azua",
    "San Cristóbal",
    "Jarabacoa",
    "Constanza",
    "Mao",
]

SECTORS = [
    "Centro",
    "Villa Olga",
    "Naco",
    "Los Prados",
    "Bella Vista",
    "Piantini",
    "Ensanche Libertad",
    "Gurabo",
    "Cienfuegos",
    "Sabana Perdida",
    "Los Mina",
    "Evaristo Morales",
    "Arroyo Hondo",
    "Alma Rosa",
    "Pueblo Nuevo",
    "La Herradura",
]

MODALIDADES = ["Con dormida", "Salida diaria"]
FUNCIONES = [
    "Limpieza general",
    "Cocinar",
    "Lavar",
    "Planchar",
    "Cuidar niños",
    "Cuidar envejecientes",
]

PERFILES = [
    "doméstica general con cocina básica",
    "niñera con experiencia",
    "cuidadora de envejecientes",
    "cocinera doméstica",
    "limpieza profunda",
    "salida diaria para apartamento",
    "con dormida para familia",
    "planchado/lavado fuerte",
    "experiencia con bebés",
    "experiencia con adultos mayores",
]

NOMBRES = [
    "Alondra", "Beatriz", "Carla", "Damaris", "Eliana", "Fabiola", "Génesis", "Helena", "Ingrid", "Julissa",
    "Kiara", "Lissette", "Mariela", "Nathaly", "Odalis", "Patricia", "Rocío", "Soraya", "Tamara", "Yocasta",
]
APELLIDOS = [
    "Pérez", "Rodríguez", "Guzmán", "Martínez", "López", "Hernández", "Castillo", "Reyes", "Núñez", "Tejeda",
    "Rosario", "Vargas", "Morillo", "de la Cruz", "Abreu", "Méndez", "Santos", "Toribio", "Ramírez", "Domínguez",
]
DESCRIPCIONES = ["responsable y organizada", "paciente y respetuosa", "puntual y trabajadora", "tranquila y colaboradora"]
SALIDAS = [
    "cerraron el contrato por cambio de ciudad",
    "la familia ya no necesitaba el servicio completo",
    "cambió el horario y no era compatible",
]


def _fake_phone(idx: int, *, offset: int = 0) -> str:
    num = 1000 + ((idx * 37 + offset * 53) % 9000)
    prefix = ["809", "829", "849"][(idx + offset) % 3]
    return f"{prefix}-555-{num:04d}"


def _build_legacy_interview(p: dict[str, Any], idx: int, *, rnd: random.Random) -> str:
    salida = SALIDAS[idx % len(SALIDAS)]
    descripcion = DESCRIPCIONES[idx % len(DESCRIPCIONES)]
    ref_fam_name = f"{NOMBRES[(idx + 2) % len(NOMBRES)]} {APELLIDOS[(idx + 7) % len(APELLIDOS)]}"
    ref_lab_name = f"{NOMBRES[(idx + 5) % len(NOMBRES)]} {APELLIDOS[(idx + 11) % len(APELLIDOS)]}"
    dir_fake = f"Calle {['Primera', 'Central', 'Los Pinos', 'San Juan', 'Luna'][(idx + 1) % 5]} #{40 + (idx % 180)}"

    cooks = "Sí, cocina básica criolla y menú semanal simple." if "Cocinar" in (p.get("tags") or "") else "No cocina avanzado; sigue recetas indicadas."
    ninos = "Tiene experiencia con bebés y niños de primaria." if "Cuidar niños" in (p.get("tags") or "") else "Puede apoyar supervisión puntual de niños."
    enve = "Ha cuidado envejecientes con acompañamiento y medicación supervisada." if "Cuidar envejecientes" in (p.get("tags") or "") else "No ha trabajado fijo con envejecientes."

    return (
        f"Entrevista QA {p['codigo']}:\n"
        f"- ¿Cuál es su nombre completo?: {p['nombre']}.\n"
        f"- ¿Qué edad tiene?: {p['edad']}.\n"
        f"- Dirección: {dir_fake}, sector {p['sector']}, {p['ciudad']}.\n"
        f"- ¿Cómo te describes como persona?: {descripcion}.\n"
        f"- Modalidad de trabajo: {p['modalidad']}. Disponible: {'inmediata' if p['disponible_inmediato'] else '1-2 semanas'}.\n"
        f"- Cocina: {cooks}\n"
        f"- Niños: {ninos}\n"
        f"- Envejecientes: {enve}\n"
        f"- Razón de salida del empleo anterior: {salida}.\n"
        f"- Referencia familiar: {ref_fam_name}, {_fake_phone(idx, offset=1)}.\n"
        f"- Referencia laboral: {ref_lab_name}, {_fake_phone(idx, offset=2)}.\n"
        f"- Observación: acepta revisión a la salida y uso de uniforme."
    )


def _structured_interview_answers(p: dict[str, Any], idx: int, question_ids: set[str]) -> dict[str, str]:
    descripcion = DESCRIPCIONES[idx % len(DESCRIPCIONES)]
    response_by_id = {
        "nombre": p["nombre"],
        "nacionalidad": "Dominicana",
        "edad": p["edad"],
        "direccion": f"Calle QA #{50 + idx}, sector {p['sector']}, {p['ciudad']}",
        "estado_civil": "Soltera",
        "tienes_hijos": "Sí" if (idx % 2 == 0) else "No",
        "numero_hijos": "2" if (idx % 2 == 0) else "0",
        "edades_hijos": "5 y 8" if (idx % 2 == 0) else "",
        "quien_cuida": "Su madre",
        "descripcion_personal": descripcion,
        "razon_trabajo": "Le gusta trabajar en casas de familia y mantener el hogar organizado.",
        "labores_anteriores": "Limpieza general, lavado, planchado y cocina básica.",
        "tiempo_ultimo_trabajo": "3 meses",
        "razon_salida": SALIDAS[idx % len(SALIDAS)],
        "situacion_dificil": "Sí",
        "manejo_situacion": "Conversando con respeto y siguiendo instrucciones.",
        "manejo_reclamo": "Mantiene calma y aclara la situación con respeto.",
        "uniforme": "Sí",
        "dias_feriados": "Sí lo pagan",
        "revision_salida": "Sí",
        "colaboracion": "Sí, dispuesta a colaborar.",
        "tipo_familia": "Familia con niños en edad escolar.",
        "cuidado_ninos": "Sí, de 2 a 10 años.",
        "sabes_cocinar": "Sí",
        "gusta_cocinar": "Sí",
        "que_cocinas": "Arroz, habichuelas, pollo guisado y pastas.",
        "postres": "Flan y bizcocho básico.",
        "tareas_casa": "Le gusta limpieza y cocina; prefiere evitar tareas pesadas nocturnas.",
        "electrodomesticos": "Sí, usa lavadora, secadora y microondas.",
        "planchar": "Sí",
        "actividad_principal": "No",
        "afiliacion_religiosa": "Cristiana",
        "cursos_domesticos": "Curso básico de higiene en el hogar.",
        "nivel_academico": "Secundaria completa",
        "condiciones_salud": "No",
        "alergico": "No",
        "medicamentos": "No",
        "seguro_medico": "Sí",
        "pruebas_medicas": "Sí",
        "vacunas_covid": "Dosis 2",
        "tomas_alcohol": "No",
        "fumas": "No",
        "tatuajes_piercings": "No",
    }
    return {
        f"domestica.{qid}": response_by_id.get(qid, "")
        for qid in question_ids
    }


def _upsert_questions() -> tuple[dict[str, EntrevistaPregunta], set[str]]:
    banco = flask_app.config.get("ENTREVISTAS_CONFIG") or {}
    domestica = (banco.get("domestica") or {}).get("preguntas") or []
    defs: list[tuple[str, str, str, list[str] | None]] = []
    for i, row in enumerate(domestica, start=1):
        qid = str((row or {}).get("id") or "").strip()
        texto = str((row or {}).get("enunciado") or "").strip()
        if not qid or not texto:
            continue
        tipo = str((row or {}).get("tipo") or "texto").strip()
        opciones = (row or {}).get("opciones")
        defs.append((f"domestica.{qid}", texto, tipo, list(opciones) if isinstance(opciones, list) else None))

    if not defs:
        raise RuntimeError("No hay preguntas de domestica en ENTREVISTAS_CONFIG")

    out: dict[str, EntrevistaPregunta] = {}
    question_ids: set[str] = set()
    for i, (clave, texto, tipo, opciones) in enumerate(defs, start=1):
        # Para compatibilidad SQLite en tests locales, persistimos opciones como None.
        safe_opciones = None
        row = EntrevistaPregunta.query.filter_by(clave=clave).first()
        if row is None:
            row = EntrevistaPregunta(clave=clave, texto=texto, tipo=tipo, opciones=safe_opciones, orden=i, activa=True)
            db.session.add(row)
            db.session.flush()
        else:
            row.texto = texto[:255]
            row.tipo = tipo[:30]
            row.opciones = safe_opciones
            row.orden = i
            row.activa = True
        out[clave] = row
        question_ids.add(clave.split(".", 1)[1])
    return out, question_ids


@dataclass
class SeedResult:
    created: int
    skipped_existing: bool
    db_url: str
    token: str


def _token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _db_url_text() -> str:
    try:
        return str(db.engine.url)
    except Exception:
        return (flask_app.config.get("SQLALCHEMY_DATABASE_URI") or "").strip()


def _looks_production_db(url: str) -> bool:
    raw = (url or "").strip().lower()
    if not raw:
        return False
    markers = [
        "production",
        "prod",
        "render.com",
        "rds.amazonaws.com",
        "herokuapp",
        "supabase.co",
        "neon.tech",
    ]
    if any(m in raw for m in markers):
        return True
    if raw.startswith("postgres") and ("localhost" not in raw and "127.0.0.1" not in raw):
        return True
    return False


def _assert_local_safety() -> None:
    app_env = (os.getenv("APP_ENV") or "").strip().lower()
    flask_env = (os.getenv("FLASK_ENV") or "").strip().lower()
    db_url = _db_url_text()
    prod_url = (os.getenv("DATABASE_URL") or "").strip()

    if app_env == "production":
        raise RuntimeError("Abortado: APP_ENV=production")
    if flask_env == "production":
        raise RuntimeError("Abortado: FLASK_ENV=production")
    if prod_url and db_url and db_url == prod_url:
        raise RuntimeError("Abortado: DB actual coincide con DATABASE_URL de producción")
    if _looks_production_db(db_url):
        raise RuntimeError(f"Abortado: URL de DB parece de producción: {db_url}")


def _seed_profile_image_bytes(initials: str, seed: int) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    rnd = random.Random(seed)
    width, height = 640, 640
    c1 = (rnd.randint(25, 110), rnd.randint(70, 160), rnd.randint(120, 220))
    c2 = (rnd.randint(130, 220), rnd.randint(80, 180), rnd.randint(25, 120))

    img = Image.new("RGB", (width, height), c1)
    draw = ImageDraw.Draw(img)

    for y in range(height):
        ratio = y / float(max(1, height - 1))
        color = (
            int(c1[0] * (1 - ratio) + c2[0] * ratio),
            int(c1[1] * (1 - ratio) + c2[1] * ratio),
            int(c1[2] * (1 - ratio) + c2[2] * ratio),
        )
        draw.line([(0, y), (width, y)], fill=color)

    circle = [90, 90, width - 90, height - 170]
    draw.ellipse(circle, outline=(255, 255, 255), width=8)

    try:
        font_big = ImageFont.truetype("DejaVuSans-Bold.ttf", 170)
        font_small = ImageFont.truetype("DejaVuSans.ttf", 40)
    except Exception:
        font_big = ImageFont.load_default()
        font_small = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), initials, font=font_big)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (width - tw) // 2
    ty = (height - th) // 2 - 40
    draw.text((tx, ty), initials, fill=(255, 255, 255), font=font_big)

    caption = "Perfil validado"
    cb = draw.textbbox((0, 0), caption, font=font_small)
    cw = cb[2] - cb[0]
    cx = (width - cw) // 2
    cy = height - 120
    draw.text((cx, cy), caption, fill=(255, 255, 255), font=font_small)

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _build_candidate_payload(idx: int) -> dict[str, Any]:
    rnd = random.Random(10_000 + idx)
    nombre = f"{NOMBRES[idx % len(NOMBRES)]} {APELLIDOS[(idx * 3) % len(APELLIDOS)]}"
    codigo = f"{SEED_PREFIX}{idx:03d}"
    ced_digits = f"9{idx:010d}"[-11:]
    ciudad = CITIES[idx % len(CITIES)]
    sector = SECTORS[(idx * 5) % len(SECTORS)]
    modalidad = MODALIDADES[idx % 2]
    edad = 23 + (idx % 29)
    anios = 1 + (idx % 15)
    perfil = PERFILES[idx % len(PERFILES)]
    sueldo_base = 17000 + ((idx * 650) % 16000)
    sueldo_hasta = sueldo_base + 3500 + (idx % 5) * 1000
    estado_roll = idx % 10
    if estado_roll <= 6:
        estado_publico = "disponible"
    elif estado_roll <= 8:
        estado_publico = "reservada"
    else:
        estado_publico = "no_disponible"
    visible = (idx % 9) != 0
    disponible_inmediato = (idx % 3) != 0

    k = 2 + (idx % 3)
    tags = rnd.sample(FUNCIONES, k=k)

    initials = "".join([part[0] for part in nombre.split()[:2]]).upper()
    img_bytes = _seed_profile_image_bytes(initials=initials, seed=idx)

    experiencia_resumen = (
        f"{perfil.capitalize()}, {anios} años de experiencia en hogares de {ciudad}. "
        f"Fortalezas en {', '.join(tags[:2]).lower()}."
    )
    experiencia_detallada = (
        f"Perfil QA local. Ha trabajado en modalidad {modalidad.lower()} en el sector {sector}. "
        f"Maneja rutinas de {', '.join(tags).lower()} y seguimiento de instrucciones del hogar."
    )
    entrevista_publica = (
        f"Perfil QA {codigo} entrevistado por la agencia; experiencia validada en {modalidad.lower()} "
        f"con fortalezas en {', '.join(tags[:2]).lower()}."
    )
    entrevista_legacy = _build_legacy_interview(
        {
            "codigo": codigo,
            "nombre": nombre,
            "edad": str(edad),
            "ciudad": ciudad,
            "sector": sector,
            "modalidad": modalidad,
            "disponible_inmediato": disponible_inmediato,
            "tags": ", ".join(tags),
        },
        idx,
        rnd=rnd,
    )
    entrevista_struct = _structured_interview_answers(
        {
            "nombre": nombre,
            "edad": str(edad),
            "ciudad": ciudad,
            "sector": sector,
            "modalidad": modalidad,
            "disponible_inmediato": disponible_inmediato,
            "tags": ", ".join(tags),
        },
        idx,
        question_ids=set(),
    )

    return {
        "codigo": codigo,
        "nombre": nombre,
        "edad": str(edad),
        "edad_publica": f"{edad} años",
        "cedula": ced_digits,
        "ciudad": ciudad,
        "sector": sector,
        "modalidad": modalidad,
        "sueldo_desde": sueldo_base,
        "sueldo_hasta": sueldo_hasta,
        "sueldo_texto": f"RD${sueldo_base:,} - RD${sueldo_hasta:,}".replace(",", "."),
        "disponible_inmediato": disponible_inmediato,
        "estado_publico": estado_publico,
        "visible": visible,
        "tags": ", ".join(tags),
        "experiencia_resumen": experiencia_resumen,
        "experiencia_detallada": experiencia_detallada,
        "entrevista_publica": entrevista_publica,
        "entrevista_legacy": entrevista_legacy,
        "entrevista_struct": entrevista_struct,
        "anios_experiencia": f"{anios} años",
        "orden_lista": rnd.randint(1, 300),
        "perfil_bytes": img_bytes,
    }


def _find_seed_candidates() -> list[Candidata]:
    return (
        Candidata.query
        .filter(Candidata.codigo.like(f"{SEED_PREFIX}%"))
        .order_by(Candidata.codigo.asc())
        .all()
    )


def _delete_seed_candidates() -> int:
    seeds = _find_seed_candidates()
    if not seeds:
        return 0
    ids = [int(row.fila) for row in seeds]
    ent_ids = [int(e.id) for e in Entrevista.query.filter(Entrevista.candidata_id.in_(ids)).all()]
    if ent_ids:
        EntrevistaRespuesta.query.filter(EntrevistaRespuesta.entrevista_id.in_(ent_ids)).delete(synchronize_session=False)
        Entrevista.query.filter(Entrevista.id.in_(ent_ids)).delete(synchronize_session=False)
    CandidataWeb.query.filter(CandidataWeb.candidata_id.in_(ids)).delete(synchronize_session=False)
    Candidata.query.filter(Candidata.fila.in_(ids)).delete(synchronize_session=False)
    db.session.flush()
    return len(ids)


def _upsert_private_catalog(token: str) -> CatalogoPrivado:
    token_hash = _token_hash(token)
    row = CatalogoPrivado.query.filter_by(token_hash=token_hash).first()
    if row is None:
        row = CatalogoPrivado(
            nombre="Catálogo privado QA tienda local",
            descripcion="SEED LOCAL QA - all_available_store",
            token_hash=token_hash,
            token_hint=token[-12:],
            scope_mode="all_available_store",
            is_active=True,
            expires_at=utc_now_naive() + timedelta(days=7),
            created_by="seed-local-qa",
        )
        db.session.add(row)
    else:
        row.nombre = "Catálogo privado QA tienda local"
        row.descripcion = "SEED LOCAL QA - all_available_store"
        row.scope_mode = "all_available_store"
        row.is_active = True
        row.expires_at = utc_now_naive() + timedelta(days=7)
    db.session.flush()
    return row


def run_seed(*, reset: bool = False, total: int = SEED_TOTAL, token: str = DEFAULT_TOKEN) -> SeedResult:
    _assert_local_safety()

    existing = _find_seed_candidates()
    skipped = False

    if existing and not reset:
        skipped = True
        _upsert_private_catalog(token)
        db.session.commit()
        return SeedResult(created=0, skipped_existing=skipped, db_url=_db_url_text(), token=token)

    if reset:
        _delete_seed_candidates()
    preguntas, question_ids = _upsert_questions()

    for idx in range(1, total + 1):
        p = _build_candidate_payload(idx)
        p["entrevista_struct"] = _structured_interview_answers(
            {
                "nombre": p["nombre"],
                "edad": p["edad"],
                "ciudad": p["ciudad"],
                "sector": p["sector"],
                "modalidad": p["modalidad"],
                "disponible_inmediato": p["disponible_inmediato"],
                "tags": p["tags"],
            },
            idx,
            question_ids=question_ids,
        )
        c = Candidata(
            nombre_completo=p["nombre"],
            edad=p["edad"],
            numero_telefono="0000000000",
            direccion_completa="LOCAL TEST ONLY",
            modalidad_trabajo_preferida=p["modalidad"],
            anos_experiencia=p["anios_experiencia"],
            areas_experiencia=p["tags"],
            contactos_referencias_laborales="LOCAL TEST ONLY",
            referencias_familiares_detalle="LOCAL TEST ONLY",
            cedula=p["cedula"],
            codigo=p["codigo"],
            medio_inscripcion="seed_local",
            origen_registro="interno",
            creado_por_staff="seed-local-qa",
            creado_desde_ruta="scripts/local/seed_private_store_candidates.py",
            perfil=p["perfil_bytes"],
            foto_perfil=p["perfil_bytes"],
            disponibilidad_inicio="Inmediata" if p["disponible_inmediato"] else "1-2 semanas",
            puede_dormir_fuera=(p["modalidad"] == "Con dormida"),
            sueldo_esperado=p["sueldo_texto"],
            motivacion_trabajo="LOCAL TEST ONLY",
            estado="lista_para_trabajar",
            entrevista=p["entrevista_legacy"],
        )
        db.session.add(c)
        db.session.flush()

        ent = Entrevista(candidata_id=int(c.fila), tipo="domestica", estado="completa")
        db.session.add(ent)
        db.session.flush()
        for clave, respuesta in (p["entrevista_struct"] or {}).items():
            q = preguntas.get(clave)
            if q is None:
                continue
            db.session.add(
                EntrevistaRespuesta(
                    entrevista_id=int(ent.id),
                    pregunta_id=int(q.id),
                    respuesta=str(respuesta).strip()[:1500],
                )
            )

        web = CandidataWeb(
            candidata_id=c.fila,
            visible=p["visible"],
            estado_publico=p["estado_publico"],
            es_destacada=(idx % 11 == 0),
            orden_lista=p["orden_lista"],
            nombre_publico=f"{p['nombre']} ({p['codigo']})",
            edad_publica=p["edad_publica"],
            ciudad_publica=p["ciudad"],
            sector_publico=p["sector"],
            modalidad_publica=p["modalidad"],
            tipo_servicio_publico="DOMESTICA",
            anos_experiencia_publicos=p["anios_experiencia"],
            experiencia_resumen=p["experiencia_resumen"],
            experiencia_detallada=p["experiencia_detallada"],
            entrevista_publica_resumen=p["entrevista_publica"],
            tags_publicos=p["tags"],
            frase_destacada="Perfil verificado para tienda privada local.",
            sueldo_desde=p["sueldo_desde"],
            sueldo_hasta=p["sueldo_hasta"],
            sueldo_texto_publico=p["sueldo_texto"],
            disponible_inmediato=p["disponible_inmediato"],
        )
        db.session.add(web)

    _upsert_private_catalog(token)
    db.session.commit()
    return SeedResult(created=total, skipped_existing=False, db_url=_db_url_text(), token=token)


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed local para tienda privada")
    parser.add_argument("--reset", action="store_true", help="Borra solo candidatas seed QA-TIENDA- y recrea")
    args = parser.parse_args()

    with flask_app.app_context():
        result = run_seed(reset=bool(args.reset), total=SEED_TOTAL, token=DEFAULT_TOKEN)

    print("SEED LOCAL DE TIENDA PRIVADA")
    print(f"cantidad creada: {result.created}")
    print(f"base de datos usada: {result.db_url}")
    if result.skipped_existing:
        print("seed existente detectado: no se duplicó (usa --reset para recrear)")
    print(f"URL local: http://127.0.0.1:5001/tienda/{result.token}")
    print("URL admin: http://127.0.0.1:5001/admin/tienda-intereses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
