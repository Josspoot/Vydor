"""Puente bidireccional entre el navegador y Gemini Live API.

Flujo:
    navegador --PCM 16 kHz--> este módulo --> Gemini Live
    navegador <--PCM 24 kHz-- este módulo <-- Gemini Live

Las llamadas a herramientas se resuelven aquí contra el motor determinista y
se devuelven al modelo dentro de la misma sesión.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from google import genai
from google.genai import types

from app.prompts import con_memoria
from app.tools import esquemas

log = logging.getLogger(__name__)

MODELO = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview")
VOZ = os.getenv("GEMINI_VOICE", "Charon")

# La API acepta PCM de 16 kHz a la entrada y emite 24 kHz a la salida.
TASA_ENTRADA = 16_000
TASA_SALIDA = 24_000

# Tope de sesión para que un navegador olvidado abierto no consuma cuota.
SEGUNDOS_MAX_SESION = int(os.getenv("MAX_SESSION_SECONDS", "600"))


def construir_config(perfil_resumen: str | None = None) -> dict:
    return {
        "response_modalities": ["AUDIO"],
        "system_instruction": con_memoria(perfil_resumen),
        "tools": [{"function_declarations": esquemas.DECLARACIONES}],
        "speech_config": {
            "voice_config": {"prebuilt_voice_config": {"voice_name": VOZ}}
        },
        # Las transcripciones alimentan la memoria y el subtitulado en pantalla.
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
    return genai.Client(api_key=api_key)


class SesionCoach:
    """Una conversación de voz. Vive lo que dura el WebSocket del navegador."""

    def __init__(self, enviar_al_navegador, perfil_resumen: str | None = None):
        self._enviar = enviar_al_navegador
        self._perfil = perfil_resumen
        self.transcripcion: list[tuple[str, str]] = []  # (quien, texto)
        self.bytes_entrada = 0
        self.bytes_salida = 0
        self._ultimo_plan: dict | None = None

    @property
    def ultimo_plan(self) -> dict | None:
        """Último plan generado, para persistirlo al cerrar la sesión."""
        return self._ultimo_plan

    async def ejecutar(self, recibir_del_navegador):
        cliente = crear_cliente()
        config = construir_config(self._perfil)

        async with cliente.aio.live.connect(model=MODELO, config=config) as sesion:
            await self._enviar({
                "tipo": "listo",
                "tasa_entrada": TASA_ENTRADA,
                "tasa_salida": TASA_SALIDA,
            })
            log.info("sesión Live abierta (modelo=%s, voz=%s)", MODELO, VOZ)

            try:
                async with asyncio.timeout(SEGUNDOS_MAX_SESION):
                    async with asyncio.TaskGroup() as grupo:
                        grupo.create_task(self._bombear_audio(recibir_del_navegador, sesion))
                        grupo.create_task(self._procesar_respuestas(sesion))
            except TimeoutError:
                await self._enviar({
                    "tipo": "fin",
                    "motivo": (
                        f"La sesión llegó al límite de "
                        f"{SEGUNDOS_MAX_SESION // 60} minutos."
                    ),
                })

    async def _bombear_audio(self, recibir, sesion):
        """Navegador -> Gemini."""
        trozos = 0
        async for trozo in recibir():
            await sesion.send_realtime_input(
                audio=types.Blob(
                    data=trozo, mime_type=f"audio/pcm;rate={TASA_ENTRADA}"
                )
            )
            self.bytes_entrada += len(trozo)
            trozos += 1
            # Un micrófono que no captura y un silencio real son
            # indistinguibles sin esto.
            if trozos % 50 == 0:
                log.info(
                    "audio del navegador: %d trozos, %.1f KB (%.1f s aprox)",
                    trozos, self.bytes_entrada / 1024,
                    self.bytes_entrada / (TASA_ENTRADA * 2),
                )
        log.info("el navegador dejó de enviar audio tras %d trozos", trozos)

    async def _procesar_respuestas(self, sesion):
        """Gemini -> navegador, resolviendo herramientas por el camino."""
        async for respuesta in sesion.receive():
            if respuesta.data:
                await self._enviar(respuesta.data)  # audio crudo
                self.bytes_salida += len(respuesta.data)

            contenido = respuesta.server_content
            if contenido:
                await self._manejar_transcripciones(contenido)
                if contenido.interrupted:
                    # El usuario habló encima: el navegador debe callar ya.
                    await self._enviar({"tipo": "interrumpido"})

            if respuesta.tool_call:
                await self._responder_herramientas(sesion, respuesta.tool_call)

    async def _manejar_transcripciones(self, contenido):
        entrada = getattr(contenido, "input_transcription", None)
        if entrada and entrada.text:
            self.transcripcion.append(("corredor", entrada.text))
            await self._enviar({"tipo": "transcripcion", "quien": "corredor",
                                "texto": entrada.text})

        salida = getattr(contenido, "output_transcription", None)
        if salida and salida.text:
            self.transcripcion.append(("coach", salida.text))
            await self._enviar({"tipo": "transcripcion", "quien": "coach",
                                "texto": salida.text})

    async def _responder_herramientas(self, sesion, tool_call):
        respuestas = []
        for llamada in tool_call.function_calls:
            argumentos = dict(llamada.args or {})
            log.info("herramienta %s(%s)", llamada.name, argumentos)

            # El cálculo es síncrono y rápido, pero va a un hilo para no
            # bloquear el bombeo de audio si algún plan crece.
            resultado = await asyncio.to_thread(
                esquemas.ejecutar, llamada.name, argumentos
            )

            if llamada.name == "generar_plan" and "error" not in resultado:
                self._ultimo_plan = resultado
                # El plan completo va a la pantalla; al modelo le basta el resumen.
                await self._enviar({"tipo": "plan", "plan": resultado})
                resultado = {
                    "resumen_hablado": resultado["resumen_hablado"],
                    "viabilidad": resultado["viabilidad"],
                    "nota": "El plan completo ya se muestra en pantalla al corredor.",
                }

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
