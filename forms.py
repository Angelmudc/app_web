# forms.py

from flask_wtf import FlaskForm
from wtforms import (
    StringField,
    PasswordField,
    SelectField,
    IntegerField,
    DateField,
    TextAreaField,
    SubmitField
)
from wtforms.validators import DataRequired, Optional

class LoginForm(FlaskForm):
    usuario = StringField("Usuario", validators=[DataRequired()])
    clave   = PasswordField("Contraseña", validators=[DataRequired()])
    submit  = SubmitField("Entrar")



class LlamadaCandidataForm(FlaskForm):
    resultado = SelectField(
        'Resultado de la llamada',
        choices=[
            ('no_contesta', 'No contestó'),
            ('inscripcion', 'Se inscribió'),
            ('rechaza',     'Rechaza'),
            ('voicemail',   'Buzón de voz'),
            ('informada',   'Información proporcionada'),
            ('exitosa',     'Llamada exitosa'),
            ('otro',        'Otro (especificar en notas)')
        ],
        validators=[DataRequired()]
    )
    duracion_minutos = IntegerField('Duración (minutos)', validators=[Optional()])
    notas            = TextAreaField('Notas', validators=[Optional()])
    submit           = SubmitField('Registrar llamada')
