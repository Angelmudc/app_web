# app_web/public/routes.py

import base64
from flask import render_template, abort, request
from . import public_bp

# IMPORTA TU MODELO DE CANDIDATA
try:
    from models import Candidata
except ImportError:
    from app.models import Candidata


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
    return render_template("public/index.html")


@public_bp.route("/servicios")
def servicios():
    return render_template("public/servicios.html")


@public_bp.route("/sobre-nosotros")
def sobre_nosotros():
    return render_template("public/sobre_nosotros.html")


@public_bp.route("/contacto")
def contacto():
    return render_template("public/contacto.html")


@public_bp.route("/faq")
def faq():
    return render_template("public/faq.html")


@public_bp.route("/gracias")
def gracias():
    return render_template("public/gracias.html")


@public_bp.route("/domesticas")
def domesticas():
    """
    Lista pública de domésticas con paginación.
    NO filtramos por estado (ENUM) para evitar errores.
    No inventamos códigos: usamos solo la columna real `codigo`.
    """
    page = request.args.get("page", 1, type=int)
    per_page = 9

    query = Candidata.query

    # Ordenar (si existe id o fila)
    if hasattr(Candidata, "id"):
        query = query.order_by(Candidata.id.desc())
    elif hasattr(Candidata, "fila"):
        query = query.order_by(Candidata.fila.desc())

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    candidatas = pagination.items

    domesticas_data = []
    for c in candidatas:
        # PK: id o fila
        pk = getattr(c, "id", None)
        if pk is None:
            pk = getattr(c, "fila", None)

        modalidad = (
            getattr(c, "modalidad_trabajo_preferida", None)
            or getattr(c, "modalidad", None)
            or ""
        )
        experiencia = (
            getattr(c, "areas_experiencia", None)
            or getattr(c, "empleo_anterior", None)
            or ""
        )
        edad = getattr(c, "edad", None)

        # ⚠️ AQUÍ: SOLO USAMOS EL CAMPO REAL `codigo`
        codigo = getattr(c, "codigo", None)

        nombre = (
            getattr(c, "nombre_completo", None)
            or getattr(c, "nombre", None)
            or "Candidata"
        )
        foto = _foto_data_uri(c)

        domesticas_data.append({
            "pk": pk,
            "codigo": codigo,
            "nombre": nombre,
            "edad": edad,
            "modalidad": modalidad,
            "experiencia": experiencia,
            "foto": foto,
        })

    return render_template(
        "public/domesticas.html",
        domesticas=domesticas_data,
        pagination=pagination,
    )


@public_bp.route("/domesticas/<int:candidata_pk>")
def detalle_domestica(candidata_pk):
    """
    Detalle de una doméstica específica.
    Usa la primary key (id o fila, según el modelo).
    """
    c = Candidata.query.get(candidata_pk)
    if not c:
        abort(404)

    modalidad = (
        getattr(c, "modalidad_trabajo_preferida", None)
        or getattr(c, "modalidad", None)
        or ""
    )
    experiencia = (
        getattr(c, "areas_experiencia", None)
        or getattr(c, "empleo_anterior", None)
        or ""
    )
    edad = getattr(c, "edad", None)

    codigo = getattr(c, "codigo", None)  # solo la columna real
    nombre = (
        getattr(c, "nombre_completo", None)
        or getattr(c, "nombre", None)
        or "Candidata"
    )
    foto = _foto_data_uri(c)

    detalle = {
        "codigo": codigo,
        "nombre": nombre,
        "edad": edad,
        "modalidad": modalidad,
        "experiencia": experiencia,
        "foto": foto,
    }

    return render_template("public/detalle_domestica.html", candidata=detalle)
