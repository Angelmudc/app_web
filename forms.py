# forms.py
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired

class LoginForm(FlaskForm):
    usuario = StringField("Usuario", validators=[DataRequired()])
    clave = PasswordField("Contraseña", validators=[DataRequired()])
    submit = SubmitField("Entrar")
