from flask_wtf import FlaskForm
from wtforms import (
    StringField,
    SelectField,
    SelectMultipleField,
    BooleanField,
    TextAreaField,
    SubmitField
)
from wtforms.validators import DataRequired, Optional

from models import TIPOS_EMPLEO_GENERAL


class ReclutaForm(FlaskForm):
    """
    Formulario corto e inteligente para reclutamiento general (NO doméstica).
    Diseñado para secretarias y admins.
    """

    # ─────────────────────────────────────────────
    # Datos personales
    # ─────────────────────────────────────────────
    nombre_completo = StringField(
        'Nombre completo',
        validators=[DataRequired()]
    )

    cedula = StringField(
        'Cédula',
        validators=[DataRequired()]
    )

    edad = StringField(
        'Edad',
        validators=[Optional()],
        description="Ej: 25, 30-35"
    )

    sexo = SelectField(
        'Sexo',
        choices=[
            ('', 'Seleccionar'),
            ('masculino', 'Masculino'),
            ('femenino', 'Femenino'),
            ('otro', 'Otro')
        ],
        validators=[Optional()]
    )

    nacionalidad = StringField(
        'Nacionalidad',
        validators=[Optional()]
    )

    # ─────────────────────────────────────────────
    # Contacto / Ubicación
    # ─────────────────────────────────────────────
    telefono = StringField(
        'Teléfono',
        validators=[DataRequired()]
    )

    email = StringField(
        'Correo electrónico',
        validators=[Optional()]
    )

    direccion_completa = StringField(
        'Dirección (ciudad / sector)',
        validators=[Optional()]
    )

    ciudad = StringField(
        'Ciudad',
        validators=[Optional()]
    )

    sector = StringField(
        'Sector',
        validators=[Optional()]
    )

    # ─────────────────────────────────────────────
    # Perfil laboral
    # ─────────────────────────────────────────────
    tipos_empleo_busca = SelectMultipleField(
        'Tipos de empleo que busca',
        choices=[(t, t.replace('_', ' ').title()) for t in TIPOS_EMPLEO_GENERAL],
        validators=[Optional()],
        description="Puede marcar varios"
    )

    empleo_principal = SelectField(
        'Empleo principal',
        choices=[('', 'Seleccionar')] + [(t, t.replace('_', ' ').title()) for t in TIPOS_EMPLEO_GENERAL],
        validators=[Optional()]
    )

    modalidad = SelectField(
        'Modalidad',
        choices=[
            ('', 'Seleccionar'),
            ('tiempo_completo', 'Tiempo completo'),
            ('medio_tiempo', 'Medio tiempo'),
            ('por_dias', 'Por días'),
            ('por_horas', 'Por horas')
        ],
        validators=[Optional()]
    )

    horario_disponible = StringField(
        'Horario disponible',
        validators=[Optional()]
    )

    sueldo_esperado = StringField(
        'Sueldo esperado',
        validators=[Optional()],
        description="Ej: RD$25,000 / A discutir"
    )

    # ─────────────────────────────────────────────
    # Experiencia / Educación / Habilidades
    # ─────────────────────────────────────────────
    tiene_experiencia = BooleanField('¿Tiene experiencia laboral?')

    anos_experiencia = StringField(
        'Años de experiencia',
        validators=[Optional()]
    )

    experiencia_resumen = StringField(
        'Experiencia resumida',
        validators=[Optional()],
        description="Resumen corto"
    )

    nivel_educativo = StringField(
        'Nivel educativo',
        validators=[Optional()]
    )

    habilidades = StringField(
        'Habilidades principales',
        validators=[Optional()],
        description="Ej: computadora, caja, atención al cliente"
    )

    # ─────────────────────────────────────────────
    # Condiciones rápidas
    # ─────────────────────────────────────────────
    documentos_al_dia = BooleanField('Documentos al día')

    disponible_fines_o_noches = BooleanField('Disponible fines de semana o noches')

    # ─────────────────────────────────────────────
    # Referencias
    # ─────────────────────────────────────────────
    referencias_laborales = TextAreaField(
        'Referencias laborales',
        validators=[Optional()],
        description="Nombres, teléfonos y relación (si aplica)"
    )

    referencias_familiares = TextAreaField(
        'Referencias familiares',
        validators=[Optional()],
        description="Nombres, teléfonos y relación (si aplica)"
    )

    # ─────────────────────────────────────────────
    # Interno
    # ─────────────────────────────────────────────
    observaciones_internas = TextAreaField(
        'Observaciones internas',
        validators=[Optional()]
    )

    submit = SubmitField('Guardar perfil')