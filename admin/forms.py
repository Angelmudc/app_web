from flask_wtf import FlaskForm
from wtforms import (
    StringField, PasswordField, TextAreaField, BooleanField,
    IntegerField, HiddenField, SelectField, SelectMultipleField,
    DecimalField, SubmitField, RadioField
)
from wtforms.validators import (
    DataRequired, InputRequired, Optional, Length,
    NumberRange, EqualTo
)
from wtforms.widgets import ListWidget, CheckboxInput

# Campo de checkbox múltiple personalizado para WTForms
class MultiCheckboxField(SelectMultipleField):
    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()

class AdminLoginForm(FlaskForm):
    username = StringField(
        'Usuario',
        validators=[DataRequired(), Length(max=50)]
    )
    password = PasswordField(
        'Contraseña',
        validators=[DataRequired(), Length(max=100)]
    )
    submit = SubmitField('Iniciar sesión')

class AdminClienteForm(FlaskForm):
    codigo = StringField(
        'Código cliente',
        validators=[DataRequired(), Length(max=20)],
        render_kw={"placeholder": "p.ej. C001"}
    )
    nombre_completo = StringField(
        'Nombre completo',
        validators=[DataRequired(), Length(max=200)]
    )
    email = StringField(
        'Email',
        validators=[DataRequired(), Length(max=100)]
    )
    telefono = StringField(
        'Teléfono',
        validators=[DataRequired(), Length(max=20)]
    )
    ciudad = StringField(
        'Ciudad',
        validators=[Optional(), Length(max=100)]
    )
    sector = StringField(
        'Sector',
        validators=[Optional(), Length(max=100)]
    )
    notas_admin = TextAreaField(
        'Notas administrativas',
        validators=[Optional()]
    )
    submit = SubmitField('Guardar cliente')

class AdminSolicitudForm(FlaskForm):
    ciudad_sector     = StringField(
        'Ciudad / Sector',
        validators=[DataRequired(), Length(max=200)]
    )
    rutas_cercanas    = StringField(
        'Rutas cercanas',
        validators=[Optional(), Length(max=200)]
    )
    modalidad_trabajo = StringField(
        'Modalidad trabajo',
        validators=[DataRequired(), Length(max=100)]
    )

    edad_requerida = RadioField(
        'Edad requerida',
        choices=[
            ('18-25', '18–25 años'),
            ('26-35', '26–35 años'),
            ('36-45', '36–45 años'),
            ('mayor45',   'Mayor de 45'),   # <— cambiamos el value
            ('otra',  'Otra...')
        ],
        validators=[InputRequired()],
        coerce=str
    )
    edad_otro = StringField(
        'Otra edad',
        validators=[Optional(), Length(max=50)],
        render_kw={"placeholder": "Especifique otra edad"}
    )

    experiencia = TextAreaField(
        'Experiencia',
        validators=[DataRequired()]
    )
    horario = StringField(
        'Horario',
        validators=[DataRequired(), Length(max=100)]
    )

    funciones = MultiCheckboxField(
        'Funciones a realizar al personal',
        choices=[
            ('limpieza',     'Limpieza General'),
            ('cocinar',      'Cocinar'),
            ('lavar',        'Lavar'),
            ('ninos',        'Cuidar Niños'),
            ('envejeciente', 'Cuidar Envejecientes'),
            ('otro',         'Otro'),
        ],
        validators=[DataRequired()]
    )

    tipo_lugar = SelectField(
        'Tipo de lugar',
        choices=[
            ('casa',    'Casa'),
            ('oficina', 'Oficina'),
            ('apto',    'Apto'),
            ('otro',    'Otro')
        ],
        validators=[DataRequired()]
    )
    habitaciones = IntegerField(
        'Habitaciones',
        validators=[DataRequired(), NumberRange(min=0)]
    )
    banos = DecimalField(
        'Baños',
        places=1,
        validators=[DataRequired(), NumberRange(min=0)],
        render_kw={"step": "0.5", "min": "0"}
    )
    dos_pisos = BooleanField('¿Tiene dos pisos?')

    areas_comunes = MultiCheckboxField(
        'Áreas comunes',
        choices=[],  # rellenas en la vista con AREAS_COMUNES_CHOICES
        default=[],
        validators=[Optional()]
    )
    area_otro = StringField(
        'Otra área',
        validators=[Optional(), Length(max=200)]
    )

    adultos = IntegerField(
        'Adultos',
        validators=[DataRequired(), NumberRange(min=0)]
    )
    ninos = IntegerField(
        'Niños',
        validators=[Optional(), NumberRange(min=0)],
        render_kw={"min": "0"}
    )
    edades_ninos = StringField(
        'Edades niños',
        validators=[Optional(), Length(max=100)]
    )

    sueldo = StringField(
        'Sueldo',
        validators=[DataRequired(), Length(max=50)],
        render_kw={"placeholder": "Ej. 23,000 mensual"}
    )

    pasaje_aporte = RadioField(
        '¿Aporta pasaje?',
        choices=[
            ('1', 'Sí, aporta pasaje'),
            ('0', 'No aporta pasaje')
        ],
        validators=[InputRequired()],
        coerce=lambda v: v == '1'
    )

    nota_cliente = TextAreaField(
        'Nota adicional',
        validators=[Optional()]
    )
    submit = SubmitField('Guardar solicitud')

class AdminGestionPlanForm(FlaskForm):
    tipo_plan = StringField(
        'Tipo de plan',
        validators=[DataRequired(), Length(max=50)]
    )
    abono = StringField(
        'Abono',
        validators=[DataRequired(), Length(max=20)]
    )
    submit = SubmitField('Guardar plan')

class AdminPagoForm(FlaskForm):
    candidata_id = SelectField(
        'Asignar candidata',
        coerce=int,
        validators=[DataRequired()]
    )
    monto_pagado = StringField(
        'Monto pagado',
        validators=[DataRequired(), Length(max=100)],
        render_kw={"placeholder": "Ej. 10,000"}
    )
    submit = SubmitField('Registrar pago')

class AdminReemplazoForm(FlaskForm):
    candidata_old_id = HiddenField(
        validators=[DataRequired()]
    )
    candidata_old_name = StringField(
        'Candidata que falló',
        validators=[DataRequired(), Length(max=200)]
    )
    motivo_fallo = TextAreaField(
        'Motivo del fallo',
        validators=[DataRequired(), Length(max=500)]
    )
    fecha_inicio_reemplazo = StringField(
        'Fecha inicio del reemplazo',
        validators=[DataRequired(), Length(max=16)],
        render_kw={"type": "datetime-local"}
    )
    submit = SubmitField('Activar reemplazo')
