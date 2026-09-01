"""Create entrevista_referencias

Revision ID: 20260831_1200_create_entrevista_referencias
Revises: 20260709_1200
Create Date: 2026-08-31 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "20260831_1200_refs"
down_revision = "20260709_1200"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "entrevista_referencias",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "entrevista_id",
            sa.Integer(),
            sa.ForeignKey("entrevistas.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tipo", sa.String(length=20), nullable=False),
        sa.Column("texto", sa.Text(), nullable=True),
        sa.Column("datos_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("creada_en", sa.DateTime(), nullable=False),
        sa.Column("actualizada_en", sa.DateTime(), nullable=True),
        sa.CheckConstraint("tipo IN ('laboral', 'familiar')", name="ck_entrevista_referencia_tipo"),
        sa.UniqueConstraint("entrevista_id", "tipo", name="uq_entrevista_referencia_entrevista_tipo"),
    )
    op.create_index(
        "ix_entrevista_referencias_entrevista_id",
        "entrevista_referencias",
        ["entrevista_id"],
        unique=False,
    )
    op.create_index(
        "ix_entrevista_referencias_tipo",
        "entrevista_referencias",
        ["tipo"],
        unique=False,
    )

    op.execute(
        sa.text(
            """
            INSERT INTO entrevista_referencias (entrevista_id, tipo, texto, creada_en, actualizada_en)
            SELECT er.entrevista_id,
                   CASE
                       WHEN ep.clave LIKE '%.referencia_laboral' THEN 'laboral'
                       WHEN ep.clave LIKE '%.referencia_familiar' THEN 'familiar'
                   END AS tipo,
                   TRIM(er.respuesta) AS texto,
                   COALESCE(er.creada_en, CURRENT_TIMESTAMP) AS creada_en,
                   er.actualizada_en
            FROM entrevista_respuestas er
            JOIN entrevista_preguntas ep ON ep.id = er.pregunta_id
            JOIN (
                SELECT entrevista_id, pregunta_id, MAX(id) AS max_id
                FROM entrevista_respuestas
                GROUP BY entrevista_id, pregunta_id
            ) latest ON latest.max_id = er.id
            WHERE ep.clave IN (
                'domestica.referencia_laboral',
                'domestica.referencia_familiar',
                'enfermera.referencia_laboral',
                'enfermera.referencia_familiar',
                'empleo_general.referencia_laboral',
                'empleo_general.referencia_familiar'
            )
              AND TRIM(COALESCE(er.respuesta, '')) <> ''
            """
        )
    )


def downgrade():
    op.drop_index("ix_entrevista_referencias_tipo", table_name="entrevista_referencias")
    op.drop_index("ix_entrevista_referencias_entrevista_id", table_name="entrevista_referencias")
    op.drop_table("entrevista_referencias")
