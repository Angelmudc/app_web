# clientes/forms.py

from flask import request
from flask_wtf import FlaskForm
from wtforms import (
    StringField, PasswordField, SelectField, SelectMultipleField,
    TextAreaField, BooleanField, IntegerField, FloatField, SubmitField, HiddenField
)
from wtforms.validators import (
    DataRequired, Length, NumberRange, Optional, ValidationError, Email
)
from wtforms.widgets import ListWidget, CheckboxInput
# ─────────────────────────────────────────────────────────────
# Validaciones estrictas de seguridad (anti símbolos raros)
# ─────────────────────────────────────────────────────────────
import re
from utils.modalidad import canonicalize_modalidad_trabajo

def _solo_texto(valor):
    """
    Valida texto de forma segura sin bloquear entradas reales del usuario.
    Permite letras, números y signos comunes de dirección/horario.
    """
    if not valor:
        return valor
    patron = r'^[A-Za-zÁÉÍÓÚáéíóúÑñ0-9\s.,:/()#&+\-]+$'
    if not re.fullmatch(patron, valor.strip()):
        raise ValidationError("Contiene caracteres no permitidos.")
    return valor

def _solo_numeros(valor):
    """
    Permite únicamente números enteros positivos.
    """
    if valor is None:
        return valor
    if not re.fullmatch(r'^\d+$', str(valor)):
        raise ValidationError("Solo se permiten números, sin símbolos ni letras.")
    return valor
# ─────────────────────────────────────────────────────────────
# Utilidades
# ─────────────────────────────────────────────────────────────
def _strip(v):
    return v.strip() if isinstance(v, str) else v

def _strip_lower(v):
    return v.strip().lower() if isinstance(v, str) else v

