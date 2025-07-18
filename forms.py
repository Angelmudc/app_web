# forms.py

from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, SelectField, TextAreaField
from wtforms.validators import DataRequired, Optional

class LoginForm(FlaskForm):
    usuario = StringField("Usuario", validators=[DataRequired()])
    clave   = PasswordField("Contraseña", validators=[DataRequired()])
    submit  = SubmitField("Entrar")

class LlamadaCandidataForm(FlaskForm):
    resultado = SelectField(
        'Resultado de la llamada',
        choices=[
            ('no_contesta', 'No contesta'),
            ('inscripcion', 'Se inscribió'),
            ('rechaza',     'Rechaza'),
            ('otro',        'Otro')
        ],
        validators=[DataRequired()]
    )
    notas  = TextAreaField('Notas', validators=[Optional()])
    submit = SubmitField('Registrar llamada')
