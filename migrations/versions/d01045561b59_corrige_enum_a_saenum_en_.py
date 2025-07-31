"""Corrige Enum a SAEnum en LlamadaCandidata

Revision ID: d01045561b59
Revises: 77b4a92b1690
Create Date: 2025-07-20 12:33:29.733872

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd01045561b59'
down_revision = '77b4a92b1690'
branch_labels = None
depends_on = None

def upgrade():
    # 1) Crear el tipo ENUM en PostgreSQL
    op.execute(
        "CREATE TYPE resultado_enum "
        "AS ENUM ('no_contesta','inscripcion','rechaza','voicemail','otro');"
    )

    # 2) Alter table
    with op.batch_alter_table('llamadas_candidatas', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'agente',
                sa.String(length=100),
                nullable=False,
                server_default='desconocido'
            )
        )
        batch_op.add_column(sa.Column('duracion_segundos', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('proxima_llamada', sa.Date(), nullable=True))
        batch_op.add_column(
            sa.Column(
                'created_at',
                sa.DateTime(),
                nullable=False,
                server_default=sa.text('now()')
            )
        )
        batch_op.alter_column(
            'resultado',
            existing_type=sa.VARCHAR(length=100),
            type_=sa.Enum(
                'no_contesta',
                'inscripcion',
                'rechaza',
                'voicemail',
                'otro',
                name='resultado_enum'
            ),
            existing_nullable=False,
            postgresql_using="resultado::resultado_enum"
        )
        batch_op.create_index(batch_op.f('ix_llamadas_candidatas_agente'), ['agente'], unique=False)
        batch_op.create_index(batch_op.f('ix_llamadas_candidatas_candidata_id'), ['candidata_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_llamadas_candidatas_fecha_llamada'), ['fecha_llamada'], unique=False)
        batch_op.create_index(batch_op.f('ix_llamadas_candidatas_proxima_llamada'), ['proxima_llamada'], unique=False)
        batch_op.create_index(batch_op.f('ix_llamadas_candidatas_resultado'), ['resultado'], unique=False)

def downgrade():
    # 1) Deshacer alteraciones
    with op.batch_alter_table('llamadas_candidatas', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_llamadas_candidatas_resultado'))
        batch_op.drop_index(batch_op.f('ix_llamadas_candidatas_proxima_llamada'))
        batch_op.drop_index(batch_op.f('ix_llamadas_candidatas_fecha_llamada'))
        batch_op.drop_index(batch_op.f('ix_llamadas_candidatas_candidata_id'))
        batch_op.drop_index(batch_op.f('ix_llamadas_candidatas_agente'))
        batch_op.alter_column(
            'resultado',
            existing_type=sa.Enum(
                'no_contesta',
                'inscripcion',
                'rechaza',
                'voicemail',
                'otro',
                name='resultado_enum'
            ),
            type_=sa.VARCHAR(length=100),
            existing_nullable=False
        )
        batch_op.drop_column('created_at')
        batch_op.drop_column('proxima_llamada')
        batch_op.drop_column('duracion_segundos')
        batch_op.drop_column('agente')
    # 2) Eliminar el tipo ENUM
    op.execute("DROP TYPE resultado_enum;")
