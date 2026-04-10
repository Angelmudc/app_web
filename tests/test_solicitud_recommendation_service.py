# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime

from app import app as flask_app
from config_app import db
from models import (
    Candidata,
    Cliente,
    Solicitud,
    SolicitudRecommendationItem,
    SolicitudRecommendationRun,
    SolicitudRecommendationSelection,
)
from services.solicitud_recommendation_policy import SolicitudRecommendationPolicy
from services.solicitud_recommendation_presenter import present_shortlist_payload
from services.solicitud_recommendation_service import SolicitudRecommendationService
from tests.t1_testkit import ensure_sqlite_compat_tables


def _ensure_tables(reset: bool = True) -> None:
    ensure_sqlite_compat_tables(
        [
            Cliente,
            Solicitud,
            Candidata,
            SolicitudRecommendationRun,
            SolicitudRecommendationItem,
            SolicitudRecommendationSelection,
        ],
        reset=reset,
    )


def _seed_cliente_solicitud(*, suffix: str = "A") -> tuple[Cliente, Solicitud]:
    cliente = Cliente(
        codigo=f"CL-REC-{suffix}",
        nombre_completo=f"Cliente Rec {suffix}",
        email=f"cliente_rec_{suffix.lower()}@example.com",
        telefono=f"8095551{suffix[-1] if suffix[-1].isdigit() else '0'}00",
    )
    db.session.add(cliente)
    db.session.flush()

    solicitud = Solicitud(
        cliente_id=int(cliente.id),
        codigo_solicitud=f"SOL-REC-{suffix}",
        estado="proceso",
        modalidad_trabajo="salida diaria",
        horario="8am-5pm",
    )
    db.session.add(solicitud)
    db.session.commit()
    return cliente, solicitud


def _seed_candidata(*, fila_hint: int, suffix: str, estado: str = "lista_para_trabajar") -> Candidata:
    cand = Candidata(
        codigo=f"C-REC-{suffix}",
        nombre_completo=f"Candidata {suffix}",
        cedula=f"{fila_hint:011d}",
        numero_telefono="8091234567",
        estado=estado,
        entrevista="ok",
        referencias_laborales_texto="ok",
        referencias_familiares_texto="ok",
        depuracion=b"1",
        perfil=b"1",
        cedula1=b"1",
        cedula2=b"1",
        modalidad_trabajo_preferida="salida diaria",
        edad="30",
    )
    db.session.add(cand)
    db.session.commit()
    return cand


class _FakePolicy:
    version = "policy-test"

    def evaluate(self, *, solicitud, candidata, score_row):
        cid = int(getattr(candidata, "fila", 0) or 0)
        if cid % 2 == 0:
            return {
                "is_eligible": False,
                "hard_fail": True,
                "hard_fail_codes": ["modalidad_incompatible"],
                "hard_fail_reasons": ["Modalidad incompatible con la solicitud."],
                "soft_fail_codes": [],
                "soft_fail_reasons": [],
                "score_final": int(score_row.get("score") or 0),
                "score_operational": int(score_row.get("operational_score") or 0),
                "confidence_band": "baja",
            }
        return {
            "is_eligible": True,
            "hard_fail": False,
            "hard_fail_codes": [],
            "hard_fail_reasons": [],
            "soft_fail_codes": ["edad_mismatch"] if cid % 3 == 0 else [],
            "soft_fail_reasons": ["Edad fuera del rango solicitado."] if cid % 3 == 0 else [],
            "score_final": int(score_row.get("score") or 0),
            "score_operational": int(score_row.get("operational_score") or 0),
            "confidence_band": "media",
        }


