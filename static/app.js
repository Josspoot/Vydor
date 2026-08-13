/**
 * Cliente de Vydor.
 *
 * Tres vistas sobre los mismos datos —la charla, el plan desglosado y el
 * calendario— más el historial, que es lo que permite tener varios planes a la
 * vez y retomar una conversación días después.
 */

/* ==================================================== estado y utilidades */

const S = {
  corredor: null,
  conversacion: null,      // charla activa; null = todavía sin abrir ninguna
  plan: null,
  planInicio: null,        // desde cuándo cuenta el plan, para el calendario
  semanaVista: 0,
  mesVista: 0,
  vista: "chat",
};

let ws, ctxEntrada, ctxSalida, micro, worklet;
let activo = false, hayVoz = false, estadoTurno = "escuchando";
let siguienteInicio = 0, finReproduccion = 0, fuentes = [], medidor = null;
let ultimoQuien = null;

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

function esc(t) {
  return String(t ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.round(s) % 60).padStart(2, "0")}`;

function tiempoHablado(seg) {
  const h = Math.floor(seg / 3600);
  const m = Math.round((seg % 3600) / 60);
  return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m} min`;
}

function fechaCorta(iso) {
  const d = new Date(iso);
  const dias = Math.floor((Date.now() - d) / 86400000);
  if (dias === 0) return "hoy";
  if (dias === 1) return "ayer";
  if (dias < 7) return `hace ${dias} días`;
  return d.toLocaleDateString("es-MX", { day: "numeric", month: "short" });
}

/* ======================================= vocabulario de entrenador a llano */

const NOMBRE_DIA = ["", "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"];
const DIA_CORTO = ["L", "M", "X", "J", "V", "S", "D"];
const MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
               "agosto", "septiembre", "octubre", "noviembre", "diciembre"];

const NOMBRE_TIPO = {
  descanso: "Descanso", facil: "Rodaje suave", largo: "Fondo largo",
  tempo: "Ritmo controlado", intervalos: "Series rápidas",
  ritmo_objetivo: "Ritmo de carrera", carrera: "¡Día de carrera!",
};
const TIPO_CORTO = {
  descanso: "—", facil: "Suave", largo: "Fondo", tempo: "Tempo",
  intervalos: "Series", ritmo_objetivo: "Ritmo", carrera: "CARRERA",
};
const NOMBRE_ZONA = {
  facil: "Fácil", maraton: "Ritmo de maratón", umbral: "Umbral",
  intervalo: "Intervalos", repeticion: "Repeticiones",
};
const GRUPO_FASE = {
  base: "normal", construccion: "normal", descarga: "recuperacion",
  afinamiento: "afinamiento", carrera: "afinamiento",
};
const NOMBRE_FASE = {
  base: "Base", construccion: "Construcción", descarga: "Semana de recuperación",
  afinamiento: "Afinamiento", carrera: "Semana de carrera",
};
const LEYENDA = [
  ["normal", "Semanas que suman carga"],
  ["recuperacion", "Recuperación: bajas para asimilar"],
  ["afinamiento", "Afinamiento y carrera"],
];
const VEREDICTO = {
  viable: { icono: "✓", titulo: "El objetivo es realista" },
  ajustado: { icono: "!", titulo: "Se puede, pero con margen justo" },
  no_recomendado: { icono: "✕", titulo: "Este objetivo es arriesgado" },
  demasiado_largo: { icono: "!", titulo: "Ese plazo es más largo de lo útil" },
};

/* ================================================================ vistas */

function cambiarVista(vista) {
  S.vista = vista;
  $$(".pestana").forEach((b) => b.setAttribute("aria-selected", b.dataset.vista === vista));
  $$(".vista").forEach((v) => (v.hidden = v.id !== `vista-${vista}`));
  if (vista === "calendario") pintarCalendario();
}

/* ============================================================= historial */

