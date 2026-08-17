/* Test de interfaz: el botón que elige de qué plan llegan los recordatorios.
 *
 * Carga el static/app.js REAL —no una copia de su lógica— sobre un DOM
 * mínimo. El proyecto no lleva navegador ni framework de front, y montar uno
 * para un puñado de funciones costaría más de lo que resuelve; esto cabe en
 * un archivo y corre en medio segundo.
 *
 * Existe porque el botón se envió deshabilitado: se apagaba cuando el plan
 * que miras ya era el activo, y como al abrir la vista casi siempre estás en
 * el más reciente, no accionaba nunca.
 *
 *   node tests/frontend/boton-recordatorios.js
 */
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const APP = path.join(__dirname, "..", "..", "static", "app.js");

/* ------------------------------------------------------------ DOM mínimo */

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

const peticiones = [];
const planes = [1, 2, 3, 4, 5, 6].map((id) => ({
  id,
  conversacion: id <= 2 ? "c1" : id <= 4 ? "c2" : "c3",
  creado: "2026-08-16T10:00:00+00:00", distancia: "3k", semanas: 4,
}));
let activo = 6;                     // sin elección explícita: el más reciente

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
  fetch: async (url, opciones) => {
    peticiones.push(`${opciones?.method || "GET"} ${url.split("?")[0]}`);
    if (url.includes("/activo")) {
      activo = Number(url.match(/planes\/(\d+)/)[1]);
      return { ok: true, json: async () => ({ activo }) };
    }
    if (url.includes("/api/planes"))
      return { ok: true, json: async () => ({ planes, activo }) };
    if (url.includes("/api/conversaciones"))
      return { ok: true, json: async () => ({ conversaciones: [] }) };
    return { ok: true, json: async () => ({ disponible: false }) };
  },
};
entorno.globalThis = entorno;
vm.createContext(entorno);

// `const S` vive en el ámbito léxico del script y no llega al objeto global:
// se expone con una línea añadida al final. Lo que se prueba no se toca.
vm.runInContext(fs.readFileSync(APP, "utf8") + "\nglobalThis.__S = S;", entorno);

/* --------------------------------------------------------------- pruebas */

const boton = $("#activar");

(async () => {
  await new Promise((r) => setTimeout(r, 50));    // deja correr cargarHistorial
  const S = entorno.__S;

  assert.equal(S.totalPlanes, 6, "la interfaz debe contar los planes guardados");
  assert.equal(S.planActivo, 6, "sin elegir nada, manda el más reciente");

  // Mirando el plan que ya manda los recordatorios: no hay nada que hacer,
  // pero el botón lo dice en vez de quedarse mudo.
  S.planId = 6;
  entorno.pintarBotonActivar();
  assert.equal(boton.hidden, false);
  assert.equal(boton.disabled, true);
  assert.match(boton.textContent, /Recibes los recordatorios/);

  // Mirando otro plan: esto es lo que estaba roto.
  S.planId = 5;
  entorno.pintarBotonActivar();
  assert.equal(boton.hidden, false, "con otro plan el botón debe verse");
  assert.equal(boton.disabled, false, "y debe poder pulsarse");

  await entorno.activarPlan();
  assert.ok(peticiones.includes("POST /api/planes/5/activo"),
            "el clic tiene que llegar al servidor");
  assert.equal(S.planActivo, 5, "y el estado debe reflejar la elección");
  assert.equal(boton.disabled, true, "el botón pasa a ser estado, no acción");

  // Con una sola meta no hay elección posible: el botón sobra.
  S.totalPlanes = 1;
  entorno.pintarBotonActivar();
  assert.equal(boton.hidden, true, "con un solo plan el botón debe ocultarse");

  // Un plan recién generado aún no tiene id: nada sobre lo que actuar.
  S.totalPlanes = 6;
  S.planId = null;
  entorno.pintarBotonActivar();
  assert.equal(boton.hidden, true, "sin id no hay plan sobre el que actuar");

  console.log("ok · botón de recordatorios: 6 comprobaciones");
})().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
