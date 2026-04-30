import hashlib
import os
import re
from datetime import datetime
from typing import Optional, Dict

from flask_login import UserMixin
from sqlalchemy import CheckConstraint, Enum as SAEnum, LargeBinary, event, inspect as sa_inspect, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import synonym
from werkzeug.security import check_password_hash, generate_password_hash

from config_app import db
from utils.secrets_manager import get_secret
from utils.timezone import utc_now_naive



class Candidata(db.Model):
    __tablename__ = 'candidatas'

    # Campos existentes
    fila                            = db.Column(db.Integer, primary_key=True)
    marca_temporal                  = db.Column(db.DateTime, default=utc_now_naive, nullable=False)
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
    cedula_norm_digits              = db.Column(
        db.String(11),
        nullable=True,
        index=True,
        comment="solo dígitos; usada para prevenir duplicados en nuevas altas"
    )
    codigo                          = db.Column(db.String(50), unique=True, index=True)
    medio_inscripcion               = db.Column(db.String(100))
    origen_registro                 = db.Column(
        db.String(32),
        nullable=True,
        index=True,
        comment="Origen de creación del registro: publico_domestica o interno"
    )
    creado_por_staff                = db.Column(
        db.String(100),
        nullable=True,
        comment="Usuario staff que creó el registro cuando aplica origen interno"
    )
    creado_desde_ruta               = db.Column(
        db.String(120),
        nullable=True,
        comment="Ruta origen desde donde se creó el registro"
    )
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
    disponibilidad_inicio           = db.Column(db.String(80), nullable=True)
    trabaja_con_ninos               = db.Column(
        db.Boolean,
        nullable=True,
        comment="Disponibilidad para trabajar en hogares con niños (true/false o null no informado)."
    )
    trabaja_con_mascotas            = db.Column(
        db.Boolean,
        nullable=True,
        comment="Disponibilidad para trabajar en hogares con mascotas (true/false o null no informado)."
    )
    puede_dormir_fuera              = db.Column(
        db.Boolean,
        nullable=True,
        comment="Si puede trabajar modalidad dormida cuando sea necesario."
    )
    sueldo_esperado                 = db.Column(
        db.String(80),
        nullable=True,
        comment="Expectativa salarial declarada por la candidata."
    )
    motivacion_trabajo              = db.Column(
        db.String(350),
        nullable=True,
        comment="Motivación breve declarada en el formulario público."
    )

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
        default=utc_now_naive,
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

    # Entrevistas estructuradas (nuevo módulo)
    # Nota: esto NO elimina datos; solo crea una relación ORM.
    entrevistas_nuevas = db.relationship(
        'Entrevista',
        back_populates='candidata',
        lazy='dynamic',
        cascade='all, delete-orphan',
        single_parent=True
    )

    # ─────────────────────────────────────────────
    # Compatibilidad con columnas duplicadas de referencias
    # (NO borra datos, solo unifica lectura/escritura)
    # ─────────────────────────────────────────────
    @property
    def referencias_laborales_texto(self) -> str:
        """Devuelve referencias laborales priorizando el campo nuevo."""
        return (self.contactos_referencias_laborales or self.referencias_laboral or '')

    @referencias_laborales_texto.setter
    def referencias_laborales_texto(self, value: str) -> None:
        val = (value or '').strip()
        self.contactos_referencias_laborales = val or None
        # Mantener compatibilidad con el campo antiguo
        self.referencias_laboral = val or None

    @property
    def referencias_familiares_texto(self) -> str:
        """Devuelve referencias familiares priorizando el campo nuevo."""
        return (self.referencias_familiares_detalle or self.referencias_familiares or '')

    @referencias_familiares_texto.setter
    def referencias_familiares_texto(self, value: str) -> None:
        val = (value or '').strip()
        self.referencias_familiares_detalle = val or None
        # Mantener compatibilidad con el campo antiguo
        self.referencias_familiares = val or None


def _cedula_digits_only(value: str) -> str:
    return re.sub(r"\D+", "", value or "")


def _sync_cedula_norm_digits(target: "Candidata") -> None:
    digits = _cedula_digits_only(getattr(target, "cedula", None))
    target.cedula_norm_digits = digits if len(digits) == 11 else None


@event.listens_for(Candidata, "before_insert")
def _candidata_before_insert(mapper, connection, target):  # pragma: no cover
    _sync_cedula_norm_digits(target)


@event.listens_for(Candidata, "before_update")
def _candidata_before_update(mapper, connection, target):  # pragma: no cover
    _sync_cedula_norm_digits(target)
# ─────────────────────────────────────────────────────────────
# ENTREVISTAS ESTRUCTURADAS (NUEVO – NO ROMPE LO EXISTENTE)
# ─────────────────────────────────────────────────────────────

from datetime import datetime
from sqlalchemy.sql import text
from sqlalchemy.dialects.postgresql import JSONB

# Asegúrate de que db viene de tu config habitual:
# from config_app import db   (o donde lo tengas)


class Entrevista(db.Model):
    __tablename__ = 'entrevistas'

    id = db.Column(db.Integer, primary_key=True)

    candidata_id = db.Column(
        db.Integer,
        db.ForeignKey('candidatas.fila'),
        nullable=False,
        index=True
    )

    # ✅ NUEVO (alineado con tus rutas: entrevista.tipo)
    # No elimina nada, solo agrega y deja default.
    tipo = db.Column(
        db.String(30),
        nullable=False,
        server_default=text("'domestica'"),
        index=True,
        comment="Tipo de entrevista: domestica / enfermera / empleo_general"
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
        default=utc_now_naive
    )

    actualizada_en = db.Column(
        db.DateTime,
        nullable=True,
        onupdate=utc_now_naive
    )

    candidata = db.relationship(
        'Candidata',
        back_populates='entrevistas_nuevas'
    )

    respuestas = db.relationship(
        'EntrevistaRespuesta',
        back_populates='entrevista',
        cascade='all, delete-orphan'
    )

    def __repr__(self):
        return f"<Entrevista {self.id} candidata_id={self.candidata_id} tipo={getattr(self, 'tipo', None)}>"


class EntrevistaPregunta(db.Model):
    __tablename__ = 'entrevista_preguntas'

    id = db.Column(db.Integer, primary_key=True)

    # ✅ Ajuste seguro: 50 se queda corto para claves tipo "domestica.algo_largo"
    # Esto NO borra nada, solo amplía.
    clave = db.Column(
        db.String(120),
        unique=True,
        nullable=False,
        index=True,
        comment="Clave interna: ej. domestica.tiene_hijos, enfermera.experiencia, etc."
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
        default=utc_now_naive
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
        default=utc_now_naive
    )

    actualizada_en = db.Column(
        db.DateTime,
        nullable=True,
        onupdate=utc_now_naive
    )

    entrevista = db.relationship(
        'Entrevista',
        back_populates='respuestas'
    )

    pregunta = db.relationship('EntrevistaPregunta')

    def __repr__(self):
        return f"<EntrevistaRespuesta entrevista={self.entrevista_id} pregunta={self.pregunta_id}>"



