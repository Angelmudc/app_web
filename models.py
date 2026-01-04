from datetime import datetime, date
from sqlalchemy import (
    Column, Integer, String, DateTime, Text, Date,
    Enum as SAEnum, Float, text, LargeBinary
)
from typing import Optional, Dict
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import relationship
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from config_app import db
from sqlalchemy.orm import synonym



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

    # ─────────────────────────────────────────────────────────
    # COMPATIBILIDAD – Perfil/Test de la CANDIDATA
    # ─────────────────────────────────────────────────────────
    compat_test_candidata_json = db.Column(
        JSONB,
        nullable=True,
        comment="Respuestas completas del test/entrevista de la candidata (estructura JSON)."
    )
    compat_test_candidata_at = db.Column(
        db.DateTime,
        nullable=True,
        comment="Fecha/hora de la última actualización del test de la candidata."
    )

    # Campos estructurados para acelerar filtros y cálculo de match
    compat_fortalezas = db.Column(
        ARRAY(db.String(50)),
        nullable=True,
        server_default=text("ARRAY[]::VARCHAR[]"),
        comment="Top fortalezas (ej.: limpieza, cocina, lavado, niños)."
    )
    compat_ritmo_preferido = db.Column(
        SAEnum('tranquilo', 'activo', 'muy_activo', name='compat_ritmo_enum'),
        nullable=True,
        comment="Ritmo de trabajo preferido."
    )
    compat_estilo_trabajo = db.Column(
        SAEnum('necesita_instrucciones', 'toma_iniciativa', name='compat_estilo_enum'),
        nullable=True,
        comment="Prefiere instrucciones o tomar iniciativa."
    )
    compat_orden_detalle_nivel = db.Column(
        db.SmallInteger,
        nullable=True,
        comment="Nivel 1–5 en orden/detalle."
    )
    compat_relacion_ninos = db.Column(
        SAEnum('comoda', 'neutral', 'prefiere_evitar', name='compat_ninos_enum'),
        nullable=True,
        comment="Comodidad trabajando con niños."
    )
    compat_limites_no_negociables = db.Column(
        ARRAY(db.String(100)),
        nullable=True,
        server_default=text("ARRAY[]::VARCHAR[]"),
        comment="Límites declarados (p. ej.: no mascotas, no cocinar, no dormir fuera)."
    )
    compat_disponibilidad_dias = db.Column(
        ARRAY(db.String(20)),
        nullable=True,
        server_default=text("ARRAY[]::VARCHAR[]"),
        comment="Días disponibles (Lun, Mar, Mie, Jue, Vie, Sab, Dom)."
    )
    compat_disponibilidad_horario = db.Column(
        db.String(100),
        nullable=True,
        comment="Franja horaria preferida (ej.: 8am–5pm)."
    )

    # Relaciones
    solicitudes = db.relationship(
        'Solicitud',
        back_populates='candidata',
        cascade='all, delete-orphan'
    )
    llamadas = db.relationship(
        'LlamadaCandidata',
        back_populates='candidata',
        cascade='all, delete-orphan'
    )
# ─────────────────────────────────────────────────────────────
# ENTREVISTAS ESTRUCTURADAS (NUEVO – NO ROMPE LO EXISTENTE)
# ─────────────────────────────────────────────────────────────

class Entrevista(db.Model):
    __tablename__ = 'entrevistas'

    id = db.Column(db.Integer, primary_key=True)

    candidata_id = db.Column(
        db.Integer,
        db.ForeignKey('candidatas.fila'),
        nullable=False,
        index=True
    )

    estado = db.Column(
        db.String(20),
        nullable=False,
        default='completa',
        comment="Estado de la entrevista: completa / borrador"
    )

    creada_en = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow
    )

    actualizada_en = db.Column(
        db.DateTime,
        nullable=True,
        onupdate=datetime.utcnow
    )

    candidata = db.relationship(
        'Candidata',
        backref=db.backref(
            'entrevistas_nuevas',
            lazy='dynamic',
            cascade='all, delete-orphan'
        )
    )

    def __repr__(self):
        return f"<Entrevista {self.id} candidata_id={self.candidata_id}>"



