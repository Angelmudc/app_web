# app_web/admin/fields.py

from wtforms import widgets, SelectMultipleField

class MultiCheckboxField(SelectMultipleField):
    """
    Un campo de casillas múltiples: renderiza una lista de checkboxes.
    Úsalo exactamente igual que tu SelectMultipleField, pero mostrará
    una checkbox por cada opción.
    """
    widget = widgets.ListWidget(prefix_label=False)
    option_widget = widgets.CheckboxInput()
