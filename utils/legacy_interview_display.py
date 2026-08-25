from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable


_EMPTY_TEXT_VALUES = {
    "",
    "{}",
    "[]",
    "null",
    "none",
    "node",
    "no",
    "n/a",
    "na",
    "sin",
    "pendiente",
    "vacio",
    "vacío",
    "--",
    "-",
}


@dataclass(frozen=True)
class LegacyInterviewEntry:
    label: str
    value: str


@dataclass(frozen=True)
class LegacyInterviewDisplay:
    has_content: bool
    entries: tuple[LegacyInterviewEntry, ...]
    source_kind: str = "text"


def _clean_text(value) -> str:
    return str(value or "").strip()


def _normalize_label(label: str) -> str:
    text = _clean_text(label)
    if not text:
        return ""
    text = text.replace("_", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    return text[:1].upper() + text[1:]


def _is_useful_text(text: str) -> bool:
    raw = _clean_text(text)
    if not raw:
        return False
    lowered = raw.lower()
    if lowered in _EMPTY_TEXT_VALUES:
        return False
    if raw.startswith("[") and "]" in raw and ":" not in raw and "\n" not in raw and len(raw) < 120:
        return False
    return True


def _append_text_entry(entries: list[LegacyInterviewEntry], label: str, value: str) -> None:
    clean_value = _clean_text(value)
    if not clean_value:
        return
    entries.append(
        LegacyInterviewEntry(
            label=_normalize_label(label) or "Entrevista histórica",
            value=clean_value,
        )
    )


def _append_json_node(entries: list[LegacyInterviewEntry], label_hint: str, node) -> None:
    if node is None:
        return
    if isinstance(node, dict):
        pregunta = None
        respuesta = None
        for key in ("pregunta", "question", "label", "texto", "enunciado", "clave", "titulo", "title"):
            value = node.get(key)
            if _is_useful_text(value):
                pregunta = _clean_text(value)
                break
        for key in ("respuesta", "answer", "value", "valor", "contenido", "text"):
            value = node.get(key)
            if _is_useful_text(value):
                respuesta = _clean_text(value)
                break
        if pregunta is not None or respuesta is not None:
            entries.append(
                LegacyInterviewEntry(
                    label=_normalize_label(pregunta or label_hint or "Pregunta") or "Pregunta",
                    value=respuesta or "",
                )
            )
            return
        for key, value in node.items():
            _append_json_node(entries, str(key), value)
        return
    if isinstance(node, list):
        for idx, item in enumerate(node, 1):
            _append_json_node(entries, f"{label_hint} {idx}".strip(), item)
        return
    _append_text_entry(entries, label_hint or "Entrevista histórica", node)


def build_legacy_interview_display(raw_value) -> LegacyInterviewDisplay:
    text = _clean_text(raw_value)
    if not _is_useful_text(text):
        return LegacyInterviewDisplay(has_content=False, entries=(), source_kind="text")

    parsed_entries: list[LegacyInterviewEntry] = []
    source_kind = "text"

    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    else:
        source_kind = "json"
        if isinstance(parsed, dict) and not parsed:
            return LegacyInterviewDisplay(has_content=False, entries=(), source_kind="json")
        if isinstance(parsed, list) and not parsed:
            return LegacyInterviewDisplay(has_content=False, entries=(), source_kind="json")
        _append_json_node(parsed_entries, "", parsed)

    if not parsed_entries:
        source_kind = "text" if source_kind == "text" else source_kind
        lines = [line.strip(" \t-•*") for line in text.splitlines()]
        for line in lines:
            if not line:
                continue
            if ":" in line:
                label, value = line.split(":", 1)
                label = label.strip()
                value = value.strip()
                if label and value:
                    parsed_entries.append(
                        LegacyInterviewEntry(
                            label=_normalize_label(label) or "Pregunta",
                            value=value,
                        )
                    )
                    continue
            parsed_entries.append(
                LegacyInterviewEntry(
                    label="Entrevista histórica",
                    value=line,
                )
            )

    if not parsed_entries:
        return LegacyInterviewDisplay(has_content=False, entries=(), source_kind=source_kind)

    return LegacyInterviewDisplay(
        has_content=True,
        entries=tuple(parsed_entries),
        source_kind=source_kind,
    )
