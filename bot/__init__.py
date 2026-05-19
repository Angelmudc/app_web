# -*- coding: utf-8 -*-
from flask import Blueprint

bot_bp = Blueprint("bot", __name__, url_prefix="/bot")

from . import whatsapp_routes  # noqa: E402,F401
