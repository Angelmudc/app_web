import psycopg2

conn = psycopg2.connect(
    host="localhost",
    port=5432,
    dbname="app_db",
    user="app_user",
    password="SuperSecretPwd123"
)
cur = conn.cursor()
cur.execute("SELECT version();")
print(cur.fetchone())
conn.close()
