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
| Duración máxima del bloque | 10–30 semanas según distancia | Estirarlo acumula cansancio, no forma |

Los ritmos salen de **VDOT (Daniels)** calculado sobre una marca real, y la
predicción entre distancias usa **VDOT + Riegel**. Los tests comparan contra las
tablas publicadas, no contra la salida del propio código: si la implementación
se desvía de la literatura, fallan.

**Se puede empezar de cero.** Cero kilómetros a la semana es un punto de
partida válido y frecuente: las primeras semanas alternan carrera y caminata, y
el mínimo de volumen exigido baja cuando el objetivo es terminar en vez de
marcar tiempo. También hay metas de iniciación —1K y 3K— porque para mucha
gente correr un kilómetro seguido *es* la carrera.

**Con fecha fija, el plazo manda.** Si la carrera ya tiene día y no se puede
mover, el plan se arma para ese plazo aunque sea corto: negarse no mueve la
carrera. Lo que cambia es a qué se aspira, y el coach lo dice una vez con
franqueza —terminar, caminando tramos si hace falta— sin repetirlo en cada turno.

**Decir "no" es una función de primera clase.** `evaluar_viabilidad()` rechaza
objetivos peligrosos ("maratón en 8 semanas corriendo 10 km") y devuelve la
alternativa concreta. El prompt instruye al coach a no ceder si el corredor insiste.

**El dolor no se improvisa.** Cualquier mención de molestia pasa por
`app/tools/sintomas.py`, que hace triaje determinista y puede bloquear la entrega
de rutina. Síntomas sistémicos (dolor de pecho, desmayo, palpitaciones) escalan a
emergencia por encima de cualquier otra señal.

## Arquitectura

```
navegador  --PCM 16 kHz-->  FastAPI  --una sesión por turno-->  Gemini Live API
    ^         (solo mientras     |                                    |
    |          hay voz)          |  function calling                  |
    +---PCM 24 kHz---------------+------------------------------------+
                                 |
                   motor determinista  +  memoria (SQLite)
            (VDOT · progresión · triaje)   (historial · perfil)
```

La API key vive solo en el servidor; el navegador nunca la ve.

### Una sesión por turno, y por qué

Lo natural sería mantener una sola sesión Live abierta durante toda la
conversación. No funciona: **la sesión responde una vez y después queda
inerte**. Sigue aceptando audio, no cierra la conexión ni envía `GO_AWAY`,
pero no vuelve a emitir nada.

Se reprodujo hablando directo con la API, sin este servidor de por medio, en
`gemini-3.1-flash-live-preview` y en `gemini-2.5-flash-native-audio-latest`,
con VAD automático y manual, con y sin compuerta de voz, con silencio digital
y con ruido de confort. Por texto la misma configuración responde a cinco
turnos seguidos; solo el canal de audio se atasca.

Como el **primer turno de cada sesión siempre funciona**, se abre una sesión
por intervención del corredor y se reinyecta el historial. Cuesta entre medio
segundo y un segundo de conexión por turno, y a cambio la conversación no se
rompe.

