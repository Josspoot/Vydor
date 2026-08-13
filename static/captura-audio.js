/**
 * AudioWorklet de captura.
 *
 * Corre en el hilo de audio. El micrófono entrega la tasa nativa del sistema
 * (44.1 o 48 kHz en la mayoría de los Mac), así que aquí se remuestrea a los
 * 16 kHz que pide la Live API y se convierte a PCM de 16 bits little-endian.
 *
 * Forzar el AudioContext a 16 kHz parece más simple, pero según el navegador
 * falla o deforma la señal cuando el hardware no soporta esa tasa.
 */
const TASA_DESTINO = 16000;
const MUESTRAS_POR_ENVIO = 1600; // 100 ms a 16 kHz

class CapturaPCM extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Int16Array(MUESTRAS_POR_ENVIO);
    this.escritas = 0;
    this.razon = sampleRate / TASA_DESTINO; // sampleRate es global aquí
    this.posicion = 0;
    this.resto = new Float32Array(0);
    this.port.postMessage({ tipo: "inicio", tasaEntrada: sampleRate });
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
        // Se transfiere una copia: el buffer se sigue reutilizando aquí.
        const copia = this.buffer.slice();
        this.port.postMessage(copia.buffer, [copia.buffer]);
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
