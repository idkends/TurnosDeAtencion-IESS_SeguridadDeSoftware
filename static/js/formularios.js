// Evita el doble envío (RT-04, CA-13): desactiva el botón al enviar. Sin scripts en línea (CSP estricta).
document.addEventListener("submit", function (evento) {
  var formulario = evento.target;
  if (!(formulario instanceof HTMLFormElement)) { return; }
  // Se difiere para no impedir que el navegador incluya el botón pulsado en el envío.
  window.setTimeout(function () {
    formulario.querySelectorAll("button[type=submit], input[type=submit]").forEach(function (boton) {
      boton.disabled = true;
      boton.setAttribute("aria-busy", "true");
    });
  }, 0);
});
// Si la persona vuelve atrás con el navegador, se reactivan los botones.
window.addEventListener("pageshow", function () {
  document.querySelectorAll("button[type=submit][disabled]").forEach(function (b) {
    b.disabled = false;
    b.removeAttribute("aria-busy");
  });
});
