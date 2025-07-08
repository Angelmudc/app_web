# clientes/forms.py

from flask_wtf import FlaskForm
from wtforms import (
    StringField, PasswordField, RadioField, SelectField, SelectMultipleField,
    TextAreaField, BooleanField, IntegerField, FloatField, SubmitField
)
from wtforms.validators import DataRequired, Length, Email, NumberRange, Optional
from wtforms.widgets import ListWidget, CheckboxInput

from admin.routes import AREAS_COMUNES_CHOICES


class ClienteLoginForm(FlaskForm):
    username = StringField(
        'Usuario',
        validators=[DataRequired(), Length(min=4, max=50)]
    )
    password = PasswordField(
        'Contraseña',
        validators=[DataRequired(), Length(min=8)]
    )


class ClienteSolicitudForm(FlaskForm):
    areas_comunes = SelectMultipleField(
        'Áreas comunes',
        choices=AREAS_COMUNES_CHOICES,
        validators=[DataRequired()],
        option_widget=CheckboxInput(),
        widget=ListWidget(prefix_label=False)
    )
    area_otro = StringField(
        'Otra área (si aplica)',
        validators=[Optional()]
    )
    detalles = TextAreaField(
        'Detalle adicional',
        validators=[DataRequired(), Length(min=5)]
    )


class ClienteCancelForm(FlaskForm):
    motivo = TextAreaField(
        'Motivo de cancelación',
        validators=[DataRequired(), Length(min=5)]
    )


from flask_wtf import FlaskForm
from wtforms import (
    StringField,
    SelectField,
    SelectMultipleField,
    TextAreaField,
    BooleanField,
    IntegerField,
    FloatField,
    SubmitField
)
from wtforms.validators import DataRequired, NumberRange, Optional
from wtforms.widgets import ListWidget, CheckboxInput

class SolicitudForm(FlaskForm):
    # Ubicación y rutas
    ciudad_sector    = StringField(
        'Ciudad / Sector',
        validators=[DataRequired()]
    )
    rutas_cercanas   = StringField(
        'Rutas de transporte cercanas',
        validators=[DataRequired()]
    )

    # Detalles de la oferta de trabajo
    modalidad_trabajo = StringField(
        'Modalidad de trabajo',
        validators=[DataRequired()]
    )
    edad_requerida    = SelectMultipleField(
        'Edad del personal',
        choices=[
            ('18-25', '18-25'),
            ('26-35', '26-35'),
            ('25+', '25 en adelante'),
            ('45+', 'Mayor de 45'),
            ('otro', 'Otro'),
        ],
        validators=[DataRequired()],
        option_widget=CheckboxInput(),
        widget=ListWidget(prefix_label=False)
    )
    experiencia       = TextAreaField(
        'Tipo de experiencia requerida',
        validators=[DataRequired()]
    )
    horario           = StringField(
        'Defina el horario de trabajo',
        validators=[DataRequired()]
    )
    funciones         = SelectMultipleField(
        'Funciones a realizar al personal',
        choices=[
            ('limpieza','Limpieza General'),
            ('cocinar','Cocinar'),
            ('lavar','Lavar'),
            ('ninos','Cuidar Niños'),
            ('envejeciente','Cuidar envejecientes'),
            ('otro','Otro'),
        ],
        validators=[DataRequired()],
        option_widget=CheckboxInput(),
        widget=ListWidget(prefix_label=False)
    )

    # Tipo de lugar
    tipo_lugar       = SelectField(
        'Tipo de lugar',
        choices=[
            ('casa', 'Casa'),
            ('oficina', 'Oficina'),
            ('apto', 'Apartamento'),
            ('otro', 'Otro'),
        ],
        validators=[DataRequired()]
    )

    # Habitaciones y baños
    habitaciones     = IntegerField(
        'Habitaciones',
        validators=[DataRequired(), NumberRange(min=0)]
    )
    banos            = FloatField(
        'Baños',
        validators=[DataRequired(), NumberRange(min=0)]
    )
    dos_pisos        = BooleanField('Dos pisos')

    # Ocupantes
    adultos          = IntegerField(
        'Cantidad de adultos',
        validators=[DataRequired(), NumberRange(min=0)]
    )
    ninos            = IntegerField(
        'Cantidad de niños',
        validators=[DataRequired(), NumberRange(min=0)]
    )
    edades_ninos     = StringField(
        'Edades de los niños',
        validators=[Optional()]
    )

    # Compensación
    sueldo           = StringField(
        'Sueldo a pagar',
        validators=[DataRequired()]
    )
    pasaje_aporte    = BooleanField('Pasaje aporta')

    # Nota del cliente
    nota_cliente     = TextAreaField(
        'Nota adicional',
        validators=[Optional()]
    )

    # Áreas comunes
    areas_comunes    = SelectMultipleField(
        'Áreas comunes',
        choices=[
            ('sala','Sala'),('comedor','Comedor'),('cocina','Cocina'),
            ('juegos','Salón de juegos'),('terraza','Terraza'),('jardin','Jardín'),
            ('estudio','Estudio'),('patio','Patio'),('piscina','Piscina'),
            ('marquesina','Marquesina'),('todas','Todas las anteriores'),('otro','Otro'),
        ],
        validators=[DataRequired()],
        option_widget=CheckboxInput(),
        widget=ListWidget(prefix_label=False)
    )
    area_otro        = StringField(
        'Otra área',
        validators=[Optional()]
    )

    submit           = SubmitField('Enviar')
