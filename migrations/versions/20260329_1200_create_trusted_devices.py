"""create trusted_devices table for intelligent MFA

Revision ID: 20260329_1200
Revises: 20260329_1100
Create Date: 2026-03-29 12:00:00
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260329_1200"
down_revision = "20260329_1100"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS trusted_devices (
                id BIGSERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES staff_users(id) ON DELETE CASCADE,
                device_fingerprint VARCHAR(128) NOT NULL UNIQUE,
                ip_address VARCHAR(64),
                user_agent VARCHAR(512),
                last_used_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                is_trusted BOOLEAN NOT NULL DEFAULT TRUE
            )
            """
        )
    else:
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS trusted_devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                device_fingerprint VARCHAR(128) NOT NULL UNIQUE,
                ip_address VARCHAR(64),
                user_agent VARCHAR(512),
                last_used_at DATETIME NOT NULL,
                created_at DATETIME NOT NULL,
                is_trusted BOOLEAN NOT NULL DEFAULT 1,
                FOREIGN KEY(user_id) REFERENCES staff_users(id) ON DELETE CASCADE
            )
            """
        )

    op.execute("CREATE INDEX IF NOT EXISTS ix_trusted_devices_user_id ON trusted_devices (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_trusted_devices_last_used_at ON trusted_devices (last_used_at)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS trusted_devices")
