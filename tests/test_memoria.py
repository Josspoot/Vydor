"""Tests de la memoria.

Importa doblemente: el historial es lo que mantiene el hilo dentro de una
conversación (una sesión de Gemini por turno) y el perfil es lo que la
mantiene entre conversaciones.
"""

import json
from datetime import date

import pytest

from app.memoria import Memoria, _ahora, _fecha_local


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


def test_guardar_plan_solo_copia_al_perfil_lo_que_es_de_la_persona(memoria):
    """El VDOT es fisiología y viaja con el corredor; la meta no.

    Guardar la distancia en el perfil hacía que un segundo plan reescribiera
    lo que el coach recordaba del primero.
    """
    memoria.guardar_plan({
        "distancia": "42k",
        "semanas": 18,
        "dias_por_semana": 5,
        "vdot": 44.2,
        "viabilidad": {"km_pico_alcanzable": 70.0},
    })
    perfil = memoria.perfil()
    assert perfil["vdot"] == 44.2
    assert "distancia_objetivo" not in perfil
    assert "semanas_plan" not in perfil


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
    memoria.actualizar_perfil(nombre="Josué")
    memoria.guardar_plan({"distancia": "21k", "semanas": 14, "dias_por_semana": 4})
    resumen = memoria.resumen_para_prompt()
    assert "Josué" in resumen
    assert "21k" in resumen
    assert "14" in resumen


def test_dos_metas_a_la_vez_no_se_pisan(bd):
    """Cada charla habla de su plan, aunque el otro sea más reciente."""
    diez = Memoria("corredor-1", "charla-10k", ruta=bd)
    diez.guardar_plan({"distancia": "10k", "semanas": 12, "dias_por_semana": 4})

    media = Memoria("corredor-1", "charla-21k", ruta=bd)
    media.guardar_plan({"distancia": "21k", "semanas": 16, "dias_por_semana": 5})

    # Volver a la charla del 10K: el coach debe seguir hablando del 10K.
    de_vuelta = Memoria("corredor-1", "charla-10k", ruta=bd)
    resumen = de_vuelta.resumen_para_prompt()
    assert "10k" in resumen
    assert "12 semanas" in resumen
    # El 21K existe, pero nombrado como lo que es: otra meta.
    assert "otras conversaciones" in resumen.lower()


def test_el_plan_activo_manda_los_recordatorios(bd):
    """Con dos planes vivos, el corredor elige de cuál quiere recordatorios."""
    diez = Memoria("corredor-1", "charla-10k", ruta=bd)
    id_diez = diez.guardar_plan({"distancia": "10k", "semanas": 12})

    media = Memoria("corredor-1", "charla-21k", ruta=bd)
    media.guardar_plan({"distancia": "21k", "semanas": 16})

    # Sin elegir nada, manda el más reciente: el comportamiento de siempre.
    assert media.ultimo_plan()["distancia"] == "21k"

    assert media.activar_plan(id_diez) is True
    assert media.ultimo_plan()["distancia"] == "10k"


def test_rehacer_el_plan_de_la_misma_charla_mueve_el_activo(bd):
    """Corregir la misma meta no debería obligar a re-elegir el plan activo."""
    charla = Memoria("corredor-1", "charla-10k", ruta=bd)
    charla.guardar_plan({"distancia": "10k", "semanas": 12})
    charla.guardar_plan({"distancia": "10k", "semanas": 14})   # se rehace
    assert charla.ultimo_plan()["semanas"] == 14


def test_un_plan_de_otra_charla_no_roba_el_activo(bd):
    """Pero una meta distinta sí respeta lo que el corredor eligió."""
    diez = Memoria("corredor-1", "charla-10k", ruta=bd)
    id_diez = diez.guardar_plan({"distancia": "10k", "semanas": 12})
    diez.activar_plan(id_diez)

    media = Memoria("corredor-1", "charla-21k", ruta=bd)
    media.guardar_plan({"distancia": "21k", "semanas": 16})

    assert media.ultimo_plan()["distancia"] == "10k"


def test_no_se_puede_activar_el_plan_de_otro_corredor(bd):
    ajeno = Memoria("corredor-2", "suya", ruta=bd)
    id_ajeno = ajeno.guardar_plan({"distancia": "5k", "semanas": 8})

    mia = Memoria("corredor-1", "mia", ruta=bd)
    mia.guardar_plan({"distancia": "10k", "semanas": 12})
    assert mia.activar_plan(id_ajeno) is False
    assert mia.ultimo_plan()["distancia"] == "10k"


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


