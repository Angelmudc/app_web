document.addEventListener("DOMContentLoaded", function () {
    function guardarDatosEnLocalStorage(clave, datos) {
        localStorage.setItem(clave, JSON.stringify(datos));
    }

    function obtenerDatosDeLocalStorage(clave) {
        const datos = localStorage.getItem(clave);
        return datos ? JSON.parse(datos) : null;
    }

    // Guarda los datos temporalmente si se hace una búsqueda
    let buscarForm = document.getElementById("buscarCandidataForm");
    if (buscarForm) {
        buscarForm.addEventListener("submit", function () {
            let buscarInput = document.getElementById("buscar").value.trim();
            guardarDatosEnLocalStorage("ultimaBusqueda", buscarInput);
        });
    }

    // Carga el último valor buscado en la caja de búsqueda
    let ultimaBusqueda = obtenerDatosDeLocalStorage("ultimaBusqueda");
    if (ultimaBusqueda) {
        document.getElementById("buscar").value = ultimaBusqueda;
    }
});