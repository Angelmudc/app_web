# clientes/forms.py

from flask_wtf import FlaskForm
from wtforms import (
    StringField, PasswordField, SelectField, SelectMultipleField,
    TextAreaField, BooleanField, IntegerField, FloatField, SubmitField
)
from wtforms.validators import (
    DataRequired, Length, NumberRange, Optional, ValidationError
)
from wtforms.widgets import ListWidget, CheckboxInput
# en clientes/forms.py
from wtforms import StringField, HiddenField
from wtforms.validators import DataRequired, Email
# ─────────────────────────────────────────────────────────────
# Utilidades
# ─────────────────────────────────────────────────────────────
def _strip(v):
    return v.strip() if isinstance(v, str) else v

STRIP = [_strip]

# ─────────────────────────────────────────────────────────────
# Intentar obtener las opciones centralizadas; si no, usar fallback
# Mantén los valores (keys) ESTABLES para que coincidan con la BD/UI.
# ─────────────────────────────────────────────────────────────
try:
    # Debe existir en admin.routes: AREAS_COMUNES_CHOICES = [(value,label), ...]
    from admin.routes import AREAS_COMUNES_CHOICES  # evita duplicar opciones
except Exception:
    AREAS_COMUNES_CHOICES = [
        ('sala', 'Sala'),
        ('comedor', 'Comedor'),
        ('cocina', 'Cocina'),
        ('salon_juegos', 'Salón de juegos'),
        ('terraza', 'Terraza'),
        ('jardin', 'Jardín'),
        ('estudio', 'Estudio'),
        ('patio', 'Patio'),
        ('piscina', 'Piscina'),
        ('marquesina', 'Marquesina'),
        ('todas_anteriores', 'Todas las anteriores'),
        ('otro', 'Otro'),
    ]

# ─────────────────────────────────────────────────────────────
# Login
# ─────────────────────────────────────────────────────────────
class ClienteLoginForm(FlaskForm):
    username = StringField(
        "Usuario o Email",
        validators=[DataRequired("Ingresa tu usuario o correo."), Length(min=3, max=120)],
        filters=STRIP,
        render_kw={"placeholder": "Tu usuario o correo", "autocomplete": "username"}
    )
    password = PasswordField(
        "Contraseña",
        validators=[DataRequired("Ingresa tu contraseña."), Length(min=6, max=128)],
        render_kw={"placeholder": "Tu contraseña", "autocomplete": "current-password"}
    )
    remember_me = BooleanField("Recordarme")
    submit = SubmitField("Ingresar")

# ─────────────────────────────────────────────────────────────
# Cancelación
# ─────────────────────────────────────────────────────────────
class ClienteCancelForm(FlaskForm):
    motivo = TextAreaField(
        "Motivo de cancelación",
        validators=[DataRequired("Indica el motivo."), Length(min=5, max=1000)],
        filters=STRIP,
        render_kw={"placeholder": "Explica el motivo de la cancelación"}
    )
    submit = SubmitField("Confirmar cancelación")

# ─────────────────────────────────────────────────────────────
# Solicitud Cliente simple
# ─────────────────────────────────────────────────────────────
class ClienteSolicitudForm(FlaskForm):
    areas_comunes = SelectMultipleField(
        "Áreas comunes",
        choices=AREAS_COMUNES_CHOICES,
        validators=[DataRequired("Selecciona al menos un área común.")],
        option_widget=CheckboxInput(),
        widget=ListWidget(prefix_label=False),
        coerce=str
    )
    area_otro = StringField(
        "Otra área (si aplica)",
        validators=[Optional(), Length(max=100)],
        filters=STRIP,
        render_kw={"placeholder": "Especifica otra área"}
    )
    detalles = TextAreaField(
        "Detalle adicional",
        validators=[DataRequired("Describe algún detalle adicional."), Length(min=5)],
        filters=STRIP,
        render_kw={"placeholder": "Información adicional relevante"}
    )
    submit = SubmitField("Guardar")

