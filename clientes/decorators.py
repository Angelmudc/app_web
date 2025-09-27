# clientes/decorators.py
# -*- coding: utf-8 -*-
from functools import wraps
from flask import redirect, url_for, request, flash
from flask_login import current_user
from models import Cliente

def cliente_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not isinstance(current_user, Cliente):
            # Si tienes login en este mismo blueprint:
            return redirect(url_for('clientes.login', next=request.url))
        return f(*args, **kwargs)
    return decorated

def politicas_requeridas(f):
    """
    Obliga a que el cliente haya aceptado las políticas
    antes de acceder a la vista protegida.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not getattr(current_user, 'acepto_politicas', False):
            flash('Debes aceptar las políticas para continuar.', 'warning')
            return redirect(url_for('clientes.politicas', next=request.url))
        return f(*args, **kwargs)
    return decorated
