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
        validators=[Optional(), Length(min=6, max=100)],
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
        # Reglas mínimas: 6+ ya la valida Length; aquí evitamos solo espacios.
        if len(pw) < 6:
            raise ValidationError('La contraseña debe tener al menos 6 caracteres.')

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
    # ─────────────────────────────────────────────
    # TIPO DE SOLICITUD
    # ─────────────────────────────────────────────
    tipo_servicio = SelectField(
        'Tipo de solicitud',
        choices=[
            ('DOMESTICA_LIMPIEZA', 'Doméstica de limpieza / general'),
            ('NINERA',             'Niñera'),
            ('ENFERMERA',          'Enfermera / Cuidadora'),
            ('CHOFER',             'Chofer'),
        ],
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

    funciones = MultiCheckboxField(
        'Funciones a realizar al personal',
        choices=[
            ('limpieza',     'Limpieza General'),
            ('cocinar',      'Cocinar'),
            ('lavar',        'Lavar'),
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
        places=1,
        validators=[Optional(), NumberRange(min=0)],
        render_kw={"min": "0", "step": "0.5"}
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
        'Tareas principales con los niños',
        choices=[
            ('jugar',          'Jugar y actividades'),
            ('tareas',         'Apoyo con tareas'),
            ('llevar_colegio', 'Llevar / buscar al colegio'),
            ('alimentar',      'Alimentarlos'),
            ('banar',          'Bañarlos'),
            ('dormir',         'Ayudar a dormir'),
            ('otro',           'Otro'),
        ],
        validators=[Optional()],
        coerce=str,
        default=[]
    )
    ninera_tareas_otro = StringField(
        'Otras tareas con los niños',
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Especifica tareas adicionales (opcional)"}
    )
    ninera_condicion_especial = TextAreaField(
        'Condiciones especiales (salud / comportamiento)',
        validators=[Optional(), Length(max=1000)],
        render_kw={"rows": 3, "placeholder": "Ej. alergias, TDAH, autismo… (opcional)"}
    )

    # ─────────────────────────────────────────────
    # CAMPOS ESPECÍFICOS – ENFERMERA / CUIDADORA
    # ─────────────────────────────────────────────
    enf_a_quien_cuida = StringField(
        '¿A quién debe cuidar?',
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Ej. señora de 80 años, paciente postrado…"}
    )
    enf_movilidad = SelectField(
        'Movilidad de la persona',
        choices=[
            ('',          'Selecciona…'),
            ('autonomo',  'Se mueve sola'),
            ('parcial',   'Necesita ayuda parcial'),
            ('postrado',  'Postrado en cama'),
        ],
        validators=[Optional()],
        coerce=str
    )
    enf_condicion_principal = TextAreaField(
        'Condición principal / diagnóstico',
        validators=[Optional(), Length(max=1000)],
        render_kw={"rows": 3, "placeholder": "Ej. Alzheimer, demencia, ACV, etc."}
    )
    enf_tareas = MultiCheckboxField(
        'Tareas principales de cuidado',
        choices=[
            ('medicacion', 'Administrar medicamentos'),
            ('aseo',       'Aseo personal'),
            ('alimentar',  'Alimentación'),
            ('movilizar',  'Ayudar a movilizar / caminar'),
            ('control',    'Control de signos / chequeos básicos'),
            ('otro',       'Otro'),
        ],
        validators=[Optional()],
        coerce=str,
        default=[]
    )
    enf_tareas_otro = StringField(
        'Otras tareas de cuidado',
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Especifica tareas adicionales (opcional)"}
    )

    # ─────────────────────────────────────────────
    # CAMPOS ESPECÍFICOS – CHOFER
    # ─────────────────────────────────────────────
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
    chofer_rutas = StringField(
        'Rutas habituales',
        validators=[Optional(), Length(max=255)],
        render_kw={"placeholder": "Ej. Dentro de la ciudad, carretera, aeropuerto…"}
    )
    chofer_viajes_largos = RadioField(
        '¿Hará viajes largos / fuera de la ciudad?',
        choices=[('1', 'Sí'), ('0', 'No')],
        validators=[Optional()],
        coerce=lambda v: v == '1'
    )
    chofer_licencia_detalle = StringField(
        'Licencia / experiencia manejando',
        validators=[Optional(), Length(max=255)],
        render_kw={"placeholder": "Ej. Licencia cat. 3, maneja mecánico y automático."}
    )

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

    # Nota
    nota_cliente = TextAreaField(
        'Nota adicional',
        validators=[Optional(), Length(max=1000)],
        render_kw={"placeholder": "Información útil para el proceso (opcional)", "rows": 3}
    )

    submit = SubmitField('Guardar solicitud')

    # ─────────────────────────────────────────────
    # VALIDACIÓN CONDICIONAL POR TIPO
    # ─────────────────────────────────────────────
    def validate(self, extra_validators=None):
        """Validación condicional según tipo de servicio."""
        rv = super().validate(extra_validators)
        tipo = (self.tipo_servicio.data or '').strip()

        # Para DOMÉSTICA y ENFERMERA: hogar obligatorio
        if tipo in ('DOMESTICA_LIMPIEZA', 'ENFERMERA', ''):
            if not self.tipo_lugar.data:
                self.tipo_lugar.errors.append("Selecciona el tipo de lugar.")
                rv = False
            if self.habitaciones.data is None:
                self.habitaciones.errors.append("Indica cuántas habitaciones.")
                rv = False
            if self.banos.data is None:
                self.banos.errors.append("Indica la cantidad de baños.")
                rv = False
            if self.adultos.data is None:
                self.adultos.errors.append("Indica cuántos adultos hay en la casa.")
                rv = False

        # Para NIÑERA: no exigimos casa/baños, pero sí niños básicos
        if tipo == 'NINERA':
            # Si no llenan cantidad de niños, al menos avisa
            if self.ninera_cant_ninos.data is None or self.ninera_cant_ninos.data <= 0:
                self.ninera_cant_ninos.errors.append("Indica cuántos niños va a cuidar.")
                rv = False
            if not (self.ninera_edades.data or "").strip():
                self.ninera_edades.errors.append("Especifica las edades de los niños.")
                rv = False

        # Para ENFERMERA: detalles mínimos de paciente
        if tipo == 'ENFERMERA':
            if not (self.enf_a_quien_cuida.data or "").strip():
                self.enf_a_quien_cuida.errors.append("Indica a quién debe cuidar.")
                rv = False
            if self.enf_movilidad.data is None or self.enf_movilidad.data == '':
                self.enf_movilidad.errors.append("Indica la movilidad de la persona.")
                rv = False

        # Para CHOFER: no exigimos hogar/ocupantes; pedimos lo mínimo de chofer
        if tipo == 'CHOFER':
            # Quitamos posibles errores de hogar/ocupantes si quedaron
            for f in (self.tipo_lugar, self.habitaciones, self.banos, self.adultos):
                f.errors.clear()

            if not self.chofer_vehiculo.data:
                self.chofer_vehiculo.errors.append("Indica si usará vehículo del cliente o propio.")
                rv = False
            if not self.chofer_tipo_vehiculo.data:
                self.chofer_tipo_vehiculo.errors.append("Selecciona el tipo de vehículo.")
                rv = False

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

    nota_adicional = TextAreaField(
        'Nota interna (opcional)',
        validators=[Optional(), Length(max=1000)],
        render_kw={
            "rows": 3,
            "placeholder": "Detalles adicionales para el seguimiento (opcional)."
        }
    )

    submit = SubmitField('Activar reemplazo')


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