async function cargarHistorial() {
  try {
    const [rc, rp] = await Promise.all([
      fetch(`/api/conversaciones?corredor=${S.corredor}`),
      fetch(`/api/planes?corredor=${S.corredor}`),
    ]);
    pintarHistorial((await rc.json()).conversaciones, (await rp.json()).planes);
  } catch {
    /* sin historial la app sigue sirviendo para una charla nueva */
  }
}

function pintarHistorial(charlas, planes) {
  const lista = charlas.length
    ? charlas.map((c) => `
        <button class="item ${c.id === S.conversacion ? "activo" : ""}"
                data-charla="${esc(c.id)}">
          <span class="titulo">${esc(c.titulo || "Sin título")}</span>
          <span class="meta">${fechaCorta(c.fin)} · ${c.turnos} turnos${
            c.planes ? ` · ${c.planes} plan${c.planes > 1 ? "es" : ""}` : ""}</span>
        </button>`).join("")
    : `<p class="vacio pequeno">Todavía no hay conversaciones guardadas.</p>`;

  const listaPlanes = planes.length
    ? planes.map((p) => `
        <button class="item ${p.id === S.planId ? "activo" : ""}" data-plan="${p.id}">
          <span class="titulo">${esc((p.distancia || "").toUpperCase())} · ${p.semanas} semanas</span>
          <span class="meta">${fechaCorta(p.creado)}${p.vdot ? ` · VDOT ${p.vdot}` : ""}</span>
        </button>`).join("")
    : `<p class="vacio pequeno">Aquí se guardarán tus planes.</p>`;

  $("#historial").innerHTML = `
    <button id="nueva" class="nueva">+ Nueva conversación</button>
    <h3>Conversaciones</h3>${lista}
    <h3>Planes guardados</h3>${listaPlanes}`;

  $("#nueva").onclick = nuevaConversacion;
  $$("#historial [data-charla]").forEach((b) =>
    (b.onclick = () => abrirConversacion(b.dataset.charla)));
  $$("#historial [data-plan]").forEach((b) =>
    (b.onclick = () => abrirPlan(+b.dataset.plan)));
}

function nuevaConversacion() {
  if (activo) detener();
  S.conversacion = null;
  S.plan = null;
  S.planId = null;
  ultimoQuien = null;
  $("#conversacion").innerHTML = plantillaChatVacio();
  $("#plan").innerHTML = `<p class="vacio">Cuéntale a Vydor qué carrera tienes en mente
    y aquí aparecerá tu plan.</p>`;
  cambiarVista("chat");
  cargarHistorial();
}

async function abrirConversacion(id) {
  if (activo) detener();
  S.conversacion = id;
  ultimoQuien = null;
  const r = await fetch(`/api/conversaciones/${id}?corredor=${S.corredor}`);
  const { turnos } = await r.json();

  $("#conversacion").innerHTML = "";
  turnos.forEach((t) => escribirTurno(t.quien, t.texto, true));
  if (!turnos.length) $("#conversacion").innerHTML = plantillaChatVacio();

  // Si de esa charla salió un plan, se abre con ella.
  const planes = (await (await fetch(`/api/planes?corredor=${S.corredor}`)).json()).planes;
  const suyo = planes.find((p) => p.conversacion === id);
  if (suyo) await abrirPlan(suyo.id, false);

  cambiarVista("chat");
  cargarHistorial();
}

async function abrirPlan(id, cambiar = true) {
  const r = await fetch(`/api/planes/${id}?corredor=${S.corredor}`);
  if (!r.ok) return;
  const { plan } = await r.json();
  S.planId = id;
  const planes = (await (await fetch(`/api/planes?corredor=${S.corredor}`)).json()).planes;
  const meta = planes.find((p) => p.id === id);
  dibujarPlan(plan, meta ? new Date(meta.creado) : new Date());
  if (cambiar) cambiarVista("plan");
  cargarHistorial();
}

