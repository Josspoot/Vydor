/* Test de interfaz: el enlace para vincular Telegram.
 *
 * Existe porque el bloque vivía dentro de la vista de Plan: desde el chat el
 * botón sencillamente no estaba, y pulsarlo "no hacía nada" porque no había
 * nada que pulsar. Ahora se pinta en el panel lateral, que se ve siempre, así
 * que el test comprueba que sale con el historial y que es un enlace de
 * verdad hacia t.me y no un botón que espera JavaScript.
 *
 *   node tests/frontend/enlace-telegram.js
 */
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const APP = path.join(__dirname, "..", "..", "static", "app.js");

const elementos = new Map();
const nuevoElemento = (sel) => ({
  sel, hidden: false, disabled: false, textContent: "", innerHTML: "",
  href: "", value: "", dataset: {}, style: {},
  classList: { add() {}, remove() {}, toggle() {} },
  addEventListener() {}, appendChild() {}, setAttribute() {}, focus() {},
  getContext: () => null,
  getBoundingClientRect: () => ({ width: 300, height: 100 }),
});
const $ = (sel) => {
  if (!elementos.has(sel)) elementos.set(sel, nuevoElemento(sel));
  return elementos.get(sel);
};

const entorno = {
  console,
  localStorage: { getItem: () => "corredor-x", setItem() {} },
  crypto: { randomUUID: () => "corredor-x" },
  setTimeout, clearTimeout, setInterval, clearInterval,
  document: {
    querySelector: $, querySelectorAll: () => [],
    body: { classList: { toggle() {} } },
    createElement: nuevoElemento, addEventListener() {},
  },
  window: { matchMedia: () => ({ matches: false, addEventListener() {} }) },
  fetch: async (url) => {
    if (url.includes("/telegram/enlace"))
      return { ok: true, json: async () => ({
        disponible: true, vinculado: false,
        url: "https://t.me/Vydor_bot?start=abc123",
      }) };
    if (url.includes("/api/planes"))
      return { ok: true, json: async () => ({ planes: [], activo: null }) };
    return { ok: true, json: async () => ({ conversaciones: [] }) };
  },
};
entorno.globalThis = entorno;
vm.createContext(entorno);
vm.runInContext(fs.readFileSync(APP, "utf8") + "\nglobalThis.__S = S;", entorno);

const lateral = () => $("#historial").innerHTML;

(async () => {
  await new Promise((r) => setTimeout(r, 50));

  // Sin vincular: hay que poder llegar a Telegram desde el panel lateral,
  // esté el corredor en la pestaña que esté.
  assert.match(lateral(), /Telegram/, "el bloque debe pintarse en el lateral");
  assert.match(lateral(), /href="https:\/\/t\.me\/Vydor_bot\?start=abc123"/,
               "el enlace debe apuntar a t.me con el código");
  assert.match(lateral(), /target="_blank"/, "debe abrirse en una pestaña nueva");
  assert.ok(!/id="enlaceTelegram"[^>]*>\s*<\/a>/.test(lateral()),
            "el enlace no puede quedarse vacío");

  // Ya vinculado: no tiene sentido invitarle a vincularse como si nada.
  entorno.__S.telegram.vinculado = true;
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
