/* Test de interfaz: el enlace para vincular Telegram.
 *
 * Existe porque el bloque vivía dentro de la vista de Plan: desde el chat el
 * botón sencillamente no estaba, y pulsarlo "no hacía nada" porque no había
 * nada que pulsar. Ahora se pinta con el historial, en el panel lateral, que
 * se ve desde cualquier pestaña.
 *
 *   node tests/frontend/enlace-telegram.js
 */
const assert = require("node:assert");
const { montar, json, respirar } = require("./lib/dom-minimo");

const { entorno, $, S } = montar({
  responder: async (url) => {
    if (url.includes("/telegram/enlace"))
      return json({ disponible: true, vinculado: false,
                    url: "https://t.me/Vydor_bot?start=abc123" });
    if (url.includes("/api/planes")) return json({ planes: [], activo: null });
    return json({ conversaciones: [] });
  },
});

const lateral = () => $("#historial").innerHTML;

(async () => {
  await respirar();

  // Sin vincular: hay que poder llegar a Telegram desde el panel lateral,
  // esté el corredor en la pestaña que esté.
  assert.match(lateral(), /Telegram/, "el bloque debe pintarse en el lateral");
  assert.match(lateral(), /href="https:\/\/t\.me\/Vydor_bot\?start=abc123"/,
               "el enlace debe apuntar a t.me con el código");
  assert.match(lateral(), /target="_blank"/, "debe abrirse en una pestaña nueva");
  assert.ok(!/<a[^>]*>\s*<\/a>/.test(lateral()), "el enlace no puede quedarse vacío");

  // Ya vinculado: no tiene sentido invitarle a vincularse como si nada.
  S().telegram.vinculado = true;
  await entorno.cargarHistorial();
  assert.match(lateral(), /Recibes el entrenamiento cada mañana/,
               "estando vinculado debe decirlo");
  assert.match(lateral(), /Vincular otro teléfono/,
               "pero el enlace sigue a mano por si cambia de teléfono");

  console.log("ok · enlace de Telegram: 6 comprobaciones");
})().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