def test_lo_guardado_ahora_cuenta_como_hoy_a_cualquier_hora():
    """Las marcas se guardan en UTC; el día natural es el local.

    Sin convertir, a partir de las 18:00 en México la fecha UTC ya era la de
    mañana: el resumen decía "hace -1 días" y la semana del plan que usan los
    recordatorios se corría un día. Este invariante se cumple a toda hora y en
    cualquier zona, así que el test no depende de cuándo se ejecute.
    """
    assert _fecha_local(_ahora()) == date.today()


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


# --------------------------------------------------------------------------
# Historial: listar charlas y planes
# --------------------------------------------------------------------------

def test_las_conversaciones_se_listan_de_la_mas_reciente_a_la_mas_vieja(bd):
    from app.memoria import conversaciones
    a = Memoria("josue", "vieja", ruta=bd)
    a.guardar_turno("user", "Quiero un 10k")
    b = Memoria("josue", "nueva", ruta=bd)
    b.guardar_turno("user", "Ahora una media")

    lista = conversaciones("josue", ruta=bd)
    assert [c["id"] for c in lista] == ["nueva", "vieja"]


def test_el_titulo_de_la_charla_es_lo_primero_que_dijo_el_corredor(bd):
    from app.memoria import conversaciones
    m = Memoria("josue", "c", ruta=bd)
    m.guardar_turno("user", "Quiero correr un maratón")
    m.guardar_turno("model", "Perfecto")
    m.guardar_turno("user", "En dieciocho semanas")

    assert conversaciones("josue", ruta=bd)[0]["titulo"] == "Quiero correr un maratón"


def test_la_charla_dice_cuántos_planes_salieron_de_ella(bd):
    from app.memoria import conversaciones
    m = Memoria("josue", "c", ruta=bd)
    m.guardar_turno("user", "hola")
    m.guardar_plan({"distancia": "10k", "semanas": 12})

    assert conversaciones("josue", ruta=bd)[0]["planes"] == 1


def test_no_se_ven_las_conversaciones_de_otro_corredor(bd):
    from app.memoria import conversaciones
    Memoria("ana", "suya", ruta=bd).guardar_turno("user", "privado")
    assert conversaciones("beto", ruta=bd) == []


def test_se_listan_los_planes_con_su_resumen(bd):
    from app.memoria import planes_de
    m = Memoria("josue", "c", ruta=bd)
    m.guardar_plan({"distancia": "5k", "semanas": 8, "dias_por_semana": 3})
    m.guardar_plan({"distancia": "42k", "semanas": 18, "dias_por_semana": 5})

    lista = planes_de("josue", ruta=bd)
    assert [p["distancia"] for p in lista] == ["42k", "5k"]
    assert lista[0]["semanas"] == 18


def test_un_plan_no_se_lee_desde_otra_cuenta(bd):
    from app.memoria import plan_por_id
    Memoria("josue", "c", ruta=bd).guardar_plan({"distancia": "10k", "semanas": 12})
    assert plan_por_id(1, "josue", ruta=bd)["distancia"] == "10k"
    assert plan_por_id(1, "intruso", ruta=bd) is None


def test_la_transcripcion_devuelve_la_charla_en_orden(bd):
    from app.memoria import transcripcion
    m = Memoria("josue", "c", ruta=bd)
    m.guardar_turno("user", "primero")
    m.guardar_turno("model", "segundo")

    turnos = transcripcion("c", "josue", ruta=bd)
    assert [t["quien"] for t in turnos] == ["corredor", "coach"]
    assert turnos[0]["texto"] == "primero"


def test_la_transcripcion_ajena_sale_vacia(bd):
    from app.memoria import transcripcion
    Memoria("ana", "suya", ruta=bd).guardar_turno("user", "privado")
    assert transcripcion("suya", "beto", ruta=bd) == []


def test_retomar_una_conversacion_recupera_su_historial(bd):
    """Es lo que permite seguir una charla días después."""
    Memoria("josue", "charla-x", ruta=bd).guardar_turno("user", "Quiero un 21k")
    retomada = Memoria("josue", "charla-x", ruta=bd)
    assert len(retomada.historial()) == 1
    assert retomada.historial()[0]["parts"][0]["text"] == "Quiero un 21k"
