from flask import current_app, flash, redirect, render_template, request, url_for

from decorators import roles_required
from models import Candidata
from core.services.search import apply_search_to_candidata_query


def _build_docs_flags(c):
    if not c:
        return {
            "depuracion": False,
            "perfil": False,
            "cedula1": False,
            "cedula2": False,
            "entrevista": "",
        }

    return {
        "depuracion": bool(getattr(c, "depuracion", None)),
        "perfil": bool(getattr(c, "perfil", None)),
        "cedula1": bool(getattr(c, "cedula1", None)),
        "cedula2": bool(getattr(c, "cedula2", None)),
        "entrevista": (getattr(c, "entrevista", "") or "").strip(),
    }


@roles_required("admin", "secretaria")
def gestionar_archivos():
    accion = (request.args.get("accion") or "buscar").strip().lower()
    mensaje = None
    resultados = []
    docs = {}
    fila = (request.args.get("fila") or "").strip()

    if accion == "descargar":
        doc = (request.args.get("doc") or "").strip().lower()
        if not fila.isdigit():
            return "Error: Fila inválida", 400
        idx = int(fila)

        if doc == "pdf":
            return redirect(url_for("generar_pdf_entrevista", fila=idx))

        return "Documento no reconocido", 400

    if accion == "buscar":
        if request.method == "POST":
            q = (request.form.get("busqueda") or "").strip()[:128]
            if not q:
                flash("⚠️ Ingresa algo para buscar.", "warning")
                return redirect(url_for("gestionar_archivos", accion="buscar"))

            try:
                filas = (
                    apply_search_to_candidata_query(Candidata.query, q)
                    .order_by(Candidata.nombre_completo.asc())
                    .limit(300)
                    .all()
                )
            except Exception:
                current_app.logger.exception("❌ Error buscando en gestionar_archivos")
                filas = []

            if filas:
                resultados = [
                    {
                        "fila": c.fila,
                        "nombre": c.nombre_completo,
                        "telefono": c.numero_telefono or "No especificado",
                        "cedula": c.cedula or "No especificado",
                    }
                    for c in filas
                ]
            else:
                mensaje = "⚠️ No se encontraron candidatas."

        return render_template(
            "gestionar_archivos.html",
            accion="buscar",
            resultados=resultados,
            mensaje=mensaje,
        )

    if accion == "ver":
        if not fila.isdigit():
            mensaje = "Error: Fila inválida."
            return render_template("gestionar_archivos.html", accion="buscar", mensaje=mensaje)

        idx = int(fila)
        c = Candidata.query.filter_by(fila=idx).first()
        if not c:
            mensaje = "⚠️ Candidata no encontrada."
            return render_template("gestionar_archivos.html", accion="buscar", mensaje=mensaje)

        docs = _build_docs_flags(c)

        return render_template(
            "gestionar_archivos.html",
            accion="ver",
            fila=idx,
            docs=docs,
            mensaje=mensaje,
        )

    return redirect(url_for("gestionar_archivos", accion="buscar"))
