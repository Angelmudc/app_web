import logging
from sqlalchemy import text
from config_app import db

def generar_codigo_unico():
    """
    Genera un código único en formato 'CAN-XXXXXX' basado en los códigos existentes en la base de datos.
    Se carga el máximo valor numérico tras 'CAN-' y devuelve el siguiente número con padding.
    """
    try:
        # Consulta para obtener el máximo número existente en la parte numérica del código
        sql = text(
            """
            SELECT MAX(CAST(SUBSTRING(codigo, 5) AS INTEGER))
            FROM candidatas
            WHERE codigo LIKE 'CAN-%'
            """
        )
        result = db.session.execute(sql).scalar()
        max_num = int(result) if result is not None else 0

        # Genera el nuevo código con padding de 6 dígitos
        siguiente = max_num + 1
        return f"CAN-{siguiente:06d}"

    except Exception as e:
        logging.error(f"Error al generar código único: {e}", exc_info=True)
        # Fallback sencillo si falla la consulta
        return "CAN-000001"