class StaffUser(UserMixin, db.Model):
    __tablename__ = 'staff_users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='secretaria')
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)
    last_login_at = db.Column(db.DateTime, nullable=True)
    last_login_ip = db.Column(db.String(64), nullable=True)
    mfa_enabled = db.Column(db.Boolean, nullable=False, default=False)
    mfa_secret = db.Column(db.String(512), nullable=True)
    mfa_last_timestep = db.Column(db.Integer, nullable=True)

    def get_id(self) -> str:
        # Evita colisión con IDs numéricos de Cliente.
        return f"staff:{self.id}"

    @property
    def is_admin(self) -> bool:
        return (self.role or "").strip().lower() in {"owner", "admin"}

    @property
    def is_owner(self) -> bool:
        return (self.role or "").strip().lower() == "owner"

    @property
    def is_staff_admin_level(self) -> bool:
        return self.is_admin

    @staticmethod
    def normalize_password(raw_password: str) -> str:
        # El login staff recorta espacios externos y limita a 128.
        # Mantener la misma normalización evita hashes desalineados.
        return (raw_password or "").strip()[:128]

    def set_password(self, raw_password: str) -> None:
        normalized = self.normalize_password(raw_password)
        self.password_hash = generate_password_hash(normalized, method="pbkdf2:sha256")

    def check_password(self, raw_password: str) -> bool:
        try:
            normalized = self.normalize_password(raw_password)
            return check_password_hash(self.password_hash or "", normalized)
        except Exception:
            return False

    @staticmethod
    def _mfa_explicit_key() -> str:
        return (get_secret("STAFF_MFA_ENCRYPTION_KEY") or "").strip()

    @staticmethod
    def _mfa_legacy_key_from_flask() -> str:
        try:
            import base64

            base_secret = (get_secret("FLASK_SECRET_KEY") or "").encode("utf-8")
            if not base_secret:
                return ""
            digest = hashlib.sha256(base_secret).digest()
            return base64.urlsafe_b64encode(digest).decode("utf-8")
        except Exception:
            return ""

    @staticmethod
    def _mfa_cipher_from_key(key_text: str):
        try:
            from cryptography.fernet import Fernet

            key_bytes = (key_text or "").strip().encode("utf-8")
            if not key_bytes:
                return None
            return Fernet(key_bytes)
        except Exception:
            return None

    @classmethod
    def _mfa_write_cipher(cls):
        return cls._mfa_cipher_from_key(cls._mfa_explicit_key())

    @classmethod
    def _mfa_read_ciphers(cls):
        ciphers = []
        explicit_key = cls._mfa_explicit_key()
        explicit_cipher = cls._mfa_cipher_from_key(explicit_key)
        if explicit_cipher is not None:
            ciphers.append(explicit_cipher)

        legacy_key = cls._mfa_legacy_key_from_flask()
        if legacy_key and legacy_key != explicit_key:
            legacy_cipher = cls._mfa_cipher_from_key(legacy_key)
            if legacy_cipher is not None:
                ciphers.append(legacy_cipher)
        return ciphers

    def set_mfa_secret(self, raw_secret: str) -> None:
        value = (raw_secret or "").strip().upper()
        if not value:
            self.mfa_secret = None
            return
        cipher = self._mfa_write_cipher()
        if cipher is None:
            allow_plain = False
            try:
                from flask import current_app

                allow_plain = bool(current_app.config.get("TESTING"))
            except Exception:
                allow_plain = False
            allow_plain = allow_plain or (
                (get_secret("STAFF_MFA_ALLOW_PLAINTEXT_SECRET") or "").strip().lower()
                in {"1", "true", "yes", "on"}
            )
            if not allow_plain:
                raise RuntimeError(
                    "STAFF_MFA_ENCRYPTION_KEY is required to write MFA secrets. "
                    "Legacy FLASK_SECRET_KEY fallback is read-only."
                )
            self.mfa_secret = f"plain:{value}"
            return
        token = cipher.encrypt(value.encode("utf-8")).decode("utf-8")
        self.mfa_secret = f"enc:{token}"

    def get_mfa_secret(self) -> str:
        raw = (self.mfa_secret or "").strip()
        if not raw:
            return ""
        if raw.startswith("plain:"):
            return raw.split(":", 1)[1].strip().upper()
        if raw.startswith("enc:"):
            token = raw.split(":", 1)[1].strip().encode("utf-8")
            read_ciphers = self._mfa_read_ciphers()
            if not read_ciphers:
                return ""
            for idx, cipher in enumerate(read_ciphers):
                try:
                    out = cipher.decrypt(token).decode("utf-8").strip().upper()
                    if idx > 0:
                        try:
                            from flask import current_app

                            current_app.logger.warning(
                                "MFA legacy key fallback used for user_id=%s username=%s",
                                int(getattr(self, "id", 0) or 0),
                                str(getattr(self, "username", "") or "").strip(),
                            )
                        except Exception:
                            pass
                    return out
                except Exception:
                    continue
            return ""
        # Compatibilidad con posibles secretos legacy en texto simple.
        return raw.upper()

    def clear_mfa(self) -> None:
        self.mfa_enabled = False
        self.mfa_secret = None
        self.mfa_last_timestep = None


class TrustedDevice(db.Model):
    __tablename__ = "trusted_devices"
    __table_args__ = (
        db.UniqueConstraint("user_id", "device_token_hash", name="uq_trusted_devices_user_token"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("staff_users.id"), nullable=False, index=True)
    device_fingerprint = db.Column(db.String(128), nullable=False, index=True)
    device_token_hash = db.Column(db.String(64), nullable=True, index=True)
    ip_address = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(512), nullable=True)
    last_used_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive)
    is_trusted = db.Column(db.Boolean, nullable=False, default=True, server_default=text("true"))

    user = db.relationship("StaffUser", backref=db.backref("trusted_devices", lazy="dynamic"))


class StaffAuditLog(db.Model):
    __tablename__ = 'staff_audit_logs'

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)

    actor_user_id = db.Column(db.Integer, db.ForeignKey('staff_users.id'), nullable=True, index=True)
    actor_role = db.Column(db.String(20), nullable=True)

    action_type = db.Column(db.String(80), nullable=False, index=True)
    entity_type = db.Column(db.String(80), nullable=True, index=True)
    entity_id = db.Column(db.String(64), nullable=True, index=True)

    route = db.Column(db.String(255), nullable=True)
    method = db.Column(db.String(10), nullable=True)
    ip = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(512), nullable=True)
    summary = db.Column(db.String(255), nullable=True)

    metadata_json = db.Column(db.JSON, nullable=False, default=dict)
    changes_json = db.Column(db.JSON, nullable=True)

    success = db.Column(db.Boolean, nullable=False, default=True, index=True)
    error_message = db.Column(db.Text, nullable=True)

    actor_user = db.relationship('StaffUser', backref=db.backref('audit_logs', lazy='dynamic'))


@event.listens_for(StaffAuditLog, "before_update")
def _prevent_staff_audit_log_update(_mapper, _connection, _target):
    run_env = (os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "").strip().lower()
    if run_env in {"test", "testing"}:
        return
    raise RuntimeError("StaffAuditLog is immutable and cannot be updated.")


@event.listens_for(StaffAuditLog, "before_delete")
def _prevent_staff_audit_log_delete(_mapper, _connection, _target):
    run_env = (os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "").strip().lower()
    if run_env in {"test", "testing"}:
        return
    raise RuntimeError("StaffAuditLog is immutable and cannot be deleted.")


class StaffPresenceState(db.Model):
    __tablename__ = "staff_presence_state"
    __table_args__ = (
        db.UniqueConstraint("user_id", "session_id", name="uq_staff_presence_user_session"),
        db.Index("ix_staff_presence_user_last_seen", "user_id", "last_seen_at"),
        db.Index("ix_staff_presence_entity", "entity_type", "entity_id"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("staff_users.id"), nullable=False, index=True)
    session_id = db.Column(db.String(120), nullable=False, index=True)

    route = db.Column(db.String(255), nullable=False, default="", server_default=text("''"))
    route_label = db.Column(db.String(120), nullable=False, default="", server_default=text("''"))

    entity_type = db.Column(db.String(40), nullable=False, default="", server_default=text("''"))
    entity_id = db.Column(db.String(64), nullable=False, default="", server_default=text("''"))
    entity_name = db.Column(db.String(160), nullable=False, default="", server_default=text("''"))
    entity_code = db.Column(db.String(64), nullable=False, default="", server_default=text("''"))

    current_action = db.Column(db.String(80), nullable=False, default="", server_default=text("''"))
    action_label = db.Column(db.String(120), nullable=False, default="", server_default=text("''"))

    tab_visible = db.Column(db.Boolean, nullable=False, default=True, server_default=text("true"), index=True)
    is_idle = db.Column(db.Boolean, nullable=False, default=False, server_default=text("false"), index=True)
    is_typing = db.Column(db.Boolean, nullable=False, default=False, server_default=text("false"), index=True)
    has_unsaved_changes = db.Column(db.Boolean, nullable=False, default=False, server_default=text("false"), index=True)
    modal_open = db.Column(db.Boolean, nullable=False, default=False, server_default=text("false"), index=True)
    lock_owner = db.Column(db.String(120), nullable=False, default="", server_default=text("''"))

    client_status = db.Column(db.String(20), nullable=False, default="active", server_default=text("'active'"), index=True)
    page_title = db.Column(db.String(160), nullable=False, default="", server_default=text("''"))
    last_interaction_at = db.Column(db.DateTime, nullable=True)
    state_hash = db.Column(db.String(64), nullable=False, default="", server_default=text("''"), index=True)
    ip = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)

    started_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)
    last_seen_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)

    user = db.relationship("StaffUser", backref=db.backref("presence_states", lazy="dynamic"))


