"""Add atractivo fields to solicitudes

Revision ID: 20260709_1200
Revises: 20260601_1545
Create Date: 2026-07-09 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260709_1200"
down_revision = "20260601_1545"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "solicitudes",
        sa.Column(
            "atractivo_score",
            sa.Integer(),
            nullable=True,
            comment="Score de atractivo operativo de la solicitud, entre 0 y 100.",
        ),
    )
    op.add_column(
        "solicitudes",
        sa.Column(
            "atractivo_label",
            sa.String(length=32),
            nullable=True,
            comment="Etiqueta legible del score de atractivo.",
        ),
    )
    op.add_column(
        "solicitudes",
        sa.Column(
            "atractivo_motivos",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Motivos principales usados para explicar el score de atractivo.",
        ),
    )
    op.add_column(
        "solicitudes",
        sa.Column(
            "atractivo_version",
            sa.String(length=32),
            nullable=True,
            comment="Versión de la fórmula usada para calcular el atractivo.",
        ),
    )
    op.add_column(
        "solicitudes",
        sa.Column(
            "atractivo_calculated_at",
            sa.DateTime(),
            nullable=True,
            comment="Fecha del último cálculo del score de atractivo.",
        ),
    )


def downgrade():
    op.drop_column("solicitudes", "atractivo_calculated_at")
    op.drop_column("solicitudes", "atractivo_version")
    op.drop_column("solicitudes", "atractivo_motivos")
    op.drop_column("solicitudes", "atractivo_label")
    op.drop_column("solicitudes", "atractivo_score")
