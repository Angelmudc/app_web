"""Módulo Reclutamiento General (NO doméstica).

Este paquete expone el Blueprint `reclutas_bp` para registrarlo en `create_app()`.

Uso:
    from reclutas import reclutas_bp
    app.register_blueprint(reclutas_bp)
"""

from .routes import reclutas_bp

__all__ = ["reclutas_bp"]
