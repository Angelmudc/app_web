# check_nulls.py
import os

files = ["app.py", "config_app.py"]
problems = []

for fn in files:
    if not os.path.exists(fn):
        continue
    data = open(fn, "rb").read()
    count = data.count(b"\x00")
    if count > 0:
        problems.append((fn, count))

if problems:
    for fn, cnt in problems:
        print(f"⚠️ {fn} tiene {cnt} bytes nulos")
    exit(1)
else:
    print("✅ Ningún byte nulo encontrado")
    exit(0)
