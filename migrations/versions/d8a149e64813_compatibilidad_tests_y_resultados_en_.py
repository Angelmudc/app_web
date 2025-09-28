"""Compatibilidad: tests y resultados en Candidata y Solicitud

Revision ID: d8a149e64813
Revises: 66dbf2258d86
Create Date: 2025-09-28 10:00:51.868406
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'd8a149e64813'
down_revision = '66dbf2258d86'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()

    # 1) Crear ENUMs (si no existen)
    compat_ritmo_enum  = postgresql.ENUM('tranquilo', 'activo', 'muy_activo', name='compat_ritmo_enum')
    compat_estilo_enum = postgresql.ENUM('necesita_instrucciones', 'toma_iniciativa', name='compat_estilo_enum')
    compat_ninos_enum  = postgresql.ENUM('comoda', 'neutral', 'prefiere_evitar', name='compat_ninos_enum')
    compat_level_enum  = postgresql.ENUM('alta', 'media', 'baja', name='compat_level_enum')

    compat_ritmo_enum.create(bind, checkfirst=True)
    compat_estilo_enum.create(bind, checkfirst=True)
    compat_ninos_enum.create(bind, checkfirst=True)
    compat_level_enum.create(bind, checkfirst=True)

    # 2) CANDIDATAS — nuevas columnas
    op.add_column('candidatas', sa.Column(
        'compat_test_candidata_json',
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=True,
        comment='Respuestas completas del test/entrevista de la candidata (JSON).'
    ))
    op.add_column('candidatas', sa.Column(
        'compat_test_candidata_at',
        sa.DateTime(),
        nullable=True,
        comment='Fecha/hora de la última actualización del test de la candidata.'
    ))
    op.add_column('candidatas', sa.Column(
        'compat_fortalezas',
        postgresql.ARRAY(sa.String(length=50)),
        nullable=True,
        server_default=sa.text("ARRAY[]::VARCHAR[]"),
        comment='Top fortalezas (ej.: limpieza, cocina, lavado, niños).'
    ))
    op.add_column('candidatas', sa.Column(
        'compat_ritmo_preferido',
        compat_ritmo_enum,
        nullable=True,
        comment='Ritmo de trabajo preferido.'
    ))
    op.add_column('candidatas', sa.Column(
        'compat_estilo_trabajo',
        compat_estilo_enum,
        nullable=True,
        comment='Prefiere instrucciones o tomar iniciativa.'
    ))
    op.add_column('candidatas', sa.Column(
        'compat_orden_detalle_nivel',
        sa.SmallInteger(),
        nullable=True,
        comment='Nivel 1–5 en orden/detalle.'
    ))
    op.add_column('candidatas', sa.Column(
        'compat_relacion_ninos',
        compat_ninos_enum,
        nullable=True,
        comment='Comodidad trabajando con niños.'
    ))
    op.add_column('candidatas', sa.Column(
        'compat_limites_no_negociables',
        postgresql.ARRAY(sa.String(length=100)),
        nullable=True,
        server_default=sa.text("ARRAY[]::VARCHAR[]"),
        comment='Límites (p. ej.: no mascotas, no cocinar, no dormir fuera).'
    ))
    op.add_column('candidatas', sa.Column(
        'compat_disponibilidad_dias',
        postgresql.ARRAY(sa.String(length=20)),
        nullable=True,
        server_default=sa.text("ARRAY[]::VARCHAR[]"),
        comment='Días disponibles (Lun, Mar, Mie, Jue, Vie, Sab, Dom).'
    ))
    op.add_column('candidatas', sa.Column(
        'compat_disponibilidad_horario',
        sa.String(length=100),
        nullable=True,
        comment='Franja horaria preferida (ej.: 8am–5pm).'
    ))

    # 3) SOLICITUDES — nuevas columnas
    op.add_column('solicitudes', sa.Column(
        'compat_test_cliente_json',
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=True,
        comment='Respuestas del test del CLIENTE (JSON).'
    ))
    op.add_column('solicitudes', sa.Column(
        'compat_test_cliente_at',
        sa.DateTime(),
        nullable=True,
        comment='Fecha/hora en que el cliente guardó su test.'
    ))
    op.add_column('solicitudes', sa.Column(
        'compat_test_cliente_version',
        sa.String(length=20),
        nullable=True,
        comment='Versión del cuestionario del cliente.'
    ))
    op.add_column('solicitudes', sa.Column(
        'compat_calc_score',
        sa.Integer(),
        nullable=True,
        comment='Porcentaje 0–100 del match cliente↔candidata (último cálculo).'
    ))
    op.add_column('solicitudes', sa.Column(
        'compat_calc_level',
        compat_level_enum,
        nullable=True,
        comment='Nivel del match según el score.'
    ))
    op.add_column('solicitudes', sa.Column(
        'compat_calc_summary',
        sa.Text(),
        nullable=True,
        comment='Coincidencias clave y explicación breve.'
    ))
    op.add_column('solicitudes', sa.Column(
        'compat_calc_risks',
        sa.Text(),
        nullable=True,
        comment='Riesgos/alertas (no negociables, horarios, etc.).'
    ))
    op.add_column('solicitudes', sa.Column(
        'compat_calc_at',
        sa.DateTime(),
        nullable=True,
        comment='Fecha/hora del último cálculo de compatibilidad.'
    ))
    op.add_column('solicitudes', sa.Column(
        'compat_pdf_path',
        sa.String(length=255),
        nullable=True,
        comment='Ruta/filename del PDF de compatibilidad.'
    ))


def downgrade():
    bind = op.get_bind()

    # Eliminar columnas de SOLICITUDES
    op.drop_column('solicitudes', 'compat_pdf_path')
    op.drop_column('solicitudes', 'compat_calc_at')
    op.drop_column('solicitudes', 'compat_calc_risks')
    op.drop_column('solicitudes', 'compat_calc_summary')
    op.drop_column('solicitudes', 'compat_calc_level')
    op.drop_column('solicitudes', 'compat_calc_score')
    op.drop_column('solicitudes', 'compat_test_cliente_version')
    op.drop_column('solicitudes', 'compat_test_cliente_at')
    op.drop_column('solicitudes', 'compat_test_cliente_json')

    # Eliminar columnas de CANDIDATAS
    op.drop_column('candidatas', 'compat_disponibilidad_horario')
    op.drop_column('candidatas', 'compat_disponibilidad_dias')
    op.drop_column('candidatas', 'compat_limites_no_negociables')
    op.drop_column('candidatas', 'compat_relacion_ninos')
    op.drop_column('candidatas', 'compat_orden_detalle_nivel')
    op.drop_column('candidatas', 'compat_estilo_trabajo')
    op.drop_column('candidatas', 'compat_ritmo_preferido')
    op.drop_column('candidatas', 'compat_fortalezas')
    op.drop_column('candidatas', 'compat_test_candidata_at')
    op.drop_column('candidatas', 'compat_test_candidata_json')

    # Borrar ENUMs (si ya no se usan)
    compat_ritmo_enum  = postgresql.ENUM('tranquilo', 'activo', 'muy_activo', name='compat_ritmo_enum')
    compat_estilo_enum = postgresql.ENUM('necesita_instrucciones', 'toma_iniciativa', name='compat_estilo_enum')
    compat_ninos_enum  = postgresql.ENUM('comoda', 'neutral', 'prefiere_evitar', name='compat_ninos_enum')
    compat_level_enum  = postgresql.ENUM('alta', 'media', 'baja', name='compat_level_enum')

    compat_level_enum.drop(bind, checkfirst=True)
    compat_ninos_enum.drop(bind, checkfirst=True)
    compat_estilo_enum.drop(bind, checkfirst=True)
    compat_ritmo_enum.drop(bind, checkfirst=True)

