"""Tests de los recordatorios proactivos.

Toda la lógica de fechas y redacción es pura: no toca la red ni Telegram.
"""

from datetime import date, timedelta

import pytest

from app.memoria import Memoria
from app.recordatorios import (
    recordatorio_para, redactar, semana_del_plan, sesion_de,
)
from app.tools import esquemas


@pytest.fixture
def plan():
    return esquemas.ejecutar("generar_plan", {
        "distancia": "10k", "semanas": 12, "km_semanales_actuales": 25,
        "dias_por_semana": 4,
        "marca_reciente_distancia_m": 10000, "marca_reciente_tiempo_s": 3000,
    })


# --------------------------------------------------------------------------
# En qué semana del plan estamos
# --------------------------------------------------------------------------

def test_el_dia_de_inicio_es_la_semana_uno():
    inicio = date(2026, 8, 12)          # miércoles
    assert semana_del_plan(inicio, inicio) == 1


def test_el_resto_de_esa_misma_semana_sigue_siendo_la_uno():
    """Empezar un miércoles no convierte el jueves en semana 2."""
    inicio = date(2026, 8, 12)          # miércoles
    assert semana_del_plan(inicio, date(2026, 8, 13)) == 1   # jueves
    assert semana_del_plan(inicio, date(2026, 8, 16)) == 1   # domingo


def test_el_lunes_siguiente_arranca_la_semana_dos():
    inicio = date(2026, 8, 12)          # miércoles
    assert semana_del_plan(inicio, date(2026, 8, 17)) == 2   # lunes


def test_las_semanas_avanzan_de_siete_en_siete():
    inicio = date(2026, 8, 10)          # lunes
    assert semana_del_plan(inicio, date(2026, 8, 24)) == 3
    assert semana_del_plan(inicio, date(2026, 10, 12)) == 10


# --------------------------------------------------------------------------
# Qué sesión toca
# --------------------------------------------------------------------------

def test_devuelve_la_sesion_del_dia_correcto(plan):
    inicio = date(2026, 8, 10)          # lunes
    domingo = date(2026, 8, 16)
    sesion = sesion_de(plan, inicio, domingo)
    assert sesion["dia"] == 7
    assert sesion["tipo"] == "largo"
    assert sesion["semana"] == 1


def test_antes_de_empezar_no_hay_sesion(plan):
    inicio = date(2026, 8, 10)
    assert sesion_de(plan, inicio, date(2026, 8, 1)) is None


def test_terminado_el_plan_no_hay_sesion(plan):
    inicio = date(2026, 8, 10)
    # 12 semanas después ya no queda plan
    assert sesion_de(plan, inicio, date(2026, 11, 30)) is None


def test_el_ultimo_dia_del_plan_todavia_tiene_sesion(plan):
    inicio = date(2026, 8, 10)          # lunes
    ultimo_domingo = inicio + timedelta(days=12 * 7 - 1)
    sesion = sesion_de(plan, inicio, ultimo_domingo)
    assert sesion is not None
    assert sesion["semana"] == 12


# --------------------------------------------------------------------------
# Redacción del mensaje
# --------------------------------------------------------------------------

def test_el_mensaje_nombra_el_entrenamiento_en_castellano_llano(plan):
    sesion = sesion_de(plan, date(2026, 8, 10), date(2026, 8, 16))
    texto = redactar(plan, sesion, "Josué")
    assert "Josué" in texto
    assert "Fondo largo" in texto
    assert "largo" == sesion["tipo"]           # el tipo crudo no se filtra
    assert "facil" not in texto and "km_fondo" not in texto


def test_el_mensaje_incluye_el_ritmo_objetivo(plan):
    sesion = sesion_de(plan, date(2026, 8, 10), date(2026, 8, 16))
    texto = redactar(plan, sesion)
    assert "Ritmo objetivo:" in texto
    assert "por km" in texto


def test_un_plan_sin_ritmos_no_inventa_ninguno():
    plan = esquemas.ejecutar("generar_plan", {
        "distancia": "5k", "semanas": 8, "km_semanales_actuales": 15})
    sesion = sesion_de(plan, date(2026, 8, 10), date(2026, 8, 16))
    assert "Ritmo objetivo" not in redactar(plan, sesion)


def test_el_dia_de_descanso_se_explica_en_positivo(plan):
    sesion = sesion_de(plan, date(2026, 8, 10), date(2026, 8, 10))  # lunes
    texto = redactar(plan, sesion)
    assert "descanso" in texto.lower()
    assert "parte del plan" in texto


def test_el_mensaje_situa_la_semana_y_nombra_el_plan(plan):
    """Con dos planes vivos, un mensaje suelto tiene que decir de cuál es."""
    sesion = sesion_de(plan, date(2026, 8, 10), date(2026, 8, 16))
    texto = redactar(plan, sesion)
    assert "semana 1 de 12" in texto
    assert "Plan 10K" in texto


def test_el_mensaje_es_corto(plan):
    """Llega a un teléfono: un muro de texto no se lee."""
    for dia in range(7):
        sesion = sesion_de(plan, date(2026, 8, 10), date(2026, 8, 10 + dia))
        assert len(redactar(plan, sesion)) < 420


def test_el_dia_de_carrera_lleva_su_aviso(plan):
    inicio = date(2026, 8, 10)
    ultimo_domingo = inicio + timedelta(days=12 * 7 - 1)
    texto = redactar(plan, sesion_de(plan, inicio, ultimo_domingo))
    assert "DÍA DE CARRERA" in texto
    assert "conservador" in texto


# --------------------------------------------------------------------------
# Integración con la memoria
# --------------------------------------------------------------------------

@pytest.fixture
def bd(tmp_path):
    return tmp_path / "recordatorios.db"


def _domingo_de_esta_semana() -> date:
    hoy = date.today()
    return hoy + timedelta(days=6 - hoy.weekday())


def test_sin_plan_guardado_no_hay_recordatorio(bd):
    Memoria("ana", "c1", ruta=bd)
    assert recordatorio_para("ana", date.today(), ruta=bd) is None


def test_recordatorio_completo_desde_la_memoria(bd, plan):
    memoria = Memoria("josue", "c1", ruta=bd)
    memoria.actualizar_perfil(nombre="Josué")
    memoria.guardar_plan(plan)

    # El plan se guarda hoy, así que cualquier día de esta semana es su semana 1.
    # La fecha se fija a propósito: con date.today() el test pasaba o fallaba
    # según el día de la semana en que se ejecutara.
    texto = recordatorio_para("josue", _domingo_de_esta_semana(), ruta=bd)
    assert texto is not None
    assert "Josué" in texto
    assert "semana 1 de 12" in texto


def test_una_molestia_abierta_se_pregunta_antes_de_entrenar(bd, plan):
    memoria = Memoria("josue", "c1", ruta=bd)
    memoria.guardar_plan(plan)
    memoria.actualizar_perfil(molestia_reciente="rodilla derecha")

    texto = recordatorio_para("josue", _domingo_de_esta_semana(), ruta=bd)
    assert "rodilla derecha" in texto
    assert "antes de entrenar" in texto
