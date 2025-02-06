// Función para guardar datos en localStorage con manejo de errores
function guardarDatosEnLocalStorage(clave, datos) {
    try {
        localStorage.setItem(clave, JSON.stringify(datos));
    } catch (error) {
        console.error("Error al guardar en localStorage:", error);
    }
}

// Función para obtener datos desde localStorage con manejo de errores
function obtenerDatosDeLocalStorage(clave) {
    try {
        let datos = localStorage.getItem(clave);
        return datos ? JSON.parse(datos) : null;
    } catch (error) {
        console.error("Error al obtener datos de localStorage:", error);
        return null;
    }
}

// Simulación de datos (para pruebas)
let candidatas = [
    { nombre: "Ana Pérez", edad: 30, ciudad: "Santiago", telefono: "809-123-4567" },
    { nombre: "María López", edad: 28, ciudad: "Santo Domingo", telefono: "829-456-7890" }
];

// Guardar datos si no existen en localStorage
if (!localStorage.getItem("candidatas")) {
    guardarDatosEnLocalStorage("candidatas", candidatas);
}

// Función para mostrar candidatas en el HTML
function mostrarCandidatas() {
    let contenedor = document.getElementById("contenedor-candidatas");
    let datos = obtenerDatosDeLocalStorage("candidatas");

    if (datos && datos.length > 0) {
        contenedor.innerHTML = ""; // Limpiar contenido previo

        datos.forEach(candidata => {
            let div = document.createElement("div");
            div.classList.add("candidata-item");
            div.innerHTML = <p><strong>Nombre:</strong> ${candidata.nombre} - <strong>Teléfono:</strong> ${candidata.telefono} - <strong>Ciudad:</strong> ${candidata.ciudad}</p>;
            contenedor.appendChild(div);
        });
    } else {
        contenedor.innerHTML = "<p>No se encontraron candidatas.</p>";
    }
}

// Ejecutar la función al cargar la página
document.addEventListener("DOMContentLoaded", mostrarCandidatas);