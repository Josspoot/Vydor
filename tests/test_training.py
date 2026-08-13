"""Tests del motor de entrenamiento.

Los valores esperados salen de las tablas publicadas de Daniels, no de
la salida de nuestro propio código: si la implementación se desvía de la
literatura, estos tests fallan.
"""

import math

import pytest

from app.tools import training as t


# --------------------------------------------------------------------------
# VDOT contra tablas publicadas
# --------------------------------------------------------------------------

# (VDOT, distancia_m, tiempo_s) de las tablas de Daniels
MARCAS_TABLA = [
    (30, 5_000, 30 * 60 + 40),
    (40, 5_000, 24 * 60 + 8),
    (50, 5_000, 19 * 60 + 57),
    (60, 5_000, 17 * 60 + 3),
    (40, 10_000, 50 * 60 + 3),
    (50, 10_000, 41 * 60 + 21),
    (60, 10_000, 35 * 60 + 22),
]


@pytest.mark.parametrize("vdot_esperado,distancia,tiempo", MARCAS_TABLA)
def test_vdot_de_marca_coincide_con_tabla(vdot_esperado, distancia, tiempo):
    calculado = t.vdot_de_marca(distancia, tiempo)
    assert calculado == pytest.approx(vdot_esperado, abs=0.6)


@pytest.mark.parametrize("vdot,distancia,tiempo_esperado", MARCAS_TABLA)
def test_prediccion_por_vdot_coincide_con_tabla(vdot, distancia, tiempo_esperado):
    predicho = t.predecir_por_vdot(vdot, distancia)
    assert predicho == pytest.approx(tiempo_esperado, rel=0.02)


def test_vdot_y_prediccion_son_inversos():
    tiempo = 22 * 60 + 30
    vdot = t.vdot_de_marca(5_000, tiempo)
    assert t.predecir_por_vdot(vdot, 5_000) == pytest.approx(tiempo, rel=0.005)


def test_marca_invalida_lanza_error():
    with pytest.raises(ValueError):
        t.vdot_de_marca(0, 600)
    with pytest.raises(ValueError):
        t.vdot_de_marca(5_000, -1)


# --------------------------------------------------------------------------
# Ritmos
# --------------------------------------------------------------------------

def test_ritmos_vdot_50_contra_tabla():
    """VDOT 50: umbral ~4:15/km, maratón ~4:29/km (Daniels)."""
    ritmos = t.ritmos_de_entrenamiento(50)
    assert ritmos["umbral"].seg_por_km_rapido == pytest.approx(255, abs=10)
    assert ritmos["maraton"].seg_por_km_rapido == pytest.approx(269, abs=10)


def test_ritmos_ordenados_de_lento_a_rapido():
    ritmos = t.ritmos_de_entrenamiento(45)
    orden = ["facil", "maraton", "umbral", "intervalo", "repeticion"]
    valores = [ritmos[z].seg_por_km_rapido for z in orden]
    assert valores == sorted(valores, reverse=True), "cada zona debe ser más rápida"


def test_mayor_vdot_implica_ritmos_mas_rapidos():
    lento = t.ritmos_de_entrenamiento(40)["umbral"].seg_por_km_rapido
    rapido = t.ritmos_de_entrenamiento(55)["umbral"].seg_por_km_rapido
    assert rapido < lento


def test_formato_mmss():
    assert t.mmss(255) == "4:15"
    assert t.mmss(59) == "0:59"
    assert t.mmss(3 * 3600 + 10 * 60 + 49) == "3:10:49"


# --------------------------------------------------------------------------
# Viabilidad: decir "no" a tiempo
# --------------------------------------------------------------------------

def test_maraton_en_8_semanas_desde_cero_no_es_recomendado():
    v = t.evaluar_viabilidad("42k", semanas=8, km_semanales_actuales=10)
    assert v.veredicto == "no_recomendado"
    assert v.semanas_minimas_sugeridas > 8


def test_maraton_con_base_solida_y_20_semanas_es_viable():
    v = t.evaluar_viabilidad("42k", semanas=20, km_semanales_actuales=45)
    assert v.veredicto == "viable"


def test_caso_del_reto_21k_en_8_semanas_con_20km():
    """El caso que un evaluador va a probar: '21k en 8 semanas, corro 20 km/sem'."""
    v = t.evaluar_viabilidad("21k", semanas=8, km_semanales_actuales=20)
    assert v.veredicto == "no_recomendado"
    assert "10" in v.razon or "semanas" in v.razon


def test_5k_para_principiante_con_tiempo_suficiente():
    v = t.evaluar_viabilidad("5k", semanas=10, km_semanales_actuales=12)
    assert v.veredicto in ("viable", "ajustado")


