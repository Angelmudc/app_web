# app_web/public/routes.py

import base64
from flask import render_template, abort, request, redirect
from . import public_bp

# IMPORTA TU MODELO DE CANDIDATA
# IMPORTA TUS MODELOS
try:
    from models import Candidata, CandidataWeb
except ImportError:
    from app.models import Candidata, CandidataWeb



# 🔌 SWITCH GENERAL: WEB PÚBLICA HABILITADA / DESHABILITADA
PUBLIC_SITE_ENABLED = True
# Cuando quieras volver a activarla en el futuro, solo cambia a:
# PUBLIC_SITE_ENABLED = True


def _foto_data_uri(candidata) -> str | None:
    """
    Convierte el campo LargeBinary 'perfil' en un data URI para usarlo en <img>.
    Si no hay foto, devuelve None.
    """
    raw = getattr(candidata, "perfil", None)
    if not raw:
        return None
    try:
        b64 = base64.b64encode(raw).decode("utf-8")
        # Cambia a image/png si tus fotos son PNG
        return f"data:image/jpeg;base64,{b64}"
    except Exception:
        return None


@public_bp.route("/")
def index():
    """
    Raíz del sitio.

    - Si la web pública está deshabilitada:
        👉 redirige al login interno (/login).
    - Si la web pública está habilitada:
        👉 muestra el landing público normal.
    """
    if not PUBLIC_SITE_ENABLED:
        # 🔴 Cambia "/login" por "/home" o la ruta que uses como inicio de sesión
        return redirect("/login")

    return render_template("public/index.html")


@public_bp.route("/servicios")
def servicios():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("public/servicios.html")


@public_bp.route("/sobre-nosotros")
def sobre_nosotros():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("public/sobre_nosotros.html")


@public_bp.route("/contacto")
def contacto():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("public/contacto.html")


@public_bp.route("/faq")
def faq():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("public/faq.html")


@public_bp.route("/gracias")
def gracias():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("public/gracias.html")


@public_bp.route("/domesticas")
def domesticas():
    """
    Lista pública de domésticas con paginación.

    - Solo muestra candidatas que tengan ficha web.
    - Deben estar visibles y en estado 'disponible'.
    - Usa los textos públicos de CandidataWeb.
    - La foto sigue viniendo del perfil binario de Candidata.
    """
    if not PUBLIC_SITE_ENABLED:
        abort(404)

    page = request.args.get("page", 1, type=int)
    per_page = 9

    # Base: candidatas con ficha web visible y disponible
    query = (
        CandidataWeb.query
        .join(Candidata, CandidataWeb.candidata_id == Candidata.fila)
        .filter(CandidataWeb.visible.is_(True))
        .filter(CandidataWeb.estado_publico == 'disponible')
        # Orden: primero orden manual, luego fecha de publicación
        .order_by(
            CandidataWeb.orden_lista.asc(),
            CandidataWeb.fecha_publicacion.desc()
        )
        .add_entity(Candidata)  # devolvemos (ficha_web, candidata)
    )

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    filas = pagination.items  # [(ficha_web, candidata), ...]

    domesticas_data = []
    for ficha, c in filas:
        # PK para la URL de detalle (usa id o fila de Candidata)
        pk = getattr(c, "id", None) or getattr(c, "fila", None)

        # Nombre público (si no, nombre real)
        nombre = (
            ficha.nombre_publico
            or getattr(c, "nombre_completo", None)
            or getattr(c, "nombre", None)
            or "Candidata"
        )

        # Edad (texto público primero)
        edad = ficha.edad_publica or getattr(c, "edad", None)

        # Modalidad visible
        modalidad = (
            ficha.modalidad_publica
            or getattr(c, "modalidad_trabajo_preferida", None)
            or getattr(c, "modalidad", None)
            or ""
        )

        # Experiencia (resumen público primero)
        experiencia = (
            ficha.experiencia_resumen
            or getattr(c, "areas_experiencia", None)
            or getattr(c, "empleo_anterior", None)
            or ""
        )

        # Código real interno (seguimos usando el de Candidata)
        codigo = getattr(c, "codigo", None)

        # Foto: se mantiene igual, desde Candidata.perfil
        foto = _foto_data_uri(c)

        domesticas_data.append({
            "pk": pk,
            "codigo": codigo,
            "nombre": nombre,
            "edad": edad,
            "modalidad": modalidad,
            "experiencia": experiencia,
            "foto": foto,

            # Extras por si luego los usas en el template
            "ciudad": ficha.ciudad_publica,
            "sector": ficha.sector_publico,
            "sueldo": ficha.sueldo_texto_publico,
            "sueldo_desde": ficha.sueldo_desde,
            "sueldo_hasta": ficha.sueldo_hasta,
            "destacada": ficha.es_destacada,
            "disponible_inmediato": ficha.disponible_inmediato,
            "frase_destacada": ficha.frase_destacada,
            "tags": ficha.tags_publicos,
        })

    return render_template(
        "public/domesticas.html",
        domesticas=domesticas_data,
        pagination=pagination,
    )


@public_bp.route("/domesticas/<int:candidata_pk>")
def detalle_domestica(candidata_pk):
    """
    Detalle de una doméstica específica (vista pública).

    - Carga la candidata interna por PK (id o fila, según el modelo).
    - Usa su ficha web (CandidataWeb) para los textos públicos.
    - La foto sigue viniendo del perfil binario de Candidata.
    """
    if not PUBLIC_SITE_ENABLED:
        abort(404)

    # Candidata interna
    c = Candidata.query.get_or_404(candidata_pk)

    # Ficha web 1–1
    ficha = getattr(c, "ficha_web", None)

    # Si no tiene ficha web o no está visible/disponible, no se muestra
    if not ficha or not ficha.visible or ficha.estado_publico != 'disponible':
        abort(404)

    # Nombre / edad / modalidad / experiencia desde la ficha web
    nombre = (
        ficha.nombre_publico
        or getattr(c, "nombre_completo", None)
        or getattr(c, "nombre", None)
        or "Candidata"
    )

    edad = ficha.edad_publica or getattr(c, "edad", None)

    modalidad = (
        ficha.modalidad_publica
        or getattr(c, "modalidad_trabajo_preferida", None)
        or getattr(c, "modalidad", None)
        or ""
    )

    experiencia_resumen = (
        ficha.experiencia_resumen
        or getattr(c, "areas_experiencia", None)
        or getattr(c, "empleo_anterior", None)
        or ""
    )

    experiencia_detallada = ficha.experiencia_detallada or ""

    codigo = getattr(c, "codigo", None)  # código interno real

    # Foto: se mantiene solo desde el perfil binario
    foto = _foto_data_uri(c)

    detalle = {
        "codigo": codigo,
        "nombre": nombre,
        "edad": edad,
        "modalidad": modalidad,
        "experiencia": experiencia_resumen,
        "experiencia_detallada": experiencia_detallada,
        "foto": foto,

        # Extras de la ficha web
        "ciudad": ficha.ciudad_publica,
        "sector": ficha.sector_publico,
        "tipo_servicio": ficha.tipo_servicio_publico,
        "anos_experiencia": ficha.anos_experiencia_publicos,
        "sueldo": ficha.sueldo_texto_publico,
        "sueldo_desde": ficha.sueldo_desde,
        "sueldo_hasta": ficha.sueldo_hasta,
        "disponible_inmediato": ficha.disponible_inmediato,
        "tags": ficha.tags_publicos,
        "frase_destacada": ficha.frase_destacada,
    }

    return render_template("public/detalle_domestica.html", candidata=detalle)

