# -*- coding: utf-8 -*-
"""
Formularios para el panel de Administración.
Incluye:
- Login
- Cliente (CRUD) + creación/edición de contraseña
- Solicitud (CRUD)
- Gestión de Plan
- Pago
- Reemplazo

Notas:
- MultiCheckboxField: checkbox múltiple para WTForms
- AREAS_COMUNES_CHOICES: intenta importarlo desde admin.routes; si falla usa fallback local
"""

from flask_wtf import FlaskForm
from wtforms import (
    StringField, PasswordField, TextAreaField, BooleanField,
    IntegerField, HiddenField, SelectField, SelectMultipleField,
    DecimalField, SubmitField, RadioField
)
from wtforms.validators import (
    DataRequired, InputRequired, Optional, Length, NumberRange, Email,
    EqualTo, Regexp, ValidationError
)
from wtforms.widgets import ListWidget, CheckboxInput


# ──────────────────────────────────────────────────────────────────────────────
# Checkbox múltiple (estilo lista de checks)
# ──────────────────────────────────────────────────────────────────────────────
class MultiCheckboxField(SelectMultipleField):
    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()


# ──────────────────────────────────────────────────────────────────────────────
# AREAS_COMUNES_CHOICES centralizado (con fallback)
# ──────────────────────────────────────────────────────────────────────────────
try:
    # Si ya las declaras en admin.routes, se aprovechan para no duplicar
    from .routes import AREAS_COMUNES_CHOICES  # type: ignore
except Exception:
    AREAS_COMUNES_CHOICES = [
        ('sala', 'Sala'), ('comedor', 'Comedor'), ('cocina', 'Cocina'),
        ('salon_juegos', 'Salón de juegos'), ('terraza', 'Terraza'),
        ('jardin', 'Jardín'), ('estudio', 'Estudio'), ('patio', 'Patio'),
        ('piscina', 'Piscina'), ('marquesina', 'Marquesina'),
        ('todas_anteriores', 'Todas las anteriores'), ('otro', 'Otro'),
    ]


# =============================================================================
#                                   LOGIN
# =============================================================================
class AdminLoginForm(FlaskForm):
    username = StringField(
        'Usuario',
        validators=[DataRequired(message="Ingresa tu usuario."), Length(max=50)],
        render_kw={"placeholder": "Tu usuario", "autocomplete": "username"}
    )
    password = PasswordField(
        'Contraseña',
        validators=[DataRequired(message="Ingresa tu contraseña."), Length(max=100)],
        render_kw={"placeholder": "Tu contraseña", "autocomplete": "current-password"}
    )
    submit = SubmitField('Iniciar sesión')


# =============================================================================
#                                  CLIENTE
# =============================================================================
class AdminClienteForm(FlaskForm):
    # Filtros de normalización
    _strip = lambda v: v.strip() if isinstance(v, str) else v
    _lower = lambda v: v.lower() if isinstance(v, str) else v
    _strip_lower = lambda v: v.strip().lower() if isinstance(v, str) else v

    # ── Credenciales / login ──────────────────────────────────────────────────
    username = StringField(
        'Usuario (login)',
        validators=[
            DataRequired("Ingresa un nombre de usuario."),
            Length(min=3, max=64, message="Entre 3 y 64 caracteres."),
            Regexp(r'^[a-zA-Z0-9_.-]+$', message="Solo letras, números, punto, guion y guion bajo.")
        ],
        filters=[_strip_lower],
        render_kw={"placeholder": "ej. juan.perez", "autocomplete": "username"}
    )

    # ── Identificación ────────────────────────────────────────────────────────
    codigo = StringField(
        'Código cliente',
        validators=[DataRequired("Ingresa el código."), Length(max=20)],
        filters=[_strip],
        render_kw={"placeholder": "Ej. CLI-001", "autocomplete": "off"}
    )
    nombre_completo = StringField(
        'Nombre completo',
        validators=[DataRequired("Ingresa el nombre completo."), Length(max=200)],
        filters=[_strip],
        render_kw={"placeholder": "Ej. Juan Pérez", "autocomplete": "name"}
    )

    # ── Contacto ──────────────────────────────────────────────────────────────
    email = StringField(
        'Email',
        validators=[DataRequired("Ingresa el correo."), Email("Correo inválido."), Length(max=100)],
        filters=[_strip_lower],
        render_kw={"placeholder": "correo@dominio.com", "inputmode": "email", "autocomplete": "email"}
    )
    telefono = StringField(
        'Teléfono',
        validators=[DataRequired("Ingresa el teléfono."), Length(max=20)],
        filters=[_strip],
        render_kw={"placeholder": "809-123-4567", "inputmode": "tel", "autocomplete": "tel"}
    )
    ciudad = StringField(
        'Ciudad',
        validators=[Optional(), Length(max=100)],
        filters=[_strip],
        render_kw={"placeholder": "Ej. Santiago"}
    )
    sector = StringField(
        'Sector',
        validators=[Optional(), Length(max=100)],
        filters=[_strip],
        render_kw={"placeholder": "Ej. Los Jardines"}
    )

    # ── Contraseña (opcional en edición; requerida en creación desde la ruta) ─
    password_new = PasswordField(
        'Nueva contraseña',
        validators=[Optional(), Length(min=6, max=128, message="La contraseña debe tener entre 6 y 128 caracteres.")],
        render_kw={"placeholder": "Dejar en blanco para no cambiar", "autocomplete": "new-password"}
    )
    password_confirm = PasswordField(
        'Confirmar contraseña',
        validators=[Optional(), EqualTo('password_new', message="Las contraseñas no coinciden.")],
        render_kw={"placeholder": "Repite la contraseña", "autocomplete": "new-password"}
    )

    # ── Notas ─────────────────────────────────────────────────────────────────
    notas_admin = TextAreaField(
        'Notas administrativas',
        validators=[Optional(), Length(max=1000)],
        filters=[_strip],
        render_kw={"placeholder": "Observaciones internas (no visibles para el cliente).", "rows": 3}
    )

    submit = SubmitField('Guardar cliente')

    # Si rellenan password_new, exigir confirmación.
    def validate_password_confirm(self, field):
        if self.password_new.data and not (field.data or "").strip():
            raise ValidationError("Confirma la contraseña.")