# ─────────────────────────────────────────────────────────────
# Formulario completo de Solicitud
# ─────────────────────────────────────────────────────────────
class SolicitudForm(FlaskForm):
    # Ubicación
    ciudad_sector = StringField(
        "Ciudad / Sector",
        validators=[DataRequired("Indica ciudad y sector.")],
        filters=STRIP,
        render_kw={"placeholder": "Ej. Santiago / Los Jardines"}
    )
    rutas_cercanas = StringField(
        "Rutas de transporte cercanas",
        validators=[Optional(), Length(max=200)],
        filters=STRIP,
        render_kw={"placeholder": "Ej. Ruta K, Av. 27 Febrero (opcional)"}
    )

    # Detalles
    modalidad_trabajo = StringField(
        "Modalidad de trabajo",
        validators=[DataRequired("Indica la modalidad de trabajo."), Length(max=100)],
        filters=STRIP,
        render_kw={"placeholder": "Ej. Dormida / Salida diaria"}
    )

    # OJO: valores EXACTOS como se guardan en la BD / rutas
    edad_requerida = SelectMultipleField(
        "Edad del personal",
        choices=[
            ("18-25", "18-25"),
            ("26-35", "26-35"),
            ("25 en adelante", "25 en adelante"),
            ("Mayor de 45", "Mayor de 45"),
            ("otro", "Otro"),
        ],
        validators=[DataRequired("Selecciona al menos una franja de edad.")],
        option_widget=CheckboxInput(),
        widget=ListWidget(prefix_label=False),
        coerce=str
    )
    edad_otro = StringField(
        "Especifica la edad (si marcaste Otro)",
        validators=[Optional(), Length(max=100)],
        filters=STRIP,
        render_kw={"placeholder": "Ej. 30 a 40, 20-30, etc."}
    )

    experiencia = TextAreaField(
        "Tipo de experiencia requerida",
        validators=[DataRequired("Describe la experiencia requerida."), Length(min=5, max=500)],
        filters=STRIP,
        render_kw={"placeholder": "Ej. Niñera, cocina, planchado… (máx. 500)", "maxlength": 500}
    )

    horario = StringField(
        "Defina el horario de trabajo",
        validators=[DataRequired("Indica el horario."), Length(max=100)],
        filters=STRIP,
        render_kw={"placeholder": "Ej. L–V 8:00 a 17:00"}
    )

    funciones = SelectMultipleField(
        "Funciones a realizar al personal",
        choices=[
            ("limpieza", "Limpieza General"),
            ("cocinar", "Cocinar"),
            ("lavar", "Lavar"),
            ("ninos", "Cuidar Niños"),
            ("envejeciente", "Cuidar envejecientes"),
            ("otro", "Otro"),
        ],
        validators=[DataRequired("Selecciona al menos una función.")],
        option_widget=CheckboxInput(),
        widget=ListWidget(prefix_label=False),
        coerce=str
    )
    funciones_otro = StringField(
        "Otra función (si marcaste Otro)",
        validators=[Optional(), Length(max=100)],
        filters=STRIP,
        render_kw={"placeholder": "Una o varias, separadas por coma"}
    )

    # Tipo de lugar
    tipo_lugar = SelectField(
        "Tipo de lugar",
        choices=[
            ("casa", "Casa"),
            ("oficina", "Oficina"),
            ("apto", "Apartamento"),
            ("otro", "Otro"),
        ],
        validators=[DataRequired("Selecciona el tipo de lugar.")],
        coerce=str
    )
    tipo_lugar_otro = StringField(
        "Especifica el tipo de lugar (si marcaste Otro)",
        validators=[Optional(), Length(max=100)],
        filters=STRIP,
        render_kw={"placeholder": "Ej. Local comercial, villa, etc."}
    )

    # Inmueble
    habitaciones = IntegerField(
        "Habitaciones",
        validators=[DataRequired("Indica cuántas habitaciones."), NumberRange(min=0)],
        render_kw={"min": 0}
    )
    banos = FloatField(
        "Baños",
        validators=[DataRequired("Indica la cantidad de baños."), NumberRange(min=0)],
        render_kw={"min": 0, "step": "0.5"}
    )
    dos_pisos = BooleanField("Dos pisos")

    # Ocupantes
    adultos = IntegerField(
        "Cantidad de adultos",
        validators=[DataRequired("Indica cuántos adultos."), NumberRange(min=0)],
        render_kw={"min": 0}
    )
    ninos = IntegerField(
        "Cantidad de niños",
        validators=[DataRequired("Indica cuántos niños."), NumberRange(min=0)],
        render_kw={"min": 0}
    )
    edades_ninos = StringField(
        "Edades de los niños",
        validators=[Optional(), Length(max=120)],
        filters=STRIP,
        render_kw={"placeholder": "Ej. 2 y 6 años (opcional)"}
    )

    # Mascota (NUEVO)
    mascota = StringField(
        "Mascota",
        validators=[Optional(), Length(max=100)],
        filters=STRIP,
        render_kw={"placeholder": "Ej. Perro, Gato (opcional)"}
    )

    # Compensación
    sueldo = StringField(
        "Sueldo a pagar",
        validators=[DataRequired("Indica el sueldo."), Length(max=100)],
        filters=STRIP,
        render_kw={"placeholder": "Ej. 18,000"}
    )
    pasaje_aporte = BooleanField("Pasaje aporta")

    # Nota
    nota_cliente = TextAreaField(
        "Nota adicional",
        validators=[Optional(), Length(max=1000)],
        filters=STRIP,
        render_kw={"placeholder": "Información útil para el proceso (opcional)", "maxlength": 1000}
    )

    # Áreas comunes (alineadas con Admin)
    areas_comunes = SelectMultipleField(
        "Áreas comunes",
        choices=AREAS_COMUNES_CHOICES,
        validators=[DataRequired("Selecciona al menos un área común.")],
        option_widget=CheckboxInput(),
        widget=ListWidget(prefix_label=False),
        coerce=str
    )
    area_otro = StringField(
        "Otra área",
        validators=[Optional(), Length(max=100)],
        filters=STRIP,
        render_kw={"placeholder": "Especifica otra área (opcional)"}
    )

    submit = SubmitField("Enviar")

    # ───────── Validaciones cruzadas (para 'Otro') ─────────
    def validate_edad_requerida(self, field):
        data = field.data or []
        if 'otro' in data and not (self.edad_otro.data and self.edad_otro.data.strip()):
            raise ValidationError("Especifica la edad cuando marcas 'Otro'.")

    def validate_funciones(self, field):
        data = field.data or []
        if 'otro' in data and not (self.funciones_otro.data and self.funciones_otro.data.strip()):
            raise ValidationError("Especifica la función cuando marcas 'Otro'.")

    def validate_tipo_lugar(self, field):
        if field.data == 'otro' and not (self.tipo_lugar_otro.data and self.tipo_lugar_otro.data.strip()):
            raise ValidationError("Especifica el tipo de lugar cuando marcas 'Otro'.")

# IMPORTANTE: esto asume que ya existe SolicitudForm en este mismo archivo.
class SolicitudPublicaForm(SolicitudForm):
    token = HiddenField(validators=[DataRequired()])

    codigo_cliente = StringField(
        "Código del cliente",
        validators=[DataRequired(), Length(min=3, max=20)]
    )
    nombre_cliente = StringField(
        "Nombre completo",
        validators=[DataRequired(), Length(min=2, max=200)]
    )
    email_cliente = StringField(
        "Gmail / Email",
        validators=[DataRequired(), Email(), Length(max=100)]
    )

    # Anti-bot: debe venir vacío
    hp = StringField("No llenar", validators=[Optional(), Length(max=10)])
