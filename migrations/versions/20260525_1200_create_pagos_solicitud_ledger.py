"""create pagos_solicitud ledger and backfill legacy payments

Revision ID: 20260525_1200
Revises: 20260522_1200
Create Date: 2026-05-25 12:00:00
"""

from decimal import Decimal, InvalidOperation

from alembic import op
import sqlalchemy as sa


revision = "20260525_1200"
down_revision = "20260522_1200"
branch_labels = None
depends_on = None


def _to_decimal(raw) -> Decimal:
    txt = str(raw or "").strip()
    if not txt:
        return Decimal("0.00")
    cleaned = "".join(ch for ch in txt if ch.isdigit() or ch in ".,-")
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def _insert_pago_if_missing(conn, *, solicitud_id: int, cliente_id: int, monto: Decimal, tipo_pago: str, origen_id: str):
    if monto <= Decimal("0.00"):
        return

    exists = conn.execute(
        sa.text(
            """
            SELECT 1
            FROM pagos_solicitud
            WHERE solicitud_id = :solicitud_id
              AND origen = 'legacy_backfill'
              AND origen_id = :origen_id
            LIMIT 1
            """
        ),
        {"solicitud_id": int(solicitud_id), "origen_id": str(origen_id)},
    ).scalar()
    if exists:
        return

    conn.execute(
        sa.text(
            """
            INSERT INTO pagos_solicitud (
                solicitud_id,
                cliente_id,
                monto,
                tipo_pago,
                origen,
                origen_id,
                created_at,
                updated_at
            ) VALUES (
                :solicitud_id,
                :cliente_id,
                :monto,
                :tipo_pago,
                'legacy_backfill',
                :origen_id,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            )
            """
        ),
        {
            "solicitud_id": int(solicitud_id),
            "cliente_id": int(cliente_id),
            "monto": str(monto.quantize(Decimal("0.01"))),
            "tipo_pago": str(tipo_pago),
            "origen_id": str(origen_id),
        },
    )


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    dialect = str(bind.dialect.name or "").lower()

    if not inspector.has_table("pagos_solicitud"):
        op.create_table(
            "pagos_solicitud",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("solicitud_id", sa.Integer(), sa.ForeignKey("solicitudes.id"), nullable=False),
            sa.Column("cliente_id", sa.Integer(), sa.ForeignKey("clientes.id"), nullable=False),
            sa.Column("monto", sa.Numeric(12, 2), nullable=False),
            sa.Column("tipo_pago", sa.String(length=30), nullable=False),
            sa.Column("metodo_pago", sa.String(length=50), nullable=True),
            sa.Column("referencia", sa.String(length=120), nullable=True),
            sa.Column("nota", sa.Text(), nullable=True),
            sa.Column("registrado_por_id", sa.Integer(), sa.ForeignKey("staff_users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("anulado_at", sa.DateTime(), nullable=True),
            sa.Column("anulado_por_id", sa.Integer(), sa.ForeignKey("staff_users.id"), nullable=True),
            sa.Column("motivo_anulacion", sa.Text(), nullable=True),
            sa.Column("origen", sa.String(length=50), nullable=True),
            sa.Column("origen_id", sa.String(length=120), nullable=True),
        )

    op.execute("CREATE INDEX IF NOT EXISTS ix_pagos_solicitud_solicitud_id ON pagos_solicitud (solicitud_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_pagos_solicitud_cliente_id ON pagos_solicitud (cliente_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_pagos_solicitud_created_at ON pagos_solicitud (created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_pagos_solicitud_anulado_at ON pagos_solicitud (anulado_at)")

    if dialect == "postgresql":
        op.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_pagos_solicitud_backfill_origin
            ON pagos_solicitud (solicitud_id, origen, origen_id)
            WHERE origen IS NOT NULL AND origen_id IS NOT NULL
            """
        )
    else:
        op.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_pagos_solicitud_backfill_origin
            ON pagos_solicitud (solicitud_id, origen, origen_id)
            """
        )

    if not inspector.has_table("solicitudes"):
        return

    rows = bind.execute(
        sa.text("SELECT id, cliente_id, abono, monto_pagado FROM solicitudes")
    ).fetchall()

    for row in rows:
        solicitud_id = int(row[0])
        cliente_id = int(row[1]) if row[1] is not None else 0
        if cliente_id <= 0:
            continue
        abono = _to_decimal(row[2])
        monto_pagado = _to_decimal(row[3])

        if abono > Decimal("0.00"):
            _insert_pago_if_missing(
                bind,
                solicitud_id=solicitud_id,
                cliente_id=cliente_id,
                monto=abono,
                tipo_pago="abono",
                origen_id=f"abono:{solicitud_id}",
            )

        if monto_pagado > Decimal("0.00") and abono <= Decimal("0.00"):
            _insert_pago_if_missing(
                bind,
                solicitud_id=solicitud_id,
                cliente_id=cliente_id,
                monto=monto_pagado,
                tipo_pago="pago",
                origen_id=f"monto_pagado:{solicitud_id}",
            )
        elif monto_pagado > abono and abono > Decimal("0.00"):
            _insert_pago_if_missing(
                bind,
                solicitud_id=solicitud_id,
                cliente_id=cliente_id,
                monto=(monto_pagado - abono),
                tipo_pago="pago",
                origen_id=f"monto_pagado_diff:{solicitud_id}",
            )

    # Sincroniza cache legacy monto_pagado desde ledger (abono cuenta como pago).
    if dialect == "postgresql":
        bind.execute(
            sa.text(
                """
                UPDATE solicitudes s
                SET monto_pagado = COALESCE(x.total_pagado::text, '0.00')
                FROM (
                    SELECT solicitud_id,
                           SUM(
                               CASE
                                   WHEN anulado_at IS NULL AND tipo_pago IN ('abono','pago','ajuste','correccion') AND monto > 0 THEN monto
                                   WHEN anulado_at IS NULL AND tipo_pago = 'devolucion' AND monto > 0 THEN -monto
                                   ELSE 0
                               END
                           ) AS total_pagado
                    FROM pagos_solicitud
                    GROUP BY solicitud_id
                ) x
                WHERE s.id = x.solicitud_id
                """
            )
        )


def downgrade():
    op.execute("DROP INDEX IF EXISTS uq_pagos_solicitud_backfill_origin")
    op.execute("DROP INDEX IF EXISTS ix_pagos_solicitud_anulado_at")
    op.execute("DROP INDEX IF EXISTS ix_pagos_solicitud_created_at")
    op.execute("DROP INDEX IF EXISTS ix_pagos_solicitud_cliente_id")
    op.execute("DROP INDEX IF EXISTS ix_pagos_solicitud_solicitud_id")
    op.drop_table("pagos_solicitud")
