/* Test de interfaz: la pantalla de inicio y el cajón del historial.
 *
 * Dos cosas que se piden a una interfaz de charla y que es fácil romper sin
 * darse cuenta: que al abrir una conversación nueva salude por tu nombre, y
 * que el historial se despliegue y se cierre por donde uno espera.
 *
 *   node tests/frontend/inicio.js
 */
const assert = require("node:assert");
const { montar, json, respirar } = require("./lib/dom-minimo");

const responder = async (url) => {
  if (url.includes("/api/corredor")) return json({ nombre: "Josué" });
  if (url.includes("/api/planes")) return json({ planes: [], activo: null });
  if (url.includes("/api/conversaciones")) return json({ conversaciones: [] });
  return json({ disponible: false });
};

(async () => {
  const { entorno, $, cuerpo } = montar({ responder });
  await respirar();

  const chat = () => $("#conversacion").innerHTML;

  // La bienvenida saluda por su nombre y ofrece por dónde empezar.
  assert.match(chat(), /Hola, Josué/, "debe saludar por su nombre");
  assert.match(chat(), /i-ondas/, "con el emblema de voz");
  assert.match(chat(), /Quiero correr un 10K/, "y con ejemplos que se pueden pulsar");
  assert.match(chat(), /data-ejemplo=/, "los ejemplos son botones, no texto suelto");
  assert.ok($("#vista-chat").clases.has("vacio"),
            "el chat vacío se marca para poder centrarlo en pantalla");

  // La frase cambia entre charlas: se comprueba que sale una de las previstas.
  assert.ok(/¿|Cuéntame|Dime|Empecemos/.test(chat()), "y una frase de entrada");

  // En cuanto hay conversación, el saludo deja sitio: el chat deja de estar
  // centrado. Que el nodo del saludo desaparezca no se puede comprobar aquí
  // —innerHTML es una cadena, no un árbol—, pero la clase sí.
  entorno.escribirTurno("corredor", "Quiero correr un 10K");
  assert.ok(!$("#vista-chat").clases.has("vacio"), "el chat deja de estar centrado");

  // El cajón: cerrado de casa, se abre, y se cierra por el velo.
  assert.ok(!cuerpo.clases.has("historialAbierto"), "arranca cerrado");
  assert.equal($("#velo").hidden, true);

  entorno.alternarHistorial();
  assert.ok(cuerpo.clases.has("historialAbierto"), "el botón lo abre");
  assert.equal($("#velo").hidden, false, "y aparece el velo para poder cerrarlo");
  assert.equal($("#abrirHistorial").getAttribute("aria-expanded"), "true");

  entorno.mostrarHistorial(false);
  assert.ok(!cuerpo.clases.has("historialAbierto"), "y se cierra");
  assert.equal($("#velo").hidden, true);

  console.log("ok · inicio e historial: 11 comprobaciones");
})().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
