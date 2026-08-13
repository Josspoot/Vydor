"""Tests de la capa que expone el motor al modelo.

El caso crítico: un dolor sin explorar no puede salir clasificado como
molestia normal solo porque el modelo llamó a la herramienta con prisa.
"""

import json

import pytest

from app.tools import esquemas


# --------------------------------------------------------------------------
# Triaje: exigir datos antes de dictaminar
# --------------------------------------------------------------------------

def test_sintoma_sin_datos_no_dictamina():
    r = esquemas.ejecutar("evaluar_sintoma", {"zona": "rodilla derecha"})
    assert "nivel" not in r, "no puede emitir veredicto sin explorar el dolor"
    assert set(r["faltan_datos"]) == set(esquemas.DATOS_MINIMOS_SINTOMA)
    assert "pregunta" in r["instruccion"].lower()


@pytest.mark.parametrize("omitido", esquemas.DATOS_MINIMOS_SINTOMA)
def test_falta_cualquiera_de_los_datos_clave(omitido):
    datos = {
        "zona": "tibia",
        "duele_en_reposo": False,
        "cambia_la_pisada": False,
        "mejora_al_calentar": True,
    }
    del datos[omitido]
    r = esquemas.ejecutar("evaluar_sintoma", datos)
    assert r["faltan_datos"] == [omitido]


def test_con_los_datos_completos_si_dictamina():
    r = esquemas.ejecutar("evaluar_sintoma", {
        "zona": "gemelo", "duele_en_reposo": False,
        "cambia_la_pisada": False, "mejora_al_calentar": True,
    })
    assert r["nivel"] == "molestia_normal"


def test_bandera_roja_llega_intacta_a_traves_del_despacho():
    r = esquemas.ejecutar("evaluar_sintoma", {
        "zona": "tibia", "duele_en_reposo": False,
        "cambia_la_pisada": True, "mejora_al_calentar": False,
    })
    assert r["nivel"] == "parar_y_consultar"
    assert r["puede_entrenar"] is False


def test_los_datos_clave_son_obligatorios_en_la_declaracion():
    decl = next(d for d in esquemas.DECLARACIONES if d["name"] == "evaluar_sintoma")
    for campo in esquemas.DATOS_MINIMOS_SINTOMA:
        assert campo in decl["parameters"]["required"]


# --------------------------------------------------------------------------
# Resto del despacho
# --------------------------------------------------------------------------

def test_toda_funcion_declarada_tiene_implementacion():
    declaradas = {d["name"] for d in esquemas.DECLARACIONES}
    assert declaradas == set(esquemas.FUNCIONES)


def test_el_plan_trae_resumen_decible():
    r = esquemas.ejecutar("generar_plan", {
        "distancia": "10k", "semanas": 12, "km_semanales_actuales": 25,
        "dias_por_semana": 4,
        "marca_reciente_distancia_m": 10000, "marca_reciente_tiempo_s": 3000,
    })
    resumen = r["resumen_hablado"]
    # En voz no se pueden recitar 12 semanas ni leer markdown
    assert len(resumen) < 600
    assert "*" not in resumen and "\n" not in resumen
    assert "10k" in resumen


def test_los_errores_no_lanzan_excepcion():
    """Una excepción aquí cortaría la conversación de voz."""
    r = esquemas.ejecutar("generar_plan", {"distancia": "99k", "semanas": 12,
                                           "km_semanales_actuales": 25})
    assert "error" in r
    assert "sugerencia" in r


def test_funcion_desconocida_devuelve_error():
    assert "error" in esquemas.ejecutar("no_existe", {})


def test_todo_resultado_es_serializable_a_json():
    casos = [
        ("evaluar_viabilidad", {"distancia": "21k", "semanas": 8,
                                "km_semanales_actuales": 20}),
        ("calcular_ritmos", {"marca_distancia_m": 5000, "marca_tiempo_s": 1500}),
        ("predecir_marca", {"marca_distancia_m": 10000, "marca_tiempo_s": 3000,
                            "distancia_objetivo_m": 42195}),
        ("evaluar_sintoma", {"zona": "pie", "duele_en_reposo": True,
                             "cambia_la_pisada": False, "mejora_al_calentar": False}),
    ]
    for nombre, args in casos:
        json.dumps(esquemas.ejecutar(nombre, args))