# =============================================================================
#                                 SOLICITUD
# =============================================================================
class AdminSolicitudForm(FlaskForm):
    # Ubicación
    ciudad_sector = StringField(
        'Ciudad / Sector',
        validators=[DataRequired("Indica ciudad y sector."), Length(max=200)],
        render_kw={"placeholder": "Ej. Santiago / Los Jardines"}
    )
    rutas_cercanas = StringField(
        'Rutas cercanas',
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Ej. Ruta K, Av. 27 Febrero (opcional)"}
    )

    # Detalles
    modalidad_trabajo = StringField(
        'Modalidad de trabajo',
        validators=[DataRequired("Indica la modalidad de trabajo."), Length(max=100)],
        render_kw={"placeholder": "Dormida / Salida diaria"}
    )

    # Admin: Radio (una sola opción) + campo "Otro"
    # Incluye "25 en adelante" como solicitaste.
    edad_requerida = RadioField(
        'Edad requerida',
        choices=[
            ('18-25', '18–25'),
            ('26-35', '26–35'),
            ('36-45', '36–45'),
            ('25 en adelante', '25 en adelante'),
            ('mayor45', 'Mayor de 45'),
            ('otra', 'Otro…'),
        ],
        validators=[InputRequired("Selecciona la edad requerida.")],
        coerce=str
    )
    edad_otro = StringField(
        'Especifica la edad (si marcaste Otro)',
        validators=[Optional(), Length(max=100)],
        render_kw={"placeholder": "Ej. 30 a 40, 20-30, etc."}
    )

    experiencia = TextAreaField(
        'Experiencia',
        validators=[DataRequired("Describe la experiencia requerida."), Length(min=5)],
        render_kw={"placeholder": "Ej. Niñera, cocina, planchado…", "rows": 3}
    )

    horario = StringField(
        'Horario',
        validators=[DataRequired("Indica el horario."), Length(max=100)],
        render_kw={"placeholder": "Ej. L–V 8:00 a 17:00"}
    )

    # Funciones (múltiple) + “Otro”
    funciones = MultiCheckboxField(
        'Funciones a realizar al personal',
        choices=[
            ('limpieza', 'Limpieza General'),
            ('cocinar', 'Cocinar'),
            ('lavar', 'Lavar'),
            ('ninos', 'Cuidar Niños'),
            ('envejeciente', 'Cuidar envejecientes'),
            ('otro', 'Otro'),
        ],
        validators=[DataRequired("Selecciona al menos una función.")]
    )
    funciones_otro = StringField(
        'Otra función (si marcaste Otro)',
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Especifica otra función (opcional)"}
    )

    # Tipo de lugar + “Otro”
    tipo_lugar = SelectField(
        'Tipo de lugar',
        choices=[('casa', 'Casa'), ('oficina', 'Oficina'), ('apto', 'Apartamento'), ('otro', 'Otro')],
        validators=[DataRequired("Selecciona el tipo de lugar.")]
    )
    tipo_lugar_otro = StringField(
        'Especifica el tipo de lugar (si marcaste Otro)',
        validators=[Optional(), Length(max=100)],
        render_kw={"placeholder": "Ej. Local comercial, villa, etc."}
    )

    # Inmueble
    habitaciones = IntegerField(
        'Habitaciones',
        validators=[DataRequired("Indica cuántas habitaciones."), NumberRange(min=0)],
        render_kw={"min": 0}
    )
    banos = DecimalField(
        'Baños',
        places=1,
        validators=[DataRequired("Indica la cantidad de baños."), NumberRange(min=0)],
        render_kw={"min": "0", "step": "0.5"}
    )
    dos_pisos = BooleanField('¿Tiene dos pisos?')

    # Áreas comunes
    areas_comunes = MultiCheckboxField(
        'Áreas comunes',
        choices=AREAS_COMUNES_CHOICES,
        default=[],
        validators=[Optional()]
    )
    area_otro = StringField(
        'Otra área',
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Especifica otra área (opcional)"}
    )

    # Ocupantes
    adultos = IntegerField(
        'Adultos',
        validators=[DataRequired("Indica cuántos adultos."), NumberRange(min=0)],
        render_kw={"min": 0}
    )
    ninos = IntegerField(
        'Niños',
        validators=[Optional(), NumberRange(min=0)],
        render_kw={"min": 0}
    )
    edades_ninos = StringField(
        'Edades de los niños',
        validators=[Optional(), Length(max=100)],
        render_kw={"placeholder": "Ej. 2 y 6 años (opcional)"}
    )

    # Mascota
    mascota = StringField(
        'Mascota',
        validators=[Optional(), Length(max=100)],
        render_kw={"placeholder": "Ej. Perro, Gato… (opcional)"}
    )

    # Compensación
    sueldo = StringField(
        'Sueldo',
        validators=[DataRequired("Indica el sueldo."), Length(max=50)],
        render_kw={"placeholder": "Ej. 23,000 mensual"}
    )
    pasaje_aporte = RadioField(
        '¿Aporta pasaje?',
        choices=[('1', 'Sí, aporta pasaje'), ('0', 'No aporta pasaje')],
        validators=[InputRequired("Indica si se aporta pasaje.")],
        # convierte a bool: '1' -> True, '0' -> False
        coerce=lambda v: v == '1'
    )

    # Nota
    nota_cliente = TextAreaField(
        'Nota adicional',
        validators=[Optional(), Length(max=1000)],
        render_kw={"placeholder": "Información útil para el proceso (opcional)", "rows": 3}
    )

    submit = SubmitField('Guardar solicitud')


