/* Test de interfaz: silenciar a Vydor y borrar una charla.
 *
 * Del silencio importa que se recuerde entre visitas —un ajuste que hay que
 * volver a poner cada vez molesta más de lo que ayuda— y del borrado, que
 * pida confirmación y que la petición llegue al servidor con el método
 * correcto.
 *
 *   node tests/frontend/silencio-y-borrado.js
 */
const assert = require("node:assert");
const { montar, json, respirar } = require("./lib/dom-minimo");

const charlas = [
  { id: "c1", titulo: "Quiero un 10K", fin: "2026-08-16T10:00:00+00:00",
    turnos: 8, planes: 1 },
  { id: "c2", titulo: "Media maratón", fin: "2026-08-16T11:00:00+00:00",
    turnos: 4, planes: 0 },
];
const responder = async (url) => {
  if (url.includes("/api/conversaciones")) return json({ conversaciones: charlas });
  if (url.includes("/api/planes")) return json({ planes: [], activo: null });
  if (url.includes("/api/corredor")) return json({ nombre: null });
  return json({ disponible: false });
};

(async () => {
  /* ------------------------------------------------------------ silencio */

  const primera = montar({ responder });
  await respirar();
  assert.equal(primera.S().silencio, false, "de casa, Vydor suena");
  assert.match(primera.$("#silencio").innerHTML, /i-sonido/);

  primera.entorno.cambiarSilencio();
  assert.equal(primera.S().silencio, true, "el botón lo silencia");
  assert.match(primera.$("#silencio").innerHTML, /i-silencio/, "y el icono lo dice");
  assert.equal(primera.$("#silencio").getAttribute("aria-pressed"), "true");
  assert.equal(primera.almacen.get("vydor-silencio"), "si", "la elección se guarda");

  primera.entorno.cambiarSilencio();
  assert.equal(primera.almacen.get("vydor-silencio"), "no", "y se puede deshacer");

  // Quien ya lo había silenciado vuelve y sigue en silencio.
  const vuelve = montar({ responder, almacenInicial: { "vydor-silencio": "si" } });
  await respirar();
  assert.equal(vuelve.S().silencio, true, "el silencio sobrevive a la recarga");
  assert.match(vuelve.$("#silencio").innerHTML, /i-silencio/);

  /* ------------------------------------------------------------- borrado */

  const { entorno, $, peticiones, S } = montar({ responder });
  await respirar();
  const lateral = () => $("#historial").innerHTML;

  assert.match(lateral(), /data-borrar="c1"/, "cada charla lleva su papelera");
  assert.ok(!/data-confirmar/.test(lateral()), "y no pregunta hasta que se pulsa");

  // Pulsar la papelera no borra: pregunta, y solo por esa charla.
  entorno.pedirBorrar("c1");
  await respirar(20);
  assert.equal(S().borrando, "c1");
  assert.match(lateral(), /¿Borrar la charla y su plan\?/, "pide confirmación");
  assert.match(lateral(), /data-confirmar="c1"/);
  assert.ok(!/data-confirmar="c2"/.test(lateral()), "solo la que se está borrando");
  assert.ok(!peticiones.some((p) => p.startsWith("DELETE")),
            "preguntar no puede borrar nada todavía");

  // Confirmar sí borra, y con el método que corresponde.
  await entorno.borrarConversacion("c1");
  assert.ok(peticiones.includes("DELETE /api/conversaciones/c1"),
            "la petición llega al servidor como DELETE");
  assert.equal(S().borrando, null, "y la fila deja de estar en duda");

  console.log("ok · silencio y borrado: 15 comprobaciones");
})().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
