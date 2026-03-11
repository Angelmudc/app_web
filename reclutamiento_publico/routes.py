from flask import render_template, abort, redirect, url_for

from models import TIPOS_EMPLEO_GENERAL
from public.routes import PUBLIC_SITE_ENABLED
from . import reclutamiento_publico_bp


@reclutamiento_publico_bp.route("", methods=["GET"], strict_slashes=False)
@reclutamiento_publico_bp.route("/", methods=["GET"])
def inicio():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    vacantes = [x.replace("_", " ").title() for x in TIPOS_EMPLEO_GENERAL if x != "otro"]
    return render_template("reclutamiento/index.html", vacantes=vacantes)


@reclutamiento_publico_bp.route("/proceso", methods=["GET"])
def proceso():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("reclutamiento/proceso.html")


@reclutamiento_publico_bp.route("/requisitos", methods=["GET"])
def requisitos():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("reclutamiento/requisitos.html")


@reclutamiento_publico_bp.route("/beneficios", methods=["GET"])
def beneficios():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("reclutamiento/beneficios.html")


@reclutamiento_publico_bp.route("/faq", methods=["GET"])
def faq():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return render_template("reclutamiento/faq.html")


@reclutamiento_publico_bp.route("/aplicar/domestica", methods=["GET"])
def aplicar_domestica():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return redirect(url_for("registro_publico.registro_publico"))


@reclutamiento_publico_bp.route("/aplicar/empleo-general", methods=["GET"])
def aplicar_empleo_general():
    if not PUBLIC_SITE_ENABLED:
        abort(404)
    return redirect(url_for("reclutas.registro_publico"))