function plantillaChatVacio() {
  return `<p class="vacio">Pulsa <strong>Hablar con Vydor</strong> y cuéntale qué
    carrera tienes en mente.</p>
    <ul class="ejemplos">
      <li>Quiero correr un 10K en 12 semanas</li>
      <li>Corro 25 kilómetros por semana y puedo entrenar 4 días</li>
      <li>Me duele la rodilla desde el martes</li>
    </ul>`;
}

/* ============================================================== el plan */

function dibujarPlan(plan, inicio = new Date()) {
  S.plan = plan;
  S.planInicio = inicio;
  S.semanaVista = 0;
  S.mesVista = 0;
  $("#plan").innerHTML = seccionResumen(plan) + seccionRitmos(plan) +
                         seccionGrafica(plan) + '<div class="bloque" id="detalleSemana"></div>';
  conectarGrafica();
  pintarSemana();
  $("#accionesPlan").hidden = false;
}

function seccionResumen(plan) {
  const v = plan.viabilidad;
  const meta = VEREDICTO[v.veredicto] || VEREDICTO.ajustado;
  const objetivo = plan.tiempo_objetivo_s
    ? `<div class="cifra"><span class="valor">${tiempoHablado(plan.tiempo_objetivo_s)}</span>
         <span class="rotulo">Tiempo alcanzable</span></div>` : "";
  return `
    <div class="hero">
      <div class="cifra"><span class="valor">${esc(plan.distancia.toUpperCase())}</span>
        <span class="rotulo">Tu carrera</span></div>
      <div class="cifra"><span class="valor">${plan.semanas}</span>
        <span class="rotulo">Semanas de plan</span></div>
      <div class="cifra"><span class="valor">${plan.dias_por_semana}</span>
        <span class="rotulo">Días por semana</span></div>
      ${objetivo}
    </div>
    <div class="veredicto ${v.veredicto}">
      <span class="icono">${meta.icono}</span>
      <span><b>${meta.titulo}</b>${esc(v.razon)}</span>
    </div>`;
}

function seccionRitmos(plan) {
  const zonas = Object.entries(plan.ritmos || {});
  if (!zonas.length) {
    return `<div class="bloque"><h3>Tus ritmos</h3>
      <p class="pie">Todavía no los tenemos. Dile a Vydor una marca reciente
      —por ejemplo, cuánto tardaste en tu último 10K— y calculará a qué ritmo
      debes correr cada tipo de entrenamiento.</p></div>`;
  }
  const tarjetas = zonas.map(([zona, r]) => `
    <div class="ritmo">
      <div class="cab"><span class="zona">${esc(NOMBRE_ZONA[zona] || zona)}</span>
        <span class="valor">${esc(formatoRitmo(r))}</span></div>
      <div class="desc">${esc(r.descripcion)}</div>
    </div>`).join("");
  return `<div class="bloque">
    <h3>Tus ritmos${plan.vdot ? ` <span class="apagado">· VDOT ${plan.vdot}</span>` : ""}</h3>
    <p class="pie">Minutos por kilómetro. Salen de tu marca real, no de una tabla genérica.</p>
    <div class="ritmos">${tarjetas}</div></div>`;
}

function formatoRitmo(r) {
  return r.seg_por_km_rapido === r.seg_por_km_lento
    ? mmss(r.seg_por_km_rapido)
    : `${mmss(r.seg_por_km_rapido)}–${mmss(r.seg_por_km_lento)}`;
}

