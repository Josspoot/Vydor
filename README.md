# Vydor — coach de running conversacional por voz

Chatbot de voz que actúa como entrenador personal para corredores de 5k, 10k,
21k y maratón. Conversación hablada en tiempo real sobre **Gemini Live API**
(speech-to-speech nativo), con la lógica de entrenamiento resuelta por un motor
determinista.

## La decisión de diseño que importa

**El LLM no calcula nada.** Un modelo improvisando planes de entrenamiento
produce recomendaciones con riesgo real de lesión: progresiones del 40% semanal,
fondos desproporcionados, ritmos inventados.

Aquí el modelo conversa y extrae datos; todos los números salen de
`app/tools/training.py`, que implementa:

| Regla | Valor | Por qué |
|---|---|---|
| Incremento semanal máximo | +10% | Techo clásico para evitar sobrecarga |
| Semana de descarga | cada 4ª, −25% | Permite absorber la carga |
| Fondo máximo | 30–45% del volumen según días | Un fondo desproporcionado lesiona |
| Distribución | ~80% fácil / 20% calidad | Entrenamiento polarizado |
| Techo por día disponible | 14 km/día promedio | Evita "rodajes suaves" de 18 km |

Los ritmos salen de **VDOT (Daniels)** calculado sobre una marca real, y la
predicción entre distancias usa **VDOT + Riegel**. Los tests comparan contra las
tablas publicadas, no contra la salida del propio código: si la implementación
se desvía de la literatura, fallan.

**Decir "no" es una función de primera clase.** `evaluar_viabilidad()` rechaza
objetivos peligrosos ("maratón en 8 semanas corriendo 10 km") y devuelve la
alternativa concreta. El prompt instruye al coach a no ceder si el corredor insiste.

**El dolor no se improvisa.** Cualquier mención de molestia pasa por
`app/tools/sintomas.py`, que hace triaje determinista y puede bloquear la entrega
de rutina. Síntomas sistémicos (dolor de pecho, desmayo, palpitaciones) escalan a
emergencia por encima de cualquier otra señal.

## Arquitectura

```
navegador  --PCM 16 kHz-->  FastAPI  --WebSocket-->  Gemini Live API
    ^                          |                          |
    |                          |  function calling        |
    +---PCM 24 kHz-------------+--------------------------+
                               |
                     motor determinista
              (VDOT · progresión · triaje de síntomas)
```

La API key vive solo en el servidor; el navegador nunca la ve.

## Puesta en marcha

```bash
uv venv && uv pip install -e ".[dev]"
cp .env.example .env        # y pon tu GEMINI_API_KEY
.venv/bin/python -m uvicorn app.main:app --reload
```

Abre http://localhost:8000 y pulsa «Hablar con Vydor».

La clave se saca gratis en [AI Studio](https://aistudio.google.com/apikey) y
**no pide tarjeta de crédito**.

> **Antes de desplegar**, revisa el límite de tu cuenta para el modelo Live en
> [tu dashboard de rate limits](https://aistudio.google.com/rate-limit). Los
> modelos preview tienen cuotas más restrictivas y no están documentadas por
> modelo: dependen de tu cuenta.

## Tests

```bash
.venv/bin/python -m pytest -q     # 92 tests
```

Cubren la matemática de VDOT contra tablas, las reglas de carga sobre planes
generados (progresión, descargas, taper, 80/20) y el triaje de síntomas.

## Costo

Con `gemini-3.1-flash-live-preview`: ~$0.005/min de audio de entrada y
~$0.018/min de salida, es decir **~$0.10 por conversación de 5 minutos**. El
desarrollo completo cabe en la capa gratuita.

`MAX_SESSION_SECONDS` y `MAX_SESSIONS_PER_IP` existen para que un enlace público
compartido no drene la cuota.

## Estado

- [x] Motor determinista de entrenamiento (VDOT, ritmos, progresión, viabilidad)
- [x] Triaje de síntomas y banderas rojas
- [x] Puente de voz bidireccional con function calling
- [x] Cliente web (captura 16 kHz, reproducción 24 kHz, barge-in)
- [ ] Memoria entre conversaciones (perfil + resumen rodante)
- [ ] Recordatorios proactivos por Telegram

### Sobre WhatsApp

El reto lo menciona como opción para recordatorios. Se eligió **Telegram**: la
Cloud API de WhatsApp exige verificación de negocio con Meta y aprobación de
plantillas, con plazos que no encajan en la duración del reto, y cobra por
conversación. Telegram da la misma funcionalidad, gratis y sin aprobaciones.

## Aviso

Herramienta de orientación de entrenamiento, no de consejo médico. El triaje de
síntomas está diseñado para derivar a profesionales, nunca para diagnosticar.
