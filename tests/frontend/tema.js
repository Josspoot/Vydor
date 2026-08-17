/* Test de interfaz: el cambio entre tema claro y oscuro.
 *
 * Lo que importa no es que el botón cambie de icono, sino que la elección
 * sobreviva a la recarga: un tema que hay que volver a poner cada vez es peor
 * que no tenerlo.
 *
 *   node tests/frontend/tema.js
 */
const assert = require("node:assert");
const { montar, json, respirar } = require("./lib/dom-minimo");

const responder = async () => json({ conversaciones: [], planes: [], activo: null,
                                     disponible: false });

(async () => {
  // Primera visita: sin nada guardado, manda el claro.
  const primera = montar({ responder });
  await respirar();
  assert.equal(primera.raiz.dataset.theme, undefined,
               "el tema de casa es el claro: <html> sin data-theme");
  assert.match(primera.$("#tema").innerHTML, /i-luna/,
               "el icono anuncia a dónde vas, no dónde estás");

  primera.entorno.cambiarTema();
  assert.equal(primera.raiz.dataset.theme, "dark", "al pulsar se va al oscuro");
  assert.match(primera.$("#tema").innerHTML, /i-sol/, "y el icono ofrece volver");
  assert.equal(primera.almacen.get("vydor-tema"), "oscuro", "la elección se guarda");

  primera.entorno.cambiarTema();
  assert.equal(primera.raiz.dataset.theme, undefined, "y se puede volver al claro");
  assert.equal(primera.almacen.get("vydor-tema"), "claro");

  // Segunda visita de quien había elegido oscuro. El <head> aplica el tema
  // antes de pintar —por eso raiz llega ya marcada—; aquí se comprueba que la
  // interfaz arranca coherente con eso y no lo pisa.
  const vuelve = montar({ responder, almacenInicial: { "vydor-tema": "oscuro" } });
  vuelve.raiz.dataset.theme = "dark";
  vuelve.entorno.pintarBotonTema();
  await respirar();
  assert.equal(vuelve.raiz.dataset.theme, "dark", "no se le pisa su elección");
  assert.match(vuelve.$("#tema").innerHTML, /i-sol/);

  console.log("ok · tema claro/oscuro: 9 comprobaciones");
})().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
