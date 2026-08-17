/* DOM mínimo para ejecutar el static/app.js REAL dentro de node.
 *
 * El proyecto no lleva navegador de pruebas ni framework de front: montar uno
 * para un puñado de funciones costaría más de lo que resuelve. Esto basta para
 * lo que se rompe de verdad —estados de botones, qué se pinta, qué se pide al
 * servidor— y corre en milisegundos.
 *
 * Lo que NO cubre: audio, WebSocket y maquetado. Para eso hace falta un
 * navegador de verdad, y conviene saberlo antes de confiarse.
 */
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const APP = path.join(__dirname, "..", "..", "..", "static", "app.js");

function montar({ responder, almacenInicial = {} } = {}) {
  const elementos = new Map();
  // Las clases y los atributos se guardan de verdad: hay comportamiento que
  // solo se puede comprobar mirándolos —si el chat está vacío, si el cajón
  // está abierto—, y un classList de adorno lo daría siempre por bueno.
  const nuevoElemento = (sel) => {
    const clases = new Set();
    const atributos = new Map();
    return {
      sel, hidden: false, disabled: false, textContent: "", innerHTML: "",
      href: "", value: "", title: "", dataset: {}, style: {}, clases, atributos,
      classList: {
        add: (c) => clases.add(c),
        remove: (c) => clases.delete(c),
        contains: (c) => clases.has(c),
        toggle: (c, forzar) =>
          (forzar ?? !clases.has(c)) ? clases.add(c) : clases.delete(c),
      },
      setAttribute: (k, v) => atributos.set(k, String(v)),
      getAttribute: (k) => (atributos.has(k) ? atributos.get(k) : null),
      addEventListener() {}, appendChild() {}, focus() {},
      requestSubmit() {},
      // innerHTML es una cadena, no un árbol: aquí no se puede buscar un nodo
      // de verdad, así que se devuelve uno de mentira para que el código que
      // encadena (`.querySelector(...).textContent = x`) no reviente. Lo que
      // este arnés comprueba son las clases, los atributos y lo que se pinta,
      // nunca la vida de los nodos.
      querySelector: () => nuevoElemento("(hijo)"), remove() {},
      get lastElementChild() { return nuevoElemento("(último)"); },
      getContext: () => null,
      getBoundingClientRect: () => ({ width: 300, height: 100 }),
    };
  };
  const $ = (sel) => {
    if (!elementos.has(sel)) elementos.set(sel, nuevoElemento(sel));
    return elementos.get(sel);
  };

  // localStorage de verdad, con estado: hay preferencias que solo importan
  // porque sobreviven a la recarga.
  const almacen = new Map(Object.entries(almacenInicial));
  const raiz = { dataset: {} };                 // <html>, donde vive el tema
  const cuerpo = nuevoElemento("body");         // <body>, donde vive el cajón
  const peticiones = [];

  const entorno = {
    console,
    localStorage: {
      getItem: (k) => (almacen.has(k) ? almacen.get(k) : null),
      setItem: (k, v) => almacen.set(k, String(v)),
      removeItem: (k) => almacen.delete(k),
    },
    crypto: { randomUUID: () => "corredor-x" },
    setTimeout, clearTimeout, setInterval, clearInterval,
    document: {
      documentElement: raiz,
      querySelector: $, querySelectorAll: () => [],
      body: cuerpo,
      createElement: nuevoElemento, addEventListener() {},
    },
    window: { matchMedia: () => ({ matches: false, addEventListener() {} }) },
    fetch: async (url, opciones) => {
      peticiones.push(`${opciones?.method || "GET"} ${url.split("?")[0]}`);
      return responder(url, opciones);
    },
  };
  entorno.globalThis = entorno;
  vm.createContext(entorno);

  // `const S` vive en el ámbito léxico del script y no llega al objeto global:
  // se expone con una línea añadida al final. Lo que se prueba no se toca.
  vm.runInContext(fs.readFileSync(APP, "utf8") + "\nglobalThis.__S = S;", entorno);

  return { entorno, $, peticiones, almacen, raiz, cuerpo, S: () => entorno.__S };
}

const json = (datos) => ({ ok: true, json: async () => datos });
const respirar = (ms = 50) => new Promise((r) => setTimeout(r, ms));

module.exports = { montar, json, respirar };