function seccionGrafica(plan) {
  const semanas = plan.semanas_plan;
  const techo = Math.ceil(Math.max(...semanas.map((s) => s.km_total)) / 10) * 10;
  const barras = semanas.map((s, i) => `
    <div class="barra ${GRUPO_FASE[s.fase]}" style="height:${Math.max(3, (s.km_total / techo) * 100)}%"
         data-i="${i}" role="button" tabindex="0"
         aria-label="Semana ${s.numero}, ${s.km_total} kilómetros, ${NOMBRE_FASE[s.fase]}"></div>`).join("");
  const paso = semanas.length > 12 ? 4 : 2;
  const ejeX = semanas.map((s, i) =>
    `<span>${i === 0 || (i + 1) % paso === 0 ? s.numero : ""}</span>`).join("");
  const leyenda = LEYENDA.map(([c, t]) =>
    `<span><i style="background:var(--fase-${c})"></i>${t}</span>`).join("");
  return `<div class="bloque">
    <h3>Cómo avanza tu plan</h3>
    <p class="pie">Kilómetros por semana. No sube en línea recta a propósito:
      cada pocas semanas baja para que el cuerpo asimile la carga.</p>
    <div class="grafica">
      <div class="ejeY"><span style="top:0">${techo}</span><span style="top:50%">${techo / 2}</span></div>
      <div class="lienzo" id="lienzo">
        <div class="rejilla"><i style="top:0"></i><i style="top:50%"></i></div>${barras}
      </div>
      <div class="ejeX">${ejeX}</div>
    </div>
    <div class="leyenda">${leyenda}</div></div>`;
}

function conectarGrafica() {
  $$("#lienzo .barra").forEach((barra) => {
    const i = +barra.dataset.i;
    const elegir = () => { S.semanaVista = i; pintarSemana(); };
    barra.addEventListener("click", elegir);
    barra.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); elegir(); }
    });
    barra.addEventListener("mouseenter", (e) => mostrarGlobo(e, i));
    barra.addEventListener("mousemove", colocarGlobo);
    barra.addEventListener("mouseleave", () => ($("#globo").style.opacity = 0));
  });
}

function mostrarGlobo(ev, i) {
  const s = S.plan.semanas_plan[i];
  $("#globo").innerHTML = `<b>Semana ${s.numero} · ${NOMBRE_FASE[s.fase]}</b>
    <span class="m">${s.km_total.toFixed(1)} km en total` +
    (s.km_fondo > 0 ? ` · fondo de ${s.km_fondo.toFixed(1)} km` : "") + `</span>`;
  $("#globo").style.opacity = 1;
  colocarGlobo(ev);
}

function colocarGlobo(ev) {
  const g = $("#globo");
  g.style.left = Math.min(Math.max(ev.clientX - g.offsetWidth / 2, 8),
                          innerWidth - g.offsetWidth - 8) + "px";
  g.style.top = (ev.clientY - g.offsetHeight - 12) + "px";
}

function filaDia(ses) {
  const clase = ses.tipo === "descanso" ? "descanso"
    : ses.tipo === "carrera" ? "carrera"
    : ["tempo", "intervalos", "ritmo_objetivo", "largo"].includes(ses.tipo) ? "clave" : "";
  return `<div class="dia ${clase}">
    <span class="nombre">${NOMBRE_DIA[ses.dia]}</span>
    <span class="que">${esc(NOMBRE_TIPO[ses.tipo] || ses.tipo)}
      <span class="detalle">${esc(ses.detalle)}</span></span>
    <span class="km">${ses.km > 0 ? `${ses.km.toFixed(1)} km` : ""}</span>
  </div>`;
}

function pintarSemana() {
  const destino = $("#detalleSemana");
  if (!destino || !S.plan) return;
  const semanas = S.plan.semanas_plan;
  const s = semanas[S.semanaVista];

  $$(".barra").forEach((b) => b.classList.toggle("sel", +b.dataset.i === S.semanaVista));

  destino.innerHTML = `
    <h3>Semana a semana</h3>
    <p class="pie">Toca una semana para ver qué toca cada día.</p>
    <div class="fichas">${semanas.map((x, i) =>
      `<button class="ficha" data-i="${i}" aria-pressed="${i === S.semanaVista}">${x.numero}</button>`).join("")}</div>
    <div class="resumenSemana">
      <span>Semana ${s.numero} de ${S.plan.semanas} · <b>${NOMBRE_FASE[s.fase]}</b></span>
      <span>Total: <b>${s.km_total.toFixed(1)} km</b></span>
      ${s.km_fondo > 0 ? `<span>Fondo: <b>${s.km_fondo.toFixed(1)} km</b></span>` : ""}
    </div>
    <div class="dias">${s.sesiones.map(filaDia).join("")}</div>`;

  destino.querySelectorAll(".ficha").forEach((f) =>
    (f.onclick = () => { S.semanaVista = +f.dataset.i; pintarSemana(); }));
}

