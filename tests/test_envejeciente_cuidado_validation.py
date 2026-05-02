from werkzeug.datastructures import MultiDict
from flask import Flask

from clientes.forms import SolicitudForm
from admin.forms import AdminSolicitudForm


def _base_payload():
    return MultiDict(
        {
            "ciudad_sector": "Santiago / Centro",
            "rutas_cercanas": "Ruta K",
            "modalidad_trabajo": "Salida diaria - lunes a viernes",
            "horario": "Lunes a viernes, de 8:00 AM a 5:00 PM",
            "modalidad_grupo": "con_salida_diaria",
            "modalidad_especifica": "sd_l_v",
            "horario_dias_trabajo": "Lunes a viernes",
            "horario_hora_entrada": "8:00 AM",
            "horario_hora_salida": "5:00 PM",
            "edad_requerida": "26-35",
            "experiencia": "Experiencia general",
            "funciones": "envejeciente",
            "tipo_lugar": "casa",
            "habitaciones": "2",
            "banos": "1",
            "adultos": "1",
            "ninos": "0",
            "sueldo": "18000",
            "areas_comunes": "sala",
            "pasaje_mode": "incluido",
            "pasaje_aporte": "0",
        }
    )


def _mk_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    app.config["WTF_CSRF_ENABLED"] = False
    return app


def test_cliente_form_envejeciente_requiere_tipo():
    app = _mk_app()
    with app.test_request_context(method="POST", data=_base_payload()):
        form = SolicitudForm(meta={"csrf": False})
        assert not form.validate()
        assert form.envejeciente_tipo_cuidado.errors


def test_cliente_form_encamado_requiere_responsabilidad_o_solo():
    app = _mk_app()
    data = _base_payload()
    data.add("envejeciente_tipo_cuidado", "encamado")
    with app.test_request_context(method="POST", data=data):
        form = SolicitudForm(meta={"csrf": False})
        assert not form.validate()
        assert form.envejeciente_responsabilidades.errors


def test_cliente_form_independiente_valido():
    app = _mk_app()
    data = _base_payload()
    data.add("envejeciente_tipo_cuidado", "independiente")
    with app.test_request_context(method="POST", data=data):
        form = SolicitudForm(meta={"csrf": False})
        assert form.validate(), form.errors


def test_admin_form_encamado_con_solo_acompanamiento_valido():
    app = _mk_app()
    data = _base_payload()
    data.add("tipo_servicio", "DOMESTICA_LIMPIEZA")
    data.add("envejeciente_tipo_cuidado", "encamado")
    data.add("envejeciente_solo_acompanamiento", "y")
    with app.test_request_context(method="POST", data=data):
        form = AdminSolicitudForm(meta={"csrf": False})
        assert form.validate(), form.errors


def test_cliente_form_no_exige_envejeciente_si_funcion_no_marcada():
    app = _mk_app()
    data = _base_payload()
    data.setlist("funciones", ["cocinar"])
    with app.test_request_context(method="POST", data=data):
        form = SolicitudForm(meta={"csrf": False})
        assert form.validate(), form.errors
