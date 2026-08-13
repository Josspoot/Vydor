/**
 * AudioWorklet de captura.
 *
 * Corre en el hilo de audio: convierte los bloques Float32 del micrófono a
 * PCM de 16 bits little-endian y los manda al hilo principal, que los empuja
 * por el WebSocket. Acumula ~100 ms por envío para no ahogar la conexión
 * con paquetes de 128 muestras.
 */
const MUESTRAS_POR_ENVIO = 1600; // 100 ms a 16 kHz

class CapturaPCM extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Int16Array(MUESTRAS_POR_ENVIO);
    this.escritas = 0;
  }

  process(entradas) {
    const canal = entradas[0]?.[0];
    if (!canal) return true;

    for (let i = 0; i < canal.length; i++) {
      // Float32 [-1, 1] -> Int16, saturando en los extremos
      const m = Math.max(-1, Math.min(1, canal[i]));
      this.buffer[this.escritas++] = m < 0 ? m * 0x8000 : m * 0x7fff;

      if (this.escritas === MUESTRAS_POR_ENVIO) {
        // Se transfiere una copia: el buffer se sigue reutilizando aquí.
        const copia = this.buffer.slice();
        this.port.postMessage(copia.buffer, [copia.buffer]);
        this.escritas = 0;
      }
    }
    return true;
  }
}

registerProcessor("captura-pcm", CapturaPCM);
