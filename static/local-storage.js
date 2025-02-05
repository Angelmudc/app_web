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

// Simulación de datos (prueba real)
let candidatas = [
    { nombre: "Ana Pérez", edad: 30, ciudad: "Santiago" },
    { nombre: "María López", edad: 28, ciudad: "Santo Domingo" }
];

// Solo guarda los datos si no existen en localStorage
if (!localStorage.getItem("candidatas")) {
    guardarDatosEnLocalStorage("candidatas", candidatas);
}

// Función para mostrar candidatas en el HTML
function mostrarCandidatas() {
    let contenedor = document.getElementById("contenedor-candidatas");
    let datos = obtenerDatosDeLocalStorage("candidatas");

    if (datos && datos.length > 0) {
        contenedor.innerHTML = ""; // Limpia el contenido previo

        datos.forEach(candidata => {
            let div = document.createElement("div");
            div.classList.add("candidata-item"); // Agregar clase para estilos
            div.innerHTML = <p><strong>Nombre:</strong> ${candidata.nombre} - <strong>Edad:</strong> ${candidata.edad} - <strong>Ciudad:</strong> ${candidata.ciudad}</p>;
            contenedor.appendChild(div);
        });
    } else {
        contenedor.innerHTML = "<p>No se encontraron datos.</p>";
    }
}

// Ejecutar la función al cargar la página
document.addEventListener("DOMContentLoaded", mostrarCandidatas);