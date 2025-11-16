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

    # ✅ Edad requerida como CHECKBOX múltiple (sincronizado con cliente y template)
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
        validators=[Optional()],
        coerce=str,
        default=[]
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
        validators=[DataRequired("Selecciona el tipo de lugar.")],
        coerce=str
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
        validators=[Optional()],
        coerce=str,
        default=[]
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
        validators=[DataRequired("Indica el sueldo.")],  # longitud libre por si lleva formato
        render_kw={"placeholder": "Ej. 23,000 mensual"}
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
    # ID de la candidata que falló (se pasa oculto desde la vista)
    candidata_old_id = HiddenField(validators=[DataRequired()])

    # Nombre solo para mostrarlo en el formulario
    candidata_old_name = StringField(
        'Candidata que falló',
        validators=[DataRequired("Indica la candidata."), Length(max=200)]
    )

    # Motivo por el cual se activa el reemplazo
    motivo_fallo = TextAreaField(
        'Motivo del fallo',
        validators=[DataRequired("Indica el motivo."), Length(max=500)],
        render_kw={"rows": 3}
    )

    # Nota interna opcional (se guarda en Reemplazo.nota_adicional si la usas)
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
    """
    Formulario para FINALIZAR el reemplazo:
    - Seleccionar la nueva candidata enviada.
    - Guardar nota opcional sobre el reemplazo.
    """

    # ID de la nueva candidata (viaja oculto, se llena desde búsqueda/autocomplete)
    candidata_new_id = HiddenField(
        'ID nueva candidata',
        validators=[DataRequired(message='Debes seleccionar la nueva candidata.')]
    )

    # Nombre de la nueva candidata: aquí TIENES que poder escribir para buscar
    candidata_new_name = StringField(
        'Nueva candidata seleccionada',
        validators=[DataRequired(message='Escribe o selecciona la nueva candidata.')],
        render_kw={
            "placeholder": "Buscar nueva candidata..."
            # OJO: NADA de readonly aquí
        }
    )

    # Nota opcional sobre el reemplazo
    nota_adicional = TextAreaField(
        'Notas sobre el reemplazo',
        validators=[Optional(), Length(max=1000)],
        render_kw={
            "rows": 3,
            "placeholder": "Notas internas sobre este cierre de reemplazo (opcional)..."
        }
    )

    submit = SubmitField('Finalizar reemplazo')
