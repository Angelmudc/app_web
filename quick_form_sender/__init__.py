from flask import Blueprint


quick_form_sender_bp = Blueprint(
    "quick_form_sender",
    __name__,
    template_folder="templates",
)


from . import routes  # noqa: E402,F401
