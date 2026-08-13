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
 * 3. Detecta si hay voz, para el indicador de la interfaz.
 *
 * La detección NO corta el envío. Se intentó, y el VAD del servidor dejaba de
 * reconocer turnos a partir del segundo: la Live API espera un flujo continuo
 * y los huecos la desincronizan. El ahorro además era mínimo, porque el audio
 * de entrada se cobra por minuto de sesión y no por byte: en una conversación
 * de cinco minutos la diferencia son un par de centavos.
 */
const TASA_DESTINO = 16000;
const MUESTRAS_POR_ENVIO = 1600;      // 100 ms a 16 kHz

// Un bloque por debajo de este nivel nunca cuenta como voz, por muy silencioso
// que sea el cuarto: evita que la compuerta se abra con ruido de ventilador.
const RMS_MINIMO = 0.008;
// Cuánto debe superar al ruido de fondo para considerarse voz.
const FACTOR_SOBRE_RUIDO = 2.5;
// Cuánto se mantiene el indicador de "hablando" tras callar, para que no
// parpadee entre palabras.
const COLA_MS = 800;

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
        // El aviso de fin se emite DESPUÉS del último bloque, no antes:
        // el servidor cerraría el turno dejando fuera audio ya capturado.
        this.cerrarTrasEmitir = true;
      }
      return true;
    }
    return false;
  }

  _emitir(bloque) {
    // El estado de voz solo alimenta el indicador; el audio sale siempre.
    this._evaluar(bloque);
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
