from datetime import datetime, date
from sqlalchemy import (
    Column, Integer, String, DateTime, Text, Date,
    Enum as SAEnum, Float, text, LargeBinary
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import relationship
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from config_app import db


class Candidata(db.Model):
    __tablename__ = 'candidatas'

    # Campos existentes
    fila                            = db.Column(db.Integer, primary_key=True)
    marca_temporal                  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    nombre_completo                 = db.Column(db.String(200), nullable=False)
    edad                            = db.Column(db.String(50))
    numero_telefono                 = db.Column(db.String(50))
    direccion_completa              = db.Column(db.String(300))
    modalidad_trabajo_preferida     = db.Column(db.String(100))
    rutas_cercanas                  = db.Column(db.String(200))
    empleo_anterior                 = db.Column(db.Text)
    anos_experiencia                = db.Column(db.String(50))
    areas_experiencia               = db.Column(db.Text)
    sabe_planchar                   = db.Column(db.Boolean, server_default=text('false'), nullable=False)
    contactos_referencias_laborales = db.Column(db.Text)
    referencias_familiares_detalle  = db.Column(db.Text)
    acepta_porcentaje_sueldo        = db.Column(
        db.Boolean,
        server_default=text('false'),
        nullable=False,
        comment="Si acepta que se cobre un porcentaje de su sueldo (true=Sí, false=No)"
    )
    cedula                          = db.Column(db.String(50), unique=True, nullable=False, index=True)
    codigo                          = db.Column(db.String(50), unique=True, index=True)
    medio_inscripcion               = db.Column(db.String(100))
    inscripcion                     = db.Column(db.Boolean, server_default=text('false'), nullable=False)
    monto                           = db.Column(db.Numeric(12, 2))
    fecha                           = db.Column(db.Date)
    fecha_de_pago                   = db.Column(db.Date)
    inicio                          = db.Column(db.Date)
    monto_total                     = db.Column(db.Numeric(12, 2))
    porciento                       = db.Column(db.Numeric(8, 2))
    calificacion                    = db.Column(db.String(100))
    entrevista                      = db.Column(db.Text)
    foto_perfil                     = db.Column(LargeBinary, nullable=True, comment="Foto de la candidata para su perfil")
    depuracion                      = db.Column(LargeBinary)
    perfil                          = db.Column(LargeBinary)
    cedula1                         = db.Column(LargeBinary)
    cedula2                         = db.Column(LargeBinary)
    referencias_laboral             = db.Column(db.Text)
    referencias_familiares          = db.Column(db.Text)

    # Nuevos campos para estado, auditoría y descalificación
    estado = db.Column(
        SAEnum(
            'en_proceso',
            'proceso_inscripcion',
            'inscrita',
            'inscrita_incompleta',
            'lista_para_trabajar',
            'trabajando',
            'descalificada',
            name='estado_candidata_enum'
        ),
        nullable=False,
        server_default=text("'en_proceso'"),
        comment="Estado actual de la candidata"
    )
    fecha_cambio_estado = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        comment="Fecha de la última actualización de estado"
    )
    usuario_cambio_estado = db.Column(
        db.String(100),
        nullable=True,
        comment="Usuario (nombre o ID) que cambió el estado"
    )
    nota_descalificacion = db.Column(
        db.Text,
        nullable=True,
        comment="Motivo o nota de por qué la candidata fue descalificada"
    )

    # Nuevas columnas para finalización del proceso y grupos de empleo
    fecha_finalizacion_proceso = db.Column(
        db.DateTime,
        nullable=True,
        comment="Fecha en que la candidata completa el cuestionario de finalización de proceso"
    )
    grupos_empleo = db.Column(
        ARRAY(db.String(100)),
        nullable=True,
        server_default=text("ARRAY[]::VARCHAR[]"),
        comment="Lista de grupos de empleo asignados a la candidata"
    )

    # Relaciones
    solicitudes = db.relationship(
        'Solicitud',
        back_populates='candidata',
        cascade='all, delete-orphan'
    )

    llamadas                        = db.relationship(
                                         'LlamadaCandidata',
                                         back_populates='candidata',
                                         cascade='all, delete-orphan'
                                      )

from datetime import datetime
from flask_login import UserMixin
from sqlalchemy.orm import synonym as orm_synonym
from config_app import db

