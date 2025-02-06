// Función para guardar datos en localStorage
function guardarDatosEnLocalStorage(clave, datos) {
    localStorage.setItem(clave, JSON.stringify(datos));
}

// Función para obtener datos de localStorage
function obtenerDatosDeLocalStorage(clave) {
    let datos = localStorage.getItem(clave);
    return datos ? JSON.parse(datos) : null;
}

// Función para mostrar candidatas en la página
function mostrarCandidatas() {
    let contenedor = document.getElementById("contenedor-candidatas");

    if (!contenedor) return;

    let datos = obtenerDatosDeLocalStorage("candidatas");

    if (datos && datos.length > 0) {
        contenedor.innerHTML = ""; // Limpiar contenido previo

        datos.forEach(candidata => {
            let div = document.createElement("div");
            div.classList.add("candidata-item"); // Agregar clase para estilos
            div.innerHTML = `
                <p><strong>Nombre:</strong> ${candidata.nombre} 
                - <strong>Edad:</strong> ${candidata.edad} 
                - <strong>Ciudad:</strong> ${candidata.ciudad}</p>
            `;
            contenedor.appendChild(div);
        });
    } else {
        contenedor.innerHTML = "<p>No se encontraron datos.</p>";
    }
}

// Ejecutar la función al cargar la página
document.addEventListener("DOMContentLoaded", mostrarCandidatas);