#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import Counter

from config_app import create_app
from models import Candidata, Solicitud
from utils.modality_normalizer import normalize_candidata_modalidad, normalize_solicitud_modalidad


def _norm_label(value):
    return value if value is not None else "None"


def main(limit: int = 50) -> None:
    app = create_app()
    with app.app_context():
        print(f"== Diagnostico modalidad (limite={limit}) ==\n")

        solicitudes = (
            Solicitud.query.filter(Solicitud.modalidad_trabajo.isnot(None))
            .filter(Solicitud.modalidad_trabajo != "")
            .order_by(Solicitud.id.desc())
            .limit(limit)
            .all()
        )

        cand_rows = (
            Candidata.query.filter(Candidata.modalidad_trabajo_preferida.isnot(None))
            .filter(Candidata.modalidad_trabajo_preferida != "")
            .order_by(Candidata.fila.desc())
            .limit(limit)
            .all()
        )

        sol_counts = Counter()
        cand_counts = Counter()

        print("Solicitudes recientes")
        for sol in solicitudes:
            raw = (sol.modalidad_trabajo or "").strip()
            norm, _reason = normalize_solicitud_modalidad(raw)
            sol_counts[_norm_label(norm)] += 1
            print(f"id={sol.id} | raw={raw!r} | norm={norm!r}")

        print("\nCandidatas recientes")
        for cand in cand_rows:
            raw = (cand.modalidad_trabajo_preferida or "").strip()
            norm, _reason = normalize_candidata_modalidad(raw)
            cand_counts[_norm_label(norm)] += 1
            print(f"fila={cand.fila} | raw={raw!r} | norm={norm!r}")

        print("\nResumen solicitudes (dormida/salida_diaria/None)")
        for key in ("dormida", "salida_diaria", "None"):
            print(f"{key}: {sol_counts.get(key, 0)}")

        print("\nResumen candidatas (dormida/salida_diaria/None)")
        for key in ("dormida", "salida_diaria", "None"):
            print(f"{key}: {cand_counts.get(key, 0)}")


if __name__ == "__main__":
    main(limit=50)
