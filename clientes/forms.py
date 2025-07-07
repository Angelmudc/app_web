# clientes/forms.py

from flask_wtf import FlaskForm
from wtforms import (
    StringField,
    PasswordField,
    RadioField,
    SelectMultipleField,
    TextAreaField,
    BooleanField,
    SubmitField
)
from wtforms.validators import DataRequired, Length, Email
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
from wtforms.widgets import ListWidget, CheckboxInput


class ClienteSolicitudForm(FlaskForm):
    areas_comunes = SelectMultipleField(
        'Áreas comunes',
        choices=AREAS_COMUNES_CHOICES,
        validators=[DataRequired()]
    )
    area_otro = StringField('Otra área (si aplica)')
    detalles = TextAreaField(
        'Detalle adicional',
        validators=[DataRequired(), Length(min=5)]
    )

class ClienteCancelForm(FlaskForm):
    motivo = TextAreaField(
        'Motivo de cancelación',
        validators=[DataRequired(), Length(min=5)]
    )


class SolicitudForm(FlaskForm):
    correo = StringField('Correo', validators=[DataRequired(), Email()])
    nombre = StringField('Nombre completo', validators=[DataRequired()])
    prev_solicitud = RadioField(
        '¿Has solicitado anteriormente en esta agencia?',
        choices=[('si','Sí'), ('no','No')],
        validators=[DataRequired()]
    )
    telefono = StringField('Teléfono', validators=[DataRequired()])
    ciudad = StringField('Ciudad', validators=[DataRequired()])
    sector = StringField('Sector', validators=[DataRequired()])
    rutas = StringField('Rutas de transporte cercanas', validators=[DataRequired()])
    con_dormida = RadioField(
        'Con dormida',
        choices=[
            ('lunes_sabado','Lunes a sábado, sale sábado después del medio día'),
            ('quincenal','Salida quincenal, sale viernes después del medio día'),
            ('lunes_viernes','Lunes a Viernes'),
            ('viernes_lunes','Viernes a Lunes'),
            ('sab_dom','Sábado y Domingo'),
            ('otro','Otro'),
        ],
        validators=[DataRequired()]
    )
    con_salida = RadioField(
        'Con salida diaria',
        choices=[
            ('1xsemana','Un día a la semana'),
            ('2xsemana','Dos días a la semana'),
            ('3xsemana','Tres días a la semana'),
            ('lv','Lunes a Viernes'),
            ('vl','Viernes a Lunes'),
            ('sd','Sábado y Domingo'),
            ('ls','Lunes a Sábado'),
            ('otro','Otro'),
        ],
        validators=[DataRequired()]
    )
    horario = StringField('Defina el horario de trabajo', validators=[DataRequired()])

    # Renderizar como checkboxes
    edad = SelectMultipleField(
        'Edad del personal',
        choices=[
            ('18-25','18-25'),
            ('26-35','26-35'),
            ('25+','25 en adelante'),
            ('45+','Mayor de 45'),
            ('otro','Otro'),
        ],
        validators=[DataRequired()],
        option_widget=CheckboxInput(),
        widget=ListWidget(prefix_label=False)
    )

    nacionalidad = RadioField(
        'Nacionalidad deseada',
        choices=[('dominicana','Dominicana'), ('indiferente','Indiferente'), ('otro','Otro')]
    )
    nivel_acad = RadioField(
        'Nivel académico',
        choices=[
            ('basico','Básico'),
            ('media','Media'),
            ('leer_escribir','Que sepa leer y escribir'),
            ('universitario','Universitario'),
            ('otro','Otro'),
        ]
    )

    experiencia = SelectMultipleField(
        'Tipo de experiencia requerida',
        choices=[
            ('limpieza','Limpieza general'),
            ('lavar','Lavar'),
            ('ninos','Cuidar Niños'),
            ('cocinar','Cocinar'),
            ('enfermeria','Enfermería'),
            ('envejeciente','Cuidar envejeciente'),
            ('adulto_mayor','Acompañar adulto mayor'),
            ('otro','Otro'),
        ],
        validators=[DataRequired()],
        option_widget=CheckboxInput(),
        widget=ListWidget(prefix_label=False)
    )

    funciones = SelectMultipleField(
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

    tipo_domi = RadioField(
        'Tipo de domicilio',
        choices=[
            ('casa','Casa'),
            ('apto','Apartamento'),
            ('villa','Villa'),
            ('oficina','Oficina'),
            ('airbnb','AirBnB'),
            ('plaza','Plaza'),
            ('residencial','Residencial'),
            ('restaurante','Restaurante'),
            ('otro','Otro'),
        ],
        validators=[DataRequired()]
    )
    habitaciones = RadioField(
        'Habitaciones',
        choices=[('2','2'),('3','3'),('4','4'),('5','5'),('otro','Otro')],
        validators=[DataRequired()]
    )
    banos = RadioField(
        'Baños',
        choices=[('1','1'),('2','2'),('2.5','2.5'),('3','3'),('3.5','3.5'),('4','4'),('5','5'),('otro','Otro')],
        validators=[DataRequired()]
    )

    areas_comunes = SelectMultipleField(
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
    area_otro    = StringField('Otro (área)')
    integrantes  = SelectMultipleField(
        'Integrantes residencia',
        choices=[('adultos','Adultos'),('ninos','Niños'),('envejecientes','Envejecientes'),('mascotas','Mascotas')],
        validators=[DataRequired()],
        option_widget=CheckboxInput(),
        widget=ListWidget(prefix_label=False)
    )

    cant_adultos  = StringField('Cantidad de adultos', validators=[DataRequired()])
    cant_ninos    = StringField('Cantidad de niños',  validators=[DataRequired()])
    cant_mascotas = StringField('Cantidad de mascotas')
    sugerencia    = StringField('Sugerencia del cliente')
    sueldo        = StringField('Sueldo a pagar', validators=[DataRequired()])

    transporte = RadioField(
        'Incluirá transporte',
        choices=[('si','Sí (ayuda aparte)'),('no','No (pasaje incluido)'),('otro','Otro')],
        validators=[DataRequired()]
    )
    acepta_terminos = BooleanField('Acepta términos', validators=[DataRequired()])
    submit          = SubmitField('Enviar')