/* ========================================================== calendario */

/** Lunes de la semana en que arranca el plan: la rejilla cuelga de ahí. */
function lunesInicial() {
  const d = new Date(S.planInicio);
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
  return d;
}

/** Sesión que corresponde a una fecha, o null si cae fuera del plan. */
function sesionEnFecha(fecha) {
  const dias = Math.floor((fecha - lunesInicial()) / 86400000);
  if (dias < 0) return null;
  const semana = S.plan.semanas_plan[Math.floor(dias / 7)];
  if (!semana) return null;
  const sesion = semana.sesiones.find((s) => s.dia === (dias % 7) + 1);
  return sesion ? { ...sesion, semana } : null;
}

function pintarCalendario() {
  const destino = $("#vista-calendario");
  if (!S.plan) {
    destino.innerHTML = `<div class="panel"><p class="vacio">El calendario aparece
      cuando tengas un plan.</p></div>`;
    return;
  }
  const inicio = lunesInicial();
  const fin = new Date(inicio);
  fin.setDate(fin.getDate() + S.plan.semanas_plan.length * 7 - 1);

  // Meses que toca el plan, para poder navegar entre ellos.
  const meses = [];
  const cursor = new Date(inicio.getFullYear(), inicio.getMonth(), 1);
  while (cursor <= fin) {
    meses.push(new Date(cursor));
    cursor.setMonth(cursor.getMonth() + 1);
  }
  S.mesVista = Math.max(0, Math.min(S.mesVista, meses.length - 1));
  const mes = meses[S.mesVista];

  const primero = new Date(mes.getFullYear(), mes.getMonth(), 1);
  const desde = new Date(primero);
  desde.setDate(desde.getDate() - ((desde.getDay() + 6) % 7));

  const hoy = new Date(); hoy.setHours(0, 0, 0, 0);
  let celdas = "";
  for (let i = 0; i < 42; i++) {
    const dia = new Date(desde);
    dia.setDate(dia.getDate() + i);
    const otroMes = dia.getMonth() !== mes.getMonth();
    const s = sesionEnFecha(dia);
    const esHoy = dia.getTime() === hoy.getTime();
    celdas += `
      <div class="celda ${otroMes ? "fuera" : ""} ${esHoy ? "hoy" : ""} ${
        s ? GRUPO_FASE[s.semana.fase] : "vacia"}"
        ${s ? `data-semana="${s.semana.numero - 1}" role="button" tabindex="0"` : ""}>
        <span class="num">${dia.getDate()}</span>
        ${s ? `<span class="et">${TIPO_CORTO[s.tipo] || ""}</span>
               ${s.km > 0 ? `<span class="k">${s.km.toFixed(0)} km</span>` : ""}` : ""}
      </div>`;
  }

  destino.innerHTML = `
    <div class="panel">
      <div class="cabMes">
        <button class="flecha" id="mesAnt" ${S.mesVista === 0 ? "disabled" : ""}>‹</button>
        <h2 class="tituloMes">${MESES[mes.getMonth()]} ${mes.getFullYear()}</h2>
        <button class="flecha" id="mesSig" ${S.mesVista === meses.length - 1 ? "disabled" : ""}>›</button>
      </div>
      <p class="pie">El plan se organiza por semanas; el mes es solo para verlo
        de un vistazo. Toca un día para abrir su semana.</p>
      <div class="rejillaMes">
        ${DIA_CORTO.map((d) => `<span class="cabDia">${d}</span>`).join("")}
        ${celdas}
      </div>
      <div class="leyenda">${LEYENDA.map(([c, t]) =>
        `<span><i style="background:var(--fase-${c})"></i>${t}</span>`).join("")}</div>
    </div>`;

  $("#mesAnt").onclick = () => { S.mesVista--; pintarCalendario(); };
  $("#mesSig").onclick = () => { S.mesVista++; pintarCalendario(); };
  destino.querySelectorAll("[data-semana]").forEach((c) => {
    c.onclick = () => { S.semanaVista = +c.dataset.semana; cambiarVista("plan"); pintarSemana(); };
  });
}

