"""Puente entre el navegador y Gemini Live API, con una sesión por turno.

Por qué una sesión por turno y no una continua, que sería lo natural:

La Live API responde una vez y después queda inerte. Sigue aceptando audio,
no cierra la conexión ni envía GO_AWAY, pero no vuelve a emitir nada. Se
reprodujo hablando directo con la API, sin este servidor de por medio, en
`gemini-3.1-flash-live-preview` y en `gemini-2.5-flash-native-audio-latest`,
con todas las combinaciones de VAD y de configuración. Por texto sí funciona
multi-turno; solo el canal de audio se atasca.

Como el primer turno de cada sesión siempre funciona, se abre una sesión por
intervención del corredor y se reinyecta el historial guardado en memoria.
Cuesta entre medio segundo y un segundo de conexión por turno, y a cambio la
conversación no se rompe.

Flujo de cada turno:
    el navegador manda audio solo mientras hay voz, y avisa al terminar
    -> se abre sesión, se reinyecta el historial
    -> se envía el audio y una cola de silencio para que el VAD cierre el turno
    -> se recibe la respuesta, se resuelven herramientas, se cierra la sesión
    -> se guardan las transcripciones en memoria
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from google import genai
from google.genai import types

from app.memoria import Memoria
from app.prompts import con_memoria
from app.tools import esquemas

log = logging.getLogger(__name__)

MODELO = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview")
VOZ = os.getenv("GEMINI_VOICE", "Charon")

# La API acepta PCM de 16 kHz a la entrada y emite 24 kHz a la salida.
TASA_ENTRADA = 16_000
TASA_SALIDA = 24_000
TROZO = 3200                       # 100 ms de PCM a 16 kHz

# Cola de silencio tras la intervención: es lo que le indica al VAD del
# servidor que el corredor terminó de hablar.
COLA_SILENCIO_S = 1.6
SILENCIO = b"\x00" * TROZO

# Por debajo de esto no es una intervención, es un golpe de mesa.
MIN_SEGUNDOS_TURNO = 0.3

SEGUNDOS_MAX_TURNO = int(os.getenv("MAX_TURN_SECONDS", "60"))
SEGUNDOS_MAX_SESION = int(os.getenv("MAX_SESSION_SECONDS", "600"))
PING_TIMEOUT = int(os.getenv("WS_PING_TIMEOUT", "60"))
MAX_TURNOS_HISTORIAL = int(os.getenv("MAX_HISTORY_TURNS", "20"))


def construir_config(perfil_resumen: str | None = None) -> dict:
    return {
        "response_modalities": ["AUDIO"],
        "system_instruction": con_memoria(perfil_resumen),
        "tools": [{"function_declarations": esquemas.DECLARACIONES}],
        "speech_config": {
            "voice_config": {"prebuilt_voice_config": {"voice_name": VOZ}}
        },
        # Las transcripciones son la única memoria que queda de cada turno:
        # sin ellas no habría historial que reinyectar en la sesión siguiente.
        "input_audio_transcription": {},
        "output_audio_transcription": {},
    }


def crear_cliente() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Falta GEMINI_API_KEY. Consigue una gratis en "
            "https://aistudio.google.com/apikey y ponla en .env"
        )
    cliente = genai.Client(api_key=api_key)

    # El SDK no expone la configuración de keepalive del WebSocket, pero sí
    # reenvía este diccionario tal cual a websockets.connect().
    # Atributo privado: si el SDK lo renombra, esto deja de aplicarse, así que
    # el fallo se registra en vez de pasar desapercibido.
    try:
        cliente._api_client._websocket_ssl_ctx["ping_timeout"] = PING_TIMEOUT
    except (AttributeError, TypeError):
        log.warning(
            "no se pudo ajustar el keepalive del WebSocket: el SDK cambió su "
            "estructura interna"
        )
    return cliente


class ConversacionCoach:
    """Una charla completa. Cada intervención del corredor abre su propia sesión."""

    def __init__(self, enviar_al_navegador, memoria: Memoria):
        self._enviar = enviar_al_navegador
        self._memoria = memoria
        self._cliente = crear_cliente()
        self.turnos = 0
        self.bytes_entrada = 0
        self.bytes_salida = 0

    async def ejecutar(self, recibir_del_navegador):
        resumen = self._memoria.resumen_para_prompt()
        if resumen:
            log.info("el corredor ya es conocido: %s", resumen[:120])

        await self._enviar({
            "tipo": "listo",
            "tasa_entrada": TASA_ENTRADA,
            "tasa_salida": TASA_SALIDA,
            "conocido": bool(resumen),
        })

        buffer = bytearray()
        turno: asyncio.Task | None = None

        async with asyncio.timeout(SEGUNDOS_MAX_SESION):
            async for tipo, valor in recibir_del_navegador():
                if tipo == "audio":
                    # Con la compuerta de voz activa, que llegue audio mientras
                    # el coach habla significa que el corredor lo interrumpió.
                    if turno and not turno.done():
                        turno.cancel()
                        await self._enviar({"tipo": "interrumpido"})
                    buffer.extend(valor)
                    self.bytes_entrada += len(valor)

                elif tipo == "fin_turno":
                    if len(buffer) < MIN_SEGUNDOS_TURNO * TASA_ENTRADA * 2:
                        buffer.clear()
                        continue
                    audio, buffer = bytes(buffer), bytearray()
                    turno = asyncio.create_task(self._atender(resumen, audio=audio))

                elif tipo == "texto":
                    # Escribir es una intervención como cualquier otra: mismo
                    # turno, mismo historial, misma respuesta hablada.
                    if turno and not turno.done():
                        turno.cancel()
                    buffer.clear()
                    turno = asyncio.create_task(self._atender(resumen, texto=valor))

        if turno and not turno.done():
            await asyncio.gather(turno, return_exceptions=True)

    async def _atender(self, resumen: str | None, audio: bytes | None = None,
                       texto: str | None = None):
        """Abre una sesión, reproduce el historial y resuelve una intervención.

        La intervención llega hablada o escrita; a partir de aquí da igual.
        """
        self.turnos += 1
        numero = self.turnos
        if texto:
            log.info("turno %d: texto (%d caracteres)", numero, len(texto))
            # Lo escrito no vuelve transcrito, así que se refleja en pantalla.
            await self._enviar({"tipo": "transcripcion", "quien": "corredor",
                                "texto": texto})
        else:
            log.info("turno %d: %.1f s de audio", numero, len(audio) / (TASA_ENTRADA * 2))
        await self._enviar({"tipo": "pensando"})

        historial = self._memoria.historial(MAX_TURNOS_HISTORIAL)
        dicho: list[str] = []
        respondido: list[str] = []

        try:
            async with asyncio.timeout(SEGUNDOS_MAX_TURNO):
                async with self._cliente.aio.live.connect(
                    model=MODELO, config=construir_config(resumen)
                ) as sesion:
                    if historial:
                        # Contexto sin pedir respuesta: la pregunta va en el audio.
                        await sesion.send_client_content(
                            turns=historial, turn_complete=False
                        )

                    if texto:
                        dicho.append(texto)
                        await sesion.send_client_content(
                            turns={"role": "user", "parts": [{"text": texto}]},
                            turn_complete=True,
                        )
                        emisor = None
                    else:
                        emisor = asyncio.create_task(self._emitir(sesion, audio))

                    try:
                        await self._recibir(sesion, dicho, respondido)
                    finally:
                        if emisor:
                            emisor.cancel()
                            await asyncio.gather(emisor, return_exceptions=True)

        except asyncio.CancelledError:
            log.info("turno %d interrumpido por el corredor", numero)
            raise
        except TimeoutError:
            log.warning("turno %d agotó los %d s", numero, SEGUNDOS_MAX_TURNO)
            await self._enviar({
                "tipo": "error",
                "mensaje": "El coach tardó demasiado en responder. Inténtalo otra vez.",
            })
        except Exception as exc:
            log.exception("turno %d falló", numero)
            await self._enviar({"tipo": "error", "mensaje": str(exc)})
        finally:
            # Se guarda lo que haya: un turno a medias sigue siendo contexto.
            self._memoria.guardar_turno("user", "".join(dicho))
            self._memoria.guardar_turno("model", "".join(respondido))
            await self._enviar({"tipo": "turno_listo"})

    async def _emitir(self, sesion, audio: bytes):
        """Envía la intervención y la cola de silencio que cierra el turno."""
        for i in range(0, len(audio), TROZO):
            trozo = audio[i : i + TROZO]
            await sesion.send_realtime_input(
                audio=types.Blob(
                    data=trozo.ljust(TROZO, b"\x00"),
                    mime_type=f"audio/pcm;rate={TASA_ENTRADA}",
                )
            )
        # Sin esta cola el VAD del servidor no da el turno por terminado.
        for _ in range(int(COLA_SILENCIO_S * 10)):
            await sesion.send_realtime_input(
                audio=types.Blob(
                    data=SILENCIO, mime_type=f"audio/pcm;rate={TASA_ENTRADA}"
                )
            )
            await asyncio.sleep(0.1)

    async def _recibir(self, sesion, dicho: list[str], respondido: list[str]):
        async for respuesta in sesion.receive():
            if respuesta.data:
                await self._enviar(respuesta.data)
                self.bytes_salida += len(respuesta.data)

            contenido = respuesta.server_content
            if contenido:
                await self._transcripciones(contenido, dicho, respondido)
                if contenido.turn_complete:
                    return

            if respuesta.tool_call:
                await self._herramientas(sesion, respuesta.tool_call)

    async def _transcripciones(self, contenido, dicho, respondido):
        entrada = getattr(contenido, "input_transcription", None)
        if entrada and entrada.text:
            dicho.append(entrada.text)
            await self._enviar({"tipo": "transcripcion", "quien": "corredor",
                                "texto": entrada.text})

        salida = getattr(contenido, "output_transcription", None)
        if salida and salida.text:
            respondido.append(salida.text)
            await self._enviar({"tipo": "transcripcion", "quien": "coach",
                                "texto": salida.text})

    async def _herramientas(self, sesion, tool_call):
        respuestas = []
        for llamada in tool_call.function_calls:
            argumentos = dict(llamada.args or {})
            log.info("herramienta %s(%s)", llamada.name, argumentos)

            resultado = await asyncio.to_thread(
                esquemas.ejecutar, llamada.name, argumentos
            )

            if llamada.name == "generar_plan" and "error" not in resultado:
                self._memoria.guardar_plan(resultado)
                await self._enviar({"tipo": "plan", "plan": resultado})
                # El plan completo ya está en pantalla; al modelo le basta el
                # resumen, que además no puede recitar 18 semanas en voz alta.
                resultado = {
                    "resumen_hablado": resultado["resumen_hablado"],
                    "viabilidad": resultado["viabilidad"],
                    "nota": "El plan completo ya se muestra en pantalla al corredor.",
                }
            elif llamada.name == "evaluar_sintoma" and "error" not in resultado:
                self._memoria.actualizar_perfil(
                    molestia_reciente=argumentos.get("zona")
                )

            await self._enviar({
                "tipo": "herramienta",
                "nombre": llamada.name,
                "resultado": _recortar(resultado),
            })
            respuestas.append(
                types.FunctionResponse(
                    id=llamada.id, name=llamada.name, response=resultado
                )
            )

        await sesion.send_tool_response(function_responses=respuestas)


def _recortar(objeto, limite: int = 600) -> str:
    """Versión abreviada para el panel de depuración del navegador."""
    texto = json.dumps(objeto, ensure_ascii=False, default=str)
    return texto if len(texto) <= limite else texto[:limite] + "…"