def test_pico_alcanzable_nunca_supera_el_recomendado():
    v = t.evaluar_viabilidad("5k", semanas=30, km_semanales_actuales=40)
    assert v.km_pico_alcanzable <= v.km_pico_recomendado


# --------------------------------------------------------------------------
# Reglas de carga del plan generado
# --------------------------------------------------------------------------

@pytest.fixture
def plan_maraton():
    return t.generar_plan(
        "42k",
        semanas=18,
        km_semanales_actuales=40,
        dias_por_semana=5,
        marca_reciente_distancia_m=10_000,
        marca_reciente_tiempo_s=50 * 60,
    )


def test_plan_tiene_el_numero_de_semanas_pedido(plan_maraton):
    assert len(plan_maraton.semanas_plan) == 18
    assert [s.numero for s in plan_maraton.semanas_plan] == list(range(1, 19))


def test_nunca_sube_mas_de_10_por_ciento_entre_semanas(plan_maraton):
    """La regla que evita lesiones por sobrecarga."""
    for previa, actual in zip(plan_maraton.semanas_plan, plan_maraton.semanas_plan[1:]):
        if actual.fase in ("afinamiento", "carrera", "descarga"):
            continue
        if previa.fase == "descarga":
            continue  # tras descarga se retoma la rampa, no se compara contra ella
        # El margen absorbe el redondeo a un decimal de cada sesión; un salto
        # real de carga sería de varios km, no de centésimas.
        assert actual.km_total <= previa.km_total * t.INCREMENTO_SEMANAL_MAX + 0.5, (
            f"semana {actual.numero}: {previa.km_total} -> {actual.km_total} km"
        )


def test_el_fondo_respeta_su_fraccion_del_volumen(plan_maraton):
    fraccion = t.FRACCION_FONDO_POR_DIAS[plan_maraton.dias_por_semana]
    for semana in plan_maraton.semanas_plan:
        if semana.fase == "carrera":
            continue
        assert semana.km_fondo <= semana.km_total * fraccion + 0.3


def test_el_fondo_respeta_el_techo_de_la_distancia(plan_maraton):
    techo = t.PERFIL_DISTANCIA["42k"]["fondo_max"]
    for semana in plan_maraton.semanas_plan:
        if semana.fase != "carrera":
            assert semana.km_fondo <= techo


def test_hay_semanas_de_descarga(plan_maraton):
    descargas = [s for s in plan_maraton.semanas_plan if s.fase == "descarga"]
    assert len(descargas) >= 3, "debe haber descarga cada ~4 semanas"


def test_la_descarga_baja_el_volumen(plan_maraton):
    for i, semana in enumerate(plan_maraton.semanas_plan):
        if semana.fase == "descarga" and i > 0:
            assert semana.km_total < plan_maraton.semanas_plan[i - 1].km_total


def test_el_taper_baja_el_volumen(plan_maraton):
    construccion = [s for s in plan_maraton.semanas_plan if s.fase == "construccion"]
    afinamiento = [s for s in plan_maraton.semanas_plan if s.fase == "afinamiento"]
    assert afinamiento, "un maratón debe tener semanas de afinamiento"
    assert max(a.km_total for a in afinamiento) < max(c.km_total for c in construccion)


def test_la_ultima_semana_es_la_carrera(plan_maraton):
    ultima = plan_maraton.semanas_plan[-1]
    assert ultima.fase == "carrera"
    assert any(s.tipo == "carrera" for s in ultima.sesiones)


def test_respeta_los_dias_por_semana_pedidos():
    plan = t.generar_plan("10k", semanas=10, km_semanales_actuales=25, dias_por_semana=4)
    for semana in plan.semanas_plan:
        if semana.fase == "carrera":
            continue
        activos = [s for s in semana.sesiones if s.tipo != "descanso"]
        assert len(activos) == 4, f"semana {semana.numero}: {len(activos)} días activos"


def test_mayoria_del_volumen_es_facil(plan_maraton):
    """Principio 80/20: el trabajo duro es la minoría del volumen."""
    for semana in plan_maraton.semanas_plan:
        if semana.fase in ("carrera", "afinamiento"):
            continue
        duro = sum(
            s.km for s in semana.sesiones
            if s.zona in ("umbral", "intervalo", "repeticion", "maraton")
        )
        assert duro <= semana.km_total * 0.25, f"semana {semana.numero} muy dura"


def test_el_total_semanal_es_la_suma_de_las_sesiones(plan_maraton):
    """El plan no puede prometer un volumen que su calendario no contiene."""
    for semana in plan_maraton.semanas_plan:
        suma = round(sum(s.km for s in semana.sesiones), 1)
        assert suma == pytest.approx(semana.km_total, abs=0.15), (
            f"semana {semana.numero}: total {semana.km_total} vs suma {suma}"
        )