/* ================================================================== PDF */

function construirImpresion() {
  // Se imprime una vista aparte con TODAS las semanas desplegadas: en pantalla
  // se ve una cada vez, pero un PDF que solo trajera esa no serviría de nada.
  const plan = S.plan;
  const inicio = lunesInicial();
  const semanas = plan.semanas_plan.map((s, i) => {
    const desde = new Date(inicio); desde.setDate(desde.getDate() + i * 7);
    const hasta = new Date(desde); hasta.setDate(hasta.getDate() + 6);
    const rango = `${desde.getDate()} ${MESES[desde.getMonth()].slice(0, 3)} – ` +
                  `${hasta.getDate()} ${MESES[hasta.getMonth()].slice(0, 3)}`;
    return `<section class="semanaImpresa">
      <h2>Semana ${s.numero} · ${NOMBRE_FASE[s.fase]}</h2>
      <p class="metaImpresa">${rango} · ${s.km_total.toFixed(1)} km en total${
        s.km_fondo > 0 ? ` · fondo de ${s.km_fondo.toFixed(1)} km` : ""}</p>
      <table><tbody>${s.sesiones.map((ses) => `
        <tr class="${ses.tipo === "descanso" ? "descansoImpreso" : ""}">
          <td class="d">${NOMBRE_DIA[ses.dia]}</td>
          <td class="t"><b>${esc(NOMBRE_TIPO[ses.tipo] || ses.tipo)}</b><br>
            <span>${esc(ses.detalle)}</span></td>
          <td class="km">${ses.km > 0 ? `${ses.km.toFixed(1)} km` : ""}</td>
        </tr>`).join("")}</tbody></table>
    </section>`;
  }).join("");

  const ritmos = Object.entries(plan.ritmos || {});
  $("#paraImprimir").innerHTML = `
    <h1>Plan de entrenamiento · ${esc(plan.distancia.toUpperCase())}</h1>
    <p class="metaImpresa">${plan.semanas} semanas · ${plan.dias_por_semana} días por semana${
      plan.vdot ? ` · VDOT ${plan.vdot}` : ""}${
      plan.tiempo_objetivo_s ? ` · objetivo ${tiempoHablado(plan.tiempo_objetivo_s)}` : ""}</p>
    <p class="veredictoImpreso"><b>${(VEREDICTO[plan.viabilidad.veredicto] || {}).titulo || ""}</b>
      ${esc(plan.viabilidad.razon)}</p>
    ${ritmos.length ? `<h2>Ritmos de entrenamiento</h2>
      <table class="ritmosImpresos"><tbody>${ritmos.map(([z, r]) => `
        <tr><td class="d">${esc(NOMBRE_ZONA[z] || z)}</td>
            <td class="km">${esc(formatoRitmo(r))} /km</td>
            <td class="t">${esc(r.descripcion)}</td></tr>`).join("")}</tbody></table>` : ""}
    ${semanas}
    <p class="avisoImpreso">Orientación de entrenamiento, no consejo médico.
      Ante dolor persistente, consulta a un profesional de la salud.
      Generado por Vydor.</p>`;
}

