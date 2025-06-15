"""Create candidatas table

Revision ID: 0001_create_candidatas
Revises: 
Create Date: 2025-05-26 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# Debe coincidir con el nombre del archivo antes del primer guión
revision = '0001_create_candidatas'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        'candidatas',
        sa.Column('fila', sa.Integer(), primary_key=True),
        sa.Column('marca_temporal', sa.DateTime(), nullable=False),
        sa.Column('nombre_completo', sa.String(length=200), nullable=False),
        sa.Column('edad', sa.Integer(), nullable=True),
        sa.Column('numero_telefono', sa.String(length=50), nullable=True),
        sa.Column('direccion_completa', sa.String(length=300), nullable=True),
        sa.Column('modalidad_trabajo_preferida', sa.String(length=100), nullable=True),
        sa.Column('rutas_cercanas', sa.String(length=200), nullable=True),
        sa.Column('empleo_anterior', sa.Text(), nullable=True),
        sa.Column('anos_experiencia', sa.Integer(), nullable=True),
        sa.Column('areas_experiencia', sa.Text(), nullable=True),
        sa.Column('sabe_planchar', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('contactos_referencias_laborales', sa.Text(), nullable=True),
        sa.Column('referencias_familiares_detalle', sa.Text(), nullable=True),
        sa.Column('acepta_porcentaje_sueldo', sa.Numeric(5,2), nullable=True),
        sa.Column('cedula', sa.String(length=50), nullable=False, unique=True),
        sa.Column('codigo', sa.String(length=50), nullable=True, unique=True),
        sa.Column('medio_inscripcion', sa.String(length=100), nullable=True),
        sa.Column('inscripcion', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('monto', sa.Numeric(12,2), nullable=True),
        sa.Column('fecha', sa.Date(), nullable=True),
        sa.Column('fecha_de_pago', sa.Date(), nullable=True),
        sa.Column('inicio', sa.Date(), nullable=True),
        sa.Column('monto_total', sa.Numeric(12,2), nullable=True),
        sa.Column('porciento', sa.Numeric(5,2), nullable=True),
        sa.Column('calificacion', sa.String(length=100), nullable=True),
        sa.Column('entrevista', sa.Text(), nullable=True),
        sa.Column('depuracion', sa.String(length=300), nullable=True),
        sa.Column('perfil', sa.String(length=300), nullable=True),
        sa.Column('cedula1', sa.String(length=300), nullable=True),
        sa.Column('cedula2', sa.String(length=300), nullable=True),
        sa.Column('referencias_laboral', sa.Text(), nullable=True),
        sa.Column('referencias_familiares', sa.Text(), nullable=True),
        sa.CheckConstraint('acepta_porcentaje_sueldo BETWEEN -999.99 AND 999.99', name='chk_acepta_porcentaje'),
        sa.CheckConstraint('porciento BETWEEN -999.99 AND 999.99', name='chk_porciento'),
    )

def downgrade():
    op.drop_table('candidatas')
