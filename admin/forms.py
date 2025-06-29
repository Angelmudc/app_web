# admin/forms.py

from flask_wtf import FlaskForm
from wtforms import (
    StringField,
    TextAreaField,
    BooleanField,
    IntegerField,
    HiddenField,
    SelectField,
    SubmitField,
    SelectMultipleField
)
from wtforms.validators import DataRequired, Length, Optional, NumberRange
from wtforms.widgets import ListWidget, CheckboxInput

# --------------------------------------------------------------------
# Campo de “checkbox múltiple” personalizado para WTForms:
class MultiCheckboxField(SelectMultipleField):
    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()
# --------------------------------------------------------------------

class AdminLoginForm(FlaskForm):
    username = StringField('Usuario', validators=[DataRequired(), Length(max=50)])
    password = StringField('Contraseña', validators=[DataRequired(), Length(max=100)])


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
    direccion = StringField(
        'Dirección',
        validators=[Optional(), Length(max=200)]
    )
    ciudad = StringField(
        'Ciudad',
        validators=[Optional(), Length(max=100)]
    )
    provincia = StringField(
        'Provincia',
        validators=[Optional(), Length(max=100)]
    )
    notas_admin = TextAreaField(
        'Notas administrativas',
        validators=[Optional()]
    )


class AdminSolicitudForm(FlaskForm):
    codigo_solicitud = StringField(
        'Código de solicitud',
        validators=[Optional(), Length(max=50)],
        render_kw={"placeholder": "p.ej. C001-A"}
    )

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
    edad_requerida    = StringField(
        'Edad requerida',
        validators=[DataRequired(), Length(max=50)]
    )
    experiencia       = TextAreaField(
        'Experiencia',
        validators=[DataRequired()]
    )
    horario           = StringField(
        'Horario',
        validators=[DataRequired(), Length(max=100)]
    )
    funciones         = TextAreaField(
        'Funciones',
        validators=[DataRequired()]
    )

    tipo_lugar = SelectField(
        'Tipo de lugar',
        choices=[
            ('casa', 'Casa'),
            ('oficina', 'Oficina'),
            ('apto', 'Apto'),
            ('otro', 'Otro')
        ],
        validators=[DataRequired()]
    )

    habitaciones = IntegerField(
        'Habitaciones',
        validators=[DataRequired(), NumberRange(min=0)]
    )
    banos = IntegerField(
        'Baños',
        validators=[DataRequired(), NumberRange(min=0)]
    )
    dos_pisos = BooleanField('¿Tiene dos pisos?')

    areas_comunes = MultiCheckboxField(
        'Áreas comunes',
        choices=[],  # Se define en la vista con AREAS_COMUNES_CHOICES
        default=[],
        validators=[Optional()]
    )
    area_otro     = StringField(
        'Otra área',
        validators=[Optional(), Length(max=200)]
    )

    adultos = IntegerField(
        'Adultos',
        validators=[DataRequired(), NumberRange(min=0)]
    )
    ninos = IntegerField(
        'Niños',
        validators=[DataRequired(), NumberRange(min=0)]
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
    pasaje_aporte = BooleanField('¿Aporta pasaje?')
    nota_cliente  = TextAreaField(
        'Nota adicional',
        validators=[Optional()]
    )


class AdminGestionPlanForm(FlaskForm):
    tipo_plan = StringField('Tipo de plan', validators=[DataRequired(), Length(max=50)])
    abono     = StringField('Abono',         validators=[DataRequired(), Length(max=20)])


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
    candidata_old_id   = HiddenField(validators=[DataRequired()])
    candidata_old_name = StringField(
        'Candidata que falló',
        validators=[DataRequired(), Length(max=200)]
    )

    motivo_fallo           = TextAreaField(
        'Motivo del fallo',
        validators=[DataRequired(), Length(max=500)]
    )
    fecha_inicio_reemplazo = StringField(
        'Fecha inicio del reemplazo',
        validators=[DataRequired(), Length(max=16)],
        render_kw={"type": "datetime-local"}
    )

    submit = SubmitField('Activar Reemplazo')
