/* Test de interfaz: el botón que elige de qué plan llegan los recordatorios.
 *
 * Existe porque se envió deshabilitado: se apagaba cuando el plan que miras ya
 * era el activo, y como al abrir la vista casi siempre estás en el más
 * reciente, no accionaba nunca.
 *
 *   node tests/frontend/boton-recordatorios.js
 */
const assert = require("node:assert");
const { montar, json, respirar } = require("./lib/dom-minimo");

const planes = [1, 2, 3, 4, 5, 6].map((id) => ({
  id,
  conversacion: id <= 2 ? "c1" : id <= 4 ? "c2" : "c3",
  creado: "2026-08-16T10:00:00+00:00", distancia: "3k", semanas: 4,
}));
let activo = 6;                     // sin elección explícita: el más reciente

const { entorno, $, peticiones, S } = montar({
  responder: async (url) => {
    if (url.includes("/activo")) {
      activo = Number(url.match(/planes\/(\d+)/)[1]);
      return json({ activo });
    }
    if (url.includes("/api/planes")) return json({ planes, activo });
    if (url.includes("/api/conversaciones")) return json({ conversaciones: [] });
    return json({ disponible: false });
  },
});

const boton = $("#activar");

(async () => {
  await respirar();

  assert.equal(S().totalPlanes, 6, "la interfaz debe contar los planes guardados");
  assert.equal(S().planActivo, 6, "sin elegir nada, manda el más reciente");

  // Mirando el plan que ya manda los recordatorios: no hay nada que hacer,
  // pero el botón lo dice en vez de quedarse mudo.
  S().planId = 6;
  entorno.pintarBotonActivar();
  assert.equal(boton.hidden, false);
  assert.equal(boton.disabled, true);
  assert.match(boton.textContent, /Recibes los recordatorios/);

  // Mirando otro plan: esto es lo que estaba roto.
  S().planId = 5;
  entorno.pintarBotonActivar();
  assert.equal(boton.hidden, false, "con otro plan el botón debe verse");
  assert.equal(boton.disabled, false, "y debe poder pulsarse");

  await entorno.activarPlan();
  assert.ok(peticiones.includes("POST /api/planes/5/activo"),
            "el clic tiene que llegar al servidor");
  assert.equal(S().planActivo, 5, "y el estado debe reflejar la elección");
  assert.equal(boton.disabled, true, "el botón pasa a ser estado, no acción");

  // Con una sola meta no hay elección posible: el botón sobra.
  S().totalPlanes = 1;
  entorno.pintarBotonActivar();
  assert.equal(boton.hidden, true, "con un solo plan el botón debe ocultarse");

  // Un plan recién generado aún no tiene id: nada sobre lo que actuar.
  S().totalPlanes = 6;
  S().planId = null;
  entorno.pintarBotonActivar();
  assert.equal(boton.hidden, true, "sin id no hay plan sobre el que actuar");

  console.log("ok · botón de recordatorios: 6 comprobaciones");
})().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
