"""Add creator and last editor references to entrevistas."""

from alembic import op
import sqlalchemy as sa


revision = "20261005_1200_interview_auth"
down_revision = "20260831_1200_refs"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "entrevistas",
        sa.Column(
            "created_by_staff_user_id",
            sa.Integer(),
            nullable=True,
            comment="Staff que creó originalmente la entrevista; NULL para históricos desconocidos.",
        ),
    )
    op.add_column(
        "entrevistas",
        sa.Column(
            "updated_by_staff_user_id",
            sa.Integer(),
            nullable=True,
            comment="Último staff que modificó la entrevista.",
        ),
    )
    op.create_foreign_key(
        "fk_entrevistas_created_by_staff_user",
        "entrevistas",
        "staff_users",
        ["created_by_staff_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_entrevistas_updated_by_staff_user",
        "entrevistas",
        "staff_users",
        ["updated_by_staff_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_entrevistas_created_by_staff_user_id",
        "entrevistas",
        ["created_by_staff_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_entrevistas_updated_by_staff_user_id",
        "entrevistas",
        ["updated_by_staff_user_id"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_entrevistas_updated_by_staff_user_id", table_name="entrevistas")
    op.drop_index("ix_entrevistas_created_by_staff_user_id", table_name="entrevistas")
    op.drop_constraint("fk_entrevistas_updated_by_staff_user", "entrevistas", type_="foreignkey")
    op.drop_constraint("fk_entrevistas_created_by_staff_user", "entrevistas", type_="foreignkey")
    op.drop_column("entrevistas", "updated_by_staff_user_id")
    op.drop_column("entrevistas", "created_by_staff_user_id")