class EntrevistaPregunta(db.Model):
    __tablename__ = 'entrevista_preguntas'

    id = db.Column(db.Integer, primary_key=True)

    clave = db.Column(
        db.String(50),
        unique=True,
        nullable=False,
        index=True,
        comment="Clave interna: ej. tiene_hijos, sabe_cocinar"
    )

    texto = db.Column(
        db.String(255),
        nullable=False,
        comment="Texto visible de la pregunta"
    )

    # ✅ NUEVO
    tipo = db.Column(
        db.String(30),
        nullable=False,
        server_default=text("'texto'"),
        comment="Tipo de campo: texto, texto_largo, radio, etc."
    )

    # ✅ NUEVO
    opciones = db.Column(
        JSONB,
        nullable=True,
        comment="Opciones para tipo=radio (lista). Ej: ['Sí','No']"
    )

    orden = db.Column(
        db.Integer,
        nullable=False,
        default=0
    )

    activa = db.Column(
        db.Boolean,
        nullable=False,
        default=True
    )

    creada_en = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow
    )

    def __repr__(self):
        return f"<EntrevistaPregunta {self.clave}>"


class EntrevistaRespuesta(db.Model):
    __tablename__ = 'entrevista_respuestas'

    id = db.Column(db.Integer, primary_key=True)

    entrevista_id = db.Column(
        db.Integer,
        db.ForeignKey('entrevistas.id'),
        nullable=False,
        index=True
    )

    pregunta_id = db.Column(
        db.Integer,
        db.ForeignKey('entrevista_preguntas.id'),
        nullable=False,
        index=True
    )

    respuesta = db.Column(
        db.Text,
        nullable=True
    )

    creada_en = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow
    )

    actualizada_en = db.Column(
        db.DateTime,
        nullable=True,
        onupdate=datetime.utcnow
    )

    entrevista = db.relationship(
        'Entrevista',
        backref=db.backref(
            'respuestas',
            cascade='all, delete-orphan'
        )
    )

    pregunta = db.relationship('EntrevistaPregunta')

    def __repr__(self):
        return f"<EntrevistaRespuesta entrevista={self.entrevista_id} pregunta={self.pregunta_id}>"




class Cliente(UserMixin, db.Model):
    __tablename__ = 'clientes'

    id                         = db.Column(db.Integer, primary_key=True)

    # ----- Estado / tracking de cuenta (sigue existiendo aunque no tenga login propio) -----
    is_active                  = db.Column(db.Boolean, nullable=False, default=True)
    created_at                 = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at                 = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ----- Identificación del cliente -----
    codigo                     = db.Column(db.String(20), unique=True, nullable=False, index=True)

    # Rol interno: 'cliente' o 'admin' (por si lo usas en lógica de negocio)
    role                       = db.Column(
                                    db.String(20),
                                    nullable=False,
                                    default='cliente',
                                    comment="Valores: 'cliente' o 'admin'"
                                )

    # Nombre (principal) y alias compatible
    nombre_completo            = db.Column(db.String(200), nullable=False)
    nombre                     = synonym('nombre_completo')

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

    # ----- Políticas y avisos -----
    acepto_politicas           = db.Column(
                                    db.Boolean,
                                    nullable=False,
                                    default=False,
                                    comment="True si el cliente ya aceptó las políticas al ingresar por primera vez."
                                )
    fecha_acepto_politicas     = db.Column(
                                    db.DateTime,
                                    nullable=True,
                                    comment="Fecha y hora en que aceptó las políticas."
                                )

    # ----- Relaciones -----
    solicitudes = db.relationship(
        'Solicitud',
        back_populates='cliente',
        order_by='Solicitud.fecha_solicitud.desc()',
        cascade='all, delete-orphan'
    )

    # 👇 NUEVO: tareas asociadas al cliente
    tareas = db.relationship(
        'TareaCliente',
        back_populates='cliente',
        order_by='TareaCliente.fecha_creacion.desc()',
        cascade='all, delete-orphan'
    )

    # ----- Métodos útiles -----
    def get_id(self):
        # Para flask-login, si en algún momento decides loguear por ID de cliente.
        return str(self.id)

    def __repr__(self):
        return f"<Cliente {self.nombre_completo} ({self.codigo})>"


