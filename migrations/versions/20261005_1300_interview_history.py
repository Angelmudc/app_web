"""Add append-only interview change history."""

from alembic import op
import sqlalchemy as sa


revision = "20261005_1300_interview_history"
down_revision = "20261005_1200_interview_auth"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "entrevista_change_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "entrevista_id",
            sa.Integer(),
            sa.ForeignKey("entrevistas.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(length=20), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column(
            "staff_user_id",
            sa.Integer(),
            sa.ForeignKey("staff_users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("staff_display_name", sa.String(length=160), nullable=True),
        sa.Column("changes_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.create_index(
        "ix_entrevista_change_history_entrevista_occurred",
        "entrevista_change_history",
        ["entrevista_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_entrevista_change_history_staff_user_id",
        "entrevista_change_history",
        ["staff_user_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_entrevista_change_history_staff_user_id",
        table_name="entrevista_change_history",
    )
    op.drop_index(
        "ix_entrevista_change_history_entrevista_occurred",
        table_name="entrevista_change_history",
    )
    op.drop_table("entrevista_change_history")
