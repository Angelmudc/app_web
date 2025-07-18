"""Add estado and audit fields to candidatas

Revision ID: 1d39e2382335
Revises: 00802568522a
Create Date: 2025-07-12 22:52:03.443635

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '1d39e2382335'
down_revision = '00802568522a'
branch_labels = None
depends_on = None


def upgrade():
    # Create ENUM type for estado
    estado_candidata = sa.Enum(
        'en_proceso',
        'proceso_inscripcion',
        'inscrita',
        'inscrita_incompleta',
        'lista_para_trabajar',
        'trabajando',
        'descalificada',
        name='estado_candidata_enum'
    )
    estado_candidata.create(op.get_bind(), checkfirst=True)

    # Add columns to candidatas table
    with op.batch_alter_table('candidatas', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'estado',
            estado_candidata,
            nullable=False,
            server_default=sa.text("'en_proceso'"),
            comment='Estado actual de la candidata'
        ))
        batch_op.add_column(sa.Column(
            'fecha_cambio_estado',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text('NOW()'),
            comment='Fecha de la última actualización de estado'
        ))
        batch_op.add_column(sa.Column(
            'usuario_cambio_estado',
            sa.String(length=100),
            nullable=True,
            comment='Usuario (nombre o ID) que cambió el estado'
        ))
        batch_op.add_column(sa.Column(
            'nota_descalificacion',
            sa.Text(),
            nullable=True,
            comment='Motivo o nota de por qué la candidata fue descalificada'
        ))


def downgrade():
    # Remove columns from candidatas table
    with op.batch_alter_table('candidatas', schema=None) as batch_op:
        batch_op.drop_column('nota_descalificacion')
        batch_op.drop_column('usuario_cambio_estado')
        batch_op.drop_column('fecha_cambio_estado')
        batch_op.drop_column('estado')

    # Drop ENUM type
    estado_candidata = sa.Enum(name='estado_candidata_enum')
    estado_candidata.drop(op.get_bind(), checkfirst=True)