class TareaCliente(db.Model):
    __tablename__ = 'tareas_clientes'

    id               = db.Column(db.Integer, primary_key=True)
    cliente_id       = db.Column(db.Integer, db.ForeignKey('clientes.id'), nullable=False, index=True)

    titulo           = db.Column(db.String(200), nullable=False)
    descripcion      = db.Column(db.Text, nullable=True)

    fecha_creacion   = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    # Día límite para hacer la tarea (lo que usamos en "Tareas de hoy")
    fecha_vencimiento = db.Column(db.Date, nullable=True, index=True)

    estado = db.Column(
        SAEnum('pendiente', 'en_progreso', 'completada', name='estado_tarea_cliente_enum'),
        nullable=False,
        default='pendiente'
    )

    prioridad = db.Column(
        SAEnum('baja', 'media', 'alta', name='prioridad_tarea_cliente_enum'),
        nullable=False,
        default='media'
    )

    completada_at    = db.Column(db.DateTime, nullable=True)

    # Relación inversa
    cliente          = db.relationship('Cliente', back_populates='tareas')

    def __repr__(self):
        return f"<TareaCliente {self.id} - {self.titulo[:20]}>"


class Solicitud(db.Model):
    __tablename__ = 'solicitudes'

    id                     = db.Column(db.Integer, primary_key=True)
    cliente_id             = db.Column(db.Integer, db.ForeignKey('clientes.id'), nullable=False)

    # Fecha original de creación (no se toca, es histórica)
    fecha_solicitud        = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    codigo_solicitud       = db.Column(db.String(50), nullable=False, unique=True)

    # ─────────────────────────────────────────────
    # SEGUIMIENTO / PRIORIDAD (NUEVO)
    # ─────────────────────────────────────────────
    # Desde cuándo se está dando seguimiento ACTIVO a esta solicitud.
    # Se debe renovar cada vez que la pongas en 'activa' (o cuando tú decidas).
    fecha_inicio_seguimiento = db.Column(db.DateTime, nullable=True)

    # Cuántas veces se ha activado la solicitud (para control interno)
    veces_activada         = db.Column(db.Integer, nullable=False, default=0)

    # Última vez que se cambió el estado
    fecha_ultimo_estado    = db.Column(db.DateTime, nullable=True)

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
    edad_requerida         = db.Column(
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
    tipo_lugar             = db.Column(
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
    mascota                = db.Column(db.String(100), nullable=True)

    # Compensación
    sueldo                 = db.Column(db.String(100), nullable=True)
    pasaje_aporte          = db.Column(db.Boolean, nullable=True, default=False)

    # Nota adicional
    nota_cliente           = db.Column(db.Text, nullable=True)

    # ─────────────────────────────────────────────
    # NUEVO: Tipo de servicio + detalles específicos
    # ─────────────────────────────────────────────
    tipo_servicio = db.Column(
        db.String(50),
        nullable=True,
        index=True,
        comment="Tipo de servicio solicitado: DOMESTICA_LIMPIEZA, NINERA, ENFERMERA, CHOFER, etc."
    )

    # Estructura sugerida (no obligatoria) para detalles_servicio:
    # {
    #   "ninera": {
    #       "cant_ninos": 2,
    #       "edades": "2 y 6 años",
    #       "tareas": ["jugar", "llevar al colegio"],
    #       "condicion_especial": "TDAH"
    #   },
    #   "enfermera": {
    #       "a_quien_cuida": "Señora 80 años",
    #       "movilidad": "parcial",
    #       "condicion_principal": "Alzheimer",
    #       "tareas": ["medicacion", "aseo", "movilizar"]
    #   },
    #   "chofer": {
    #       "vehiculo": "cliente" / "empleado",
    #       "tipo_vehiculo": "carro" / "yipeta" / "otro",
    #       "tipo_vehiculo_otro": "Minibús",
    #       "rutas": "PP–Santiago, dentro de la ciudad",
    #       "viajes_largos": true/false,
    #       "licencia_detalle": "Cat. 3, maneja mecánico"
    #   }
    # }
    detalles_servicio = db.Column(
        JSONB,
        nullable=True,
        comment="Bloque de respuestas específicas según el tipo de servicio (niñera, chofer, etc.)."
    )

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

    # ─────────────────────────────────────────────────────────────
    # LÓGICA DE PRIORIDAD / SEGUIMIENTO (SOLO CÁLCULO)
    # ─────────────────────────────────────────────────────────────
    @property
    def dias_desde_creacion(self) -> Optional[int]:
        """
        Días desde que se creó la solicitud (histórico).
        Esto NO se usa para prioridad, solo referencia general.
        """
        if not self.fecha_solicitud:
            return None
        delta = datetime.utcnow() - self.fecha_solicitud
        return delta.days

    @property
    def dias_en_seguimiento(self) -> Optional[int]:
        """
        Días contando desde fecha_inicio_seguimiento.
        Si no tiene fecha_inicio_seguimiento, cae a fecha_solicitud como backup.
        ESTO es lo que se usa para decidir si es prioritaria.
        """
        base = self.fecha_inicio_seguimiento or self.fecha_solicitud
        if not base:
            return None
        delta = datetime.utcnow() - base
        return delta.days

    @property
    def es_prioritaria(self) -> bool:
        """
        Una solicitud es PRIORITARIA si:
        - Está en estado 'proceso', 'activa' o 'reemplazo'
        - Y lleva 7 días o más EN SEGUIMIENTO (no solo desde que se creó).
        """
        estados_activos = {'proceso', 'activa', 'reemplazo'}
        if self.estado not in estados_activos:
            return False

        dias = self.dias_en_seguimiento
        if dias is None:
            return False

        return dias >= 7

    @property
    def nivel_prioridad(self) -> str:
        """
        Niveles para pintar distinto:
        - 'normal'   -> no prioritaria
        - 'media'    -> 7 a 9 días en seguimiento
        - 'alta'     -> 10 a 14 días
        - 'critica'  -> 15 días o más
        """
        if not self.es_prioritaria:
            return 'normal'

        dias = self.dias_en_seguimiento or 0
        if dias >= 15:
            return 'critica'
        elif dias >= 10:
            return 'alta'
        else:
            return 'media'

    def marcar_activada(self):
        """
        Llamar a esto cada vez que la solicitud se ponga en 'activa'
        (o cuando reinicies el seguimiento):
        - Reinicia fecha_inicio_seguimiento
        - Aumenta veces_activada
        - Actualiza fecha_ultimo_estado
        """
        ahora = datetime.utcnow()
        self.fecha_inicio_seguimiento = ahora
        self.fecha_ultimo_estado = ahora
        self.veces_activada = (self.veces_activada or 0) + 1

    # ─────────────────────────────────────────────────────────
    # HELPERS – Etiquetas legibles para tipo_servicio
    # ─────────────────────────────────────────────────────────
    @property
    def tipo_servicio_label(self) -> str:
        """
        Devuelve una etiqueta legible según el código de tipo_servicio.
        Si está en NULL, lo tratamos como 'Doméstica general' (las solicitudes antiguas).
        """
        mapping = {
            'DOMESTICA_LIMPIEZA': 'Doméstica de limpieza',
            'NINERA': 'Niñera',
            'ENFERMERA': 'Enfermera / Cuidadora',
            'CHOFER': 'Chofer',
        }

        if not self.tipo_servicio:
            # Solicitudes antiguas que no tienen tipo: las tratamos como doméstica general
            return 'Doméstica general'

        return mapping.get(self.tipo_servicio, self.tipo_servicio)

    # ─────────────────────────────────────────────────────────
    # HELPERS – Acceso cómodo al JSON de detalles_servicio
    # ─────────────────────────────────────────────────────────
    def _get_detalles_bloque(self, bloque: str) -> dict:
        """
        Devuelve el dict del bloque indicado dentro de detalles_servicio.
        Ej: bloque 'ninera', 'enfermera', 'chofer'.
        Nunca devuelve None (si no existe, devuelve {}).
        """
        base = self.detalles_servicio or {}
        data = base.get(bloque) or {}
        # Garantizamos que siempre sea dict
        return data if isinstance(data, dict) else {}

    def  _set_detalles_bloque(self, bloque: str, data: Optional[dict]) -> None:
        """
        Actualiza un bloque específico dentro de detalles_servicio.
        Si data es None o {}, borra el bloque.
        """
        base = dict(self.detalles_servicio or {})
        if data:
            base[bloque] = data
        else:
            base.pop(bloque, None)
        self.detalles_servicio = base or None

    @property
    def detalles_ninera(self) -> Dict:
        """Acceso directo al bloque de niñera dentro de detalles_servicio."""
        return self._get_detalles_bloque("ninera")

    @detalles_ninera.setter
    def detalles_ninera(self, value: Optional[Dict]) -> None:
        self._set_detalles_bloque("ninera", value)

    @property
    def detalles_enfermera(self) -> Dict:
        """Acceso directo al bloque de enfermera/cuidadora dentro de detalles_servicio."""
        return self._get_detalles_bloque("enfermera")

    @detalles_enfermera.setter
    def detalles_enfermera(self, value: Optional[Dict]) -> None:
        self._set_detalles_bloque("enfermera", value)

    @property
    def detalles_chofer(self) -> Dict:
        """Acceso directo al bloque de chofer dentro de detalles_servicio."""
        return self._get_detalles_bloque("chofer")

    @detalles_chofer.setter
    def detalles_chofer(self, value: Optional[Dict]) -> None:
        self._set_detalles_bloque("chofer", value)

    # ─────────────────────────────────────────────────────────
    # COMPATIBILIDAD – Test del CLIENTE (por solicitud)
    # ─────────────────────────────────────────────────────────
    compat_test_cliente_json = db.Column(
        JSONB,
        nullable=True,
        comment="Respuestas del test del CLIENTE (estructura JSON) para esta solicitud."
    )
    compat_test_cliente_at = db.Column(
        db.DateTime,
        nullable=True,
        comment="Fecha/hora en que el cliente guardó su test."
    )
    compat_test_cliente_version = db.Column(
        db.String(20),
        nullable=True,
        comment="Versión del cuestionario del cliente (para futuras migraciones)."
    )

    # ─────────────────────────────────────────────────────────
    # COMPATIBILIDAD – Resultado de cálculo (cliente ↔ candidata asignada)
    # ─────────────────────────────────────────────────────────
    compat_calc_score = db.Column(
        db.Integer,
        nullable=True,
        comment="Porcentaje 0–100 del match cliente↔candidata (último cálculo)."
    )
    compat_calc_level = db.Column(
        SAEnum('alta', 'media', 'baja', name='compat_level_enum'),
        nullable=True,
        comment="Nivel del match según el score."
    )
    compat_calc_summary = db.Column(
        db.Text,
        nullable=True,
        comment="Coincidencias clave y explicación breve del resultado."
    )
    compat_calc_risks = db.Column(
        db.Text,
        nullable=True,
        comment="Riesgos/alertas detectados (no negociables, horarios, etc.)."
    )
    compat_calc_at = db.Column(
        db.DateTime,
        nullable=True,
        comment="Fecha/hora del último cálculo de compatibilidad."
    )
    compat_pdf_path = db.Column(
        db.String(255),
        nullable=True,
        comment="Ruta/filename del PDF generado con el informe de compatibilidad."
    )



class Reemplazo(db.Model):
    __tablename__ = 'reemplazos'

    id                     = db.Column(db.Integer, primary_key=True)
    solicitud_id           = db.Column(db.Integer, db.ForeignKey('solicitudes.id'), nullable=False)

    # Candidata que falló
    candidata_old_id       = db.Column(db.Integer, db.ForeignKey('candidatas.fila'), nullable=False)
    motivo_fallo           = db.Column(db.Text,    nullable=False)

    # Cuándo falló (para historial)
    fecha_fallo            = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    # Si esta solicitud queda “abierta” para buscar alguien nuevo
    oportunidad_nueva      = db.Column(db.Boolean, nullable=False, default=False)

    # Reemplazo como proceso:
    # - inicio: se abre el reemplazo y se empieza nueva búsqueda
    # - fin: se cierra porque se le envió nueva candidata
    fecha_inicio_reemplazo = db.Column(db.DateTime, nullable=True)
    fecha_fin_reemplazo    = db.Column(db.DateTime, nullable=True)

    # Nueva candidata asignada al cerrar el reemplazo
    candidata_new_id       = db.Column(db.Integer, db.ForeignKey('candidatas.fila'), nullable=True)

    # Notas internas del reemplazo
    nota_adicional         = db.Column(db.Text,    nullable=True)

    # Registro técnico
    created_at             = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    # Relaciones
    solicitud              = db.relationship('Solicitud', back_populates='reemplazos')
    candidata_old          = db.relationship('Candidata', foreign_keys=[candidata_old_id])
    candidata_new          = db.relationship('Candidata', foreign_keys=[candidata_new_id])

    # ──────────────────────────────────────────────
    # Helpers de estado (solo lógica, no BD)
    # ──────────────────────────────────────────────
    @property
    def reemplazo_activo(self) -> bool:
        """
        Un reemplazo se considera ACTIVO cuando:
        - Tiene fecha_inicio_reemplazo
        - Y todavía no tiene fecha_fin_reemplazo
        """
        return self.fecha_inicio_reemplazo is not None and self.fecha_fin_reemplazo is None

    @property
    def dias_en_reemplazo(self) -> Optional[int]:
        """
        Devuelve cuántos días lleva o duró el reemplazo:
        - Si está activo  -> desde fecha_inicio_reemplazo hasta ahora.
        - Si está cerrado -> desde fecha_inicio_reemplazo hasta fecha_fin_reemplazo.
        """
        if not self.fecha_inicio_reemplazo:
            return None

        fin = self.fecha_fin_reemplazo or datetime.utcnow()
        delta = fin - self.fecha_inicio_reemplazo
        return delta.days

    # ──────────────────────────────────────────────
    # Acciones de negocio (para usar en las rutas)
    # ──────────────────────────────────────────────
    def iniciar_reemplazo(self):
        """
        Marca el INICIO del reemplazo:
        - Coloca fecha_inicio_reemplazo con la hora actual.
        - Marca oportunidad_nueva = True (hay que buscar nueva candidata).
        - Limpia fecha_fin_reemplazo por si acaso.
        """
        ahora = datetime.utcnow()
        self.fecha_inicio_reemplazo = ahora
        self.fecha_fin_reemplazo = None
        self.oportunidad_nueva = True

    def cerrar_reemplazo(self, candidata_nueva_id: Optional[int] = None):
        """
        Marca el FIN del reemplazo:
        - Coloca fecha_fin_reemplazo con la hora actual.
        - Asigna la nueva candidata (si se pasa).
        - Marca oportunidad_nueva = False (reemplazo cerrado).
        """
        ahora = datetime.utcnow()
        self.fecha_fin_reemplazo = ahora
        self.oportunidad_nueva = False
        if candidata_nueva_id is not None:
            self.candidata_new_id = candidata_nueva_id


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


class CandidataWeb(db.Model):
    """
    Ficha pública de la candidata para la web (landing Domésticas disponibles).
    No toca los datos internos de la candidata, solo lo que se muestra al cliente.
    """
    __tablename__ = 'candidatas_web'

    id = db.Column(db.Integer, primary_key=True)

    # Relación 1–1 con la candidata interna
    candidata_id = db.Column(
        db.Integer,
        db.ForeignKey('candidatas.fila'),
        nullable=False,
        unique=True,
        index=True
    )

    # ─────────────────────────────────────────────
    # CONTROL DE PUBLICACIÓN
    # ─────────────────────────────────────────────
    visible = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default=text('true'),
        comment="Si está en True, la candidata aparece en la web pública."
    )

    estado_publico = db.Column(
        SAEnum('disponible', 'reservada', 'no_disponible',
               name='estado_publico_candidata_enum'),
        nullable=False,
        default='disponible',
        server_default=text("'disponible'"),
        comment="Estado visible para el cliente en la web."
    )

    es_destacada = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default=text('false'),
        comment="Si es True, se muestra como candidata destacada."
    )

    orden_lista = db.Column(
        db.Integer,
        nullable=True,
        comment="Orden manual en la lista pública (1,2,3...)."
    )

    fecha_publicacion = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        comment="Cuándo se publicó por primera vez en la web."
    )

    fecha_ultima_actualizacion = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        comment="Última vez que se actualizó esta ficha web."
    )

    # ─────────────────────────────────────────────
    # TEXTO QUE VE EL CLIENTE (MARKETING)
    # ─────────────────────────────────────────────
    nombre_publico = db.Column(
        db.String(200),
        nullable=True,
        comment="Nombre que se muestra en la web. Si es NULL, se usa el nombre real."
    )

    edad_publica = db.Column(
        db.String(50),
        nullable=True,
        comment="Texto libre de edad: '37 años', 'Mayor de 45 años', etc."
    )

    ciudad_publica = db.Column(
        db.String(120),
        nullable=True,
        comment="Ciudad que se muestra al cliente."
    )

    sector_publico = db.Column(
        db.String(120),
        nullable=True,
        comment="Sector o zona de referencia para el cliente."
    )

    modalidad_publica = db.Column(
        db.String(120),
        nullable=True,
        comment="Modalidad visible: con dormida, sin dormida, por días, etc."
    )

    tipo_servicio_publico = db.Column(
        db.String(50),
        nullable=True,
        comment="Tipo de servicio: DOMESTICA, NINERA, ENFERMERA, etc."
    )

    anos_experiencia_publicos = db.Column(
        db.String(50),
        nullable=True,
        comment="Texto de años de experiencia: '5 años', 'Más de 10 años', etc."
    )

    experiencia_resumen = db.Column(
        db.Text,
        nullable=True,
        comment="Resumen corto que sale en la tarjeta de la lista."
    )

    experiencia_detallada = db.Column(
        db.Text,
        nullable=True,
        comment="Descripción más larga para la página de detalle."
    )

    tags_publicos = db.Column(
        db.String(255),
        nullable=True,
        comment="Lista de tags separados por coma: 'Limpieza, Cocina básica, Niñera'."
    )

    frase_destacada = db.Column(
        db.String(200),
        nullable=True,
        comment="Frase llamativa para enganchar al cliente."
    )

    # ─────────────────────────────────────────────
    # SUELDO / DISPONIBILIDAD / FOTO
    # ─────────────────────────────────────────────
    sueldo_desde = db.Column(
        db.Integer,
        nullable=True,
        comment="Rango mínimo sugerido de sueldo en RD$ (solo referencia)."
    )

    sueldo_hasta = db.Column(
        db.Integer,
        nullable=True,
        comment="Rango máximo sugerido de sueldo en RD$ (solo referencia)."
    )

    sueldo_texto_publico = db.Column(
        db.String(120),
        nullable=True,
        comment="Texto que se muestra: 'RD$16,000 en adelante', 'Según horario', etc."
    )

    foto_publica_url = db.Column(
        db.String(255),
        nullable=True,
        comment="URL de la foto que se muestra en la web. Si está NULL, usas la de la BD interna."
    )

    disponible_inmediato = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default=text('true'),
        comment="Si está disponible para iniciar de inmediato."
    )

    # ─────────────────────────────────────────────
    # RELACIONES
    # ─────────────────────────────────────────────
    candidata = db.relationship(
        'Candidata',
        backref=db.backref('ficha_web', uselist=False)
    )

    # ─────────────────────────────────────────────
    # ALIAS / SINÓNIMOS PARA CAMPOS USADOS EN LAS RUTAS
    # (NO CAMBIAN LA BD, SOLO DAN OTROS NOMBRES)
    # ─────────────────────────────────────────────
    # Campos "lógicos"
    disponible_en_web = synonym('visible')
    destacada_en_web = synonym('es_destacada')
    orden_web = synonym('orden_lista')
    fecha_publicacion_web = synonym('fecha_publicacion')

    # Alias de texto usados en las rutas / formularios
    ciudad_web = synonym('ciudad_publica')
    modalidad_web = synonym('modalidad_publica')
    experiencia_web = synonym('experiencia_resumen')
    nota_publica = synonym('experiencia_detallada')

    def __repr__(self) -> str:
        return f"<CandidataWeb {self.id} – candidata_id={self.candidata_id}>"