class PublicSolicitudTokenUso(db.Model):
    __tablename__ = "public_solicitud_tokens_usados"

    id = db.Column(db.Integer, primary_key=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False, index=True)
    solicitud_id = db.Column(db.Integer, db.ForeignKey("solicitudes.id"), nullable=True, index=True)

    used_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive)

    cliente = db.relationship("Cliente", lazy="joined")
    solicitud = db.relationship("Solicitud", lazy="joined")

    def __repr__(self):
        return f"<PublicSolicitudTokenUso id={self.id} cliente_id={self.cliente_id} solicitud_id={self.solicitud_id}>"


class PublicSolicitudClienteNuevoTokenUso(db.Model):
    __tablename__ = "public_solicitud_cliente_nuevo_tokens_usados"

    id = db.Column(db.Integer, primary_key=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=True, index=True)
    solicitud_id = db.Column(db.Integer, db.ForeignKey("solicitudes.id"), nullable=True, index=True)

    used_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive)

    cliente = db.relationship("Cliente", lazy="joined")
    solicitud = db.relationship("Solicitud", lazy="joined")

    def __repr__(self):
        return (
            f"<PublicSolicitudClienteNuevoTokenUso id={self.id} "
            f"cliente_id={self.cliente_id} solicitud_id={self.solicitud_id}>"
        )


class PublicSolicitudShareAlias(db.Model):
    __tablename__ = "public_solicitud_share_aliases"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(24), nullable=False, unique=True, index=True)
    link_type = db.Column(db.String(24), nullable=False, index=True)
    token = db.Column(db.Text, nullable=False)
    token_hash = db.Column(db.String(64), nullable=False, index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=text("true"))

    created_by = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)
    last_seen_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f"<PublicSolicitudShareAlias id={self.id} type={self.link_type} code={self.code}>"


class Cliente(UserMixin, db.Model):
    __tablename__ = 'clientes'

    id                         = db.Column(db.Integer, primary_key=True)

    # ----- Estado / tracking de cuenta (sigue existiendo aunque no tenga login propio) -----
    is_active                  = db.Column(db.Boolean, nullable=False, default=True)
    created_at                 = db.Column(db.DateTime, nullable=False, default=utc_now_naive)
    updated_at                 = db.Column(db.DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)

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

    # ----- Credenciales (portal de clientes) -----
    # Username opcional: si no se define, podemos usar el código como identificador.
    username                   = db.Column(db.String(80), unique=True, index=True)

    # Hash de contraseña (Werkzeug). Si está deshabilitada, se marca como DISABLED_RESET_REQUIRED.
    password_hash              = db.Column(
                                    db.String(255),
                                    nullable=False,
                                    default="DISABLED_RESET_REQUIRED"
                                )

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

    # Alias (por si decides ocultar ciudad/sector en UI pero mantener BD)
    ciudad_texto = synonym('ciudad')
    sector_texto = synonym('sector')

    # ----- Métricas de solicitudes -----
    total_solicitudes          = db.Column(db.Integer,  nullable=False, default=0)
    fecha_ultima_solicitud     = db.Column(db.DateTime, nullable=True)
    fecha_registro             = db.Column(db.DateTime, nullable=False, default=utc_now_naive)
    fecha_ultima_actividad     = db.Column(db.DateTime, nullable=True)

    # ----- Políticas y avisos -----
    acepto_politicas           = db.Column(
                                    db.Boolean,
                                    nullable=False,
                                    default=False,
                                    comment="True si el cliente ya aceptó las políticas al ingresar por primera vez."
                                )

    # ⚠️ IMPORTANTE:
    # No se puede hacer synonym del mismo nombre porque rompe SQLAlchemy.
    # Si en algún módulo quieres un alias, debe ser con un nombre DIFERENTE.
    acepto_politicas_ok        = synonym('acepto_politicas')

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
    chat_conversations = db.relationship(
        "ChatConversation",
        order_by="desc(ChatConversation.last_message_at), desc(ChatConversation.id)",
        cascade="all, delete-orphan",
        lazy="dynamic",
        back_populates="cliente",
    )

    # ----- Métodos útiles -----

    def set_password(self, raw_password: str) -> None:
        """Guarda el hash de la contraseña (requiere Werkzeug)."""
        from werkzeug.security import generate_password_hash
        raw = (raw_password or '').strip()
        if not raw:
            # Mantener deshabilitada si no se pasa contraseña
            self.password_hash = "DISABLED_RESET_REQUIRED"
            return
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw_password: str) -> bool:
        """Valida la contraseña contra el hash guardado."""
        from werkzeug.security import check_password_hash
        if not getattr(self, 'password_hash', None):
            return False
        if self.password_hash == "DISABLED_RESET_REQUIRED":
            return False
        return check_password_hash(self.password_hash, raw_password or '')

    @property
    def requiere_reset_password(self) -> bool:
        return (getattr(self, 'password_hash', None) or '') == "DISABLED_RESET_REQUIRED"

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

    fecha_creacion   = db.Column(db.DateTime, nullable=False, default=utc_now_naive)
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
    fecha_solicitud        = db.Column(db.DateTime, nullable=False, default=utc_now_naive)
    codigo_solicitud       = db.Column(db.String(50), nullable=False, unique=True)

    # ─────────────────────────────────────────────
    # SEGUIMIENTO / PRIORIDAD (NUEVO)
    # ─────────────────────────────────────────────
    # Desde cuándo se está dando seguimiento ACTIVO a esta solicitud.
    # Se debe renovar cada vez que la pongas en 'activa' (o cuando tú decidas).
    fecha_inicio_seguimiento = db.Column(db.DateTime, nullable=True)
    # Fecha manual de seguimiento operativo (recordatorio elegido por staff).
    fecha_seguimiento_manual = db.Column(db.Date, nullable=True, index=True)

    # Cuántas veces se ha activado la solicitud (para control interno)
    veces_activada         = db.Column(db.Integer, nullable=False, default=0)

    # Última vez que se cambió el estado
    fecha_ultimo_estado    = db.Column(db.DateTime, nullable=True)
    # Desde cuándo está en el estado actual (fuente de verdad operativa).
    estado_actual_desde    = db.Column(db.DateTime, nullable=True)

    # Plan y pago
    tipo_plan              = db.Column(db.String(50), nullable=True)
    abono                  = db.Column(db.String(100), nullable=True)
    estado                 = db.Column(
                                SAEnum(
                                    'proceso','activa','pagada','cancelada','reemplazo','espera_pago',
                                    name='estado_solicitud_enum'
                                ),
                                nullable=False,
                                default='proceso'
                             )
    estado_previo_espera_pago = db.Column(
                                db.String(50),
                                nullable=True,
                                comment="Estado previo usado para restaurar al quitar espera de pago"
                             )
    fecha_cambio_espera_pago = db.Column(
                                db.DateTime,
                                nullable=True,
                                comment="Fecha del ultimo cambio de espera de pago"
                             )
    usuario_cambio_espera_pago = db.Column(
                                db.String(100),
                                nullable=True,
                                comment="Usuario que cambio espera de pago"
                             )
    # Versionado optimista para flujos críticos (pagos/estados).
    row_version = db.Column(
                                db.Integer,
                                nullable=False,
                                default=1,
                                server_default=text("1"),
                                comment="Version de concurrencia optimista de la solicitud"
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
                                db.String(200),
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
    chat_conversations     = db.relationship("ChatConversation", lazy="dynamic", back_populates="solicitud")
    reemplazos             = db.relationship(
                                'Reemplazo',
                                back_populates='solicitud',
                                cascade='all, delete-orphan'
                             )
    recommendation_runs    = db.relationship(
                                "SolicitudRecommendationRun",
                                back_populates="solicitud",
                                cascade="all, delete-orphan",
                                lazy="dynamic",
                             )
    recommendation_selections = db.relationship(
                                "SolicitudRecommendationSelection",
                                back_populates="solicitud",
                                cascade="all, delete-orphan",
                                lazy="dynamic",
                             )

    # Fechas de publicación y modificación
    last_copiado_at        = db.Column(db.DateTime, nullable=True)
    fecha_ultima_modificacion = db.Column(db.DateTime, nullable=True)

    # Cancelación
    fecha_cancelacion      = db.Column(db.DateTime, nullable=True)
    motivo_cancelacion     = db.Column(db.String(255), nullable=True)

    __mapper_args__ = {
        "version_id_col": row_version,
    }

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
        delta = utc_now_naive() - self.fecha_solicitud
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
        delta = utc_now_naive() - base
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
        ahora = utc_now_naive()
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
    fecha_fallo            = db.Column(db.DateTime, nullable=False, default=utc_now_naive)

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
    estado_previo_solicitud = db.Column(
        db.String(50),
        nullable=True,
        comment="Estado de la solicitud justo antes de abrir el reemplazo"
    )

    # Registro técnico
    created_at             = db.Column(db.DateTime, nullable=False, default=utc_now_naive)

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

        fin = self.fecha_fin_reemplazo or utc_now_naive()
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
        ahora = utc_now_naive()
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
        ahora = utc_now_naive()
        self.fecha_fin_reemplazo = ahora
        self.oportunidad_nueva = False
        if candidata_nueva_id is not None:
            self.candidata_new_id = candidata_nueva_id


class LlamadaCandidata(db.Model):
    __tablename__ = 'llamadas_candidatas'

    id = db.Column(db.Integer, primary_key=True)
    candidata_id = db.Column(db.Integer, db.ForeignKey('candidatas.fila'), nullable=False, index=True)
    agente = db.Column(db.String(100), nullable=False, index=True)  # quién hace la llamada
    fecha_llamada = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)
    duracion_segundos = db.Column(db.Integer, nullable=True)  # duración real

    resultado = db.Column(
        SAEnum(
            'no_contesta',
            'inscripcion',
            'rechaza',
            'voicemail',
            'informada',
            'exitosa',
            'otro',
            name='resultado_enum'
        ),
        nullable=False,
        index=True
    )

    notas = db.Column(db.Text, nullable=True)
    proxima_llamada = db.Column(db.Date, nullable=True, index=True)  # siguiente llamada sugerida
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive)

    candidata = db.relationship('Candidata', back_populates='llamadas')


