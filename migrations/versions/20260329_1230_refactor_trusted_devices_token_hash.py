"""refactor trusted devices identity to per-user token hash

Revision ID: 20260329_1230
Revises: 20260329_1200
Create Date: 2026-03-29 12:30:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "20260329_1230"
down_revision = "20260329_1200"
branch_labels = None
depends_on = None


def _has_table(bind, table_name: str) -> bool:
    try:
        return bool(inspect(bind).has_table(table_name))
    except Exception:
        return False


def _column_names(bind, table_name: str) -> set[str]:
    try:
        return {str(col.get("name") or "") for col in inspect(bind).get_columns(table_name)}
    except Exception:
        return set()


def upgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    if not _has_table(bind, "trusted_devices"):
        return

    cols = _column_names(bind, "trusted_devices")
    if "device_token_hash" not in cols:
        op.add_column("trusted_devices", sa.Column("device_token_hash", sa.String(length=64), nullable=True))

    # Backfill inicial para conservar la confianza de dispositivos previos.
    op.execute(
        """
        UPDATE trusted_devices
        SET device_token_hash = device_fingerprint
        WHERE device_token_hash IS NULL
        """
    )

    # Remueve unicidad global legacy sobre device_fingerprint.
    if dialect == "postgresql":
        op.execute(
            """
            DO $$
            DECLARE rec RECORD;
            BEGIN
              FOR rec IN
                SELECT c.conname
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                JOIN unnest(c.conkey) AS ck(attnum) ON true
                JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ck.attnum
                WHERE c.contype = 'u'
                  AND t.relname = 'trusted_devices'
                  AND n.nspname = current_schema()
                GROUP BY c.conname
                HAVING COUNT(*) = 1 AND MIN(a.attname) = 'device_fingerprint'
              LOOP
                EXECUTE format('ALTER TABLE trusted_devices DROP CONSTRAINT IF EXISTS %I', rec.conname);
              END LOOP;
            END
            $$;
            """
        )
        op.execute(
            """
            DO $$
            DECLARE rec RECORD;
            BEGIN
              FOR rec IN
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND tablename = 'trusted_devices'
                  AND indexdef ILIKE 'CREATE UNIQUE INDEX%'
                  AND indexdef ILIKE '%(device_fingerprint)%'
              LOOP
                EXECUTE format('DROP INDEX IF EXISTS %I', rec.indexname);
              END LOOP;
            END
            $$;
            """
        )
    else:
        # SQLite: no se elimina la constraint declarativa sin rebuild;
        # se agrega índice compuesto para el flujo nuevo.
        pass

    # Unicidad correcta por usuario + token hash.
    if dialect == "postgresql":
        op.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_trusted_devices_user_token
            ON trusted_devices (user_id, device_token_hash)
            """
        )
    else:
        op.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_trusted_devices_user_token
            ON trusted_devices (user_id, device_token_hash)
            """
        )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_trusted_devices_token_hash
        ON trusted_devices (device_token_hash)
        """
    )


def downgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    if not _has_table(bind, "trusted_devices"):
        return

    op.execute("DROP INDEX IF EXISTS ix_trusted_devices_token_hash")
    op.execute("DROP INDEX IF EXISTS uq_trusted_devices_user_token")

    if dialect == "postgresql":
        op.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_trusted_devices_device_fingerprint
            ON trusted_devices (device_fingerprint)
            """
        )

    cols = _column_names(bind, "trusted_devices")
    if "device_token_hash" in cols:
        op.drop_column("trusted_devices", "device_token_hash")
