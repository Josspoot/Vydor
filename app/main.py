"""Servidor FastAPI: sirve el cliente web y hace de puente hacia Gemini Live.

La API key vive solo aquí. El navegador nunca la ve.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict, deque
from contextlib import suppress
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.live_agent import SesionCoach

# Ruta explícita: load_dotenv() busca desde el directorio actual, así que el
# servidor fallaba según desde dónde se arrancara.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("coach")

RAIZ = Path(__file__).resolve().parent.parent
ESTATICOS = RAIZ / "static"

# Freno de mano: sin esto, un enlace público compartido puede drenar la cuota.
SESIONES_POR_IP_AL_DIA = int(os.getenv("MAX_SESSIONS_PER_IP", "20"))
_historial: dict[str, deque[float]] = defaultdict(deque)

app = FastAPI(title="Coach de running por voz")
app.mount("/static", StaticFiles(directory=ESTATICOS), name="static")


@app.get("/")
async def raiz():
    return FileResponse(ESTATICOS / "index.html")


@app.get("/salud")
async def salud():
    return {
        "ok": True,
        "tiene_api_key": bool(os.getenv("GEMINI_API_KEY")),
        "modelo": os.getenv("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview"),
    }


def _primer_fallo(grupo: BaseExceptionGroup) -> BaseException:
    """Desanida ExceptionGroup hasta la primera excepción real."""
    error = grupo.exceptions[0]
    while isinstance(error, BaseExceptionGroup):
        error = error.exceptions[0]
    return error


def _cuota_disponible(ip: str) -> bool:
    ahora = time.time()
    marcas = _historial[ip]
    while marcas and ahora - marcas[0] > 86_400:
        marcas.popleft()
    if len(marcas) >= SESIONES_POR_IP_AL_DIA:
        return False
    marcas.append(ahora)
    return True


@app.websocket("/ws")
async def websocket_coach(ws: WebSocket):
    await ws.accept()
    ip = ws.client.host if ws.client else "desconocida"

    if not _cuota_disponible(ip):
        await ws.send_json({
            "tipo": "error",
            "mensaje": "Alcanzaste el límite de sesiones de demo por hoy.",
        })
        await ws.close()
        return

    # La cola lleva dos cosas: trozos de audio y las marcas de inicio/fin de
    # habla que envía la compuerta de voz del navegador.
    entrantes: asyncio.Queue[tuple[str, object] | None] = asyncio.Queue(maxsize=64)

    async def leer_del_navegador():
        """Vuelca lo que llega del navegador en la cola hasta que cierre."""
        try:
            while True:
                mensaje = await ws.receive()
                if mensaje["type"] == "websocket.disconnect":
                    break
                if (datos := mensaje.get("bytes")) is not None:
                    await entrantes.put(("audio", datos))
                elif (texto := mensaje.get("text")) is not None:
                    evento = json.loads(texto)
                    if evento.get("tipo") == "actividad":
                        await entrantes.put(("actividad", bool(evento["activa"])))
        except (WebSocketDisconnect, RuntimeError):
            pass
        except (ValueError, KeyError) as exc:
            log.warning("mensaje de control inválido del navegador: %s", exc)
        finally:
            await entrantes.put(None)  # centinela de cierre

    async def audio_entrante():
        while (elemento := await entrantes.get()) is not None:
            yield elemento

    # La escritura hacia el navegador va por su propia cola y su propia tarea.
    # Si se hiciera en línea, un navegador lento frenaría el bucle que lee de
    # Gemini, y esa conexión se cae por "keepalive ping timeout" cuando sus
    # pongs no se procesan a tiempo.
    salientes: asyncio.Queue = asyncio.Queue(maxsize=512)
    descartados = 0

    async def enviar(carga):
        nonlocal descartados
        if isinstance(carga, bytes):
            try:
                salientes.put_nowait(carga)
            except asyncio.QueueFull:
                # Perder un trozo de audio es mucho menos grave que perder
                # la sesión entera.
                descartados += 1
        else:
            await salientes.put(carga)   # los mensajes de control no se pierden

    async def escribir_al_navegador():
        while (carga := await salientes.get()) is not None:
            try:
                if isinstance(carga, bytes):
                    await ws.send_bytes(carga)
                else:
                    await ws.send_json(carga)
            except (WebSocketDisconnect, RuntimeError):
                break

    sesion = SesionCoach(enviar)
    lector = asyncio.create_task(leer_del_navegador())
    escritor = asyncio.create_task(escribir_al_navegador())

    try:
        await sesion.ejecutar(audio_entrante)
    except WebSocketDisconnect:
        log.info("el navegador cerró la conexión")
    except BaseExceptionGroup as grupo:
        # TaskGroup agrupa los fallos; al usuario le sirve el primero.
        fallo = _primer_fallo(grupo)
        if isinstance(fallo, WebSocketDisconnect):
            log.info("el navegador cerró la conexión")
        else:
            log.exception("sesión terminada por error", exc_info=fallo)
            with suppress(Exception):
                await ws.send_json({"tipo": "error", "mensaje": str(fallo)})
    except Exception as exc:
        log.exception("sesión terminada por error")
        with suppress(Exception):
            await ws.send_json({"tipo": "error", "mensaje": str(exc)})
    finally:
        lector.cancel()
        with suppress(asyncio.CancelledError):
            await lector
        await salientes.put(None)          # deja que el escritor vacíe la cola
        with suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(escritor, timeout=3)
        escritor.cancel()
        with suppress(Exception):
            await ws.close()
        log.info(
            "sesión cerrada | %d turnos transcritos | %.1f KB del navegador | "
            "%.1f KB de audio devuelto%s",
            len(sesion.transcripcion),
            sesion.bytes_entrada / 1024,
            sesion.bytes_salida / 1024,
            f" | {descartados} trozos descartados" if descartados else "",
        )
        if sesion.bytes_entrada == 0:
            log.warning(
                "el navegador no envió NADA de audio: revisa permisos del "
                "micrófono y la consola del navegador"
            )
