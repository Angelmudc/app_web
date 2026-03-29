from flask import redirect, render_template, request, session, url_for

from decorators import roles_required
from models import Candidata


@roles_required("admin", "secretaria")
def candidatas_porcentaje():
    # Proteger la vista: si no hay usuario logueado, mandar a login
    if "usuario" not in session:
        return redirect(url_for("login"))

    # Página actual (por defecto 1)
    page = request.args.get("page", 1, type=int)
    per_page = 50

    base_query = (
        Candidata.query
        .with_entities(
            Candidata.fila,
            Candidata.codigo,
            Candidata.nombre_completo.label("nombre"),
            Candidata.numero_telefono.label("telefono"),
            Candidata.modalidad_trabajo_preferida.label("modalidad"),
            Candidata.inicio.label("fecha_inicio"),
            Candidata.fecha_de_pago.label("fecha_pago"),
            Candidata.monto_total,
            Candidata.porciento,
        )
        .filter(
            Candidata.porciento.isnot(None),
            Candidata.porciento > 0,
        )
        .order_by(
            Candidata.fecha_de_pago.asc().nullslast(),
            Candidata.fila.asc(),
        )
    )

    pagination = base_query.paginate(page=page, per_page=per_page, error_out=False)
    candidatas = pagination.items

    return render_template(
        "candidatas_porcentaje.html",
        candidatas=candidatas,
        pagination=pagination,
    )