function exportarPDF() {
  construirImpresion();
  print();
}

/* ============================================== voz, audio y conexión */

function coachOcupado() {
  if (estadoTurno === "pensando") return true;
  return !!ctxSalida && ctxSalida.currentTime < finReproduccion + 0.4;
}

function marcar(texto, clase = "") {
  $("#estado").textContent = texto;
  $("#punto").className = "punto " + clase;
}

function arrancarMedidor() {
  medidor = setInterval(() => {
    if (!activo) return;
    if (estadoTurno === "pensando") marcar("Vydor está pensando…", "activo");
    else if (coachOcupado()) marcar("Vydor está hablando…", "activo");
    else if (hayVoz) marcar("te escucho…", "activo");
    else marcar("listo, habla cuando quieras");
  }, 500);
}

async function iniciar() {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });

  const proto = location.protocol === "https:" ? "wss" : "ws";
  const charla = S.conversacion ? `&conversacion=${S.conversacion}` : "";
  ws = new WebSocket(`${proto}://${location.host}/ws?corredor=${S.corredor}${charla}`);
  ws.binaryType = "arraybuffer";
  ws.onmessage = (ev) =>
    typeof ev.data === "string" ? manejarJson(JSON.parse(ev.data)) : reproducir(ev.data);
  ws.onerror = () => marcar("error de conexión", "error");
  ws.onclose = () => { detener(); cargarHistorial(); };
  await new Promise((r) => (ws.onopen = r));

  // Tasa nativa del sistema: el worklet remuestrea a 16 kHz. Forzar el
  // contexto a 16 kHz falla en algunos navegadores según el hardware.
  ctxEntrada = new AudioContext();
  await ctxEntrada.audioWorklet.addModule("/static/captura-audio.js");
  micro = ctxEntrada.createMediaStreamSource(stream);
  worklet = new AudioWorkletNode(ctxEntrada, "captura-pcm");

  worklet.port.onmessage = (ev) => {
    if (ev.data.tipo === "inicio") {
      console.log(`captura a ${ev.data.tasaEntrada} Hz -> 16000 Hz`);
      return;
    }
    // Mientras el coach habla, el micrófono no cuenta: su propia voz vuelve a
    // entrar por los altavoces y la compuerta la tomaba por una interrupción.
    if (coachOcupado()) return;
    if (ev.data.tipo === "voz") {
      hayVoz = ev.data.activa;
      if (!hayVoz && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ tipo: "fin_turno" }));
      }
      return;
    }
    if (ws.readyState === WebSocket.OPEN) ws.send(ev.data);
  };

  micro.connect(worklet);
  // El grafo de Web Audio se evalúa desde el destino hacia atrás: un nodo sin
  // salida conectada puede no ejecutar process() nunca.
  const mudo = ctxEntrada.createGain();
  mudo.gain.value = 0;
  worklet.connect(mudo).connect(ctxEntrada.destination);

  ctxSalida = new AudioContext({ sampleRate: 24000 });
  siguienteInicio = finReproduccion = 0;
  activo = true;
  $("#boton").textContent = "Terminar";
  $("#boton").classList.add("detener");
  marcar("listo, habla cuando quieras", "activo");
  arrancarMedidor();
  cambiarVista("chat");
}

function detener() {
  if (!activo) return;
  activo = false;
  clearInterval(medidor);
  ws?.readyState === WebSocket.OPEN && ws.close();
  micro?.mediaStream?.getTracks().forEach((t) => t.stop());
  ctxEntrada?.close();
  ctxSalida?.close();
  $("#boton").textContent = "Hablar con Vydor";
  $("#boton").classList.remove("detener");
  marcar("desconectado");
}

