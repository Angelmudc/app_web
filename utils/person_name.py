"""Shared semantic validation for names entered in public forms."""

import re
import unicodedata


PUBLIC_PERSON_NAME_ERROR = (
    "Escribe un nombre válido usando solo letras, espacios, guiones o apóstrofes."
)

_EMAIL_OR_URL_RE = re.compile(r"(?:https?://|www\.|@)", re.IGNORECASE)
_ALLOWED_SEPARATORS = {" ", "-", "'", "’"}


def normalize_person_name(value):
    """Trim outer whitespace and collapse internal whitespace for a name."""
    if not isinstance(value, str):
        return value
    return " ".join(value.strip().split())


def _is_letter_or_mark(char: str) -> bool:
    return unicodedata.category(char).startswith(("L", "M"))


def is_valid_person_name(value) -> bool:
    """Return whether *value* is a realistic human name for public intake."""
    normalized = normalize_person_name(value)
    if not isinstance(normalized, str) or not normalized:
        return False
    if len(normalized) > 200 or _EMAIL_OR_URL_RE.search(normalized):
        return False

    chars = list(unicodedata.normalize("NFC", normalized))
    if not chars or not _is_letter_or_mark(chars[0]) or not _is_letter_or_mark(chars[-1]):
        return False
    if any(not (_is_letter_or_mark(char) or char in _ALLOWED_SEPARATORS) for char in chars):
        return False
    if sum(_is_letter_or_mark(char) for char in chars) < 2:
        return False

    for index, char in enumerate(chars):
        if char in {"-", "'", "’"}:
            if index == 0 or index == len(chars) - 1:
                return False
            if not _is_letter_or_mark(chars[index - 1]) or not _is_letter_or_mark(chars[index + 1]):
                return False
    return True


def validate_person_name(form, field):
    """WTForms validator for public person-name fields."""
    field.data = normalize_person_name(field.data)
    if not is_valid_person_name(field.data):
        from wtforms.validators import ValidationError

        raise ValidationError(PUBLIC_PERSON_NAME_ERROR)
