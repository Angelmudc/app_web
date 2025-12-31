# utils/__init__.py
# Este archivo convierte la carpeta "utils" en paquete y expone utilidades
# usadas por otras partes del sistema (ej: admin/routes.py).

def letra_por_indice(n: int) -> str:
    """
    Convierte 1 -> 'A', 2 -> 'B' ... 26 -> 'Z', 27 -> 'AA', etc.
    Útil para columnas de Excel/Sheets.
    """
    if n is None:
        return ""
    try:
        n = int(n)
    except Exception:
        return ""

    if n <= 0:
        return ""

    result = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result