class SeguimientoCandidataCaso(db.Model):
    __tablename__ = "seguimiento_candidatas_casos"
    __table_args__ = (
        db.UniqueConstraint("public_id", name="uq_seg_caso_public_id"),
        db.Index("ix_seg_caso_estado_due_at", "estado", "due_at"),
        db.Index("ix_seg_caso_owner_estado", "owner_staff_user_id", "estado"),
        db.Index("ix_seg_caso_last_movement_at_desc", "last_movement_at"),
        CheckConstraint(
            "estado IN ('nuevo','en_gestion','esperando_candidata','esperando_staff','programado','listo_para_enviar','enviado','cerrado_exitoso','cerrado_no_exitoso','duplicado')",
            name="ck_seg_caso_estado",
        ),
        CheckConstraint(
            "prioridad IN ('baja','normal','alta','urgente')",
            name="ck_seg_caso_prioridad",
        ),
        CheckConstraint(
            "canal_origen IN ('llamada','whatsapp','chat','presencial','referida','otro')",
            name="ck_seg_caso_canal_origen",
        ),
        CheckConstraint(
            "NOT (candidata_id IS NULL AND contacto_id IS NULL)",
            name="ck_seg_caso_identity_present",
        ),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    public_id = db.Column(db.String(40), nullable=False, unique=True, index=True)

    candidata_id = db.Column(db.Integer, db.ForeignKey("candidatas.fila", ondelete="SET NULL"), nullable=True, index=True)
    solicitud_id = db.Column(db.Integer, db.ForeignKey("solicitudes.id", ondelete="SET NULL"), nullable=True, index=True)
    contacto_id = db.Column(db.Integer, db.ForeignKey("seguimiento_candidatas_contactos.id", ondelete="SET NULL"), nullable=True, index=True)

    nombre_contacto = db.Column(db.String(200), nullable=True)
    telefono_norm = db.Column(db.String(32), nullable=True, index=True)
    canal_origen = db.Column(db.String(20), nullable=False, default="otro", server_default=text("'otro'"), index=True)
    estado = db.Column(db.String(30), nullable=False, default="nuevo", server_default=text("'nuevo'"), index=True)
    prioridad = db.Column(db.String(20), nullable=False, default="normal", server_default=text("'normal'"), index=True)

    owner_staff_user_id = db.Column(db.Integer, db.ForeignKey("staff_users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_staff_user_id = db.Column(db.Integer, db.ForeignKey("staff_users.id"), nullable=False, index=True)
    taken_at = db.Column(db.DateTime, nullable=True)

    proxima_accion_tipo = db.Column(db.String(40), nullable=True)
    proxima_accion_detalle = db.Column(db.String(300), nullable=True)
    due_at = db.Column(db.DateTime, nullable=True, index=True)
    waiting_since_at = db.Column(db.DateTime, nullable=True, index=True)
    status_changed_at = db.Column(db.DateTime, nullable=True)
    last_inbound_at = db.Column(db.DateTime, nullable=True)
    last_outbound_at = db.Column(db.DateTime, nullable=True)
    last_movement_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)

    closed_at = db.Column(db.DateTime, nullable=True, index=True)
    closed_by_staff_user_id = db.Column(db.Integer, db.ForeignKey("staff_users.id", ondelete="SET NULL"), nullable=True, index=True)
    close_reason = db.Column(db.String(255), nullable=True)

    merge_into_case_id = db.Column(db.Integer, db.ForeignKey("seguimiento_candidatas_casos.id", ondelete="SET NULL"), nullable=True, index=True)
    is_merged = db.Column(db.Boolean, nullable=False, default=False, server_default=text("false"), index=True)

    row_version = db.Column(db.Integer, nullable=False, default=1, server_default=text("1"))
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive, index=True)

    candidata = db.relationship("Candidata", lazy="select")
    solicitud = db.relationship("Solicitud", lazy="select")
    contacto = db.relationship("SeguimientoCandidataContacto", lazy="select")
    owner_staff_user = db.relationship("StaffUser", foreign_keys=[owner_staff_user_id], lazy="select")
    created_by_staff_user = db.relationship("StaffUser", foreign_keys=[created_by_staff_user_id], lazy="select")
    closed_by_staff_user = db.relationship("StaffUser", foreign_keys=[closed_by_staff_user_id], lazy="select")
    merged_into_case = db.relationship("SeguimientoCandidataCaso", remote_side=[id], lazy="select")
    eventos = db.relationship(
        "SeguimientoCandidataEvento",
        back_populates="caso",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    __mapper_args__ = {"version_id_col": row_version}


class SeguimientoCandidataContacto(db.Model):
    __tablename__ = "seguimiento_candidatas_contactos"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    telefono_norm = db.Column(db.String(32), nullable=True, index=True)
    nombre_reportado = db.Column(db.String(200), nullable=True)
    canal_preferido = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive, index=True)


class SeguimientoCandidataEvento(db.Model):
    __tablename__ = "seguimiento_candidatas_eventos"
    __table_args__ = (
        db.Index("ix_seg_evento_caso_created", "caso_id", "created_at"),
    )

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    caso_id = db.Column(
        db.Integer,
        db.ForeignKey("seguimiento_candidatas_casos.id"),
        nullable=False,
        index=True,
    )
    event_type = db.Column(db.String(60), nullable=False, index=True)
    actor_staff_user_id = db.Column(db.Integer, db.ForeignKey("staff_users.id", ondelete="SET NULL"), nullable=True, index=True)
    old_value = db.Column(db.JSON, nullable=True)
    new_value = db.Column(db.JSON, nullable=True)
    note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)

    caso = db.relationship("SeguimientoCandidataCaso", back_populates="eventos", lazy="joined")
    actor_staff_user = db.relationship("StaffUser", lazy="joined")


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
        default=utc_now_naive,
        comment="Cuándo se publicó por primera vez en la web."
    )

    fecha_ultima_actualizacion = db.Column(
        db.DateTime,
        nullable=False,
        default=utc_now_naive,
        onupdate=utc_now_naive,
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


# ─────────────────────────────────────────────────────────────
# RECLUTAMIENTO GENERAL (NO DOMÉSTICA) – NUEVO MÓDULO
# - No toca candidatas (domésticas) ni rompe lo existente.
# - Pensado para secretarias y admin (control interno).
# ─────────────────────────────────────────────────────────────

# Tipos de empleo generales (EXCLUYE DOMÉSTICA)
# Nota: esto se guarda como texto/enum para filtrar rápido.
TIPOS_EMPLEO_GENERAL = (
    'seguridad',
    'chofer',
    'recepcionista',
    'cajero',
    'oficina',
    'ventas',
    'call_center',
    'almacen',
    'mensajeria',
    'tecnico',
    'obrero',
    'salud',
    'hoteleria',
    'educacion',
    'otro'
)


class ReclutaPerfil(db.Model):
    """
    Perfil de talento reclutado para empleos generales (NO doméstica).

    Formulario corto (secretaria):
    - nombre, cédula, edad, sexo, nacionalidad
    - teléfono, ubicación (dirección), email (opcional)
    - tipo(s) de empleo que busca + empleo principal
    - modalidad, horario, sueldo esperado
    - experiencia (sí/no), años, resumen
    - nivel educativo, habilidades
    - documentos al día (sí/no)
    - disponibilidad especial (fines de semana/noches) (sí/no)
    - observaciones internas + estado

    Importante:
    - NO usa FK a tabla de usuarios para evitar romper tu login actual.
      Guardamos `creado_por` / `actualizado_por` como texto.
    """

    __tablename__ = 'reclutas_perfiles'

    id = db.Column(db.Integer, primary_key=True)


    # Estado interno del perfil
    estado = db.Column(
        SAEnum('nuevo', 'aprobado', 'rechazado', name='estado_recluta_enum'),
        nullable=False,
        default='nuevo',
        server_default=text("'nuevo'"),
        index=True,
        comment="Estado interno del perfil reclutado"
    )


    # Auditoría simple (texto para no amarrarnos a otro modelo)
    creado_por = db.Column(
        db.String(100),
        nullable=True,
        comment="Usuario/Nombre (admin o secretaria) que creó el perfil"
    )
    actualizado_por = db.Column(
        db.String(100),
        nullable=True,
        comment="Usuario/Nombre (admin o secretaria) que actualizó por última vez"
    )
    origen_registro = db.Column(
        db.String(32),
        nullable=True,
        index=True,
        comment="Origen de creación del registro: publico_empleo_general o interno"
    )
    creado_desde_ruta = db.Column(
        db.String(120),
        nullable=True,
        comment="Ruta origen desde donde se creó el perfil"
    )

    created_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)

    # ─────────────────────────────────────────────
    # Datos personales (corto)
    # ─────────────────────────────────────────────
    nombre_completo = db.Column(db.String(200), nullable=False, index=True)
    cedula = db.Column(db.String(50), nullable=False, index=True)

    # Guardamos edad directo (más rápido y práctico para secretarias).
    edad = db.Column(db.String(20), nullable=True, comment="Edad en texto: '25', '30-35', etc")

    sexo = db.Column(
        SAEnum('masculino', 'femenino', 'otro', name='sexo_recluta_enum'),
        nullable=True
    )

    nacionalidad = db.Column(db.String(80), nullable=True)

    # ─────────────────────────────────────────────
    # Contacto / Ubicación (SIN repetir)
    # - `direccion_completa` es un solo campo que incluye ciudad/sector.
    # - Opcionales `ciudad` y `sector` para facilitar filtros (no obligatorios en el form).
    # ─────────────────────────────────────────────
    telefono = db.Column(db.String(50), nullable=False, index=True)
    email = db.Column(db.String(120), nullable=True, index=True)

    direccion_completa = db.Column(
        db.String(300),
        nullable=True,
        comment="Dirección completa (incluye ciudad/sector si aplica)"
    )
    ciudad = db.Column(db.String(120), nullable=True, index=True)
    sector = db.Column(db.String(120), nullable=True, index=True)

    # ─────────────────────────────────────────────
    # Referencias
    # ─────────────────────────────────────────────
    referencias_laborales = db.Column(
        db.Text,
        nullable=True,
        comment="Referencias laborales: nombres, teléfonos y relación."
    )

    referencias_familiares = db.Column(
        db.Text,
        nullable=True,
        comment="Referencias familiares: nombres, teléfonos y relación."
    )

    # ─────────────────────────────────────────────
    # Perfil laboral
    # ─────────────────────────────────────────────
    # Selección múltiple (para búsqueda/filtrado)
    tipos_empleo_busca = db.Column(
        ARRAY(db.String(50)),
        nullable=True,
        server_default=text("ARRAY[]::VARCHAR[]"),
        comment="Lista de tipos de empleo que busca (NO doméstica)"
    )

    # Principal (uno)
    empleo_principal = db.Column(
        db.String(50),
        nullable=True,
        index=True,
        comment="Tipo de empleo principal preferido (NO doméstica)"
    )

    modalidad = db.Column(
        SAEnum('tiempo_completo', 'medio_tiempo', 'por_dias', 'por_horas', name='modalidad_recluta_enum'),
        nullable=True,
        index=True
    )

    horario_disponible = db.Column(db.String(120), nullable=True)

    sueldo_esperado = db.Column(
        db.String(80),
        nullable=True,
        comment="Texto libre: 'RD$25,000', 'a discutir', 'por día', etc"
    )

    # ─────────────────────────────────────────────
    # Experiencia / Educación / Habilidades
    # ─────────────────────────────────────────────
    tiene_experiencia = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default=text('false'),
        index=True
    )

    anos_experiencia = db.Column(db.String(20), nullable=True)

    experiencia_resumen = db.Column(
        db.String(300),
        nullable=True,
        comment="Resumen corto (1-2 líneas)"
    )

    nivel_educativo = db.Column(
        db.String(80),
        nullable=True,
        comment="Ej: primaria, secundaria, técnico, universitario"
    )

    habilidades = db.Column(
        db.String(300),
        nullable=True,
        comment="Ej: computadora, caja, servicio al cliente, Excel, etc"
    )

    # ─────────────────────────────────────────────
    # Condiciones rápidas
    # ─────────────────────────────────────────────
    documentos_al_dia = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default=text('false'),
        comment="Si tiene documentos al día (según verificación rápida)"
    )

    disponible_fines_o_noches = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default=text('false'),
        comment="Si puede fines de semana o noches"
    )

    # ─────────────────────────────────────────────
    # Interno
    # ─────────────────────────────────────────────
    observaciones_internas = db.Column(db.Text, nullable=True)

    def __repr__(self) -> str:
        return f"<ReclutaPerfil {self.id} estado={self.estado} cedula={self.cedula}>"


