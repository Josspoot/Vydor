"""Tests de la vinculación de Telegram.

No tocan la red: se sustituye el envío por un doble que solo apunta lo que
se habría mandado. Lo que sí importa comprobar es que un código no sirve
dos veces y que un enlace ajeno no engancha la cuenta de nadie.
"""

import pytest

from app import telegram
from app.memoria import Memoria, corredor_por_codigo, corredores_notificables
from app.tools import esquemas


@pytest.fixture
def bd(tmp_path):
    return tmp_path / "telegram.db"


@pytest.fixture
def enviados(monkeypatch):
    """Sustituye el envío real por una lista de lo enviado."""
    registro = []

    async def falso_enviar(chat_id, texto):
        registro.append((chat_id, texto))
        return True

    monkeypatch.setattr(telegram, "enviar", falso_enviar)
    return registro


# --------------------------------------------------------------------------
# Códigos de vinculación
# --------------------------------------------------------------------------

def test_el_codigo_identifica_al_corredor(bd):
    codigo = telegram.generar_codigo("josue", ruta=bd)
    assert corredor_por_codigo(codigo, ruta=bd) == "josue"


def test_un_codigo_inventado_no_identifica_a_nadie(bd):
    telegram.generar_codigo("josue", ruta=bd)
    assert corredor_por_codigo("cualquier-cosa", ruta=bd) is None


def test_cada_codigo_es_distinto(bd):
    assert telegram.generar_codigo("a", ruta=bd) != telegram.generar_codigo("b", ruta=bd)


async def test_el_start_guarda_el_chat_del_corredor(bd, enviados):
    codigo = telegram.generar_codigo("josue", ruta=bd)
    await telegram._atender_mensaje(
        {"text": f"/start {codigo}", "chat": {"id": 4242}}, ruta=bd)

    perfil = Memoria("josue", "c", ruta=bd).perfil()
    assert perfil["telegram_chat_id"] == 4242
    assert enviados[0][0] == 4242
    assert "Vydor" in enviados[0][1]


async def test_el_codigo_se_quema_al_usarlo(bd, enviados):
    """Un enlace filtrado no puede servir para engancharse dos veces."""
    codigo = telegram.generar_codigo("josue", ruta=bd)
    await telegram._atender_mensaje({"text": f"/start {codigo}", "chat": {"id": 1}}, ruta=bd)
    await telegram._atender_mensaje({"text": f"/start {codigo}", "chat": {"id": 999}}, ruta=bd)

    assert Memoria("josue", "c", ruta=bd).perfil()["telegram_chat_id"] == 1
    assert "ya no vale" in enviados[-1][1]


async def test_un_start_sin_codigo_no_vincula_nada(bd, enviados):
    await telegram._atender_mensaje({"text": "/start", "chat": {"id": 7}}, ruta=bd)
    assert "ya no vale" in enviados[-1][1]


async def test_los_mensajes_que_no_son_start_se_ignoran(bd, enviados):
    await telegram._atender_mensaje({"text": "hola", "chat": {"id": 7}}, ruta=bd)
    assert enviados == []


# --------------------------------------------------------------------------
# A quién se le manda el recordatorio
# --------------------------------------------------------------------------

def test_solo_se_notifica_a_quien_tiene_telegram_y_plan(bd):
    plan = esquemas.ejecutar("generar_plan", {
        "distancia": "10k", "semanas": 12, "km_semanales_actuales": 25})

    # vinculado y con plan: sí
    a = Memoria("con-todo", "c", ruta=bd)
    a.guardar_plan(plan)
    a.actualizar_perfil(telegram_chat_id=1)

    # vinculado pero sin plan: no hay nada que recordarle
    Memoria("sin-plan", "c", ruta=bd).actualizar_perfil(telegram_chat_id=2)

    # con plan pero sin vincular: no hay por dónde escribirle
    Memoria("sin-telegram", "c", ruta=bd).guardar_plan(plan)

    assert corredores_notificables(ruta=bd) == ["con-todo"]


async def test_se_envia_el_entrenamiento_de_hoy(bd, enviados):
    plan = esquemas.ejecutar("generar_plan", {
        "distancia": "10k", "semanas": 12, "km_semanales_actuales": 25,
        "dias_por_semana": 4})
    memoria = Memoria("josue", "c", ruta=bd)
    memoria.guardar_plan(plan)
    memoria.actualizar_perfil(nombre="Josué", telegram_chat_id=555)

    assert await telegram.enviar_recordatorios(ruta=bd) == 1
    chat, texto = enviados[0]
    assert chat == 555
    assert "Josué" in texto
    assert "Semana 1 de 12" in texto


async def test_sin_nadie_vinculado_no_se_envia_nada(bd, enviados):
    assert await telegram.enviar_recordatorios(ruta=bd) == 0
    assert enviados == []