class Cliente(UserMixin, db.Model):
    __tablename__ = 'clientes'

    id                         = db.Column(db.Integer, primary_key=True)

    # ----- Credenciales / Login -----
    username                   = db.Column(db.String(64), unique=True, index=True, nullable=False)
    password_hash              = db.Column(db.String(256), nullable=False)

    # Activo / tracking
    is_active                  = db.Column(db.Boolean, nullable=False, default=True)
    created_at                 = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at                 = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ----- Identificación del cliente -----
    codigo                     = db.Column(db.String(20), unique=True, nullable=False, index=True)

    # Rol: 'cliente' o 'admin'
    role                       = db.Column(
                                    db.String(20),
                                    nullable=False,
                                    default='cliente',
                                    comment="Valores: 'cliente' o 'admin'"
                                )

    # Nombre (principal) y alias compatible
    nombre_completo            = db.Column(db.String(200), nullable=False)
    # Alias para compatibilidad: permite usar 'nombre=' al crear o leer
    nombre                     = orm_synonym('nombre_completo')

    # Contacto
    email                      = db.Column(db.String(100), nullable=False, unique=True, index=True)
    telefono                   = db.Column(db.String(20),  nullable=False)

    # ----- Depósitos / estados financieros -----
    porcentaje_deposito        = db.Column(db.Numeric(5, 2),  nullable=False, default=0.00)
    monto_deposito_requerido   = db.Column(db.Numeric(10, 2))
    monto_deposito_pagado      = db.Column(db.Numeric(10, 2))
    estado_deposito            = db.Column(
                                    db.Enum('pendiente', 'confirmado', name='estado_deposito_enum'),
                                    nullable=False,
                                    default='pendiente'
                                )
    notas_admin                = db.Column(db.Text)

    # ----- Ubicación -----
    ciudad                     = db.Column(db.String(100))
    sector                     = db.Column(db.String(100))

    # ----- Métricas de solicitudes -----
    total_solicitudes          = db.Column(db.Integer,  nullable=False, default=0)
    fecha_ultima_solicitud     = db.Column(db.DateTime, nullable=True)
    fecha_registro             = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    fecha_ultima_actividad     = db.Column(db.DateTime, nullable=True)

    # ----- Relaciones -----
    solicitudes = db.relationship(
        'Solicitud',
        back_populates='cliente',
        order_by='Solicitud.fecha_solicitud.desc()',
        cascade='all, delete-orphan'
    )

    # ----- Métodos útiles -----
    def get_id(self):
        return str(self.id)

    def __repr__(self):
        return f"<Cliente {self.username} ({self.codigo})>"

        
