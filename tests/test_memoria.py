"""Tests de la memoria.

Importa doblemente: el historial es lo que mantiene el hilo dentro de una
conversación (una sesión de Gemini por turno) y el perfil es lo que la
mantiene entre conversaciones.
"""

import json

import pytest

from app.memoria import Memoria


@pytest.fixture
def bd(tmp_path):
    return tmp_path / "prueba.db"


@pytest.fixture
def memoria(bd):
    return Memoria("corredor-1", "charla-1", ruta=bd)


# --------------------------------------------------------------------------
# Historial dentro de una conversación
# --------------------------------------------------------------------------

def test_historial_vacio_al_empezar(memoria):
    assert memoria.historial() == []


def test_historial_conserva_el_orden(memoria):
    memoria.guardar_turno("user", "Quiero correr un 10k")
    memoria.guardar_turno("model", "¿Cuántas semanas tienes?")
    memoria.guardar_turno("user", "Doce")

    historial = memoria.historial()
    assert [t["role"] for t in historial] == ["user", "model", "user"]
    assert historial[0]["parts"][0]["text"] == "Quiero correr un 10k"
    assert historial[-1]["parts"][0]["text"] == "Doce"


def test_historial_tiene_el_formato_que_espera_la_api(memoria):
    memoria.guardar_turno("user", "hola")
    turno = memoria.historial()[0]
    assert set(turno) == {"role", "parts"}
    assert turno["parts"][0]["text"] == "hola"


def test_historial_se_recorta_a_los_ultimos_turnos(memoria):
    for i in range(30):
        memoria.guardar_turno("user", f"mensaje {i}")
    historial = memoria.historial(max_turnos=5)
    assert len(historial) == 5
    # Se conservan los más recientes, no los primeros
    assert historial[-1]["parts"][0]["text"] == "mensaje 29"


def test_no_se_guardan_turnos_vacios(memoria):
    memoria.guardar_turno("user", "   ")
    memoria.guardar_turno("user", "")
    assert memoria.historial() == []


def test_el_historial_no_mezcla_conversaciones(bd):
    uno = Memoria("corredor-1", "charla-1", ruta=bd)
    dos = Memoria("corredor-1", "charla-2", ruta=bd)
    uno.guardar_turno("user", "de la primera charla")
    dos.guardar_turno("user", "de la segunda charla")

    assert len(uno.historial()) == 1
    assert uno.historial()[0]["parts"][0]["text"] == "de la primera charla"
    assert dos.historial()[0]["parts"][0]["text"] == "de la segunda charla"


def test_el_historial_no_mezcla_corredores(bd):
    a = Memoria("ana", "charla", ruta=bd)
    b = Memoria("beto", "charla", ruta=bd)
    a.guardar_turno("user", "soy Ana")
    assert b.historial() == []


# --------------------------------------------------------------------------
# Perfil entre conversaciones
# --------------------------------------------------------------------------

def test_perfil_arranca_vacio(memoria):
    assert memoria.perfil() == {}


def test_actualizar_perfil_fusiona_sin_borrar(memoria):
    memoria.actualizar_perfil(nombre="Josué", distancia_objetivo="21k")
    memoria.actualizar_perfil(vdot=45.0)
    perfil = memoria.perfil()
    assert perfil["nombre"] == "Josué"
    assert perfil["distancia_objetivo"] == "21k"
    assert perfil["vdot"] == 45.0


def test_los_none_no_pisan_valores_existentes(memoria):
    memoria.actualizar_perfil(vdot=50.0)
    memoria.actualizar_perfil(vdot=None, distancia_objetivo="10k")
    assert memoria.perfil()["vdot"] == 50.0


def test_el_perfil_sobrevive_a_una_nueva_conversacion(bd):
    Memoria("corredor-1", "charla-1", ruta=bd).actualizar_perfil(nombre="Josué")
    otra = Memoria("corredor-1", "charla-2", ruta=bd)
    assert otra.perfil()["nombre"] == "Josué"


def test_guardar_plan_alimenta_el_perfil(memoria):
    memoria.guardar_plan({
        "distancia": "42k",
        "semanas": 18,
        "dias_por_semana": 5,
        "vdot": 44.2,
        "viabilidad": {"km_pico_alcanzable": 70.0},
    })
    perfil = memoria.perfil()
    assert perfil["distancia_objetivo"] == "42k"
    assert perfil["semanas_plan"] == 18
    assert perfil["vdot"] == 44.2
    assert perfil["km_pico"] == 70.0


def test_se_recupera_el_ultimo_plan(memoria):
    memoria.guardar_plan({"distancia": "5k", "semanas": 8})
    memoria.guardar_plan({"distancia": "10k", "semanas": 12})
    assert memoria.ultimo_plan()["distancia"] == "10k"


def test_sin_planes_no_hay_ultimo_plan(memoria):
    assert memoria.ultimo_plan() is None


# --------------------------------------------------------------------------
# Resumen que se inyecta en el prompt
# --------------------------------------------------------------------------

def test_corredor_nuevo_no_genera_resumen(memoria):
    assert memoria.resumen_para_prompt() is None


def test_el_resumen_menciona_nombre_y_objetivo(memoria):
    memoria.actualizar_perfil(nombre="Josué", distancia_objetivo="21k",
                              semanas_plan=14, dias_por_semana=4)
    resumen = memoria.resumen_para_prompt()
    assert "Josué" in resumen
    assert "21k" in resumen
    assert "14" in resumen


def test_el_resumen_recuerda_una_molestia(memoria):
    memoria.actualizar_perfil(molestia_reciente="rodilla derecha")
    assert "rodilla derecha" in memoria.resumen_para_prompt()


def test_el_resumen_recoge_la_charla_anterior(bd):
    vieja = Memoria("corredor-1", "charla-1", ruta=bd)
    vieja.guardar_turno("model", "Nos vemos, avísame cómo salió el fondo del domingo.")

    nueva = Memoria("corredor-1", "charla-2", ruta=bd)
    resumen = nueva.resumen_para_prompt()
    assert "fondo del domingo" in resumen
    assert "hoy" in resumen


def test_el_resumen_ignora_la_conversacion_en_curso(memoria):
    """Lo dicho hace un minuto ya está en el historial; repetirlo sobra."""
    memoria.guardar_turno("model", "Esto es de la charla actual")
    assert memoria.resumen_para_prompt() is None


def test_el_resumen_es_prosa_no_una_ficha(memoria):
    """Va en el prompt de un coach de voz: no puede sonar a expediente."""
    memoria.actualizar_perfil(nombre="Ana", distancia_objetivo="10k", vdot=48.0)
    resumen = memoria.resumen_para_prompt()
    assert "\n" not in resumen
    assert ":" not in resumen.replace("así:", "")


def test_borrar_del_perfil_elimina_de_verdad(memoria):
    """actualizar_perfil ignora los None; borrar exige un método propio."""
    memoria.actualizar_perfil(codigo_telegram="abc", nombre="Ana")
    memoria.actualizar_perfil(codigo_telegram=None)
    assert memoria.perfil()["codigo_telegram"] == "abc", "los None no deben borrar"

    memoria.borrar_del_perfil("codigo_telegram")
    assert "codigo_telegram" not in memoria.perfil()
    assert memoria.perfil()["nombre"] == "Ana", "no debe tocar el resto"


def test_borrar_una_clave_inexistente_no_falla(memoria):
    memoria.borrar_del_perfil("no_existe")
