#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app
from config_app import db


def _has_col(table_name: str, col: str) -> bool:
    row = db.session.execute(
        text(
            """
            select 1
            from information_schema.columns
            where table_schema='public' and table_name=:t and column_name=:c
            limit 1
            """
        ),
        {"t": table_name, "c": col},
    ).first()
    return bool(row)


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Auditoría de integridad de intake público")
    p.add_argument("--limit", type=int, default=120)
    p.add_argument("--token-hash", default="")
    p.add_argument("--cliente-id", type=int, default=0)
    p.add_argument("--email-like", default="")
    p.add_argument("--telefono-like", default="")
    p.add_argument("--nombre-like", default="")
    p.add_argument("--cedula-like", default="")
    return p.parse_args()


def run() -> int:
    args = _args()
    with app.app_context():
        has_reason = _has_col("public_solicitud_tokens_usados", "consumption_reason")
        has_source = _has_col("public_solicitud_tokens_usados", "public_form_source")
        has_ip = _has_col("public_solicitud_tokens_usados", "request_ip")
        has_ua = _has_col("public_solicitud_tokens_usados", "request_user_agent")
        has_solicitud_cedula = _has_col("solicitudes", "cedula")

        reason_expr = "coalesce(consumption_reason,'submitted')" if has_reason else "'submitted'"
        source_expr = "public_form_source" if has_source else "null::varchar"
        ip_expr = "request_ip" if has_ip else "null::varchar"
        ua_expr = "request_user_agent" if has_ua else "null::varchar"
        cedula_expr = "coalesce(s.cedula,'')" if has_solicitud_cedula else "''"

        print("PUBLIC_INTAKE_AUDIT_START")

        summary = db.session.execute(text("""
            select
              (select count(*) from solicitudes where review_status='nuevo') as nuevo,
              (select count(*) from solicitudes where review_status='en_gestion') as en_gestion,
              (select count(*) from solicitudes where review_status='revisado') as revisado,
              (select count(*) from solicitudes where review_status='descartado') as descartado,
              (select count(*) from solicitudes where review_status in ('nuevo','en_gestion')) as pending
        """)).mappings().first()
        print(f"COUNTS nuevo={summary['nuevo']} en_gestion={summary['en_gestion']} revisado={summary['revisado']} descartado={summary['descartado']} pending={summary['pending']}")

        rows = db.session.execute(text(f"""
            with used as (
                select 'cliente_existente' as token_table, id, token_hash, cliente_id, solicitud_id, used_at,
                       {reason_expr} as consumption_reason,
                       {source_expr} as public_form_source, {ip_expr} as request_ip, {ua_expr} as request_user_agent
                from public_solicitud_tokens_usados
                union all
                select 'cliente_nuevo' as token_table, id, token_hash, cliente_id, solicitud_id, used_at,
                       {reason_expr} as consumption_reason,
                       {source_expr} as public_form_source, {ip_expr} as request_ip, {ua_expr} as request_user_agent
                from public_solicitud_cliente_nuevo_tokens_usados
            )
            select
              u.token_table,
              u.id as token_use_id,
              u.token_hash,
              u.used_at,
              u.consumption_reason,
              u.public_form_source as usage_source,
              u.request_ip,
              u.cliente_id as usage_cliente_id,
              c.codigo as cliente_codigo,
              c.nombre_completo,
              c.telefono,
              c.email,
              s.id as solicitud_id,
              s.codigo_solicitud,
              s.review_status,
              s.public_form_source as solicitud_source,
              case when s.review_status in ('nuevo','en_gestion') then true else false end as appears_in_pending,
              case
                when s.id is null and u.consumption_reason='submitted' then 'token_submitted_without_solicitud'
                when s.id is null then 'token_consumed_non_submit_path'
                when s.review_status not in ('nuevo','en_gestion') then 'solicitud_outside_pending_filter'
                when s.cliente_id is distinct from u.cliente_id then 'cliente_mismatch_between_usage_and_solicitud'
                else 'ok'
              end as diagnosis
            from used u
            left join solicitudes s on s.id=u.solicitud_id
            left join clientes c on c.id=coalesce(u.cliente_id, s.cliente_id)
            where (:token_hash='' or u.token_hash=:token_hash)
              and (:cliente_id=0 or coalesce(u.cliente_id,s.cliente_id)=:cliente_id)
              and (:email_like='' or coalesce(c.email,'') ilike '%' || :email_like || '%')
              and (:telefono_like='' or coalesce(c.telefono,'') ilike '%' || :telefono_like || '%')
              and (:nombre_like='' or coalesce(c.nombre_completo,'') ilike '%' || :nombre_like || '%')
              and (:cedula_like='' or {cedula_expr} ilike '%' || :cedula_like || '%')
            order by u.used_at desc, u.id desc
            limit {max(1, min(int(args.limit), 1000))}
        """), {
            "token_hash": (args.token_hash or "").strip(),
            "cliente_id": int(args.cliente_id or 0),
            "email_like": (args.email_like or "").strip(),
            "telefono_like": (args.telefono_like or "").strip(),
            "nombre_like": (args.nombre_like or "").strip(),
            "cedula_like": (args.cedula_like or "").strip(),
        }).mappings().all()

        for r in rows:
            print(
                "ROW"
                f" token_table={r['token_table']} token_use_id={r['token_use_id']} token_hash={r['token_hash']}"
                f" used_at={r['used_at']} reason={r['consumption_reason']} usage_source={r['usage_source']}"
                f" usage_cliente_id={r['usage_cliente_id']} cliente_codigo={r['cliente_codigo']}"
                f" solicitud_id={r['solicitud_id']} codigo={r['codigo_solicitud']} review_status={r['review_status']}"
                f" appears_in_pending={r['appears_in_pending']} diagnosis={r['diagnosis']}"
            )

        agg = db.session.execute(text(f"""
            with used as (
                select {reason_expr} as consumption_reason, solicitud_id
                from public_solicitud_tokens_usados
                union all
                select {reason_expr} as consumption_reason, solicitud_id
                from public_solicitud_cliente_nuevo_tokens_usados
            )
            select consumption_reason, count(*) as total,
                   sum(case when solicitud_id is null then 1 else 0 end) as without_solicitud
            from used
            group by consumption_reason
            order by total desc
        """)).mappings().all()
        for a in agg:
            print(f"AGG reason={a['consumption_reason']} total={a['total']} without_solicitud={a['without_solicitud']}")

        print("PUBLIC_INTAKE_AUDIT_END")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
