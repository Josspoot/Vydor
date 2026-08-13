/**
 * AudioWorklet de captura con compuerta de voz.
 *
 * Corre en el hilo de audio y hace tres cosas:
 *
 * 1. Remuestrea de la tasa nativa del micrófono (44.1 o 48 kHz en la mayoría
 *    de los Mac) a los 16 kHz que pide la Live API. Forzar el AudioContext a
 *    16 kHz parece más simple, pero según el navegador falla o deforma la
 *    señal cuando el hardware no soporta esa tasa.
 *
 * 2. Convierte a PCM de 16 bits little-endian.
 *
 * 3. Delimita los turnos. Como el servidor abre una sesión de Gemini por cada
 *    intervención, alguien tiene que decidir dónde empieza y dónde acaba: eso
 *    es esta compuerta. Solo se envía audio mientras hay voz, y al cerrarse
 *    avisa para que el servidor procese el turno.
 */
const TASA_DESTINO = 16000;
const MUESTRAS_POR_ENVIO = 1600;      // 100 ms a 16 kHz

// Un bloque por debajo de este nivel nunca cuenta como voz, por muy silencioso
// que sea el cuarto: evita que la compuerta se abra con ruido de ventilador.
const RMS_MINIMO = 0.008;
// Cuánto debe superar al ruido de fondo para considerarse voz.
const FACTOR_SOBRE_RUIDO = 2.5;
// Margen antes de dar el turno por terminado. Corto parte las frases en cuanto
// alguien duda; largo hace que el coach tarde en arrancar.
const COLA_MS = 900;
// Se guardan bloques anteriores al disparo: la compuerta siempre reacciona
// tarde y sin esto se pierde la primera sílaba.
const PREVIO_BLOQUES = 4;

class CapturaPCM extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Int16Array(MUESTRAS_POR_ENVIO);
    this.escritas = 0;
    this.razon = sampleRate / TASA_DESTINO;   // sampleRate es global aquí
    this.posicion = 0;
    this.resto = new Float32Array(0);

    this.pisoRuido = 0.01;
    this.hablando = false;
    this.bloquesDeCola = 0;
    this.previos = [];
    this.cerrarTrasEmitir = false;
    this.port.postMessage({ tipo: "inicio", tasaEntrada: sampleRate });
  }

  /** Decide si el bloque contiene voz y actualiza el estado de la compuerta. */
  _evaluar(bloque) {
    let suma = 0;
    for (let i = 0; i < bloque.length; i++) {
      const m = bloque[i] / 32768;
      suma += m * m;
    }
    const rms = Math.sqrt(suma / bloque.length);
    const umbral = Math.max(RMS_MINIMO, this.pisoRuido * FACTOR_SOBRE_RUIDO);

    if (rms > umbral) {
      if (!this.hablando) this.port.postMessage({ tipo: "voz", activa: true });
      this.hablando = true;
      this.bloquesDeCola = Math.ceil(COLA_MS / 100);
      return true;
    }

    // Solo se aprende el ruido de fondo mientras nadie habla.
    this.pisoRuido = this.pisoRuido * 0.95 + rms * 0.05;

    if (this.bloquesDeCola > 0) {
      this.bloquesDeCola--;
      if (this.bloquesDeCola === 0) {
        this.hablando = false;
        // El fin de turno se avisa DESPUÉS del último bloque, no antes: si no,
        // el servidor cerraría el turno dejando fuera audio ya capturado.
        this.cerrarTrasEmitir = true;
      }
      return true;
    }
    return false;
  }

  _emitir(bloque) {
    if (!this._evaluar(bloque)) {
      this.previos.push(bloque);
      if (this.previos.length > PREVIO_BLOQUES) this.previos.shift();
      return;
    }

    for (const anterior of this.previos) {
      this.port.postMessage(anterior.buffer, [anterior.buffer]);
    }
    this.previos = [];
    this.port.postMessage(bloque.buffer, [bloque.buffer]);

    if (this.cerrarTrasEmitir) {
      this.cerrarTrasEmitir = false;
      this.port.postMessage({ tipo: "voz", activa: false });
    }
  }

  process(entradas) {
    const canal = entradas[0]?.[0];
    if (!canal) return true;

    // La cola del bloque anterior se une al bloque nuevo: el remuestreo cae
    // casi siempre entre dos muestras y necesita continuidad entre bloques.
    const datos = new Float32Array(this.resto.length + canal.length);
    datos.set(this.resto, 0);
    datos.set(canal, this.resto.length);

    let pos = this.posicion;
    while (pos + 1 < datos.length) {
      const i = Math.floor(pos);
      const frac = pos - i;
      const muestra = datos[i] * (1 - frac) + datos[i + 1] * frac;

      const m = Math.max(-1, Math.min(1, muestra));
      this.buffer[this.escritas++] = m < 0 ? m * 0x8000 : m * 0x7fff;

      if (this.escritas === MUESTRAS_POR_ENVIO) {
        this._emitir(this.buffer.slice());
        this.escritas = 0;
      }
      pos += this.razon;
    }

    const consumidas = Math.floor(pos);
    this.resto = datos.slice(consumidas);
    this.posicion = pos - consumidas;
    return true;
  }
}

registerProcessor("captura-pcm", CapturaPCM);