class ReclutaCambio(db.Model):
    """Historial simple de cambios (auditoría) para reclutas (opcional pero útil)."""

    __tablename__ = 'reclutas_cambios'

    id = db.Column(db.Integer, primary_key=True)

    recluta_id = db.Column(
        db.Integer,
        db.ForeignKey('reclutas_perfiles.id'),
        nullable=False,
        index=True
    )

    accion = db.Column(
        db.String(50),
        nullable=False,
        comment="Ej: creado, editado, aprobado, rechazado"
    )

    usuario = db.Column(
        db.String(100),
        nullable=True,
        comment="Usuario/Nombre que realizó el cambio"
    )

    nota = db.Column(db.Text, nullable=True)

    creado_en = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)

    recluta = db.relationship(
        'ReclutaPerfil',
        backref=db.backref('cambios', cascade='all, delete-orphan', lazy='dynamic')
    )

    def __repr__(self) -> str:
        return f"<ReclutaCambio {self.id} recluta_id={self.recluta_id} accion={self.accion}>"


# ─────────────────────────────────────────────────────────────
# NOTA PARA RUTAS / VALIDACIONES (no rompe nada):
# - En el formulario, valida que `empleo_principal` y `tipos_empleo_busca`
#   NO contengan 'domestica'.
# - Usa los valores de TIPOS_EMPLEO_GENERAL.
# ─────────────────────────────────────────────────────────────


