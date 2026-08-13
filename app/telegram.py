"""Recordatorios por Telegram.

Se eligió Telegram sobre WhatsApp a propósito: la Cloud API de Meta exige
verificación de negocio y aprobación de plantillas, con plazos que no encajan
en la duración de este proyecto, y cobra por conversación. Aquí basta con
hablar con @BotFather y pegar el token.

Vinculación: el corredor abre un enlace con un código de un solo uso, el bot
recibe "/start <codigo>" y así se aprende su chat_id. Sin formularios ni
pedirle que copie nada.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets

import httpx

from app.memoria import Memoria, corredor_por_codigo, corredores_notificables
from app.recordatorios import recordatorio_para

log = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
BASE = f"https://api.telegram.org/bot{TOKEN}"
HORA_RECORDATORIO = int(os.getenv("TELEGRAM_HORA", "7"))

BIENVENIDA = (
    "¡Listo! Soy Vydor, tu entrenador.\n\n"
    "A partir de mañana te escribo cada mañana con el entrenamiento del día: "
    "qué toca, cuántos kilómetros y a qué ritmo.\n\n"
    "Si algo te duele o no puedes entrenar, dímelo hablando conmigo en la web "
    "y ajusto el plan."
)


def configurado() -> bool:
    return bool(TOKEN)


# --------------------------------------------------------------------------
# Envío
# --------------------------------------------------------------------------

async def enviar(chat_id: int | str, texto: str) -> bool:
    """Manda un mensaje. Nunca lanza: un fallo aquí no debe tumbar el servidor."""
    if not configurado():
        log.warning("TELEGRAM_BOT_TOKEN sin definir: no se envía nada")
        return False
    try:
        async with httpx.AsyncClient(timeout=20) as cliente:
            r = await cliente.post(f"{BASE}/sendMessage", json={
                "chat_id": chat_id,
                "text": texto,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            })
        if r.status_code != 200:
            log.warning("Telegram rechazó el envío (%s): %s", r.status_code, r.text[:200])
            return False
        return True
    except httpx.HTTPError as exc:
        log.warning("no se pudo hablar con Telegram: %s", exc)
        return False


async def nombre_del_bot() -> str | None:
    if not configurado():
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as cliente:
            r = await cliente.get(f"{BASE}/getMe")
        return r.json()["result"]["username"] if r.status_code == 200 else None
    except (httpx.HTTPError, KeyError, ValueError):
        return None


# --------------------------------------------------------------------------
# Vinculación de la cuenta
# --------------------------------------------------------------------------

def generar_codigo(corredor_id: str, ruta=None) -> str:
    """Código de un solo uso que viaja en el enlace de Telegram."""
    codigo = secrets.token_urlsafe(9)
    Memoria(corredor_id, conversacion="vinculacion", ruta=ruta).actualizar_perfil(
        codigo_telegram=codigo
    )
    return codigo


async def escuchar(ruta=None) -> None:
    """Escucha en segundo plano los /start y guarda el chat_id de cada corredor.

    Usa long polling en vez de webhook porque el webhook necesitaría una URL
    pública con HTTPS, y esto tiene que funcionar en localhost.
    """
    if not configurado():
        log.info("Telegram desactivado: falta TELEGRAM_BOT_TOKEN")
        return

    usuario = await nombre_del_bot()
    log.info("bot de Telegram activo: @%s", usuario or "desconocido")
    desplazamiento = None

    while True:
        try:
            async with httpx.AsyncClient(timeout=40) as cliente:
                r = await cliente.get(f"{BASE}/getUpdates", params={
                    "timeout": 30,
                    **({"offset": desplazamiento} if desplazamiento else {}),
                })
            if r.status_code != 200:
                await asyncio.sleep(5)
                continue

            for update in r.json().get("result", []):
                desplazamiento = update["update_id"] + 1
                await _atender_mensaje(update.get("message") or {}, ruta)

        except asyncio.CancelledError:
            raise
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            log.warning("fallo escuchando Telegram: %s", exc)
            await asyncio.sleep(5)


async def _atender_mensaje(mensaje: dict, ruta=None) -> None:
    texto = (mensaje.get("text") or "").strip()
    chat_id = (mensaje.get("chat") or {}).get("id")
    if not chat_id or not texto.startswith("/start"):
        return

    partes = texto.split(maxsplit=1)
    codigo = partes[1].strip() if len(partes) > 1 else ""
    corredor = corredor_por_codigo(codigo, ruta) if codigo else None

    if not corredor:
        await enviar(chat_id, "Ese enlace ya no vale. Genera uno nuevo desde la web.")
        return

    memoria = Memoria(corredor, conversacion="vinculacion", ruta=ruta)
    memoria.actualizar_perfil(telegram_chat_id=chat_id)
    # El código se quema al usarlo: un enlace filtrado no sirve dos veces.
    memoria.borrar_del_perfil("codigo_telegram")
    log.info("corredor %s vinculado al chat %s", corredor, chat_id)
    await enviar(chat_id, BIENVENIDA)


# --------------------------------------------------------------------------
# Recordatorio diario
# --------------------------------------------------------------------------

async def enviar_recordatorios(ruta=None) -> int:
    """Manda a cada corredor vinculado su entrenamiento de hoy."""
    enviados = 0
    for corredor in corredores_notificables(ruta):
        texto = recordatorio_para(corredor, ruta=ruta)
        if not texto:
            continue
        chat = Memoria(corredor, "recordatorio", ruta=ruta).perfil().get("telegram_chat_id")
        if chat and await enviar(chat, texto):
            enviados += 1
    log.info("recordatorios enviados: %d", enviados)
    return enviados
