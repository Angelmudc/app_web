from types import SimpleNamespace

from scripts.local.round9_harness import (
    CONCURRENCY_BATCHES,
    CONTENT_TYPE_CASES,
    INPUT_VALIDATION_CASES,
    REPLAY_CASES,
    fixture_value,
    isolated_login_identity,
    owns_fixture,
    run_concurrency_batches,
    run_prefix,
)


def test_round9_fixture_manifest_isolated_and_deterministic_contract():
    prefix = run_prefix()
    value = fixture_value(prefix, "cliente A")

    assert prefix.startswith("QA-R9C-")
    assert value == f"{prefix}cliente-A"
    assert owns_fixture(value, prefix)
    assert not owns_fixture("QA-REEMP-001", prefix)
    assert not owns_fixture("QA-E2E-001", prefix)


def test_round9_harness_carries_required_execution_matrices():
    assert CONCURRENCY_BATCHES == (2, 5, 10)
    assert set(REPLAY_CASES) == {
        "same_request",
        "same_token",
        "same_idempotency_key",
        "same_action_two_sessions",
    }
    assert {case.name for case in CONTENT_TYPE_CASES} == {
        "json_valido",
        "json_invalido",
        "form_urlencoded",
        "multipart",
        "text_plain",
        "sin_content_type",
        "required_ausente",
        "null",
        "enum_invalido",
        "tipo_incorrecto",
        "string_largo",
        "campo_extra",
    }
    assert {case.name for case in INPUT_VALIDATION_CASES} == {
        "required_ausente",
        "null",
        "tipo_incorrecto",
        "enum_invalido",
        "string_largo",
        "unicode",
        "campo_extra",
        "array_en_escalar",
        "objeto_en_escalar",
    }


def test_round9_concurrency_adapter_accepts_response_and_tuple_without_unpack_error():
    def request_factory(index):
        def request():
            response = SimpleNamespace(
                status_code=200 if index == 0 else 409,
                get_json=lambda silent=True: {
                    "row_version": index + 1,
                    "state": "updated" if index == 0 else "conflict",
                },
            )
            if index % 2:
                return ({"row_version": index + 1}, response.status_code)
            return response

        request.round9_metadata = {
            "idempotency_key": f"QA-R9C-IDEM-{index}",
            "audit_logs": [f"audit-{index}"],
        }
        return request

    batches = run_concurrency_batches(
        request_factory,
        final_state_reader=lambda: {"state": "conflict_or_updated"},
        audit_reader=lambda: ["audit-summary"],
    )

    assert [batch["batch_size"] for batch in batches] == [2, 5, 10]
    assert all(len(batch["requests"]) == batch["batch_size"] for batch in batches)
    assert all(batch["errors"] == [] for batch in batches)
    assert all(batch["final_state"]["state"] == "conflict_or_updated" for batch in batches)
    assert all(item.idempotency_key.startswith("QA-R9C-") for item in batches[-1]["requests"])


def test_round9_login_preparation_gets_isolated_synthetic_identity():
    first = isolated_login_identity("preparation")
    second = isolated_login_identity("rate-limit")
    assert first["username"].startswith("QA-R9C-")
    assert second["username"].startswith("QA-R9C-")
    assert first != second
