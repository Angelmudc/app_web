import psycopg2

# 1) Parámetros de conexión
conn = psycopg2.connect(
    dbname="app_db",
    user="app_user",
    password="SuperSecretPwd123",
    host="localhost",
    port="5432"
)
cur = conn.cursor()

# 2) Creamos la tabla con todos los campos de tu hoja
cur.execute("""
CREATE TABLE IF NOT EXISTS candidata (
    id SERIAL PRIMARY KEY,
    marca_temporal TIMESTAMP,
    nombre_completo TEXT,
    edad INT,
    numero_telefono TEXT,
    direccion_completa TEXT,
    modalidad_trabajo TEXT,
    rutas_cercanas TEXT,
    empleo_anterior TEXT,
    anos_experiencia TEXT,
    areas_experiencia TEXT,
    monto_inscripcion NUMERIC,
    fecha_inscripcion DATE,
    fecha_pago DATE,
    inicio TEXT,
    monto_total NUMERIC,
    porcentaje NUMERIC,
    calificacion TEXT,
    entrevista TEXT,
    depuracion TEXT,
    perfil TEXT,
    cedula1 TEXT,
    cedula2 TEXT,
    referencias_laborales TEXT,
    referencias_familiares TEXT,
    acepta_porcentaje BOOLEAN,
    cedula TEXT,
    codigo TEXT,
    medio_inscripcion TEXT,
    inscripcion BOOLEAN
);
""")

# 3) Confirmar y cerrar
conn.commit()
cur.close()
conn.close()
print("✅ Tabla creada (o ya existía).")