# =============================================================================
#                              GESTIÓN DE PLAN
# =============================================================================
class AdminGestionPlanForm(FlaskForm):
    tipo_plan = StringField(
        'Tipo de plan',
        validators=[DataRequired("Indica el plan."), Length(max=50)],
        render_kw={"placeholder": "Ej. Básico, Premium…"}
    )
    abono = StringField(
        'Abono',
        validators=[DataRequired("Indica el abono."), Length(max=20)],
        render_kw={"placeholder": "Ej. 10,000"}
    )
    submit = SubmitField('Guardar plan')


# =============================================================================
#                                   PAGO
# =============================================================================
class AdminPagoForm(FlaskForm):
    candidata_id = SelectField(
        'Asignar candidata',
        coerce=int,
        validators=[DataRequired("Selecciona la candidata.")]
    )
    monto_pagado = StringField(
        'Monto pagado',
        validators=[DataRequired("Indica el monto pagado."), Length(max=100)],
        render_kw={"placeholder": "Ej. 10,000"}
    )
    submit = SubmitField('Registrar pago')


# =============================================================================
#                                REEMPLAZO
# =============================================================================
class AdminReemplazoForm(FlaskForm):
    candidata_old_id = HiddenField(validators=[DataRequired()])
    candidata_old_name = StringField(
        'Candidata que falló',
        validators=[DataRequired("Indica la candidata."), Length(max=200)]
    )
    motivo_fallo = TextAreaField(
        'Motivo del fallo',
        validators=[DataRequired("Indica el motivo."), Length(max=500)],
        render_kw={"rows": 3}
    )
    fecha_inicio_reemplazo = StringField(
        'Fecha inicio del reemplazo',
        validators=[DataRequired("Indica la fecha de inicio."), Length(max=16)],
        render_kw={"type": "datetime-local"}
    )
    submit = SubmitField('Activar reemplazo')