class SolicitudCandidata(db.Model):
    """
    Relación interna para matching entre solicitudes y candidatas.
    No reemplaza la asignación final de `solicitud.candidata_id`.
    """

    __tablename__ = "solicitudes_candidatas"

    id = db.Column(db.Integer, primary_key=True)
    solicitud_id = db.Column(
        db.Integer,
        db.ForeignKey("solicitudes.id"),
        nullable=False,
        index=True,
    )
    candidata_id = db.Column(
        db.Integer,
        db.ForeignKey("candidatas.fila"),
        nullable=False,
        index=True,
    )

    score_snapshot = db.Column(db.Integer, nullable=True)
    breakdown_snapshot = db.Column(JSONB, nullable=True)
    status = db.Column(
        SAEnum(
            "sugerida",
            "enviada",
            "vista",
            "descartada",
            "seleccionada",
            "liberada",
            name="solicitud_candidata_status_enum",
        ),
        nullable=False,
        default="sugerida",
        server_default=text("'sugerida'"),
    )
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)
    created_by = db.Column(db.String(120), nullable=True)

    solicitud = db.relationship("Solicitud", lazy="joined")
    candidata = db.relationship("Candidata", lazy="joined")

    def __repr__(self) -> str:
        return (
            f"<SolicitudCandidata id={self.id} solicitud_id={self.solicitud_id} "
            f"candidata_id={self.candidata_id} status={self.status}>"
        )


