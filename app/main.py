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
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import telegram
from app.live_agent import ConversacionCoach
from app.memoria import Memoria

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

def _programar_recordatorios() -> None:
    """Programa el recordatorio diario, si la librería está disponible.

    APScheduler se importa aquí y no arriba a propósito: los recordatorios son
    opcionales, y que falte su dependencia no puede impedir que arranque el
    coach de voz, que es el producto.
    """
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except ImportError:
        log.warning(
            "apscheduler no está instalado: no habrá recordatorio automático. "
            "Instálalo con 'uv pip install apscheduler', o lánzalos a mano con "
            "'python -m app.telegram'."
        )
        return

    planificador = AsyncIOScheduler(
        timezone=os.getenv("TZ_RECORDATORIOS", "America/Mexico_City")
    )
    planificador.add_job(
        telegram.enviar_recordatorios, "cron",
        hour=telegram.HORA_RECORDATORIO, minute=0, id="recordatorio_diario",
    )
    planificador.start()
    log.info("recordatorios diarios programados a las %02d:00",
             telegram.HORA_RECORDATORIO)


@asynccontextmanager
async def ciclo_de_vida(_: FastAPI):
    """Arranca el bot de Telegram y el recordatorio diario, si están configurados."""
    tareas = []
    if telegram.configurado():
        tareas.append(asyncio.create_task(telegram.escuchar()))
        _programar_recordatorios()
    else:
        log.info("Telegram desactivado: define TELEGRAM_BOT_TOKEN para activarlo")

    yield

    for tarea in tareas:
        tarea.cancel()
        with suppress(asyncio.CancelledError):
            await tarea


app = FastAPI(title="Coach de running por voz", lifespan=ciclo_de_vida)
app.mount("/static", StaticFiles(directory=ESTATICOS), name="static")


@app.get("/telegram/enlace")
async def enlace_telegram(corredor: str):
    """Enlace de un solo uso para vincular el Telegram de este corredor."""
    if not telegram.configurado():
        return {"disponible": False}
    usuario = await telegram.nombre_del_bot()
    if not usuario:
        return {"disponible": False}
    codigo = telegram.generar_codigo(corredor.strip()[:64])
    return {"disponible": True, "url": f"https://t.me/{usuario}?start={codigo}"}


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
                    if evento.get("tipo") == "fin_turno":
                        await entrantes.put(("fin_turno", None))
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

    # El corredor se identifica con un id que el navegador guarda en
    # localStorage. Suficiente para el demo: sin cuentas ni contraseñas, pero
    # la memoria entre conversaciones funciona en el mismo dispositivo.
    corredor = (ws.query_params.get("corredor") or "").strip()[:64] or f"anon-{ip}"
    memoria = Memoria(corredor, conversacion=uuid4().hex)

    sesion = ConversacionCoach(enviar, memoria)
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
            "conversación cerrada | corredor %s | %d turnos | %.1f KB entrada | "
            "%.1f KB salida%s",
            corredor, sesion.turnos,
            sesion.bytes_entrada / 1024, sesion.bytes_salida / 1024,
            f" | {descartados} trozos descartados" if descartados else "",
        )
        if sesion.bytes_entrada == 0:
            log.warning(
                "el navegador no envió NADA de audio: revisa permisos del "
                "micrófono y la consola del navegador"
            )
