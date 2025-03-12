import imghdr

logo_path = r"C:\Users\domes\OneDrive\Escritorio\app_web\static\logo.png"
tipo = imghdr.what(logo_path)
print("Tipo de imagen:", tipo)
