from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import (
    StringField,
    SelectField,
    SelectMultipleField,
    BooleanField,
    TextAreaField,
    SubmitField,
    HiddenField,
)
from wtforms.validators import DataRequired, Optional, ValidationError

from models import TIPOS_EMPLEO_GENERAL
from utils.cedula_normalizer import normalize_cedula_for_store
from utils.public_intake import clean_spaces, digits_only, has_min_real_chars


def _normalize_optional(raw: str | None, max_len: int) -> str:
    return clean_spaces(raw, max_len=max_len)


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
        validators=[DataRequired()],
        filters=[lambda v: _normalize_optional(v, 200)]
    )

    cedula = StringField(
        'Cédula',
        validators=[DataRequired()],
        filters=[lambda v: _normalize_optional(v, 50)]
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
        validators=[DataRequired()],
        filters=[lambda v: _normalize_optional(v, 50)]
    )

    email = StringField(
        'Correo electrónico',
        validators=[Optional()],
        filters=[lambda v: _normalize_optional(v, 120)]
    )

    direccion_completa = StringField(
        'Dirección (ciudad / sector)',
        validators=[Optional()],
        filters=[lambda v: _normalize_optional(v, 300)]
    )

    ciudad = StringField(
        'Ciudad',
        validators=[Optional()],
        filters=[lambda v: _normalize_optional(v, 120)]
    )

    sector = StringField(
        'Sector',
        validators=[Optional()],
        filters=[lambda v: _normalize_optional(v, 120)]
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
        validators=[Optional()],
        filters=[lambda v: _normalize_optional(v, 120)]
    )

    sueldo_esperado = StringField(
        'Sueldo esperado',
        validators=[Optional()],
        filters=[lambda v: _normalize_optional(v, 80)],
        description="Ej: RD$25,000 / A discutir"
    )

    # ─────────────────────────────────────────────
    # Experiencia / Educación / Habilidades
    # ─────────────────────────────────────────────
    tiene_experiencia = BooleanField('¿Tiene experiencia laboral?')

    anos_experiencia = StringField(
        'Años de experiencia',
        validators=[Optional()],
        filters=[lambda v: _normalize_optional(v, 20)]
    )

    experiencia_resumen = StringField(
        'Experiencia resumida',
        validators=[Optional()],
        filters=[lambda v: _normalize_optional(v, 300)]
    )

    nivel_educativo = StringField(
        'Nivel educativo',
        validators=[Optional()],
        filters=[lambda v: _normalize_optional(v, 80)]
    )

    habilidades = StringField(
        'Habilidades principales',
        validators=[Optional()],
        filters=[lambda v: _normalize_optional(v, 300)],
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
        validators=[Optional()],
        filters=[lambda v: _normalize_optional(v, 1000)]
    )

    referencias_familiares = TextAreaField(
        'Referencias familiares',
        validators=[Optional()],
        filters=[lambda v: _normalize_optional(v, 1000)]
    )

    # ─────────────────────────────────────────────
    # Interno (SOLO PRIVADO)
    # ─────────────────────────────────────────────
    observaciones_internas = TextAreaField(
        'Observaciones internas',
        validators=[Optional()],
        filters=[lambda v: _normalize_optional(v, 1200)]
    )

    submit = SubmitField('Guardar perfil')

    def validate_nombre_completo(self, field):
        if not has_min_real_chars(field.data, min_chars=6):
            raise ValidationError("El nombre completo debe tener al menos 6 letras.")

    def validate_cedula(self, field):
        normalized = normalize_cedula_for_store(field.data or "")
        digits = digits_only(normalized)
        if len(digits) != 11:
            raise ValidationError("La cédula debe contener exactamente 11 dígitos.")
        field.data = normalized

    def validate_telefono(self, field):
        digits = digits_only(field.data or "")
        if len(digits) != 10:
            raise ValidationError("El teléfono debe contener exactamente 10 dígitos.")
        field.data = digits


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
    bot_field = HiddenField('No completar', validators=[Optional()])

    # Texto del botón en público
    submit = SubmitField('Enviar')