class SolicitudRecommendationRun(db.Model):
    __tablename__ = "solicitud_recommendation_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','completed','error','stale')",
            name="ck_sol_rec_run_status",
        ),
        db.Index("ix_sol_rec_runs_sol_status_req", "solicitud_id", "status", "requested_at"),
        db.Index("ix_sol_rec_runs_sol_active_req", "solicitud_id", "is_active", "requested_at"),
        db.Index("ix_sol_rec_runs_fingerprint_hash", "fingerprint_hash"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    solicitud_id = db.Column(db.Integer, db.ForeignKey("solicitudes.id"), nullable=False, index=True)
    trigger_source = db.Column(db.String(40), nullable=False, default="manual", server_default=text("'manual'"))
    status = db.Column(db.String(20), nullable=False, default="pending", server_default=text("'pending'"), index=True)
    fingerprint_hash = db.Column(db.String(64), nullable=False)
    model_version = db.Column(db.String(40), nullable=False, default="rec-v1", server_default=text("'rec-v1'"))
    policy_version = db.Column(db.String(40), nullable=False, default="policy-v1", server_default=text("'policy-v1'"))
    requested_by = db.Column(db.String(120), nullable=True)
    requested_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    failed_at = db.Column(db.DateTime, nullable=True)
    error_code = db.Column(db.String(80), nullable=True)
    error_message = db.Column(db.String(500), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=text("true"), index=True)
    pool_size = db.Column(db.Integer, nullable=False, default=0, server_default=text("0"))
    eligible_count = db.Column(db.Integer, nullable=False, default=0, server_default=text("0"))
    hard_fail_count = db.Column(db.Integer, nullable=False, default=0, server_default=text("0"))
    soft_fail_count = db.Column(db.Integer, nullable=False, default=0, server_default=text("0"))
    items_count = db.Column(db.Integer, nullable=False, default=0, server_default=text("0"))
    meta = db.Column(db.JSON, nullable=False, default=dict, server_default=text("'{}'"))

    solicitud = db.relationship("Solicitud", back_populates="recommendation_runs", lazy="joined")
    items = db.relationship(
        "SolicitudRecommendationItem",
        back_populates="run",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )


class SolicitudRecommendationItem(db.Model):
    __tablename__ = "solicitud_recommendation_items"
    __table_args__ = (
        db.UniqueConstraint("run_id", "candidata_id", name="uq_sol_rec_item_run_candidata"),
        CheckConstraint(
            "confidence_band IN ('alta','media','baja') OR confidence_band IS NULL",
            name="ck_sol_rec_item_confidence_band",
        ),
        db.Index("ix_sol_rec_items_sol_run_rank", "solicitud_id", "run_id", "rank_position"),
        db.Index("ix_sol_rec_items_sol_eligible_score", "solicitud_id", "is_eligible", "score_final"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    run_id = db.Column(db.Integer, db.ForeignKey("solicitud_recommendation_runs.id"), nullable=False, index=True)
    solicitud_id = db.Column(db.Integer, db.ForeignKey("solicitudes.id"), nullable=False, index=True)
    candidata_id = db.Column(db.Integer, db.ForeignKey("candidatas.fila"), nullable=False, index=True)
    rank_position = db.Column(db.Integer, nullable=True, index=True)
    is_eligible = db.Column(db.Boolean, nullable=False, default=False, server_default=text("false"), index=True)
    hard_fail = db.Column(db.Boolean, nullable=False, default=False, server_default=text("false"), index=True)
    hard_fail_codes = db.Column(db.JSON, nullable=False, default=list, server_default=text("'[]'"))
    hard_fail_reasons = db.Column(db.JSON, nullable=False, default=list, server_default=text("'[]'"))
    soft_fail_codes = db.Column(db.JSON, nullable=False, default=list, server_default=text("'[]'"))
    soft_fail_reasons = db.Column(db.JSON, nullable=False, default=list, server_default=text("'[]'"))
    score_final = db.Column(db.Integer, nullable=True)
    score_operational = db.Column(db.Integer, nullable=True)
    confidence_band = db.Column(db.String(20), nullable=True)
    policy_snapshot = db.Column(db.JSON, nullable=False, default=dict, server_default=text("'{}'"))
    breakdown_snapshot = db.Column(db.JSON, nullable=False, default=dict, server_default=text("'{}'"))
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)

    run = db.relationship("SolicitudRecommendationRun", back_populates="items", lazy="joined")
    solicitud = db.relationship("Solicitud", lazy="joined")
    candidata = db.relationship("Candidata", lazy="joined")


class SolicitudRecommendationSelection(db.Model):
    __tablename__ = "solicitud_recommendation_selections"
    __table_args__ = (
        db.UniqueConstraint("solicitud_id", "run_id", "candidata_id", name="uq_sol_rec_sel_sol_run_cand"),
        CheckConstraint(
            "status IN ('pending_validation','valid','invalidated','confirmed')",
            name="ck_sol_rec_sel_status",
        ),
        db.Index("ix_sol_rec_sel_sol_created", "solicitud_id", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    solicitud_id = db.Column(db.Integer, db.ForeignKey("solicitudes.id"), nullable=False, index=True)
    run_id = db.Column(db.Integer, db.ForeignKey("solicitud_recommendation_runs.id"), nullable=False, index=True)
    recommendation_item_id = db.Column(
        db.Integer,
        db.ForeignKey("solicitud_recommendation_items.id"),
        nullable=True,
        index=True,
    )
    candidata_id = db.Column(db.Integer, db.ForeignKey("candidatas.fila"), nullable=False, index=True)
    status = db.Column(
        db.String(30),
        nullable=False,
        default="pending_validation",
        server_default=text("'pending_validation'"),
        index=True,
    )
    validation_code = db.Column(db.String(80), nullable=True)
    validation_message = db.Column(db.String(300), nullable=True)
    validated_at = db.Column(db.DateTime, nullable=True)
    selected_by = db.Column(db.String(120), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)
    meta = db.Column(db.JSON, nullable=False, default=dict, server_default=text("'{}'"))

    solicitud = db.relationship("Solicitud", back_populates="recommendation_selections", lazy="joined")
    run = db.relationship("SolicitudRecommendationRun", lazy="joined")
    item = db.relationship("SolicitudRecommendationItem", lazy="joined")
    candidata = db.relationship("Candidata", lazy="joined")


class ChatConversation(db.Model):
    __tablename__ = "chat_conversations"
    __table_args__ = (
        db.UniqueConstraint("scope_key", name="uq_chat_conversation_scope_key"),
        db.Index("ix_chat_conv_cliente_last_msg", "cliente_id", "last_message_at"),
        db.Index("ix_chat_conv_staff_unread", "staff_unread_count", "last_message_at"),
        db.Index("ix_chat_conv_cliente_unread", "cliente_unread_count", "last_message_at"),
        db.Index("ix_chat_conv_assigned_staff_last_msg", "assigned_staff_user_id", "last_message_at"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    scope_key = db.Column(db.String(80), nullable=False, index=True)
    conversation_type = db.Column(db.String(20), nullable=False, default="general", server_default=text("'general'"), index=True)
    status = db.Column(db.String(20), nullable=False, default="open", server_default=text("'open'"), index=True)

    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False, index=True)
    solicitud_id = db.Column(db.Integer, db.ForeignKey("solicitudes.id"), nullable=True, index=True)
    assigned_staff_user_id = db.Column(db.Integer, db.ForeignKey("staff_users.id"), nullable=True, index=True)
    assigned_at = db.Column(db.DateTime, nullable=True, index=True)
    subject = db.Column(db.String(200), nullable=True)

    last_message_at = db.Column(db.DateTime, nullable=True, index=True)
    last_message_preview = db.Column(db.String(240), nullable=True)
    last_message_sender_type = db.Column(db.String(20), nullable=True)
    cliente_unread_count = db.Column(db.Integer, nullable=False, default=0, server_default=text("0"))
    staff_unread_count = db.Column(db.Integer, nullable=False, default=0, server_default=text("0"))
    client_last_read_at = db.Column(db.DateTime, nullable=True)
    staff_last_read_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive, index=True)

    cliente = db.relationship("Cliente", lazy="select", back_populates="chat_conversations")
    solicitud = db.relationship("Solicitud", lazy="select", back_populates="chat_conversations")
    assigned_staff_user = db.relationship("StaffUser", lazy="joined")
    messages = db.relationship(
        "ChatMessage",
        back_populates="conversation",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<ChatConversation id={self.id} cliente_id={self.cliente_id} "
            f"solicitud_id={self.solicitud_id} type={self.conversation_type}>"
        )


class ChatMessage(db.Model):
    __tablename__ = "chat_messages"
    __table_args__ = (
        db.Index("ix_chat_msg_conv_created", "conversation_id", "created_at"),
        db.Index("ix_chat_msg_conv_id_desc", "conversation_id", "id"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("chat_conversations.id"), nullable=False, index=True)
    sender_type = db.Column(db.String(20), nullable=False, index=True)
    sender_cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=True, index=True)
    sender_staff_user_id = db.Column(db.Integer, db.ForeignKey("staff_users.id"), nullable=True, index=True)
    body = db.Column(db.Text, nullable=False)
    meta = db.Column(db.JSON, nullable=False, default=dict, server_default=text("'{}'"))
    is_deleted = db.Column(db.Boolean, nullable=False, default=False, server_default=text("false"), index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)

    conversation = db.relationship("ChatConversation", back_populates="messages", lazy="joined")
    sender_cliente = db.relationship("Cliente", lazy="joined")
    sender_staff_user = db.relationship("StaffUser", lazy="joined")

    def __repr__(self) -> str:
        return (
            f"<ChatMessage id={self.id} conversation_id={self.conversation_id} "
            f"sender_type={self.sender_type}>"
        )


class RequestIdempotencyKey(db.Model):
    __tablename__ = "request_idempotency_keys"
    __table_args__ = (
        db.UniqueConstraint("scope", "idempotency_key", name="uq_request_idempotency_scope_key"),
        db.Index("ix_request_idempotency_scope_actor", "scope", "actor_id"),
    )

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    scope = db.Column(db.String(80), nullable=False, index=True)
    idempotency_key = db.Column(db.String(128), nullable=False)
    actor_id = db.Column(db.String(64), nullable=True, index=True)
    entity_type = db.Column(db.String(50), nullable=True, index=True)
    entity_id = db.Column(db.String(64), nullable=True, index=True)
    request_hash = db.Column(db.String(64), nullable=False)
    response_status = db.Column(db.Integer, nullable=True)
    response_code = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)
    last_seen_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)


class DomainOutbox(db.Model):
    __tablename__ = "domain_outbox"
    __table_args__ = (
        db.Index("ix_domain_outbox_published_created", "published_at", "created_at"),
        db.Index("ix_domain_outbox_aggregate", "aggregate_type", "aggregate_id"),
    )

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    event_id = db.Column(db.String(64), nullable=False, unique=True, index=True)
    event_type = db.Column(db.String(80), nullable=False, index=True)
    aggregate_type = db.Column(db.String(80), nullable=False, index=True)
    aggregate_id = db.Column(db.String(64), nullable=False, index=True)
    aggregate_version = db.Column(db.Integer, nullable=True)
    occurred_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)
    actor_id = db.Column(db.String(64), nullable=True, index=True)
    region = db.Column(db.String(40), nullable=True)
    payload = db.Column(db.JSON, nullable=False, default=dict)
    schema_version = db.Column(db.Integer, nullable=False, default=1, server_default=text("1"))
    correlation_id = db.Column(db.String(64), nullable=True, index=True)
    idempotency_key = db.Column(db.String(128), nullable=True, index=True)
    published_attempts = db.Column(db.Integer, nullable=False, default=0, server_default=text("0"))
    relay_status = db.Column(db.String(20), nullable=False, default="pending", server_default=text("'pending'"), index=True)
    first_failed_at = db.Column(db.DateTime, nullable=True)
    last_error = db.Column(db.String(500), nullable=True)
    last_attempt_at = db.Column(db.DateTime, nullable=True)
    next_retry_at = db.Column(db.DateTime, nullable=True, index=True)
    quarantined_at = db.Column(db.DateTime, nullable=True, index=True)
    quarantine_reason = db.Column(db.String(80), nullable=True)
    published_at = db.Column(db.DateTime, nullable=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)


class OutboxConsumerReceipt(db.Model):
    __tablename__ = "outbox_consumer_receipts"
    __table_args__ = (
        db.UniqueConstraint("consumer_name", "event_id", name="uq_outbox_consumer_event"),
    )

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    consumer_name = db.Column(db.String(80), nullable=False, index=True)
    event_id = db.Column(db.String(64), nullable=False, index=True)
    processed_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)


class OperationalMetricSnapshot(db.Model):
    __tablename__ = "operational_metric_snapshots"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    captured_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)
    window_minutes = db.Column(db.Integer, nullable=False, default=15, server_default=text("15"))
    metrics = db.Column(db.JSON, nullable=False, default=dict)


class StaffNotificacion(db.Model):
    __tablename__ = "staff_notificaciones"
    __table_args__ = (
        db.UniqueConstraint("tipo", "entity_type", "entity_id", name="uq_staff_notif_tipo_entity"),
    )

    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(50), nullable=False, index=True)
    entity_type = db.Column(db.String(30), nullable=False, index=True)
    entity_id = db.Column(db.Integer, nullable=False, index=True)
    titulo = db.Column(db.String(180), nullable=False)
    mensaje = db.Column(db.String(300), nullable=True)
    payload = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)

    lecturas = db.relationship(
        "StaffNotificacionLectura",
        back_populates="notificacion",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )


class StaffNotificacionLectura(db.Model):
    __tablename__ = "staff_notificaciones_lecturas"
    __table_args__ = (
        db.UniqueConstraint("notificacion_id", "reader_key", name="uq_staff_notif_read_by_reader"),
    )

    id = db.Column(db.Integer, primary_key=True)
    notificacion_id = db.Column(
        db.Integer,
        db.ForeignKey("staff_notificaciones.id"),
        nullable=False,
        index=True,
    )
    reader_key = db.Column(db.String(120), nullable=False, index=True)
    read_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)

    notificacion = db.relationship("StaffNotificacion", back_populates="lecturas", lazy="joined")