El efecto secundario es afortunado: la persistencia deja de ser un parche y
pasa a ser la memoria que el reto pide como punto extra.

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
.venv/bin/python -m pytest -q     # 214 tests
```

Cubren la matemática de VDOT contra tablas, las reglas de carga sobre planes
generados (progresión, descargas, taper, 80/20), el triaje de síntomas, la
memoria y los recordatorios. Los de Telegram sustituyen el envío por un doble:
no tocan la red.

Los de interfaz van en node (`tests/frontend/`) y cargan el `static/app.js`
real sobre un DOM mínimo, en vez de repetir su lógica. Los lanza el mismo
`pytest` —un test que hay que acordarse de ejecutar aparte es un test que nadie
ejecuta— y se saltan si no hay node instalado.

Un caso merece mención: el triaje **se niega a dictaminar** si no sabe si el
dolor aparece en reposo, si hace cojear y si mejora al calentar. Salió de una
prueba real en la que el modelo llamó a la herramienta con solo la zona del
dolor y los valores por defecto lo despacharon como molestia normal.

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
- [x] Memoria por conversación: retomar una charla la recuerda entera, abrir
      una nueva empieza limpia
- [x] Recordatorios proactivos por Telegram
- [x] Historial navegable: varias charlas y varios planes a la vez
- [x] Vistas de chat, plan semanal y calendario mensual
- [x] Exportar el plan a PDF desglosado día a día

## La interfaz

Tres vistas sobre los mismos datos, más un historial lateral:

- **Chat** — la conversación, por voz o **escribiendo**: quien prefiere no
  hablar tiene un campo de texto, y el permiso de micrófono solo se pide si se
  pulsa el botón de hablar. Cada charla se guarda y se puede retomar días
  después: su historial vuelve a entrar en la sesión y el coach sigue el hilo.
- **Plan** — cifras del objetivo, veredicto de viabilidad, zonas de ritmo, la
  forma del bloque en una gráfica, y el desglose de los siete días de cualquier
  semana.
- **Calendario** — el mes de un vistazo, con el día de hoy marcado. El plan se
  organiza en semanas y esa sigue siendo la unidad; el mes es solo una forma de
  mirarlo.

Un corredor puede tener **varios planes a la vez** —preparar un 10K y pensar ya
en la media— y el historial los lista todos.

Cada charla es una meta, y eso vale también para lo que el coach recuerda: la
memoria que entra en la sesión describe *su* plan y nada más. **Una
conversación nueva empieza limpia**, porque quien abre una es casi siempre
porque quiere hablar de otra cosa, y arrancar preguntando por lo que quedó
pendiente en otra charla no viene al caso. Lo anterior no se pierde: sigue en
su conversación, y retomarla la trae entera.

Solo cruzan esa frontera dos cosas, por ser del corredor y no del tema: cómo se
llama y una molestia que dejó abierta. La segunda es de seguridad —nadie
debería recibir una sesión de series por haber abierto una pestaña nueva con la
rodilla a medias—.

Al revés también: retomar una charla no es solo recordar de qué iba, es saber
**por dónde va**. El resumen sitúa al corredor en su plan con el mismo cálculo
que decide el recordatorio diario —«va por la semana 4 de 12, y hoy miércoles
le toca series rápidas de 2.5 km»— y, si el plan ya terminó, el coach pregunta
por la carrera antes de proponer nada nuevo.

### PDF

El botón *Descargar plan en PDF* abre el diálogo de impresión con una vista
aparte que despliega **todas** las semanas con sus fechas reales, no solo la que
se ve en pantalla. Se resuelve con una hoja de estilos de impresión: sin
dependencias nuevas, y el maquetado ya era HTML.

## Recordatorios por Telegram

Cada mañana el corredor recibe el entrenamiento del día: qué toca, cuántos
kilómetros y a qué ritmo. Si dejó una molestia sin cerrar, el mensaje pregunta
por ella antes que por el entrenamiento.

Con dos metas vivas hay que poder decir cuál se está corriendo: el botón
*Recibir recordatorios de este plan* lo elige, y mientras no se elija nada
mandan los del plan más reciente. El mensaje nombra el plan del que sale
—«Plan 10K · semana 3 de 12»— porque suelto en el teléfono, si no, no se sabe.

Para activarlo, habla con [@BotFather](https://t.me/BotFather), crea un bot con
`/newbot` y pon el token en `.env`. Sin token la app funciona igual y el botón
de vincular simplemente no aparece.

La vinculación no pide registrarse: el corredor abre un enlace con un código de
un solo uso, el bot recibe `/start <codigo>` y aprende su chat. El código se
quema al usarlo, para que un enlace filtrado no sirva dos veces.

```bash
TELEGRAM_BOT_TOKEN=...   # de @BotFather
TELEGRAM_HORA=7          # hora local del recordatorio
```

Para probarlo sin esperar a la hora programada:

```bash
.venv/bin/python -m app.telegram --simular   # imprime lo que saldría
.venv/bin/python -m app.telegram             # lo envía de verdad
```

### Sobre WhatsApp

El reto lo menciona como opción para recordatorios. Se eligió **Telegram**: la
Cloud API de WhatsApp exige verificación de negocio con Meta y aprobación de
plantillas, con plazos que no encajan en la duración del reto, y cobra por
conversación. Telegram da la misma funcionalidad, gratis y sin aprobaciones.

## Aviso

Herramienta de orientación de entrenamiento, no de consejo médico. El triaje de
síntomas está diseñado para derivar a profesionales, nunca para diagnosticar.
