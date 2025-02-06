document.addEventListener("DOMContentLoaded", function() {
    console.log("Local Storage activo.");

    function guardarDatosEnLocalStorage(clave, datos) {
        localStorage.setItem(clave, JSON.stringify(datos));
    }

    function obtenerDatosDeLocalStorage(clave) {
        let datos = localStorage.getItem(clave);
        return datos ? JSON.parse(datos) : [];
    }

    // Verificar si ya hay datos en localStorage
    let candidatas = obtenerDatosDeLocalStorage("candidatas");

    if (candidatas.length === 0) {
        console.log("No hay datos en localStorage. Creando datos de prueba...");
        candidatas = [
            { nombre: "Ana Pérez", cédula: "001-0000001-0", telefono: "809-123-4567", estado: "Pendiente" },
            { nombre: "María López", cédula: "002-0000002-0", telefono: "809-987-6543", estado: "Disponible" }
        ];
        guardarDatosEnLocalStorage("candidatas", candidatas);
    }

    function mostrarCandidatasEnHTML() {
        let contenedor = document.getElementById("contenedor-candidatas");
        let datos = obtenerDatosDeLocalStorage("candidatas");

        if (datos.length > 0) {
            contenedor.innerHTML = "";
            datos.forEach(candidata => {
                let div = document.createElement("div");
                div.classList.add("candidata-item");
                div.innerHTML = <p><strong>Nombre:</strong> ${candidata.nombre} - <strong>Cédula:</strong> ${candidata.cédula} - <strong>Teléfono:</strong> ${candidata.telefono}</p>;
                contenedor.appendChild(div);
            });
        } else {
            contenedor.innerHTML = "<p>No se encontraron candidatas.</p>";
        }
    }

    mostrarCandidatasEnHTML();
});