class Solicitud(db.Model):
    __tablename__ = 'solicitudes'

    id                     = db.Column(db.Integer, primary_key=True)
    cliente_id             = db.Column(db.Integer, db.ForeignKey('clientes.id'), nullable=False)
    fecha_solicitud        = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    codigo_solicitud       = db.Column(db.String(50), nullable=False, unique=True)

    # Plan y pago
    tipo_plan              = db.Column(db.String(50), nullable=True)
    abono                  = db.Column(db.String(100), nullable=True)
    estado                 = db.Column(
                                SAEnum(
                                    'proceso','activa','pagada','cancelada','reemplazo',
                                    name='estado_solicitud_enum'
                                ),
                                nullable=False,
                                default='proceso'
                             )

    # Ubicación y rutas
    ciudad_sector          = db.Column(db.String(200), nullable=True)
    rutas_cercanas         = db.Column(db.String(200), nullable=True)

    # Detalles de la oferta de trabajo
    modalidad_trabajo      = db.Column(db.String(100), nullable=True)
    edad_requerida = db.Column(
        ARRAY(db.Text),
        nullable=False,
        default=list,
        server_default=text("ARRAY[]::TEXT[]")
    )
    experiencia            = db.Column(db.Text, nullable=True)
    horario                = db.Column(db.String(100), nullable=True)
    funciones              = db.Column(
                                ARRAY(db.String(50)),
                                nullable=True,
                                default=list,
                                server_default=text("ARRAY[]::VARCHAR[]")
                             )

    funciones_otro         = db.Column(
                                String(200),
                                nullable=True
                             )

    # Tipo de lugar
    tipo_lugar = db.Column(
        db.String(200),
        nullable=True
    )

    # Habitaciones y baños
    habitaciones           = db.Column(db.Integer, nullable=True)
    banos                  = db.Column(db.Float,   nullable=True)
    dos_pisos              = db.Column(db.Boolean, nullable=False, default=False)

    # Ocupantes
    adultos                = db.Column(db.Integer, nullable=True)
    ninos                  = db.Column(db.Integer, nullable=True)
    edades_ninos           = db.Column(db.String(100), nullable=True)
    mascota      = db.Column(db.String(100), nullable=True)

    # Compensación
    sueldo                 = db.Column(db.String(100), nullable=True)
    pasaje_aporte          = db.Column(db.Boolean, nullable=True, default=False)

    # Nota adicional
    nota_cliente           = db.Column(db.Text, nullable=True)

    # Áreas comunes (nuevo portal)
    areas_comunes          = db.Column(
                                ARRAY(db.String(50)),
                                nullable=False,
                                default=list,
                                server_default=text("ARRAY[]::VARCHAR[]")
                             )
    area_otro              = db.Column(
                                db.String(200),
                                nullable=True,
                                default='',
                                server_default=text("''")
                             )

    # Pago total y candidatas
    candidata_id           = db.Column(db.Integer, db.ForeignKey('candidatas.fila'), nullable=True)
    monto_pagado           = db.Column(db.String(100), nullable=True)

    # Relaciones
    cliente                = db.relationship('Cliente', back_populates='solicitudes')
    candidata              = db.relationship('Candidata', back_populates='solicitudes')
    reemplazos             = db.relationship(
                                'Reemplazo',
                                back_populates='solicitud',
                                cascade='all, delete-orphan'
                             )

    # Fechas de publicación y modificación
    last_copiado_at        = db.Column(db.DateTime, nullable=True)
    fecha_ultima_modificacion = db.Column(db.DateTime, nullable=True)

    # Cancelación
    fecha_cancelacion      = db.Column(db.DateTime, nullable=True)
    motivo_cancelacion     = db.Column(db.String(255), nullable=True)


class Reemplazo(db.Model):
    __tablename__ = 'reemplazos'
    id                     = db.Column(db.Integer, primary_key=True)
    solicitud_id           = db.Column(db.Integer, db.ForeignKey('solicitudes.id'), nullable=False)
    candidata_old_id       = db.Column(db.Integer, db.ForeignKey('candidatas.fila'), nullable=False)
    motivo_fallo           = db.Column(db.Text,    nullable=False)
    fecha_fallo            = db.Column(db.DateTime,nullable=False, default=datetime.utcnow)
    oportunidad_nueva      = db.Column(db.Boolean, nullable=False, default=False)
    fecha_inicio_reemplazo = db.Column(db.DateTime,nullable=True)
    fecha_fin_reemplazo    = db.Column(db.DateTime,nullable=True)
    candidata_new_id       = db.Column(db.Integer, db.ForeignKey('candidatas.fila'), nullable=True)
    nota_adicional         = db.Column(db.Text,    nullable=True)
    created_at             = db.Column(db.DateTime,nullable=False, default=datetime.utcnow)
    # relaciones:
    solicitud              = db.relationship('Solicitud', back_populates='reemplazos')
    candidata_old          = db.relationship('Candidata', foreign_keys=[candidata_old_id])
    candidata_new          = db.relationship('Candidata', foreign_keys=[candidata_new_id])



class LlamadaCandidata(db.Model):
    __tablename__ = 'llamadas_candidatas'

    id                = Column(Integer, primary_key=True)
    candidata_id      = Column(Integer, db.ForeignKey('candidatas.fila'), nullable=False, index=True)
    agente            = Column(String(100), nullable=False, index=True)      # quién hace la llamada
    fecha_llamada     = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    duracion_segundos = Column(Integer, nullable=True)                       # duración real
    resultado = Column(
        SAEnum(
            'no_contesta',
            'inscripcion',
            'rechaza',
            'voicemail',
            'informada',   # nueva opción
            'exitosa',     # nueva opción
            'otro',
            name='resultado_enum'
        ),
        nullable=False,
        index=True
    )
    notas             = Column(Text, nullable=True)
    proxima_llamada   = Column(Date, nullable=True, index=True)             # siguiente llamada sugerida
    created_at        = Column(DateTime, nullable=False, default=datetime.utcnow)

    candidata         = relationship('Candidata', back_populates='llamadas')