function manejarJson(msg) {
  switch (msg.tipo) {
    case "conversacion":
      S.conversacion = msg.id;
      break;
    case "listo":
      estadoTurno = "escuchando";
      if (msg.conocido) escribirTurno("sistema", "Vydor ya te conoce de antes.");
      break;
    case "pensando":
      estadoTurno = "pensando";
      hayVoz = false;
      break;
    case "turno_listo":
      estadoTurno = "escuchando";
      worklet?.port.postMessage({ tipo: "reset" });
      cargarHistorial();
      break;
    case "transcripcion":
      escribirTurno(msg.quien, msg.texto);
      break;
    case "plan":
      S.planId = null;
      dibujarPlan(msg.plan, new Date());
      cambiarVista("plan");
      break;
    case "interrumpido":
      fuentes.forEach((f) => { try { f.stop(); } catch {} });
      fuentes = [];
      siguienteInicio = finReproduccion = 0;
      break;
    case "fin":
    case "error":
      marcar(msg.mensaje || msg.motivo, "error");
      escribirTurno("sistema", msg.mensaje || msg.motivo);
      break;
  }
}

function escribirTurno(quien, texto, completo = false) {
  const caja = $("#conversacion");
  caja.querySelector(".vacio")?.remove();
  caja.querySelector(".ejemplos")?.remove();
  // La API transcribe por fragmentos: se anexan al mismo turno salvo que
  // venga ya completo desde el historial.
  if (!completo && quien === ultimoQuien) {
    caja.lastElementChild.querySelector(".texto").textContent += texto;
  } else {
    const div = document.createElement("div");
    div.className = `turno ${quien}`;
    div.innerHTML = `<span class="quien">${
      quien === "corredor" ? "Tú" : quien === "coach" ? "Vydor" : "Sistema"
    }</span><span class="texto"></span>`;
    div.querySelector(".texto").textContent = texto;
    caja.appendChild(div);
    ultimoQuien = completo ? null : quien;
  }
  caja.scrollTop = caja.scrollHeight;
}

function reproducir(buffer) {
  if (!ctxSalida) return;
  const pcm = new Int16Array(buffer);
  const audio = ctxSalida.createBuffer(1, pcm.length, 24000);
  const canal = audio.getChannelData(0);
  for (let i = 0; i < pcm.length; i++) canal[i] = pcm[i] / 32768;

  const fuente = ctxSalida.createBufferSource();
  fuente.buffer = audio;
  fuente.connect(ctxSalida.destination);
  // Encolar en el tiempo evita cortes entre trozos consecutivos.
  siguienteInicio = Math.max(siguienteInicio, ctxSalida.currentTime);
  fuente.start(siguienteInicio);
  siguienteInicio += audio.duration;
  finReproduccion = siguienteInicio;
  fuentes.push(fuente);
  fuente.onended = () => (fuentes = fuentes.filter((f) => f !== fuente));
}

/* ============================================================= arranque */

(function inicio() {
  S.corredor = localStorage.getItem("vydor-corredor");
  if (!S.corredor) {
    S.corredor = crypto.randomUUID();
    localStorage.setItem("vydor-corredor", S.corredor);
  }

  $("#boton").onclick = () =>
    activo ? detener() : iniciar().catch((e) => marcar(e.message, "error"));
  $$(".pestana").forEach((b) => (b.onclick = () => cambiarVista(b.dataset.vista)));
  $("#pdf").onclick = exportarPDF;
  $("#abrirHistorial").onclick = () => document.body.classList.toggle("historialAbierto");

  cargarHistorial();

  // El bloque de Telegram solo aparece si el servidor tiene bot configurado:
  // ofrecer un botón que no lleva a ningún sitio es peor que no ofrecerlo.
  fetch(`/telegram/enlace?corredor=${S.corredor}`)
    .then((r) => r.json())
    .then((d) => {
      if (!d.disponible) return;
      $("#enlaceTelegram").href = d.url;
      $("#telegram").hidden = false;
    })
    .catch(() => {});
})();
