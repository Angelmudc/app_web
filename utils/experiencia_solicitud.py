"""Shared experience choices and legacy-value compatibility for Solicitud forms."""

from wtforms import SelectField

EXPERIENCIA_CHOICES = (
    ("Doméstica completa", "Doméstica completa"),
    ("Cocina", "Cocina"),
    ("Niñera", "Niñera"),
    ("Enfermera", "Enfermera"),
    ("Cuidado de envejeciente", "Cuidado de envejeciente"),
    ("Limpieza", "Limpieza"),
    ("otro", "Otro"),
)

EXPERIENCIA_CLOSED_VALUES = frozenset(value for value, _label in EXPERIENCIA_CHOICES if value != "otro")


def split_experiencia_value(raw_value):
    """Return the UI selection and preserve non-catalog text byte-for-byte."""
    raw = "" if raw_value is None else str(raw_value)
    if raw in EXPERIENCIA_CLOSED_VALUES:
        return raw, ""
    if raw:
        return "otro", raw
    return "", ""


def experiencia_value_from_form(form):
    """Resolve the selected UI value back to the legacy text DB column."""
    selection = str(getattr(getattr(form, "experiencia", None), "data", "") or "")
    if selection != "otro":
        return selection
    return str(getattr(getattr(form, "experiencia_otro", None), "data", "") or "")


def normalize_experiencia_submission(form, field):
    """Accept legacy free-text POSTs as Otro for backward compatibility."""
    raw = "" if field.data is None else str(field.data)
    allowed = {value for value, _label in EXPERIENCIA_CHOICES}
    if raw and raw not in allowed:
        if not (getattr(form.experiencia_otro, "data", "") or ""):
            form.experiencia_otro.data = raw
        field.data = "otro"


class LegacyExperienceSelectField(SelectField):
    """Select field that accepts a pre-existing free-text POST as Otro."""

    def pre_validate(self, form):
        normalize_experiencia_submission(form, self)
        super().pre_validate(form)


def load_experiencia_value(form, raw_value):
    """Populate the selection and secondary text without normalizing history."""
    selection, other_text = split_experiencia_value(raw_value)
    form.experiencia.data = selection
    if hasattr(form, "experiencia_otro"):
        form.experiencia_otro.data = other_text
