from __future__ import annotations

from utils import letra_por_indice


def compose_codigo_solicitud(prefix: str, sequence_index: int) -> str:
    """
    Reglas:
    - sequence_index=0  -> <prefix>
    - sequence_index>=1 -> <prefix> - <LETRA>
      donde 1->B, 2->C, etc.
    """
    base = (prefix or "").strip()
    if sequence_index <= 0:
        return base
    return f"{base} - {letra_por_indice(sequence_index + 1)}"