def test_generation_persists_run_and_items(monkeypatch):
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables(reset=True)
        _, solicitud = _seed_cliente_solicitud(suffix="01")
        c1 = _seed_candidata(fila_hint=10000000001, suffix="01")
        c2 = _seed_candidata(fila_hint=10000000002, suffix="02")

        monkeypatch.setattr(
            "services.solicitud_recommendation_service.candidate_query_prefilter",
            lambda _s: [c1, c2],
        )
        monkeypatch.setattr(
            "services.solicitud_recommendation_service._score_candidate",
            lambda _s, cand: {
                "score": 80 if int(cand.fila) == int(c1.fila) else 55,
                "operational_score": 70,
                "risks": [],
                "breakdown_snapshot": {"modalidad_match": True, "edad_match": True},
            },
        )

        service = SolicitudRecommendationService(policy=_FakePolicy())
        run = service.request_generation(int(solicitud.id), trigger_source="test", requested_by="pytest")

        assert run is not None
        assert str(run.status or "") == "completed"
        assert int(run.items_count or 0) == 2
        assert int(run.eligible_count or 0) == 1

        rows = (
            SolicitudRecommendationItem.query
            .filter_by(run_id=int(run.id))
            .order_by(SolicitudRecommendationItem.id.asc())
            .all()
        )
        assert len(rows) == 2
        assert any(bool(getattr(r, "is_eligible", False)) for r in rows)
        assert any(bool(getattr(r, "hard_fail", False)) for r in rows)


def test_get_active_shortlist_pending_ready_and_stale(monkeypatch):
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables(reset=True)
        _, solicitud = _seed_cliente_solicitud(suffix="02")
        c1 = _seed_candidata(fila_hint=10000000003, suffix="03")

        monkeypatch.setattr(
            "services.solicitud_recommendation_service.candidate_query_prefilter",
            lambda _s: [c1],
        )
        monkeypatch.setattr(
            "services.solicitud_recommendation_service._score_candidate",
            lambda _s, _c: {
                "score": 88,
                "operational_score": 80,
                "risks": [],
                "breakdown_snapshot": {"modalidad_match": True, "edad_match": True},
            },
        )

        service = SolicitudRecommendationService(policy=_FakePolicy())

        pending_run = service.request_generation(
            int(solicitud.id),
            trigger_source="test_pending",
            requested_by="pytest",
            synchronous=False,
            commit=True,
        )
        assert pending_run is not None
        pending_payload = service.get_active_shortlist(int(solicitud.id))
        assert (pending_payload.get("state") or {}).get("code") == "pending"

        service.generate_snapshot(run_id=int(pending_run.id), commit=True)
        ready_payload = service.get_active_shortlist(int(solicitud.id))
        assert (ready_payload.get("state") or {}).get("code") == "ready"
        assert len(ready_payload.get("items") or []) == 1

        solicitud.horario = "9am-6pm"
        db.session.commit()

        stale_payload = service.get_active_shortlist(int(solicitud.id))
        assert (stale_payload.get("state") or {}).get("code") == "stale"


def test_generate_snapshot_error_sets_run_error(monkeypatch):
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables(reset=True)
        _, solicitud = _seed_cliente_solicitud(suffix="03")
        c1 = _seed_candidata(fila_hint=10000000004, suffix="04")

        monkeypatch.setattr(
            "services.solicitud_recommendation_service.candidate_query_prefilter",
            lambda _s: [c1],
        )

        def _boom(_s, _c):
            raise RuntimeError("score_error")

        monkeypatch.setattr("services.solicitud_recommendation_service._score_candidate", _boom)

        service = SolicitudRecommendationService(policy=_FakePolicy())
        run = service.request_generation(int(solicitud.id), trigger_source="test_error", requested_by="pytest")
        assert run is not None

        refreshed = SolicitudRecommendationRun.query.filter_by(id=int(run.id)).first()
        assert refreshed is not None
        assert str(refreshed.status or "") == "error"


