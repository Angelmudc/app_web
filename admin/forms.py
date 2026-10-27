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
"""

from flask_wtf import FlaskForm
from flask import request
from wtforms import (
    StringField,
    PasswordField,
    TextAreaField,
    BooleanField,
    IntegerField,
    HiddenField,
    SelectField,
    SelectMultipleField,
    DecimalField,
    SubmitField,
    RadioField
)
from wtforms.validators import (
    DataRequired,
    InputRequired,
    Optional,
    Length,
    NumberRange,
    Email,
    EqualTo,
    Regexp,
    ValidationError
)
from wtforms.widgets import ListWidget, CheckboxInput
from utils.modalidad import canonicalize_modalidad_trabajo, validate_modalidad_context
from utils.horario_mode import build_horario_from_form
from utils.envejeciente import clean_list as _clean_list_envejeciente
from services.payment_rules import get_plan_choices, is_valid_plan, normalize_plan
from utils.experiencia_solicitud import (
    EXPERIENCIA_CHOICES,
    LegacyExperienceSelectField,
    experiencia_value_from_form,
    load_experiencia_value,
    normalize_experiencia_submission,
)
from services.service_catalog import (
    ENFERMERA_ASSISTANCE_CHOICES,
    ENFERMERA_CONDITION_CHOICES,
    ENFERMERA_EXPERIENCE_CHOICES,
    ENFERMERA_LEGACY_TASK_CHOICES,
    ENFERMERA_MOBILITY_CHOICES,
    ENFERMERA_RESPONSIBILITY_CHOICES,
    CHOFER_LICENSE_CHOICES, CHOFER_ROUTE_CHOICES, CHOFER_PURPOSE_CHOICES,
    CONSERJE_INSTALLATION_CHOICES, CONSERJE_RESIDENTIAL_TYPES, CONSERJE_RESPONSIBILITY_CHOICES,
    SERVICE_CHOICES,
    NINERA_RESPONSIBILITY_CHOICES,
    NINERA_SPECIAL_NEED_CHOICES,
    NINERA_LEGACY_TASK_CHOICES,
    OFFICE_ESTABLISHMENT_CHOICES, OFFICE_TASK_CHOICES, OFFICE_AREA_CHOICES,
    admin_details_from_form,
    canonical_service_code, is_legacy_service_code,
    validate_service_details,
)



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


class StaffUserCreateForm(FlaskForm):
    username = StringField(
        'Usuario',
        validators=[
            DataRequired("Ingresa el usuario."),
            Length(min=3, max=80),
            Regexp(r'^[a-zA-Z0-9._-]+$', message="Usa solo letras, números, ., _ o -."),
        ],
        filters=[lambda v: v.strip() if isinstance(v, str) else v],
        render_kw={"autocomplete": "username"}
    )
    email = StringField(
        'Email (opcional)',
        validators=[Optional(), Email("Correo inválido."), Length(max=255)],
        filters=[lambda v: v.strip().lower() if isinstance(v, str) else v],
        render_kw={"autocomplete": "email"}
    )
    role = SelectField(
        'Rol',
        choices=[('secretaria', 'Secretaria'), ('admin', 'Admin'), ('owner', 'Owner')],
        validators=[DataRequired("Selecciona un rol.")]
    )
    password = PasswordField(
        'Contraseña inicial',
        validators=[DataRequired("Ingresa una contraseña."), Length(min=8, max=128)],
        render_kw={"autocomplete": "new-password"}
    )
    submit = SubmitField('Crear usuario')


class StaffUserEditForm(FlaskForm):
    email = StringField(
        'Email (opcional)',
        validators=[Optional(), Email("Correo inválido."), Length(max=255)],
        filters=[lambda v: v.strip().lower() if isinstance(v, str) else v],
        render_kw={"autocomplete": "email"}
    )
    role = SelectField(
        'Rol',
        choices=[('secretaria', 'Secretaria'), ('admin', 'Admin'), ('owner', 'Owner')],
        validators=[DataRequired("Selecciona un rol.")]
    )
    change_password = BooleanField('Cambiar contraseña')
    new_password = PasswordField(
        'Reset password (opcional)',
        validators=[Optional(), Length(min=8, max=128)],
        render_kw={"autocomplete": "new-password"}
    )
    new_password_confirm = PasswordField(
        'Confirmar nueva contraseña',
        validators=[Optional(), EqualTo('new_password', message='Las contraseñas no coinciden.')],
        render_kw={"autocomplete": "new-password"}
    )
    submit = SubmitField('Guardar cambios')


# =============================================================================
#                                  CLIENTE
# =============================================================================
class AdminClienteForm(FlaskForm):
    _strip = lambda v: v.strip() if isinstance(v, str) else v
    _lower = lambda v: v.lower() if isinstance(v, str) else v
    _strip_lower = lambda v: v.strip().lower() if isinstance(v, str) else v

    codigo = StringField(
        'Código cliente',
        validators=[
            DataRequired("Ingresa el código."),
            Length(max=20)
        ],
        filters=[_strip],
        render_kw={
            "placeholder": "Ej. CLI-001",
            "autocomplete": "off"
        }
    )

    nombre_completo = StringField(
        'Nombre completo',
        validators=[
            DataRequired("Ingresa el nombre completo."),
            Length(max=200)
        ],
        filters=[_strip],
        render_kw={
            "placeholder": "Ej. Juan Pérez",
            "autocomplete": "name"
        }
    )

    email = StringField(
        'Email',
        validators=[
            DataRequired("Ingresa el correo."),
            Email("Correo inválido."),
            Length(max=100)
        ],
        filters=[_strip_lower],
        render_kw={
            "placeholder": "correo@dominio.com",
            "inputmode": "email",
            "autocomplete": "email"
        }
    )

    telefono = StringField(
        'Teléfono',
        validators=[
            DataRequired("Ingresa el teléfono."),
            Length(max=20)
        ],
        filters=[_strip],
        render_kw={
            "placeholder": "809-123-4567",
            "inputmode": "tel",
            "autocomplete": "tel"
        }
    )

    # ----- Credenciales (portal de clientes) -----
    # Reglas:
    # - `username` puede quedar vacío: si está vacío, el backend puede usar `email`.
    # - `password` puede quedar vacío: si está vacío, NO se cambia la contraseña.
    # - Si se escribe `password`, se exige `password_confirm`.
    # - Si se escribe `password_confirm`, se exige `password`.

    username = StringField(
        'Usuario (opcional)',
        validators=[Optional(), Length(min=3, max=80)],
        filters=[_strip_lower],
        render_kw={
            "placeholder": "Ej. juan.perez (si lo dejas vacío, se usa el email)",
            "autocomplete": "username"
        }
    )

    password = PasswordField(
        'Contraseña (opcional)',
        validators=[Optional(), Length(min=8, max=100)],
        render_kw={
            "placeholder": "Solo si quieres crear/cambiar la contraseña",
            "autocomplete": "new-password"
        }
    )

    password_confirm = PasswordField(
        'Confirmar contraseña',
        validators=[Optional(), EqualTo('password', message='Las contraseñas no coinciden.')],
        render_kw={
            "placeholder": "Repite la contraseña (si llenaste contraseña)",
            "autocomplete": "new-password"
        }
    )

    def validate_username(self, field):
        """Valida el formato del usuario si se llena (sin romper si se deja vacío)."""
        val = (field.data or '').strip()
        if not val:
            return
        # Permitimos letras, números, punto, guion, guion bajo.
        import re
        if not re.match(r'^[a-z0-9._-]+$', val, flags=re.IGNORECASE):
            raise ValidationError('El usuario solo puede tener letras, números, punto (.), guion (-) y guion bajo (_).')

    def validate_password(self, field):
        """Si escriben contraseña, exigir confirmación y evitar contraseñas demasiado débiles."""
        pw = (field.data or '').strip()
        if not pw:
            return
        if len(pw) < 8:
            raise ValidationError('La contraseña debe tener al menos 8 caracteres.')

    def validate_password_confirm(self, field):
        """Si se escribe contraseña, exigir confirmación. Si se escribe confirmación, exigir contraseña."""
        pw = (self.password.data or '').strip()
        pc = (field.data or '').strip()

        if pw and not pc:
            raise ValidationError('Confirma la contraseña.')
        if pc and not pw:
            raise ValidationError('Debes escribir la contraseña primero.')

    ciudad = StringField(
        'Ciudad',
        validators=[
            Optional(),
            Length(max=100)
        ],
        filters=[_strip],
        render_kw={
            "placeholder": "Ej. Santiago"
        }
    )

    sector = StringField(
        'Sector',
        validators=[
            Optional(),
            Length(max=100)
        ],
        filters=[_strip],
        render_kw={
            "placeholder": "Ej. Los Jardines"
        }
    )

    notas_admin = TextAreaField(
        'Notas administrativas',
        validators=[
            Optional(),
            Length(max=1000)
        ],
        filters=[_strip],
        render_kw={
            "placeholder": "Observaciones internas (no visibles para el cliente).",
            "rows": 3
        }
    )


    submit = SubmitField('Guardar cliente')


# =============================================================================
#                                 SOLICITUD
# =============================================================================
class AdminSolicitudForm(FlaskForm):
    _strip = lambda v: v.strip() if isinstance(v, str) else v
    HOUSEHOLD_FUNCIONES = {"limpieza", "cocinar", "lavar", "planchar"}
    PLAN_CHOICES = get_plan_choices()
    # ─────────────────────────────────────────────
    # TIPO DE SOLICITUD
    # ─────────────────────────────────────────────
    tipo_servicio = SelectField(
        'Tipo de solicitud',
        choices=[('', 'Selecciona un servicio…'), *SERVICE_CHOICES],
        validators=[DataRequired("Selecciona el tipo de solicitud.")],
        coerce=str
    )

    # ─────────────────────────────────────────────
    # UBICACIÓN
    # ─────────────────────────────────────────────
    ciudad_sector = StringField(
        'Ciudad / Sector',
        validators=[DataRequired("Indica ciudad y sector."), Length(max=200)],
        render_kw={"placeholder": "Ej. Santiago / Los Jardines"}
    )
    direccion_precisa = StringField(
        "Dirección precisa del servicio",
        validators=[Optional(), Length(max=300)],
        filters=[_strip],
        render_kw={"placeholder": "Calle, número, residencial o referencia exacta", "autocomplete": "street-address"},
    )
    rutas_cercanas = StringField(
        'Rutas cercanas',
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Ej. Ruta K, Av. 27 Febrero (opcional)"}
    )

    # ─────────────────────────────────────────────
    # DETALLES GENERALES (cualquier tipo)
    # ─────────────────────────────────────────────
    modalidad_trabajo = StringField(
        'Modalidad de trabajo',
        validators=[DataRequired("Indica la modalidad de trabajo."), Length(max=100)],
        render_kw={"placeholder": "Dormida / Salida diaria"}
    )

    edad_requerida = MultiCheckboxField(
        'Edad requerida',
        choices=[
            ('18-25', '18–25'),
            ('26-35', '26–35'),
            ('36-45', '36–45'),
            ('25+',   '25 en adelante'),
            ('45+',   'Mayor de 45'),
            ('otro',  'Otro…'),
        ],
        validators=[Optional()],
        coerce=str,
        default=[]
    )
    edad_otro = StringField(
        'Especifica la edad (si marcaste Otro)',
        validators=[Optional(), Length(max=100)],
        render_kw={"placeholder": "Ej. 30 a 40, 20-30, etc."}
    )
    experiencia = LegacyExperienceSelectField(
        'Experiencia',
        choices=EXPERIENCIA_CHOICES,
        validators=[DataRequired("Selecciona una opción de experiencia.")],
        coerce=str,
    )
    experiencia_otro = TextAreaField(
        'Especifica la experiencia',
        validators=[Optional()],
        render_kw={"placeholder": "Describe la experiencia específica", "rows": 3}
    )

    def validate_experiencia(self, field):
        normalize_experiencia_submission(self, field)
        if field.data == 'otro' and not (self.experiencia_otro.data or '').strip():
            raise ValidationError("Especifica la experiencia cuando marcas 'Otro'.")

    def experiencia_value_for_model(self):
        return experiencia_value_from_form(self)

    def load_experiencia_from_model(self, raw_value):
        load_experiencia_value(self, raw_value)

    horario = StringField(
        'Horario',
        validators=[DataRequired("Indica el horario."), Length(max=100)],
        render_kw={"placeholder": "Ej. L–V 8:00 a 17:00"}
    )

    funciones = MultiCheckboxField(
        'Funciones a realizar al personal',
        choices=[
            ('limpieza',     'Limpieza General'),
            ('cocinar',      'Cocinar'),
            ('lavar',        'Lavar'),
            ('planchar',     'Planchar'),
            ('ninos',        'Cuidar Niños'),
            ('envejeciente', 'Cuidar envejecientes'),
            ('otro',         'Otro'),
        ],
        validators=[Optional()],
        coerce=str,
        default=[]
    )
    funciones_otro = StringField(
        'Otra función (si marcaste Otro)',
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Especifica otra función (opcional)"}
    )
    envejeciente_tipo_cuidado = RadioField(
        "Tipo de cuidado del envejeciente",
        choices=[("independiente", "Independiente"), ("encamado", "Encamado")],
        validators=[Optional()],
        coerce=str
    )
    envejeciente_responsabilidades = MultiCheckboxField(
        "Responsabilidades con envejeciente encamado",
        choices=[
            ("pampers", "Cambiar pampers"),
            ("higiene", "Banarlo / higiene personal"),
            ("comida", "Darle de comer"),
            ("medicamentos", "Darle medicamentos"),
            ("movilidad", "Movilizarlo o ayudarlo a levantarse"),
            ("otro", "Otro cuidado especial"),
        ],
        validators=[Optional()],
        coerce=str,
        default=[]
    )
    envejeciente_solo_acompanamiento = BooleanField(
        "La domestica no realizara higiene, panales, alimentacion ni medicamentos; solo acompanamiento o supervision."
    )
    envejeciente_nota = TextAreaField(
        "Nota adicional sobre el envejeciente",
        validators=[Optional(), Length(max=1000)],
        render_kw={"rows": 3, "placeholder": "Opcional"}
    )

    # ─────────────────────────────────────────────
    # TIPO DE LUGAR
    # ─────────────────────────────────────────────
    # OJO: ahora son Optional, la lógica de obligatorio la hacemos en validate()
    tipo_lugar = SelectField(
        'Tipo de lugar',
        choices=[
            ('casa',    'Casa'),
            ('oficina', 'Oficina'),
            ('apto',    'Apartamento'),
            ('otro',    'Otro')
        ],
        validators=[Optional()],
        coerce=str
    )
    tipo_lugar_otro = StringField(
        'Especifica el tipo de lugar (si marcaste Otro)',
        validators=[Optional(), Length(max=100)],
        render_kw={"placeholder": "Ej. Local comercial, villa, etc."}
    )

    habitaciones = IntegerField(
        'Habitaciones',
        validators=[Optional(), NumberRange(min=0)],
        render_kw={"min": 0}
    )
    banos = DecimalField(
        'Baños',
        places=None,
        validators=[Optional(), NumberRange(min=0)],
        render_kw={"min": "0", "step": "any"}
    )
    dos_pisos = BooleanField('¿Tiene dos pisos?')

    # Áreas comunes
    areas_comunes = MultiCheckboxField(
        'Áreas comunes',
        choices=AREAS_COMUNES_CHOICES,
        validators=[Optional()],
        coerce=str,
        default=[]
    )
    area_otro = StringField(
        'Otra área',
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Ej. Balcón, cuarto de juegos…"}
    )

    # ─────────────────────────────────────────────
    # OCUPANTES
    # ─────────────────────────────────────────────
    # Igual: ponemos Optional y validamos por tipo en validate()
    adultos = IntegerField(
        'Adultos',
        validators=[Optional(), NumberRange(min=0)],
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
    ayuda_cuidado_ninos = SelectField(
        '¿La empleada tendrá ayuda con el cuidado de los niños?',
        choices=[
            ("", "Selecciona una opción"),
            ("sin_ayuda", "No tendrá ayuda"),
            ("con_ayuda", "Sí tendrá ayuda"),
        ],
        validators=[Optional()],
        coerce=str,
    )

    mascota = StringField(
        'Mascota',
        validators=[Optional(), Length(max=100)],
        render_kw={"placeholder": "Ej. Perro, Gato… (opcional)"}
    )

    # ─────────────────────────────────────────────
    # CAMPOS ESPECÍFICOS – NIÑERA
    # ─────────────────────────────────────────────
    ninera_cant_ninos = IntegerField(
        'Cantidad de niños a cuidar',
        validators=[Optional(), NumberRange(min=0)],
        render_kw={"min": 0}
    )
    ninera_edades = StringField(
        'Edades de los niños a cuidar',
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Ej. 1 año y 4 años"}
    )
    ninera_tareas = MultiCheckboxField(
        'Responsabilidades con los niños',
        choices=NINERA_RESPONSIBILITY_CHOICES + NINERA_LEGACY_TASK_CHOICES,
        validators=[Optional()],
        coerce=str,
        default=[]
    )
    ninera_tareas_otro = StringField(
        'Otras tareas con los niños',
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Describe otras responsabilidades relacionadas con el cuidado de los niños"}
    )
    ninera_necesidad_especial_publicable = TextAreaField(
        'Describe los cuidados especiales que necesitará el niño',
        validators=[Optional(), Length(max=500)],
        render_kw={"rows": 3, "placeholder": "Describe las tareas de cuidado necesarias, sin diagnósticos ni historial médico"}
    )
    ninera_condicion_especial = TextAreaField(
        'Cuidados o adaptaciones necesarias (privado)',
        validators=[Optional(), Length(max=1000)],
        render_kw={"rows": 2, "placeholder": "Describe brevemente los cuidados o adaptaciones (opcional)"}
    )
    ninera_necesidad_especial_respuesta = SelectField(
        'Necesidades de atención especial',
        choices=[('', 'Selecciona'), *NINERA_SPECIAL_NEED_CHOICES],
        validators=[Optional()], coerce=str, validate_choice=False,
    )
    ninera_responsable_principal = SelectField(
        '¿La niñera será la principal responsable del cuidado?',
        choices=[('', 'Selecciona'), ('si', 'Sí, tendrá la responsabilidad principal'), ('no', 'No, compartirá el cuidado con otra persona')],
        validators=[Optional()], coerce=str,
    )
    ninera_responsabilidad_integral = BooleanField('Responsabilidad integral del cuidado de los niños')

    # ─────────────────────────────────────────────
    # CAMPOS ESPECÍFICOS – ENFERMERA / CUIDADORA
    # ─────────────────────────────────────────────
    enf_a_quien_cuida = StringField(
        '¿A quién debe cuidar?',
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Ej. señora de 80 años, paciente postrado…"}
    )
    enf_cantidad_personas = IntegerField(
        'Cantidad de personas que necesitan cuidados', validators=[Optional(), NumberRange(min=1, max=20)],
        render_kw={"min": 1, "max": 20, "inputmode": "numeric"}
    )
    enf_personas_cuidado = HiddenField(default="[]")
    enf_personas_cuidado_historicas = HiddenField(default="[]")
    enf_personas_cuidado_necesidades_historicas = HiddenField(default="[]")
    enf_edad_persona = IntegerField(
        'Edad de la persona', validators=[Optional(), NumberRange(min=0, max=120)],
        render_kw={"min": 0, "max": 120, "inputmode": "numeric"}
    )
    enf_edades_personas = StringField(
        'Edades de las personas', validators=[Optional(), Length(max=120)],
        render_kw={"placeholder": "Ej. 72, 80 y 85 años"}
    )
    enf_movilidad = SelectField(
        'Movilidad de la persona',
        choices=[('', 'Selecciona…'), *ENFERMERA_MOBILITY_CHOICES],
        validators=[Optional()],
        coerce=str
    )
    enf_ayuda_higiene = SelectField('¿Necesita ayuda con su higiene personal?', choices=[('', 'Selecciona'), ('si', 'Sí'), ('no', 'No')], validators=[Optional()], coerce=str)
    enf_ayuda_alimentacion = SelectField('¿Necesita ayuda para alimentarse?', choices=[('', 'Selecciona'), ('si', 'Sí'), ('no', 'No')], validators=[Optional()], coerce=str)
    enf_tipo_asistencia = SelectField('Tipo de asistencia requerida', choices=[('', 'Selecciona'), *ENFERMERA_ASSISTANCE_CHOICES], validators=[Optional()], coerce=str)
    enf_condiciones = MultiCheckboxField('Enfermedades y condiciones de salud', choices=ENFERMERA_CONDITION_CHOICES, validators=[Optional()], coerce=str, default=[])
    enf_condicion_otro = StringField('Especifica otra condición', validators=[Optional(), Length(max=250)], render_kw={"placeholder": "Describe brevemente la condición relevante para el cuidado"})
    enf_condicion_principal = TextAreaField(
        'Condición principal / diagnóstico',
        validators=[Optional(), Length(max=1000)],
        render_kw={"rows": 3, "placeholder": "Ej. Alzheimer, demencia, ACV, etc."}
    )
    enf_tareas = MultiCheckboxField(
        'Tareas principales de cuidado',
        choices=ENFERMERA_RESPONSIBILITY_CHOICES + ENFERMERA_LEGACY_TASK_CHOICES,
        validators=[Optional()],
        coerce=str,
        default=[]
    )
    enf_tareas_otro = StringField(
        'Otras tareas de cuidado',
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Especifica tareas adicionales (opcional)"}
    )
    enf_responsabilidad_principal = SelectField(
        '¿La trabajadora será la principal responsable del cuidado?',
        choices=[('', 'Selecciona'), ('si', 'Sí, tendrá la responsabilidad principal'), ('no', 'No, compartirá el cuidado con otra persona')],
        validators=[Optional()], coerce=str,
    )
    enf_requiere_preparacion_profesional = BooleanField(
        '¿Requiere preparación profesional específica?', default=False
    )
    enf_formacion_requerida = StringField(
        'Preparación profesional requerida', validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Ej. Enfermería, auxiliar de geriatría…"}
    )

    # ─────────────────────────────────────────────
    # CAMPOS ESPECÍFICOS – CHOFER
    # ─────────────────────────────────────────────
    chofer_tipo_servicio = SelectField('¿Para qué necesitas el chofer?', choices=[('', 'Selecciona…'), *CHOFER_PURPOSE_CHOICES], validators=[Optional()], coerce=str)
    chofer_tipo_servicio_otro = StringField('Especifica otra finalidad', validators=[Optional(), Length(max=120)])
    chofer_responsabilidades = TextAreaField('Responsabilidades adicionales (opcional)', validators=[Optional(), Length(max=300)])
    chofer_vehiculo = RadioField(
        'Vehículo que usará',
        choices=[
            ('cliente',  'Vehículo del cliente'),
            ('empleado', 'Vehículo propio del chofer')
        ],
        validators=[Optional()],
        coerce=str
    )
    chofer_tipo_vehiculo = SelectField(
        'Tipo de vehículo',
        choices=[
            ('',              'Selecciona…'),
            ('carro',         'Carro'),
            ('yipeta',        'Yipeta'),
            ('pickup',        'Pickup'),
            ('minibus',       'Minibús'),
            ('camion_ligero', 'Camión ligero'),
            ('otro',          'Otro'),
        ],
        validators=[Optional()],
        coerce=str
    )
    chofer_tipo_vehiculo_otro = StringField(
        'Especifica el tipo de vehículo (si marcaste Otro)',
        validators=[Optional(), Length(max=100)],
        render_kw={"placeholder": "Ej. Guagua turística, camión mediano…"}
    )
    chofer_rutas = MultiCheckboxField('¿Qué recorridos realizará?', choices=list(CHOFER_ROUTE_CHOICES), validators=[Optional()], coerce=str)
    chofer_rutas_otro = StringField('Especifica los desplazamientos', validators=[Optional(), Length(max=200)])
    chofer_categoria_licencia = SelectField('Categoría de licencia requerida', choices=[('', 'Selecciona…'), *CHOFER_LICENSE_CHOICES], validators=[Optional()], coerce=str)
    # Campos históricos de solo lectura, conservados para solicitudes antiguas.
    chofer_rutas_legacy = StringField('Rutas históricas', validators=[Optional()])
    chofer_licencia_detalle = StringField('Licencia histórica', validators=[Optional()])

    # Campos de instalaciones (sin nombres comerciales ni cantidad de empleados).
    office_tipo_establecimiento = SelectField('Tipo de establecimiento', choices=[('', 'Selecciona…'), *OFFICE_ESTABLISHMENT_CHOICES], validators=[Optional()], coerce=str)
    office_tipo_establecimiento_otro = StringField('Especifica el tipo de establecimiento', validators=[Optional(), Length(max=120)])
    office_tamano = SelectField('Tamaño aproximado', choices=[('', 'Selecciona…'), ('Pequeño', 'Pequeño'), ('Mediano', 'Mediano'), ('Grande', 'Grande')], validators=[Optional()], coerce=str)
    office_caracteristicas = HiddenField('Características estructuradas', validators=[Optional()])
    office_espacios_otro = StringField('Describe los espacios', validators=[Optional(), Length(max=200)])
    office_areas = MultiCheckboxField('Áreas que debe limpiar', choices=sorted({(code, label) for choices in OFFICE_AREA_CHOICES.values() for code, label in choices}), validators=[Optional()], coerce=str)
    office_areas_legacy = StringField('Áreas históricas', validators=[Optional(), Length(max=250)])
    office_areas_otro = StringField('Especifica otras áreas', validators=[Optional(), Length(max=200)])
    office_funciones = MultiCheckboxField('Funciones que realizará el personal', choices=list(OFFICE_TASK_CHOICES), validators=[Optional()], coerce=str)
    office_funciones_otro = StringField('Especifica otras funciones', validators=[Optional(), Length(max=200)])
    office_trabaja_con_companeros = SelectField('¿La persona contratada trabajará con otros empleados de limpieza?', choices=[('', 'Selecciona…'), ('si', 'Sí'), ('no', 'No')], validators=[Optional()], coerce=str)
    office_cantidad_companeros_limpieza = IntegerField('Cantidad de compañeros de limpieza (sin incluir a la persona contratada)', validators=[Optional(), NumberRange(min=1)])
    office_despachos = IntegerField('Cantidad de despachos u oficinas', validators=[Optional(), NumberRange(min=0, max=500)])
    office_reuniones = IntegerField('Cantidad de salas de reuniones', validators=[Optional(), NumberRange(min=0, max=500)])
    office_ventas = IntegerField('Cantidad de áreas de venta', validators=[Optional(), NumberRange(min=0, max=500)])
    office_probadores = IntegerField('Cantidad de probadores', validators=[Optional(), NumberRange(min=0, max=500)])
    office_aulas = IntegerField('Cantidad de aulas', validators=[Optional(), NumberRange(min=0, max=500)])
    office_administracion = IntegerField('Cantidad de oficinas administrativas', validators=[Optional(), NumberRange(min=0, max=500)])
    office_consultorios = IntegerField('Cantidad de consultorios', validators=[Optional(), NumberRange(min=0, max=500)])
    office_espera = IntegerField('Cantidad de salas de espera', validators=[Optional(), NumberRange(min=0, max=500)])
    office_comedores = IntegerField('Cantidad de salones o áreas de comedor', validators=[Optional(), NumberRange(min=0, max=500)])
    office_cocinas = IntegerField('Cantidad de cocinas', validators=[Optional(), NumberRange(min=0, max=500)])
    office_areas_comunes = IntegerField('Cantidad de áreas comunes', validators=[Optional(), NumberRange(min=0, max=500)])
    office_niveles = IntegerField('Cantidad de niveles', validators=[Optional(), NumberRange(min=0, max=100)])
    office_banos = IntegerField('Cantidad de baños', validators=[Optional(), NumberRange(min=0, max=500)])
    office_frecuencia = StringField('Frecuencia histórica', validators=[Optional(), Length(max=100)])
    office_horario = StringField('Horario histórico', validators=[Optional(), Length(max=120)])
    conserje_tipo_instalacion = StringField('Tipo de instalación', validators=[Optional(), Length(max=100)])
    conserje_tipo_residencial = SelectField('Tipo de residencial', choices=[('', 'Selecciona…'), *CONSERJE_RESIDENTIAL_TYPES], validators=[Optional()], coerce=str)
    conserje_tipo_residencial_otro = StringField('Especifica el tipo de residencial', validators=[Optional(), Length(max=120)])
    conserje_niveles = IntegerField('Cantidad de niveles', validators=[Optional(), NumberRange(min=0)])
    conserje_apartamentos = IntegerField('Cantidad de unidades o propiedades', validators=[Optional(), NumberRange(min=0)])
    conserje_ascensores = IntegerField('Cantidad de ascensores', validators=[Optional(), NumberRange(min=0)])
    conserje_cantidad_edificios = IntegerField('Cantidad de edificios o bloques', validators=[Optional(), NumberRange(min=1, max=10000)])
    conserje_cantidad_casas = IntegerField('Cantidad de casas', validators=[Optional(), NumberRange(min=1, max=10000)])
    conserje_unidades = IntegerField('Cantidad de unidades o propiedades', validators=[Optional(), NumberRange(min=1, max=10000)])
    conserje_tiene_ascensor = SelectField('¿Tiene ascensor?', choices=[('', 'Selecciona'), ('si', 'Sí'), ('no', 'No')], validators=[Optional()], coerce=str)
    conserje_parqueos_comunes = SelectField('¿Tiene parqueos comunes?', choices=[('', 'Selecciona'), ('si', 'Sí'), ('no', 'No')], validators=[Optional()], coerce=str)
    conserje_areas_recreativas = SelectField('¿Tiene áreas recreativas comunes?', choices=[('', 'Selecciona'), ('si', 'Sí'), ('no', 'No')], validators=[Optional()], coerce=str)
    conserje_jardines_comunes = SelectField('¿Tiene jardines o áreas verdes comunes?', choices=[('', 'Selecciona'), ('si', 'Sí'), ('no', 'No')], validators=[Optional()], coerce=str)
    conserje_calles_internas = SelectField('¿Tiene calles internas comunes?', choices=[('', 'Selecciona'), ('si', 'Sí'), ('no', 'No')], validators=[Optional()], coerce=str)
    conserje_areas_comunes = StringField('Áreas comunes', validators=[Optional(), Length(max=250)])
    conserje_banos = IntegerField('Cantidad de baños', validators=[Optional(), NumberRange(min=0)])
    conserje_escaleras = IntegerField('Escaleras', validators=[Optional(), NumberRange(min=0)])
    conserje_parqueos = IntegerField('Parqueos', validators=[Optional(), NumberRange(min=0, max=500)])
    conserje_areas_exteriores = StringField('Áreas exteriores', validators=[Optional(), Length(max=200)])
    conserje_manejo_basura = StringField('Manejo de basura', validators=[Optional(), Length(max=200)])
    conserje_mantenimiento = StringField('Mantenimiento básico', validators=[Optional(), Length(max=200)])
    conserje_estacionamiento = SelectField('¿Tiene estacionamiento?', choices=[('', 'Sin respuesta'), ('si', 'Sí'), ('no', 'No')], validators=[Optional()], coerce=str)
    conserje_instalaciones = MultiCheckboxField('Instalaciones a cargo', choices=CONSERJE_INSTALLATION_CHOICES, validators=[Optional()], coerce=str)
    conserje_otras_instalaciones = StringField('Especifica otras instalaciones', validators=[Optional(), Length(max=200)])
    conserje_responsabilidades = MultiCheckboxField('Responsabilidades', choices=CONSERJE_RESPONSIBILITY_CHOICES, validators=[Optional()], coerce=str)
    conserje_responsabilidades_otro = StringField('Especifica otras responsabilidades', validators=[Optional(), Length(max=250)])
    conserje_trabaja_con_conserjes = SelectField('¿Trabajará con otros conserjes?', choices=[('', 'Sin respuesta'), ('si', 'Sí'), ('no', 'No')], validators=[Optional()], coerce=str)
    conserje_cantidad_conserjes = IntegerField('¿Cuántos conserjes trabajarán junto a la persona contratada?', validators=[Optional(), NumberRange(min=1)])

    # ─────────────────────────────────────────────
    # COMPENSACIÓN
    # ─────────────────────────────────────────────
    _strip = lambda v: v.strip() if isinstance(v, str) else v
    _digits_only = lambda v: ''.join(ch for ch in v if ch.isdigit()) if isinstance(v, str) else v
    sueldo = StringField(
        'Sueldo',
        validators=[
            DataRequired("Indica el sueldo."),
            Regexp(r'^\d+$', message='El sueldo debe contener solo números (sin RD$, sin comas).')
        ],
        filters=[_strip, _digits_only],
        render_kw={
            "placeholder": "Ej. 23000",
            "inputmode": "numeric",
            "autocomplete": "off"
        }
    )
    pasaje_aporte = RadioField(
        '¿Aporta pasaje?',
        choices=[('1', 'Sí, aporta pasaje'), ('0', 'No aporta pasaje')],
        validators=[InputRequired("Indica si se aporta pasaje.")],
        coerce=lambda v: v == '1'
    )
    tipo_plan = RadioField(
        'Plan',
        choices=PLAN_CHOICES,
        validators=[Optional()],
        coerce=str,
        validate_choice=False,
    )

    # Nota
    nota_cliente = TextAreaField(
        'Nota adicional',
        validators=[Optional(), Length(max=1000)],
        render_kw={"placeholder": "Información útil para el proceso (opcional)", "rows": 3}
    )

    submit = SubmitField('Guardar solicitud')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        raw_service = (self.tipo_servicio.data or '').strip()
        service = canonical_service_code(raw_service)
        if is_legacy_service_code(raw_service):
            label = {"LIMPIEZA_OFICINA": "Limpieza de oficina (histórico)", "CONSERJE": "Conserje (histórico)"}[raw_service]
            self.tipo_servicio.choices = [('', 'Selecciona un servicio…'), *SERVICE_CHOICES, (raw_service, label)]
        elif raw_service and service not in {code for code, _label in SERVICE_CHOICES}:
            # Unknown historical identifiers must stay visible and must never
            # fall through to the first catalog option (Doméstica).
            self.tipo_servicio.choices = [
                ('', 'Selecciona un servicio…'), *SERVICE_CHOICES,
                (raw_service, f'Servicio sin clasificar ({raw_service})'),
            ]
        if service == "LIMPIEZA_ESTABLECIMIENTOS":
            raw_office_type = str(self.office_tipo_establecimiento.data or '').strip()
            if raw_office_type and raw_office_type not in {value for value, _ in OFFICE_ESTABLISHMENT_CHOICES}:
                self.office_tipo_establecimiento.choices = [('', 'Selecciona…'), *OFFICE_ESTABLISHMENT_CHOICES, (raw_office_type, f'Histórico: {raw_office_type}')]
            existing_exp = str(self.experiencia.data or '').strip()
            self.experiencia.choices = [("Limpieza", "Limpieza")] + ([(existing_exp, f"Experiencia histórica: {existing_exp}")] if existing_exp and existing_exp != "Limpieza" else [])
            if not existing_exp: self.experiencia.data = "Limpieza"
        if service == "CHOFER":
            self.experiencia.choices = [("Chofer", "Chofer")]
            self.experiencia.data = "Chofer"
        elif service == "LIMPIEZA_ESTABLECIMIENTOS":
            self.experiencia.choices = [("Limpieza", "Limpieza")]
            self.experiencia.data = "Limpieza"
        elif service == "ENFERMERA":
            choices = list(ENFERMERA_EXPERIENCE_CHOICES)
            raw = str(self.experiencia.data or '').strip()
            known = {value for value, _label in choices}
            if raw and raw not in known:
                choices.append((raw, f"Experiencia histórica: {raw}"))
            self.experiencia.choices = choices

    @staticmethod
    def _has_limpieza_selected() -> bool:
        try:
            raw = request.form.getlist('funciones')
        except Exception:
            raw = []
        vals = [str(x).strip().lower() for x in (raw or []) if str(x).strip()]
        return 'limpieza' in vals

    @staticmethod
    def _selected_funciones_values() -> list[str]:
        try:
            raw = request.form.getlist('funciones')
        except Exception:
            raw = []
        return [str(x).strip().lower() for x in (raw or []) if str(x).strip()]

    @classmethod
    def _requires_adultos_for_funciones(cls, funciones_selected: list[str]) -> bool:
        vals = set(funciones_selected or [])
        if not vals:
            return False
        return bool(vals.intersection(cls.HOUSEHOLD_FUNCIONES))

    def validate_modalidad_trabajo(self, field):
        field.data = canonicalize_modalidad_trabajo(field.data)

    # ─────────────────────────────────────────────
    # VALIDACIÓN CONDICIONAL POR TIPO
    # ─────────────────────────────────────────────
    def validate(self, extra_validators=None):
        """Validación condicional según tipo de servicio."""
        self.admin_non_field_errors = []
        raw_tipo = (self.tipo_servicio.data or '').strip()
        tipo = canonical_service_code(raw_tipo)
        if tipo == "ENFERMERA":
            self.experiencia.choices = ENFERMERA_EXPERIENCE_CHOICES
        elif tipo == "CHOFER":
            self.experiencia.choices = [("Chofer", "Chofer")]
            self.experiencia.data = "Chofer"
        elif tipo == "LIMPIEZA_ESTABLECIMIENTOS":
            existing_exp = str(self.experiencia.data or '').strip()
            self.experiencia.choices = [("Limpieza", "Limpieza")] + ([(existing_exp, f"Experiencia histórica: {existing_exp}")] if existing_exp and existing_exp != "Limpieza" else [])
            if not existing_exp: self.experiencia.data = "Limpieza"
        elif tipo == "CONSERJERIA_RESIDENCIAL" and request.method == "POST" and getattr(request, "endpoint", "") == "admin.nueva_solicitud_admin":
            self.experiencia.choices = [("Conserjería", "Conserjería")]
            self.experiencia.data = "Conserjería"
        rv = super().validate(extra_validators)
        if (
            request.method == "POST"
            and getattr(request, "endpoint", "") == "admin.nueva_solicitud_admin"
            and len(request.form.getlist("edad_requerida")) > 1
        ):
            self.edad_requerida.errors.append("Selecciona una sola franja de edad.")
            rv = False
        if tipo == "LIMPIEZA_ESTABLECIMIENTOS":
            if request.method == "POST" and getattr(request, "endpoint", "") == "admin.nueva_solicitud_admin" and self.office_trabaja_con_companeros.data not in {"si", "no"}:
                self.office_trabaja_con_companeros.errors.append("Indica si trabajará con otros empleados de limpieza.")
                rv = False
            if self.office_trabaja_con_companeros.data == "si" and self.office_cantidad_companeros_limpieza.data is None:
                self.office_cantidad_companeros_limpieza.errors.append("Indica un número entero positivo de compañeros de limpieza.")
                rv = False
            if self.office_trabaja_con_companeros.data == "no":
                self.office_cantidad_companeros_limpieza.data = None
            if self.office_tipo_establecimiento.data == "otro" and not (self.office_tipo_establecimiento_otro.data or "").strip():
                self.office_tipo_establecimiento_otro.errors.append("Especifica el tipo de establecimiento.")
                rv = False
            if "otras" in (self.office_areas.data or []) and not (self.office_areas_otro.data or "").strip():
                self.office_areas_otro.errors.append("Especifica las otras áreas.")
                rv = False
            if "otras" in (self.office_funciones.data or []) and not (self.office_funciones_otro.data or "").strip():
                self.office_funciones_otro.errors.append("Especifica las otras funciones.")
                rv = False
        if tipo == "CONSERJERIA_RESIDENCIAL" and request.method == "POST" and getattr(request, "endpoint", "") == "admin.nueva_solicitud_admin":
            modality_group = str(request.form.get("modalidad_grupo") or "").strip()
            valid_modality, _label, _reason = validate_modalidad_context(
                modality_group, request.form.get("modalidad_especifica"), request.form.get("modalidad_trabajo"),
                other_value=request.form.get("modalidad_otro_text"),
            )
            duplicate_modality = any(len(request.form.getlist(name)) > 1 for name in ("modalidad_grupo", "modalidad_especifica", "modalidad_trabajo"))
            if modality_group != "con_salida_diaria" or not valid_modality or duplicate_modality:
                self.modalidad_trabajo.errors.append("Conserjería residencial requiere salida diaria.")
                rv = False
            if self.conserje_trabaja_con_conserjes.data == "no":
                self.conserje_cantidad_conserjes.data = None
        if tipo == "NINERA" and self.experiencia.data not in {"Niñera", "Enfermera"}:
            self.experiencia.errors.append("Para NIÑERA selecciona Niñera o Enfermera.")
            rv = False
        funciones_selected = self._selected_funciones_values()
        requiere_limpieza = 'limpieza' in funciones_selected
        requiere_adultos = self._requires_adultos_for_funciones(funciones_selected)

        # Las validaciones de vivienda solo corresponden al servicio doméstico.
        if tipo in ('DOMESTICA_LIMPIEZA', '') and requiere_limpieza:
            if not self.tipo_lugar.data:
                self.tipo_lugar.errors.append("Selecciona el tipo de lugar.")
                rv = False
            if self.habitaciones.data is None:
                self.habitaciones.errors.append("Indica cuántas habitaciones.")
                rv = False
            if self.banos.data is None:
                self.banos.errors.append("Indica la cantidad de baños.")
                rv = False
            if requiere_adultos and self.adultos.data is None:
                self.adultos.errors.append("Indica cuántos adultos hay en la casa.")
                rv = False

        # Los campos obligatorios específicos se definen una sola vez en el catálogo.
        if tipo in {"NINERA", "ENFERMERA", "CHOFER", "LIMPIEZA_ESTABLECIMIENTOS", "CONSERJERIA_RESIDENCIAL"}:
            is_new_admin_request = request.method == "POST" and getattr(request, "endpoint", "") == "admin.nueva_solicitud_admin"
            catalog_errors = validate_service_details(tipo, admin_details_from_form(tipo, self), strict_public=(tipo == "CONSERJERIA_RESIDENCIAL" and is_new_admin_request))
            if tipo == "ENFERMERA" and self.enf_cantidad_personas.data is None and not (self.enf_a_quien_cuida.data or "").strip():
                catalog_errors["cantidad_personas"] = "Indica cuántas personas necesitan cuidados."
            fields_by_key = {
                "cantidad_ninos": self.ninera_cant_ninos, "edades_ninos": self.ninera_edades,
                "necesidad_especial_respuesta": self.ninera_necesidad_especial_respuesta,
                "necesidad_especial_publicable": self.ninera_necesidad_especial_publicable,
                "ninera_responsable_principal": self.ninera_responsable_principal,
                "a_quien_cuida": self.enf_a_quien_cuida, "movilidad": self.enf_movilidad,
                "tareas": self.enf_tareas if tipo == "ENFERMERA" else self.ninera_tareas,
                "cantidad_personas": self.enf_cantidad_personas, "edad_persona": self.enf_edad_persona,
                "edades_personas": self.enf_edades_personas, "tipo_asistencia": self.enf_tipo_asistencia,
                "experiencia_requerida": self.experiencia,
                "condiciones": self.enf_condiciones, "condicion_otro": self.enf_condicion_otro,
                "ayuda_higiene": self.enf_ayuda_higiene, "ayuda_alimentacion": self.enf_ayuda_alimentacion,
                "enf_tareas_otro": self.enf_tareas_otro,
                "personas_cuidado": self.enf_personas_cuidado,
                "tipo_servicio": self.chofer_tipo_servicio, "tipo_vehiculo": self.chofer_tipo_vehiculo,
                "finalidad": self.chofer_tipo_servicio, "finalidad_otro": self.chofer_tipo_servicio_otro,
                "tipo_vehiculo_otro": self.chofer_tipo_vehiculo_otro, "rutas_otro": self.chofer_rutas_otro,
                "categoria_licencia_requerida": self.chofer_categoria_licencia, "rutas": self.chofer_rutas,
                "tipo_establecimiento": self.office_tipo_establecimiento, "tipo_establecimiento_otro": self.office_tipo_establecimiento_otro,
                "tamano": self.office_tamano, "areas": self.office_areas, "areas_legacy": self.office_areas_legacy, "areas_otro": self.office_areas_otro,
                "funciones": self.office_funciones, "funciones_otro": self.office_funciones_otro,
                "tipo_instalacion": self.conserje_tipo_instalacion,
                "tipo_residencial": self.conserje_tipo_residencial, "tipo_residencial_otro": self.conserje_tipo_residencial_otro,
                "niveles": self.conserje_niveles, "apartamentos": self.conserje_apartamentos, "ascensores": self.conserje_ascensores,
                "cantidad_edificios": self.conserje_cantidad_edificios, "casas": self.conserje_cantidad_casas, "unidades": self.conserje_unidades,
                "tiene_ascensor": self.conserje_tiene_ascensor, "parqueos_comunes": self.conserje_parqueos_comunes,
                "areas_recreativas": self.conserje_areas_recreativas, "jardines_comunes": self.conserje_jardines_comunes,
                "calles_internas": self.conserje_calles_internas,
                "escaleras": self.conserje_escaleras, "banos": self.conserje_banos, "estacionamiento": self.conserje_estacionamiento,
                "instalaciones": self.conserje_instalaciones, "otras_instalaciones": self.conserje_otras_instalaciones,
                "responsabilidades": self.conserje_responsabilidades, "responsabilidades_otro": self.conserje_responsabilidades_otro,
                "trabaja_con_conserjes": self.conserje_trabaja_con_conserjes, "cantidad_conserjes": self.conserje_cantidad_conserjes,
                "formacion_requerida": self.enf_formacion_requerida,
            }
            for key, message in catalog_errors.items():
                field = fields_by_key.get(key)
                if field is not None:
                    field.errors.append(message)
                else:
                    self.admin_non_field_errors.append(f"{key.replace('_', ' ').strip().capitalize()}: {message}")
                rv = False

        # Para CHOFER: no exigimos hogar/ocupantes; pedimos lo mínimo de chofer
        if tipo == 'CHOFER':
            # Quitamos posibles errores de hogar/ocupantes si quedaron
            for f in (self.tipo_lugar, self.habitaciones, self.banos, self.adultos):
                f.errors.clear()
        elif requiere_adultos and self.adultos.data is None:
            if "Indica cuántos adultos." not in (self.adultos.errors or []):
                self.adultos.errors.append("Indica cuántos adultos.")
            rv = False

        modalidad_group = ""
        try:
            modalidad_group = str((request.form or {}).get("modalidad_grupo") or "").strip()
        except Exception:
            modalidad_group = ""
        horario_txt, _payload, horario_errors = build_horario_from_form(
            modalidad_group=modalidad_group,
            modalidad_trabajo=self.modalidad_trabajo.data,
            dias_trabajo=(request.form or {}).get("horario_dias_trabajo"),
            hora_entrada=(request.form or {}).get("horario_hora_entrada"),
            hora_salida=(request.form or {}).get("horario_hora_salida"),
            dormida_entrada=(request.form or {}).get("horario_dormida_entrada"),
            dormida_salida=(request.form or {}).get("horario_dormida_salida"),
            horario_legacy=self.horario.data,
        )
        self.horario.data = horario_txt
        if horario_errors:
            for msg in horario_errors:
                if msg not in (self.horario.errors or []):
                    self.horario.errors.append(msg)
            rv = False
        if tipo == 'CHOFER':
            if not self.chofer_vehiculo.data:
                self.chofer_vehiculo.errors.append("Indica si usará vehículo del cliente o propio.")
                rv = False
        requiere_envejeciente = "envejeciente" in _clean_list_envejeciente(funciones_selected)
        tipo_cuidado = (self.envejeciente_tipo_cuidado.data or "").strip().lower()
        responsabilidades = _clean_list_envejeciente(self.envejeciente_responsabilidades.data or [])
        solo_acomp = bool(self.envejeciente_solo_acompanamiento.data)
        if requiere_envejeciente:
            if tipo_cuidado not in {"independiente", "encamado"}:
                self.envejeciente_tipo_cuidado.errors.append("Selecciona el tipo de cuidado del envejeciente.")
                rv = False
            if tipo_cuidado == "encamado" and (not solo_acomp) and not responsabilidades:
                self.envejeciente_responsabilidades.errors.append(
                    "Para encamado debes marcar responsabilidades o seleccionar solo acompanamiento/supervision."
                )
                rv = False

        raw_plan = (self.tipo_plan.data or "").strip()
        if raw_plan and not is_valid_plan(raw_plan):
            self.tipo_plan.errors = ["Selecciona un plan válido: Básico, Premium o VIP."]
            rv = False
        elif raw_plan:
            self.tipo_plan.data = normalize_plan(raw_plan)

        return rv


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
        validators=[Optional(), Length(max=20)],
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
        validators=[Optional(), Length(max=100)],
        render_kw={"placeholder": "Ej. 10,000"}
    )
    submit = SubmitField('Registrar pago')




# =============================================================================
#                                REEMPLAZO
# =============================================================================


class AdminReemplazoForm(FlaskForm):
    """Iniciar reemplazo (SIN búsqueda).

    La candidata que falló se toma automáticamente desde la solicitud:
    - sol.candidata / sol.candidata_id

    `candidata_old_id` se mantiene como HiddenField para poder enviar/guardar el id,
    pero la regla real se valida en la ruta.
    """

    # ✅ Ahora no es SelectField. No necesita choices.
    candidata_old_id = HiddenField()

    # ✅ Opcional: para rehidratar UI si quieres mostrar el nombre sin otra consulta
    candidata_old_name = HiddenField(validators=[Optional(), Length(max=200)])

    motivo_fallo = TextAreaField(
        'Motivo del fallo',
        validators=[DataRequired(message="Indica el motivo."), Length(max=500)],
        render_kw={
            "rows": 3,
            "placeholder": "Describe brevemente el motivo del reemplazo..."
        }
    )

    descalificar_candidata_fallida = BooleanField(
        "¿Descalificar candidata que falló?"
    )

    motivo_descalificacion = TextAreaField(
        "Motivo descalificación",
        validators=[Optional(), Length(max=1000)],
        render_kw={
            "rows": 3,
            "placeholder": "Obligatorio si marcas descalificación."
        }
    )

    nota_adicional = TextAreaField(
        'Nota interna (opcional)',
        validators=[Optional(), Length(max=1000)],
        render_kw={
            "rows": 3,
            "placeholder": "Detalles adicionales para el seguimiento (opcional)."
        }
    )

    submit = SubmitField('Abrir reemplazo')


class AdminReemplazoFinForm(FlaskForm):
    """Finalizar reemplazo.

    Este flujo es server-side (SIN JS):
    - La ruta arma `choices` según ?q= (igual que en pago)
    - El usuario selecciona en un <select>
    """

    candidata_new_id = SelectField(
        'Asignar candidata (reemplazo)',
        coerce=int,
        validators=[DataRequired(message='Debes seleccionar la nueva candidata.')]
    )

    # (Opcional) se puede seguir usando para rehidratar UI o logs, pero NO es obligatorio
    candidata_new_name = HiddenField(validators=[Optional(), Length(max=200)])

    nota_adicional = TextAreaField(
        'Notas sobre el reemplazo',
        validators=[Optional(), Length(max=1000)],
        render_kw={
            "rows": 3,
            "placeholder": "Notas internas sobre este cierre de reemplazo (opcional)..."
        }
    )

    submit = SubmitField('Finalizar reemplazo')
