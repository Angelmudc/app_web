"""Datos y matrices inertes para ejecutar Round 9 sin compartir fixtures.

Este módulo no conecta a la base de datos ni crea datos. Work puede importar
los contratos para construir sus pruebas con un prefijo exclusivo por corrida.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from hashlib import sha256
from uuid import uuid4


ROUND9_PREFIX = "QA-R9C-"
CONCURRENCY_BATCHES = (2, 5, 10)


@dataclass(frozen=True)
class ContentTypeCase:
    name: str
    content_type: str | None
    body_kind: str
    expected_class: str


@dataclass(frozen=True)
class HarnessObservation:
    request_index: int
    status: int | None
    row_version: object = None
    state: object = None
    idempotency_key: str | None = None
    audit_logs: object = None
    body: object = None
    error: str | None = None


@dataclass(frozen=True)
class ReplayScenario:
    name: str
    endpoint: str
    expectation: str
    notes: str


@dataclass(frozen=True)
class InputValidationCase:
    name: str
    content_type: str | None
    body_kind: str
    expectation: str


CONTENT_TYPE_CASES = (
    ContentTypeCase("json_valido", "application/json", "valid_json", "accepted_or_validated"),
    ContentTypeCase("json_invalido", "application/json", "invalid_json", "safe_4xx"),
    ContentTypeCase("form_urlencoded", "application/x-www-form-urlencoded", "form", "accepted_or_validated"),
    ContentTypeCase("multipart", "multipart/form-data", "multipart", "accepted_or_validated"),
    ContentTypeCase("text_plain", "text/plain", "plain_text", "safe_4xx"),
    ContentTypeCase("sin_content_type", None, "raw", "safe_4xx"),
    ContentTypeCase("required_ausente", "application/json", "missing_required", "safe_4xx"),
    ContentTypeCase("null", "application/json", "null", "safe_4xx"),
    ContentTypeCase("enum_invalido", "application/json", "invalid_enum", "safe_4xx"),
    ContentTypeCase("tipo_incorrecto", "application/json", "wrong_type", "safe_4xx"),
    ContentTypeCase("string_largo", "application/json", "oversized_string", "safe_4xx"),
    ContentTypeCase("campo_extra", "application/json", "extra_field", "accepted_or_validated"),
)


REPLAY_CASES = (
    "same_request",
    "same_token",
    "same_idempotency_key",
    "same_action_two_sessions",
)


REPLAY_SCENARIOS = (
    ReplayScenario("same_request", "/admin/reemplazos/<id>/fase", "idempotent_or_409", "repetir payload idéntico"),
    ReplayScenario("same_token", "/clientes/solicitudes/publica/<token>", "single_use_or_safe_reject", "repetir token actual"),
    ReplayScenario("same_idempotency_key", "/admin/clientes/<cliente_id>/solicitudes/<id>/pago", "idempotent_or_409", "misma key y payload"),
    ReplayScenario("same_action_two_sessions", "/admin/solicitudes/<id>/activar", "one_success_one_409", "dos sesiones autorizadas"),
)


INPUT_VALIDATION_CASES = (
    InputValidationCase("required_ausente", "application/json", "missing_required", "safe_4xx_no_write"),
    InputValidationCase("null", "application/json", "null", "safe_4xx_no_write"),
    InputValidationCase("tipo_incorrecto", "application/json", "wrong_type", "safe_4xx_no_write"),
    InputValidationCase("enum_invalido", "application/json", "invalid_enum", "safe_4xx_no_write"),
    InputValidationCase("string_largo", "application/json", "oversized_string", "safe_4xx_no_write"),
    InputValidationCase("unicode", "application/json", "unicode", "accepted_or_validated"),
    InputValidationCase("campo_extra", "application/json", "extra_field", "accepted_or_validated"),
    InputValidationCase("array_en_escalar", "application/json", "array_for_scalar", "safe_4xx_no_write"),
    InputValidationCase("objeto_en_escalar", "application/json", "object_for_scalar", "safe_4xx_no_write"),
)


def run_prefix() -> str:
    """Return an isolated prefix for one Work execution."""
    return f"{ROUND9_PREFIX}{uuid4().hex[:12].upper()}-"


def fixture_value(prefix: str, label: str) -> str:
    """Build a fixture identifier and reject non-Round-9 ownership."""
    normalized = str(prefix or "")
    if not normalized.startswith(ROUND9_PREFIX):
        raise ValueError("Round 9 fixture prefix must start with QA-R9C-")
    clean_label = "-".join(str(label or "fixture").split())[:80]
    return f"{normalized}{clean_label}"


def owns_fixture(value: str, prefix: str) -> bool:
    return bool(prefix and prefix.startswith(ROUND9_PREFIX) and str(value or "").startswith(prefix))


def isolated_login_identity(label: str) -> dict[str, str]:
    """Return a unique synthetic login identity for one isolated harness phase."""
    digest = sha256(str(label or "round9").encode("utf-8")).hexdigest()[:8]
    return {
        "username": fixture_value(run_prefix(), f"LOGIN-{digest}"),
        "ip": f"198.51.100.{(int(digest[:2], 16) % 200) + 1}",
    }


def _normalize_result(result, request_index: int, *, metadata: dict | None = None) -> HarnessObservation:
    """Normalize Flask responses, `(body, status)` tuples, and plain mappings.

    The previous Work harness assumed exactly two return values. Current
    endpoints return either a Flask response or a response tuple, so this
    adapter deliberately avoids fixed-arity unpacking.
    """
    metadata = dict(metadata or {})
    response = result
    body = None
    status = None
    if isinstance(result, tuple):
        response = next((item for item in result if hasattr(item, "status_code")), None)
        status = next((item for item in result if isinstance(item, int)), None)
        body = next((item for item in result if item is not response and not isinstance(item, int)), None)
    if response is not None and hasattr(response, "status_code"):
        status = int(response.status_code)
        if hasattr(response, "get_json"):
            try:
                body = response.get_json(silent=True)
            except TypeError:
                body = response.get_json()
        elif hasattr(response, "data"):
            body = response.data
    elif isinstance(result, dict):
        status = result.get("status") or result.get("status_code")
        body = result.get("body", result)
    return HarnessObservation(
        request_index=request_index,
        status=int(status) if status is not None else None,
        row_version=metadata.get("row_version", body.get("row_version") if isinstance(body, dict) else None),
        state=metadata.get("state", body.get("state") if isinstance(body, dict) else None),
        idempotency_key=metadata.get("idempotency_key"),
        audit_logs=metadata.get("audit_logs"),
        body=body,
    )


def run_concurrent_batch(request_factory, batch_size: int, *, final_state_reader=None, audit_reader=None) -> dict:
    """Run one prepared batch and collect every response/error without unpacking assumptions.

    `request_factory(index)` must return a zero-argument callable. The factory
    owns its Flask session/client. This helper performs no DB setup or cleanup.
    """
    if int(batch_size) not in CONCURRENCY_BATCHES:
        raise ValueError(f"batch_size debe ser uno de {CONCURRENCY_BATCHES}")

    observations = []

    def invoke(index: int):
        request = request_factory(index)
        metadata = getattr(request, "round9_metadata", {}) or {}
        try:
            return _normalize_result(request(), index, metadata=metadata)
        except Exception as exc:  # pragma: no cover - caller-specific failure reporting
            return HarnessObservation(request_index=index, status=None, error=f"{type(exc).__name__}: {exc}")

    with ThreadPoolExecutor(max_workers=int(batch_size)) as executor:
        futures = [executor.submit(invoke, index) for index in range(int(batch_size))]
        for future in as_completed(futures):
            observations.append(future.result())
    observations.sort(key=lambda item: item.request_index)
    result = {"batch_size": int(batch_size), "requests": observations}
    result["final_state"] = final_state_reader() if final_state_reader else None
    result["audit_logs"] = audit_reader() if audit_reader else None
    result["errors"] = [item.error for item in observations if item.error]
    return result


def run_concurrency_batches(request_factory, *, final_state_reader=None, audit_reader=None) -> list[dict]:
    """Prepare/run the three supported batches: 2, 5 and 10."""
    return [
        run_concurrent_batch(
            request_factory,
            batch_size,
            final_state_reader=final_state_reader,
            audit_reader=audit_reader,
        )
        for batch_size in CONCURRENCY_BATCHES
    ]