class ClienteNotificacion(db.Model):
    __tablename__ = "clientes_notificaciones"
    __table_args__ = (
        db.Index("ix_clientes_notif_cliente_read_deleted", "cliente_id", "is_read", "is_deleted"),
    )

    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(
        db.Integer,
        db.ForeignKey("clientes.id"),
        nullable=False,
        index=True,
    )
    solicitud_id = db.Column(
        db.Integer,
        db.ForeignKey("solicitudes.id"),
        nullable=True,
        index=True,
    )
    tipo = db.Column(db.String(80), nullable=False, index=True)
    titulo = db.Column(db.String(200), nullable=False)
    cuerpo = db.Column(db.Text, nullable=True)
    payload = db.Column(JSONB, nullable=True)
    is_read = db.Column(db.Boolean, nullable=False, default=False, server_default=text("false"), index=True)
    is_deleted = db.Column(db.Boolean, nullable=False, default=False, server_default=text("false"), index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)

    cliente = db.relationship("Cliente", lazy="joined")
    solicitud = db.relationship("Solicitud", lazy="joined")

    def __repr__(self) -> str:
        return (
            f"<ClienteNotificacion id={self.id} cliente_id={self.cliente_id} "
            f"solicitud_id={self.solicitud_id} tipo={self.tipo} is_read={self.is_read}>"
        )


class ContratoDigital(db.Model):
    __tablename__ = "contratos_digitales"
    __table_args__ = (
        db.UniqueConstraint("solicitud_id", "version", name="uq_contrato_solicitud_version"),
        db.UniqueConstraint("token_hash", name="uq_contrato_token_hash"),
        db.CheckConstraint(
            "estado IN ('borrador','enviado','visto','firmado','expirado','anulado')",
            name="ck_contrato_estado_valido",
        ),
        db.CheckConstraint(
            "token_hash IS NULL OR length(token_hash) = 64",
            name="ck_contrato_token_hash_len",
        ),
        db.CheckConstraint(
            "firma_png_sha256 IS NULL OR length(firma_png_sha256) = 64",
            name="ck_contrato_firma_hash_len",
        ),
        db.CheckConstraint(
            "pdf_final_sha256 IS NULL OR length(pdf_final_sha256) = 64",
            name="ck_contrato_pdf_hash_len",
        ),
        db.CheckConstraint(
            "token_expira_at IS NULL OR token_generado_at IS NULL OR token_expira_at > token_generado_at",
            name="ck_contrato_expira_gt_generado",
        ),
        db.CheckConstraint(
            "estado <> 'firmado' OR (firmado_at IS NOT NULL AND firma_png_sha256 IS NOT NULL AND pdf_final_sha256 IS NOT NULL AND pdf_generado_at IS NOT NULL)",
            name="ck_contrato_firmado_campos_minimos",
        ),
        db.Index("ix_contrato_cliente_created", "cliente_id", "created_at"),
        db.Index("ix_contrato_estado_expira", "estado", "token_expira_at"),
        db.Index("ix_contrato_solicitud", "solicitud_id"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    solicitud_id = db.Column(db.Integer, db.ForeignKey("solicitudes.id"), nullable=False)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False)
    version = db.Column(db.SmallInteger, nullable=False, default=1, server_default=text("1"))
    contrato_padre_id = db.Column(db.BigInteger, db.ForeignKey("contratos_digitales.id"), nullable=True)
    estado = db.Column(db.String(16), nullable=False, default="borrador", server_default=text("'borrador'"))
    contenido_snapshot_json = db.Column(db.JSON, nullable=False, default=dict)
    snapshot_fijado_at = db.Column(db.DateTime, nullable=True)

    token_version = db.Column(db.Integer, nullable=False, default=1, server_default=text("1"))
    token_hash = db.Column(db.String(64), nullable=True)
    token_generado_at = db.Column(db.DateTime, nullable=True)
    token_expira_at = db.Column(db.DateTime, nullable=True)
    token_revocado_at = db.Column(db.DateTime, nullable=True)

    enviado_at = db.Column(db.DateTime, nullable=True)
    primer_visto_at = db.Column(db.DateTime, nullable=True)
    ultimo_visto_at = db.Column(db.DateTime, nullable=True)
    primera_ip = db.Column(db.String(64), nullable=True)
    primer_user_agent = db.Column(db.String(512), nullable=True)

    firma_png = db.Column(LargeBinary, nullable=True)
    firma_png_sha256 = db.Column(db.String(64), nullable=True)
    firma_nombre = db.Column(db.String(180), nullable=True)
    firmado_at = db.Column(db.DateTime, nullable=True)
    firmado_ip = db.Column(db.String(64), nullable=True)
    firmado_user_agent = db.Column(db.String(512), nullable=True)

    pdf_final_bytea = db.Column(LargeBinary, nullable=True)
    pdf_final_sha256 = db.Column(db.String(64), nullable=True)
    pdf_final_size_bytes = db.Column(db.Integer, nullable=True)
    pdf_generado_at = db.Column(db.DateTime, nullable=True)

    anulado_at = db.Column(db.DateTime, nullable=True)
    anulado_por_staff_id = db.Column(db.Integer, db.ForeignKey("staff_users.id"), nullable=True)
    anulado_motivo = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)

    solicitud = db.relationship("Solicitud", lazy="joined")
    cliente = db.relationship("Cliente", lazy="joined")
    anulado_por_staff = db.relationship("StaffUser", lazy="joined")
    contrato_padre = db.relationship("ContratoDigital", remote_side=[id], lazy="joined")


class ContratoEvento(db.Model):
    __tablename__ = "contratos_eventos"
    __table_args__ = (
        db.CheckConstraint(
            "(estado_anterior IS NULL OR estado_anterior IN ('borrador','enviado','visto','firmado','expirado','anulado'))"
            " AND (estado_nuevo IS NULL OR estado_nuevo IN ('borrador','enviado','visto','firmado','expirado','anulado'))",
            name="ck_evento_estados_validos",
        ),
        db.CheckConstraint(
            "actor_tipo IN ('staff','cliente_publico','sistema')",
            name="ck_evento_actor_tipo",
        ),
        db.Index("ix_evento_contrato_created", "contrato_id", "created_at"),
        db.Index("ix_evento_tipo_created", "evento_tipo", "created_at"),
        db.Index("ix_evento_success_created", "success", "created_at"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    contrato_id = db.Column(db.BigInteger, db.ForeignKey("contratos_digitales.id"), nullable=False)
    evento_tipo = db.Column(db.String(48), nullable=False)
    estado_anterior = db.Column(db.String(16), nullable=True)
    estado_nuevo = db.Column(db.String(16), nullable=True)
    actor_tipo = db.Column(db.String(16), nullable=False, default="sistema")
    actor_staff_id = db.Column(db.Integer, db.ForeignKey("staff_users.id"), nullable=True)
    ip = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(512), nullable=True)
    metadata_json = db.Column(db.JSON, nullable=False, default=dict)
    success = db.Column(db.Boolean, nullable=False, default=True, server_default=text("true"))
    error_code = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive)

    contrato = db.relationship("ContratoDigital", lazy="joined")
    actor_staff = db.relationship("StaffUser", lazy="joined")


@event.listens_for(ContratoDigital, "before_update")
def _protect_signed_contract_immutability(_mapper, _connection, target: ContratoDigital):
    state = sa_inspect(target)
    if not state.persistent:
        return
    if state.attrs.estado.history.has_changes():
        old_estado = state.attrs.estado.history.deleted[0] if state.attrs.estado.history.deleted else None
    else:
        old_estado = target.estado
    was_signed = str(old_estado or "").strip().lower() == "firmado"
    if not was_signed:
        return

    allowed_after_signed = {"ultimo_visto_at", "updated_at"}
    changed = {
        attr.key
        for attr in state.attrs
        if attr.history.has_changes()
    }
    blocked = sorted(changed - allowed_after_signed)
    if blocked:
        raise RuntimeError(
            "Contrato firmado inmutable: columnas bloqueadas modificadas: "
            + ", ".join(blocked)
        )