def test_validate_client_selection_persists_selection(monkeypatch):
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables(reset=True)
        _, solicitud = _seed_cliente_solicitud(suffix="04")
        c1 = _seed_candidata(fila_hint=10000000005, suffix="05")

        monkeypatch.setattr(
            "services.solicitud_recommendation_service.candidate_query_prefilter",
            lambda _s: [c1],
        )
        monkeypatch.setattr(
            "services.solicitud_recommendation_service._score_candidate",
            lambda _s, _c: {
                "score": 90,
                "operational_score": 82,
                "risks": [],
                "breakdown_snapshot": {"modalidad_match": True, "edad_match": True},
            },
        )

        service = SolicitudRecommendationService(policy=_FakePolicy())
        run = service.request_generation(int(solicitud.id), trigger_source="test_validate", requested_by="pytest")
        assert run is not None
        assert str(run.status or "") == "completed"

        result = service.validate_client_selection(
            solicitud_id=int(solicitud.id),
            candidata_id=int(c1.fila),
            selected_by="cliente:test",
        )
        assert result.get("ok") is True
        assert result.get("code") == "valid"

        selection = (
            SolicitudRecommendationSelection.query
            .filter_by(solicitud_id=int(solicitud.id), candidata_id=int(c1.fila))
            .first()
        )
        assert selection is not None
        assert str(selection.status or "") == "valid"


def test_policy_modalidad_hard_fail_and_confidence_band():
    policy = SolicitudRecommendationPolicy()

    class _Solicitud:
        id = 10
        cliente_id = 20

    class _Cand:
        fila = 99
        estado = "lista_para_trabajar"

    score_row = {
        "score": 77,
        "operational_score": 70,
        "risks": ["Riesgo de horarios"],
        "breakdown_snapshot": {"modalidad_match": False, "edad_match": False},
    }

    # Evita dependencia de BD/invariantes para este test puntual.
    from services import solicitud_recommendation_policy as mod

    mod_candidate_blocked = mod.candidate_blocked_by_other_client
    mod_candidate_active = mod.candidate_has_active_assignment
    mod_ready = mod.candidata_is_ready_to_send
    try:
        mod.candidate_blocked_by_other_client = lambda **kwargs: False
        mod.candidate_has_active_assignment = lambda **kwargs: False
        mod.candidata_is_ready_to_send = lambda _cand: (True, [])

        out = policy.evaluate(solicitud=_Solicitud(), candidata=_Cand(), score_row=score_row)
        assert out["hard_fail"] is True
        assert "modalidad_incompatible" in out["hard_fail_codes"]
        assert "edad_mismatch" in out["soft_fail_codes"]
        assert out["confidence_band"] == "baja"
    finally:
        mod.candidate_blocked_by_other_client = mod_candidate_blocked
        mod.candidate_has_active_assignment = mod_candidate_active
        mod.candidata_is_ready_to_send = mod_ready


def test_presenter_returns_clean_dto_shape():
    class _Solicitud:
        id = 1
        cliente_id = 2

    class _Run:
        id = 3
        status = "completed"
        trigger_source = "manual"
        fingerprint_hash = "abc"
        model_version = "m1"
        policy_version = "p1"
        requested_at = datetime(2026, 4, 10, 10, 0, 0)
        started_at = datetime(2026, 4, 10, 10, 0, 1)
        completed_at = datetime(2026, 4, 10, 10, 0, 2)
        failed_at = None
        error_code = None
        error_message = None
        pool_size = 5
        items_count = 2
        eligible_count = 1
        hard_fail_count = 1
        soft_fail_count = 0

    payload = present_shortlist_payload(
        solicitud=_Solicitud(),
        state_code="ready",
        run=_Run(),
        items=[],
        stale=False,
        state_message="ok",
    )

    assert payload["solicitud_id"] == 1
    assert payload["cliente_id"] == 2
    assert payload["state"]["code"] == "ready"
    assert isinstance(payload.get("run"), dict)
    assert payload.get("items") == []


def test_generation_zero_candidates_completes(monkeypatch):
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        _ensure_tables(reset=True)
        _, solicitud = _seed_cliente_solicitud(suffix="05")

        monkeypatch.setattr(
            "services.solicitud_recommendation_service.candidate_query_prefilter",
            lambda _s: [],
        )
        service = SolicitudRecommendationService(policy=_FakePolicy())
        run = service.request_generation(int(solicitud.id), trigger_source="test_zero", requested_by="pytest")

        assert run is not None
        assert str(run.status or "") == "completed"
        assert int(run.items_count or 0) == 0
        assert int(run.eligible_count or 0) == 0