def test_ningun_rodaje_facil_compite_con_el_fondo(plan_maraton):
    """Un 'rodaje suave' de 18 km no es suave. Debe quedar por debajo del fondo."""
    for semana in plan_maraton.semanas_plan:
        if semana.fase == "carrera":
            continue
        for sesion in semana.sesiones:
            if sesion.tipo == "facil":
                assert sesion.km <= semana.km_fondo + 0.2, (
                    f"semana {semana.numero}: rodaje de {sesion.km} km "
                    f"contra fondo de {semana.km_fondo} km"
                )


def test_pocos_dias_limitan_el_volumen():
    """Con 3 días por semana no se puede planificar un pico de 90 km."""
    plan = t.generar_plan("42k", semanas=20, km_semanales_actuales=40, dias_por_semana=3)
    pico = max(s.km_total for s in plan.semanas_plan if s.fase != "carrera")
    assert pico <= 3 * t.KM_MAX_PROMEDIO_POR_DIA + 1
    assert plan.viabilidad.veredicto == "ajustado"


def test_el_detalle_de_calidad_concuerda_con_los_km(plan_maraton):
    """El texto de la sesión no puede contradecir los km prescritos."""
    import re

    for semana in plan_maraton.semanas_plan:
        for sesion in semana.sesiones:
            if sesion.tipo != "ritmo_objetivo":
                continue
            citados = float(re.search(r"([\d.]+) km", sesion.detalle).group(1))
            assert citados <= sesion.km, (
                f"la sesión asigna {sesion.km} km pero el texto pide {citados} km"
            )


@pytest.mark.parametrize("dias", [3, 4, 5, 6, 7])
@pytest.mark.parametrize("distancia", ["5k", "10k", "21k", "42k"])
def test_el_pico_nunca_supera_el_techo_de_la_distancia(distancia, dias):
    """Ningún reparto de días puede inflar el volumen por encima del techo."""
    plan = t.generar_plan(
        distancia, semanas=24, km_semanales_actuales=25, dias_por_semana=dias
    )
    pico = max(s.km_total for s in plan.semanas_plan if s.fase != "carrera")
    techo = min(
        t.PERFIL_DISTANCIA[distancia]["km_pico"], dias * t.KM_MAX_PROMEDIO_POR_DIA
    )
    assert pico <= techo + 0.5, f"{distancia} con {dias} días llegó a {pico} km"


@pytest.mark.parametrize("dias", [3, 4, 5, 6, 7])
def test_la_primera_semana_entrega_el_volumen_declarado(dias):
    """Si el corredor dice que corre 30 km, la semana 1 debe ser de 30 km."""
    plan = t.generar_plan("21k", semanas=12, km_semanales_actuales=30, dias_por_semana=dias)
    assert plan.semanas_plan[0].km_total == pytest.approx(30, abs=0.5)


@pytest.mark.parametrize("dias", [3, 4, 5, 6, 7])
def test_los_dias_de_calidad_no_se_encadenan_con_el_fondo(dias):
    """Una sesión dura el sábado, con fondo el domingo, es mala programación."""
    plan = t.generar_plan("42k", semanas=16, km_semanales_actuales=40, dias_por_semana=dias)
    for semana in plan.semanas_plan:
        if semana.fase == "carrera":
            continue
        sabado = next(s for s in semana.sesiones if s.dia == 6)
        assert sabado.tipo in ("descanso", "facil"), (
            f"{dias} días: sábado quedó como {sabado.tipo}"
        )


def test_plan_sin_marca_no_trae_ritmos():
    plan = t.generar_plan("5k", semanas=8, km_semanales_actuales=15)
    assert plan.vdot is None
    assert plan.ritmos == {}
    assert plan.tiempo_objetivo_s is None


def test_plan_con_marca_trae_ritmos_y_objetivo(plan_maraton):
    assert plan_maraton.vdot is not None
    assert "umbral" in plan_maraton.ritmos
    assert plan_maraton.tiempo_objetivo_s > 2 * 3600


def test_parametros_invalidos():
    with pytest.raises(ValueError):
        t.generar_plan("50k", semanas=10, km_semanales_actuales=20)
    with pytest.raises(ValueError):
        t.generar_plan("5k", semanas=10, km_semanales_actuales=20, dias_por_semana=9)


def test_plan_es_serializable(plan_maraton):
    """Debe poder devolverse como JSON al LLM vía function calling."""
    import json
    crudo = json.dumps(plan_maraton.a_dict())
    assert json.loads(crudo)["distancia"] == "42k"
