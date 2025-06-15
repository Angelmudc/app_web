# models.py
from config_app import db
from datetime import datetime
from sqlalchemy import CheckConstraint, LargeBinary, text

class Candidata(db.Model):
    __tablename__ = 'candidatas'

    # ─── Campos básicos ─────────────────────────────────────────────────────
    fila                           = db.Column(db.Integer,   primary_key=True)
    marca_temporal                 = db.Column(db.DateTime,  default=datetime.utcnow, nullable=False)
    nombre_completo                = db.Column(db.String(200), nullable=False)
    edad                           = db.Column(db.String(50))
    numero_telefono                = db.Column(db.String(50))
    direccion_completa             = db.Column(db.String(300))
    modalidad_trabajo_preferida    = db.Column(db.String(100))
    rutas_cercanas                 = db.Column(db.String(200))
    empleo_anterior                = db.Column(db.Text)
    anos_experiencia               = db.Column(db.String(50))
    areas_experiencia              = db.Column(db.Text)
    sabe_planchar                  = db.Column(db.Boolean, server_default=text('false'), nullable=False)
    contactos_referencias_laborales= db.Column(db.Text)
    referencias_familiares_detalle = db.Column(db.Text)

    # ─── Inscripción y pagos ────────────────────────────────────────────────
    acepta_porcentaje_sueldo = db.Column(
    db.Boolean,
    server_default=text('false'),
    nullable=False,
    comment="Si acepta que se cobre un porcentaje de su sueldo (true=Sí, false=No)"
    )
    cedula                         = db.Column(db.String(50), unique=True, nullable=False, index=True)
    codigo                         = db.Column(db.String(50), unique=True, index=True)
    medio_inscripcion              = db.Column(db.String(100))
    inscripcion                    = db.Column(db.Boolean, server_default=text('false'), nullable=False)
    monto                          = db.Column(db.Numeric(12, 2))
    fecha                          = db.Column(db.Date)
    fecha_de_pago                  = db.Column(db.Date)
    inicio                         = db.Column(db.Date)
    monto_total                    = db.Column(db.Numeric(12, 2))
    porciento                      = db.Column(db.Numeric(8, 2))
    calificacion                   = db.Column(db.String(100))

    # ─── Entrevista ─────────────────────────────────────────────────────────
    entrevista                     = db.Column(db.Text)

    # ─── Imágenes (ahora almacenadas como BLOB directo) ────────────────────
    depuracion                     = db.Column(LargeBinary)
    perfil                         = db.Column(LargeBinary)
    cedula1                        = db.Column(LargeBinary)
    cedula2                        = db.Column(LargeBinary)

    # ─── Referencias finales ───────────────────────────────────────────────
    referencias_laboral            = db.Column(db.Text)
    referencias_familiares         = db.Column(db.Text)

    __table_args__ = (
        CheckConstraint(
            'acepta_porcentaje_sueldo BETWEEN -10000.00 AND 10000.00',
            name='chk_acepta_porcentaje'
        ),
        CheckConstraint(
            'porciento BETWEEN -10000.00 AND 10000.00',
            name='chk_porciento'
        ),
    )

    def __repr__(self):
        return f"<Candidata {self.nombre_completo}>"