STRIP = [_strip]
STRIP_LOWER = [_strip_lower]

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
        "Usuario, Email o Código",
        validators=[DataRequired("Ingresa tu usuario, correo o código."), Length(min=3, max=120)],
        filters=STRIP_LOWER,
        render_kw={
            "placeholder": "Tu usuario, correo o código",
            "autocomplete": "username"
        }
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
        render_kw={"placeholder": "Ej. Santiago / Los Jardines", "inputmode": "text", "autocomplete": "address-level2"}
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
        render_kw={"placeholder": "Ej. Niñera, cocina, planchado… (máx. 500)", "maxlength": 500, "inputmode": "text"}
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
            ("planchar", "Planchar"),
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
        validators=[Optional(), NumberRange(min=0)],
        render_kw={"min": 0}
    )
    edades_ninos = StringField(
        "Edades de los niños",
        validators=[Optional(), Length(max=120)],
        filters=STRIP,
        render_kw={"placeholder": "Ej. 2 y 6 años"}
    )

    # Mascota (NUEVO)
    mascota = StringField(
        "Mascota",
        validators=[Optional(), Length(max=100)],
        filters=STRIP,
        render_kw={"placeholder": "Ej. Perro, Gato"}
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

    @staticmethod
    def _has_limpieza_selected() -> bool:
        try:
            raw = request.form.getlist('funciones')
        except Exception:
            raw = []
        vals = [str(x).strip().lower() for x in (raw or []) if str(x).strip()]
        return 'limpieza' in vals

    def validate(self, extra_validators=None):
        requiere_limpieza = self._has_limpieza_selected()
        if not requiere_limpieza:
            def _strip_required(validators):
                return [
                    v for v in (validators or [])
                    if not isinstance(v, (DataRequired, NumberRange))
                ]
            if hasattr(self, 'tipo_lugar'):
                self.tipo_lugar.validators = _strip_required(self.tipo_lugar.validators)
            if hasattr(self, 'habitaciones'):
                self.habitaciones.validators = _strip_required(self.habitaciones.validators)
            if hasattr(self, 'banos'):
                self.banos.validators = _strip_required(self.banos.validators)
            if hasattr(self, 'areas_comunes'):
                self.areas_comunes.validators = _strip_required(self.areas_comunes.validators)

        ok = super().validate(extra_validators=extra_validators)
        if not requiere_limpieza:
            for fname in ('tipo_lugar', 'habitaciones', 'banos', 'areas_comunes'):
                field = getattr(self, fname, None)
                if not field:
                    continue
                try:
                    field.errors = []
                except Exception:
                    pass
                try:
                    field.process_errors = []
                except Exception:
                    pass
            ok = all(not f.errors for f in self._fields.values())
        modalidad_group = ""
        modalidad_specific = ""
        try:
            modalidad_group = str((request.form or {}).get("modalidad_grupo") or "").strip()
            modalidad_specific = str((request.form or {}).get("modalidad_especifica") or "").strip()
        except Exception:
            modalidad_group = ""
            modalidad_specific = ""

        def _append_modalidad_error(msg: str):
            if msg not in (self.modalidad_trabajo.errors or []):
                self.modalidad_trabajo.errors.append(msg)

        funciones = self.funciones.data or []

        try:
            ninos_cnt = int(self.ninos.data) if self.ninos.data is not None else 0
        except Exception:
            ninos_cnt = 0

        if 'ninos' in funciones and self.ninos.data is None:
            self.ninos.errors.append("Indica cuántos niños.")
            ok = False

        if 'ninos' in funciones and ninos_cnt > 0:
            if not (self.edades_ninos.data and str(self.edades_ninos.data).strip()):
                self.edades_ninos.errors.append("Debes indicar las edades de los niños.")
                ok = False

        if not modalidad_group:
            _append_modalidad_error("Selecciona la modalidad de trabajo.")
            ok = False

        if not modalidad_specific:
            _append_modalidad_error("Selecciona la modalidad específica.")
            ok = False

        return ok

    # ───────── Validaciones cruzadas (para 'Otro') ─────────
    def validate_ciudad_sector(self, field):
        _solo_texto(field.data)

    def validate_experiencia(self, field):
        if field.data and len((field.data or '').strip()) < 5:
            raise ValidationError("Describe un poco más la experiencia requerida.")

    def validate_edad_requerida(self, field):
        data = field.data or []
        if 'otro' in data and not (self.edad_otro.data and self.edad_otro.data.strip()):
            raise ValidationError("Especifica la edad cuando marcas 'Otro'.")

    def validate_funciones(self, field):
        data = field.data or []
        if 'otro' in data and not (self.funciones_otro.data and self.funciones_otro.data.strip()):
            raise ValidationError("Especifica la función cuando marcas 'Otro'.")

    def validate_tipo_lugar(self, field):
        if not self._has_limpieza_selected():
            return
        if field.data == 'otro' and not (self.tipo_lugar_otro.data and self.tipo_lugar_otro.data.strip()):
            raise ValidationError("Especifica el tipo de lugar cuando marcas 'Otro'.")

    def validate_modalidad_trabajo(self, field):
        field.data = canonicalize_modalidad_trabajo(field.data)
        _solo_texto((field.data or '').replace('💤', ''))

    def validate_horario(self, field):
        # Permite letras, números y espacios (horario necesita números)
        if field.data:
            if not re.fullmatch(r'^[A-Za-zÁÉÍÓÚáéíóúÑñ0-9\s:–\-/.,]+$', field.data.strip()):
                raise ValidationError("Horario inválido. Solo texto y números.")

    def validate_funciones_otro(self, field):
        if field.data:
            _solo_texto(field.data)

    def validate_area_otro(self, field):
        if field.data:
            _solo_texto(field.data)

    def validate_tipo_lugar_otro(self, field):
        if field.data:
            _solo_texto(field.data)

    def validate_mascota(self, field):
        if field.data:
            _solo_texto(field.data)

    def validate_nota_cliente(self, field):
        if field.data:
            # Nota libre con puntuación común
            if not re.fullmatch(r'^[A-Za-zÁÉÍÓÚáéíóúÑñ0-9\s,.:;()#&+\-_/!?]+$', field.data.strip()):
                raise ValidationError("La nota contiene caracteres no permitidos.")

    def validate_sueldo(self, field):
        if field.data:
            raw = str(field.data).strip()
            clean = raw.replace('RD$', '').replace('$', '').replace(',', '').replace('.', '').strip()
            if not re.fullmatch(r'^\d+$', clean):
                raise ValidationError("Sueldo inválido. Usa solo números (ej: 18000 o 18,000).")

    def validate_adultos(self, field):
        _solo_numeros(field.data)

    def validate_ninos(self, field):
        _solo_numeros(field.data)

    def validate_habitaciones(self, field):
        _solo_numeros(field.data)

    def validate_banos(self, field):
        # Permite números y punto decimal
        if field.data is not None:
            if not re.fullmatch(r'^\d+(\.\d+)?$', str(field.data)):
                raise ValidationError("Cantidad de baños inválida.")

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


class SolicitudClienteNuevoPublicaForm(SolicitudForm):
    nombre_completo = StringField(
        "Nombre completo",
        validators=[DataRequired("Ingresa tu nombre completo."), Length(min=3, max=200)],
        filters=STRIP,
        render_kw={"placeholder": "Ej. Maria Perez"}
    )
    email_contacto = StringField(
        "Correo electrónico / Gmail",
        validators=[DataRequired("Ingresa un correo electrónico."), Email("Correo inválido."), Length(max=100)],
        filters=STRIP_LOWER,
        render_kw={"placeholder": "nombre@gmail.com", "autocomplete": "email"}
    )
    telefono_contacto = StringField(
        "Número de teléfono",
        validators=[DataRequired("Ingresa un número de teléfono."), Length(min=7, max=20)],
        filters=STRIP,
        render_kw={"placeholder": "809-000-0000", "autocomplete": "tel"}
    )
    ciudad_cliente = StringField(
        "Ciudad",
        validators=[DataRequired("Indica la ciudad."), Length(min=2, max=100)],
        filters=STRIP,
        render_kw={"placeholder": "Ej. Santiago"}
    )
    sector_cliente = StringField(
        "Sector",
        validators=[DataRequired("Indica el sector."), Length(min=2, max=100)],
        filters=STRIP,
        render_kw={"placeholder": "Ej. Los Jardines"}
    )

    # Anti-bot: debe venir vacío
    hp = StringField("No llenar", validators=[Optional(), Length(max=10)])

    def validate_nombre_completo(self, field):
        _solo_texto(field.data)

    def validate_ciudad_cliente(self, field):
        _solo_texto(field.data)

    def validate_sector_cliente(self, field):
        _solo_texto(field.data)

    def validate_telefono_contacto(self, field):
        raw = (field.data or "").strip()
        if not re.fullmatch(r"^[0-9+\-\s()]{7,20}$", raw):
            raise ValidationError("Teléfono inválido. Usa solo números y símbolos comunes.")
