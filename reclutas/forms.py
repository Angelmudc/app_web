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


# ======================================================
# FORMULARIO PRIVADO (Admins / Secretarias)
# ======================================================
class ReclutaForm(FlaskForm):
    """
    Formulario para reclutamiento general (NO doméstica).
    Uso interno: admins y secretarias.
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
        choices=(
            [
                (
                    t,
                    'Secretario' if t == 'oficina' else t.replace('_', ' ').title()
                )
                for t in TIPOS_EMPLEO_GENERAL
                if t not in ('otro', 'otros')
            ]
            + [('panadero', 'Panadero'), ('pintor', 'Pintor')]
            + [('otros', 'Otros')]
        ),
        validators=[Optional()],
        description="Puede marcar varios"
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
        validators=[Optional()]
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
    # Condiciones
    # ─────────────────────────────────────────────
    documentos_al_dia = BooleanField('Documentos al día')

    disponible_fines_o_noches = BooleanField(
        'Disponible fines de semana o noches'
    )

    # ─────────────────────────────────────────────
    # Referencias
    # ─────────────────────────────────────────────
    referencias_laborales = TextAreaField(
        'Referencias laborales',
        validators=[Optional()]
    )

    referencias_familiares = TextAreaField(
        'Referencias familiares',
        validators=[Optional()]
    )

    # ─────────────────────────────────────────────
    # Interno (SOLO PRIVADO)
    # ─────────────────────────────────────────────
    observaciones_internas = TextAreaField(
        'Observaciones internas',
        validators=[Optional()]
    )

    submit = SubmitField('Guardar perfil')


# ======================================================
# FORMULARIO PÚBLICO
# ======================================================
class ReclutaPublicForm(ReclutaForm):
    """
    Versión pública del formulario.
    Mismos campos que el privado, SIN observaciones internas.
    """

    # En público no existe el campo interno
    observaciones_internas = None

    # Texto del botón en público
    submit = SubmitField('Enviar')