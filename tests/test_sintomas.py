"""Tests del triaje de molestias.

El caso que más importa: que el coach NUNCA entregue una rutina cuando
hay señales de alarma.
"""

import pytest

from app.tools import sintomas as s


# --------------------------------------------------------------------------
# Emergencias: mandan a parar sin matices
# --------------------------------------------------------------------------

@pytest.mark.parametrize("sintoma", sorted(s.SINTOMAS_SISTEMICOS))
def test_todo_sintoma_sistemico_es_emergencia(sintoma):
    e = s.evaluar_sintoma("pecho", sintomas_sistemicos=[sintoma])
    assert e.nivel == "emergencia"
    assert e.puede_entrenar is False


def test_emergencia_gana_sobre_cualquier_otra_señal():
    """Aunque todo lo demás pinte benigno, el síntoma sistémico manda."""
    e = s.evaluar_sintoma(
        "rodilla",
        dias_con_sintoma=1,
        mejora_al_calentar=True,
        sintomas_sistemicos=["dolor_pecho"],
    )
    assert e.nivel == "emergencia"


def test_sintoma_sistemico_desconocido_se_ignora():
    e = s.evaluar_sintoma("rodilla", sintomas_sistemicos=["aburrimiento"])
    assert e.nivel != "emergencia"


# --------------------------------------------------------------------------
# Banderas rojas: parar y consultar
# --------------------------------------------------------------------------

def test_cojera_manda_a_consultar():
    e = s.evaluar_sintoma("rodilla derecha", cambia_la_pisada=True)
    assert e.nivel == "parar_y_consultar"
    assert e.puede_entrenar is False


def test_dolor_en_reposo_manda_a_consultar():
    e = s.evaluar_sintoma("tendón de Aquiles", duele_en_reposo=True)
    assert e.nivel == "parar_y_consultar"
    assert e.puede_entrenar is False


def test_sospecha_de_fractura_por_estres():
    """Punto óseo punzante que NO mejora al calentar."""
    e = s.evaluar_sintoma(
        "tibia",
        dolor_punzante_localizado=True,
        mejora_al_calentar=False,
    )
    assert e.nivel == "parar_y_consultar"


def test_ninguna_bandera_roja_permite_entrenar():
    e = s.evaluar_sintoma("gemelo", dias_con_sintoma=2, mejora_al_calentar=True)
    assert e.nivel == "molestia_normal"
    assert e.puede_entrenar is True


# --------------------------------------------------------------------------
# Precaución: zona intermedia
# --------------------------------------------------------------------------

def test_dos_señales_leves_dan_precaucion():
    e = s.evaluar_sintoma("planta del pie", hinchazon=True, mejora_al_calentar=False)
    assert e.nivel == "precaucion"
    assert e.puede_entrenar is True
    assert "40" in e.ajuste_sugerido


def test_molestia_persistente_una_semana_da_precaucion():
    e = s.evaluar_sintoma("cadera", dias_con_sintoma=8)
    assert e.nivel == "precaucion"


# --------------------------------------------------------------------------
# Invariantes generales
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "kwargs",
    [
        {"cambia_la_pisada": True},
        {"duele_en_reposo": True},
        {"sintomas_sistemicos": ["desmayo"]},
        {"dolor_punzante_localizado": True, "mejora_al_calentar": False},
    ],
)
def test_nunca_se_permite_entrenar_con_bandera_roja(kwargs):
    e = s.evaluar_sintoma("zona", **kwargs)
    assert e.puede_entrenar is False, f"permitió entrenar con {kwargs}"


def test_la_zona_aparece_en_el_mensaje():
    e = s.evaluar_sintoma("tendón de Aquiles izquierdo", cambia_la_pisada=True)
    assert "Aquiles" in e.mensaje


def test_evaluacion_es_serializable():
    import json
    e = s.evaluar_sintoma("rodilla", cambia_la_pisada=True)
    assert json.loads(json.dumps(e.a_dict()))["nivel"] == "parar_y_consultar"
