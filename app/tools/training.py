"""Motor de entrenamiento determinista.

Toda la lógica que puede lesionar a alguien vive aquí, NO en el LLM.
El modelo conversa; estas funciones deciden ritmos, volúmenes y progresión.

Referencias:
- VDOT / VO2max: Jack Daniels, "Daniels' Running Formula" (3ª ed.)
- Predicción entre distancias: fórmula de Riegel (1981)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Literal

# --------------------------------------------------------------------------
# Distancias oficiales
# --------------------------------------------------------------------------

DISTANCIAS = {
    "5k": 5_000,
    "10k": 10_000,
    "21k": 21_097,
    "42k": 42_195,
}

TipoDistancia = Literal["5k", "10k", "21k", "42k"]


# --------------------------------------------------------------------------
# VDOT: capacidad aeróbica a partir de una marca real
# --------------------------------------------------------------------------

def _vo2_de_velocidad(v_m_min: float) -> float:
    """Costo de oxígeno para una velocidad dada, en ml/kg/min (Daniels)."""
    return -4.60 + 0.182258 * v_m_min + 0.000104 * v_m_min**2


def _velocidad_de_vo2(vo2: float) -> float:
    """Inversa de _vo2_de_velocidad: resuelve la cuadrática para v."""
    a, b, c = 0.000104, 0.182258, -(4.60 + vo2)
    return (-b + math.sqrt(b**2 - 4 * a * c)) / (2 * a)


def _porcentaje_vo2max(minutos: float) -> float:
    """Fracción del VO2max sostenible durante una duración dada."""
    return (
        0.8
        + 0.1894393 * math.exp(-0.012778 * minutos)
        + 0.2989558 * math.exp(-0.1932605 * minutos)
    )


def vdot_de_marca(distancia_m: float, tiempo_s: float) -> float:
    """Calcula el VDOT a partir de una carrera real.

    >>> round(vdot_de_marca(5000, 19 * 60 + 57))
    50
    """
    if distancia_m <= 0 or tiempo_s <= 0:
        raise ValueError("distancia y tiempo deben ser positivos")

    minutos = tiempo_s / 60
    velocidad = distancia_m / minutos
    return _vo2_de_velocidad(velocidad) / _porcentaje_vo2max(minutos)


def predecir_riegel(
    distancia_conocida_m: float,
    tiempo_conocido_s: float,
    distancia_objetivo_m: float,
) -> float:
    """Predice tiempo en otra distancia. Exponente 1.06 (Riegel).

    Pierde precisión al extrapolar mucho (5k -> maratón); para eso el
    VDOT es más confiable.
    """
    razon = distancia_objetivo_m / distancia_conocida_m
    return tiempo_conocido_s * (razon**1.06)


def predecir_por_vdot(vdot: float, distancia_m: float) -> float:
    """Tiempo estimado para una distancia dado un VDOT, en segundos.

    Resuelve iterativamente porque el %VO2max depende de la duración,
    que es justo lo que queremos calcular.
    """
    minutos = distancia_m / 200  # semilla: ~200 m/min
    for _ in range(50):
        vo2_objetivo = vdot * _porcentaje_vo2max(minutos)
        velocidad = _velocidad_de_vo2(vo2_objetivo)
        nuevos_minutos = distancia_m / velocidad
        if abs(nuevos_minutos - minutos) < 1e-6:
            break
        minutos = nuevos_minutos
    return minutos * 60


# --------------------------------------------------------------------------
# Zonas de ritmo
# --------------------------------------------------------------------------

# Fracción del VDOT a la que se corre cada zona (Daniels)
INTENSIDADES = {
    "facil": (0.59, 0.74),
    "maraton": (0.84, 0.84),
    "umbral": (0.86, 0.88),
    "intervalo": (0.97, 1.00),
    "repeticion": (1.05, 1.10),
}

DESCRIPCION_ZONA = {
    "facil": "Conversacional. Deberías poder hablar en frases completas.",
    "maraton": "Ritmo objetivo de maratón. Cómodamente duro.",
    "umbral": "Ritmo de tempo, sostenible ~1 hora en carrera.",
    "intervalo": "Ritmo de 3-5 km. Series de 3-5 min con recuperación.",
    "repeticion": "Rápido y corto. Técnica y economía, no jadeo.",
}


@dataclass
class RangoRitmo:
    zona: str
    seg_por_km_rapido: int
    seg_por_km_lento: int
    descripcion: str

    def formato(self) -> str:
        r, l = mmss(self.seg_por_km_rapido), mmss(self.seg_por_km_lento)
        return r if r == l else f"{r}–{l}"


def mmss(segundos: float) -> str:
    """Formatea segundos como m:ss (para ritmos) o h:mm:ss (para tiempos)."""
    segundos = round(segundos)
    horas, resto = divmod(segundos, 3600)
    minutos, seg = divmod(resto, 60)
    if horas:
        return f"{horas}:{minutos:02d}:{seg:02d}"
    return f"{minutos}:{seg:02d}"


def ritmos_de_entrenamiento(vdot: float) -> dict[str, RangoRitmo]:
    """Devuelve el ritmo por km de cada zona para un VDOT dado."""
    ritmos = {}
    for zona, (pct_bajo, pct_alto) in INTENSIDADES.items():
        # Mayor %VDOT -> más rápido -> menos segundos por km
        rapido = 1000 / _velocidad_de_vo2(vdot * pct_alto) * 60
        lento = 1000 / _velocidad_de_vo2(vdot * pct_bajo) * 60
        ritmos[zona] = RangoRitmo(
            zona=zona,
            seg_por_km_rapido=round(rapido),
            seg_por_km_lento=round(lento),
            descripcion=DESCRIPCION_ZONA[zona],
        )
    return ritmos


# --------------------------------------------------------------------------
# Reglas de carga: lo que evita lesiones
# --------------------------------------------------------------------------

INCREMENTO_SEMANAL_MAX = 1.10   # +10% de volumen por semana como techo
FACTOR_DESCARGA = 0.75          # semana de descarga: -25%
CADA_N_SEMANAS_DESCARGA = 4
# Fracción del volumen semanal que se lleva el fondo. Depende de los días:
# con 3 días el fondo pesa más por pura aritmética, y los planes reales de
# baja frecuencia efectivamente lo hacen.
FRACCION_FONDO_POR_DIAS = {3: 0.45, 4: 0.38, 5: 0.34, 6: 0.32, 7: 0.30}

# Un corredor amateur no absorbe más de ~14 km de promedio por día de
# entrenamiento. Sin este techo el plan reparte 90 km en 5 días y produce
# "rodajes suaves" de 18 km, que no son suaves ni sostenibles.
KM_MAX_PROMEDIO_POR_DIA = 14

# Volumen semanal pico recomendado y mínimo viable, en km
# semanas_max no es una regla de seguridad sino de sentido: un bloque de
# preparación tiene una duración útil, y estirarlo no mejora nada. El tiempo
# sobrante se emplea en construir base, no en alargar el plan.
PERFIL_DISTANCIA = {
    "5k":  {"km_min": 20, "km_pico": 45, "fondo_max": 12, "semanas_taper": 1,
            "semanas_min": 6,  "semanas_max": 16},
    "10k": {"km_min": 25, "km_pico": 55, "fondo_max": 16, "semanas_taper": 1,
            "semanas_min": 8,  "semanas_max": 20},
    "21k": {"km_min": 30, "km_pico": 70, "fondo_max": 22, "semanas_taper": 2,
            "semanas_min": 10, "semanas_max": 24},
    "42k": {"km_min": 40, "km_pico": 90, "fondo_max": 32, "semanas_taper": 3,
            "semanas_min": 16, "semanas_max": 30},
}


@dataclass
class Viabilidad:
    veredicto: Literal["viable", "ajustado", "no_recomendado", "demasiado_largo"]
    razon: str
    km_pico_alcanzable: float
    km_pico_recomendado: float
    semanas_minimas_sugeridas: int
    semanas_recomendadas: int = 0    # las que el plan usará de verdad


def evaluar_viabilidad(
    distancia: TipoDistancia,
    semanas: int,
    km_semanales_actuales: float,
    dias_por_semana: int = 5,
) -> Viabilidad:
    """¿Le da tiempo a este corredor de prepararse sin romperse?

    Es la función más importante del proyecto: decir "no" a tiempo
    vale más que cualquier plan bonito.
    """
    perfil = PERFIL_DISTANCIA[distancia]

    if km_semanales_actuales <= 0:
        raise ValueError("km_semanales_actuales debe ser positivo")

    semanas_construccion = max(1, semanas - perfil["semanas_taper"])

    # Cuánto se puede subir en el tiempo disponible respetando el +10%,
    # descontando las semanas de descarga que no aportan progresión.
    semanas_progresion = semanas_construccion - (
        semanas_construccion // CADA_N_SEMANAS_DESCARGA
    )
    # Dos límites independientes: la forma física de la que se parte y los
    # días de los que se dispone. Confundirlos hace que el coach suene
    # pesimista con corredores que en realidad van bien.
    pico_por_forma = km_semanales_actuales * (
        INCREMENTO_SEMANAL_MAX**semanas_progresion
    )
    pico_por_dias = dias_por_semana * KM_MAX_PROMEDIO_POR_DIA
    pico_alcanzable = min(pico_por_forma, perfil["km_pico"], pico_por_dias)

    if semanas > perfil["semanas_max"]:
        return Viabilidad(
            veredicto="demasiado_largo",
            razon=(
                f"{semanas} semanas es mucho más de lo que aporta un bloque de "
                f"{distancia}: a partir de {perfil['semanas_max']} semanas se "
                f"acumula cansancio sin ganar forma. Te preparo un bloque de "
                f"{perfil['semanas_max']} semanas y hasta entonces lo suyo es "
                f"construir base con rodajes suaves."
            ),
            km_pico_alcanzable=round(min(pico_alcanzable, perfil["km_pico"]), 1),
            km_pico_recomendado=perfil["km_pico"],
            semanas_minimas_sugeridas=perfil["semanas_min"],
            semanas_recomendadas=perfil["semanas_max"],
        )

    if semanas < perfil["semanas_min"]:
        return Viabilidad(
            veredicto="no_recomendado",
            razon=(
                f"{semanas} semanas es poco para {distancia}. "
                f"Lo mínimo razonable son {perfil['semanas_min']} semanas."
            ),
            km_pico_alcanzable=round(pico_alcanzable, 1),
            km_pico_recomendado=perfil["km_pico"],
            semanas_minimas_sugeridas=perfil["semanas_min"],
            semanas_recomendadas=semanas,
        )

    if pico_alcanzable < perfil["km_min"]:
        return Viabilidad(
            veredicto="no_recomendado",
            razon=(
                f"Partiendo de {km_semanales_actuales:g} km/semana no se llega "
                f"al mínimo de {perfil['km_min']} km/semana para {distancia} "
                f"en {semanas} semanas sin saltarse el +10% semanal."
            ),
            km_pico_alcanzable=round(pico_alcanzable, 1),
            km_pico_recomendado=perfil["km_pico"],
            semanas_minimas_sugeridas=_semanas_para_llegar(
                km_semanales_actuales, perfil["km_min"], perfil["semanas_taper"]
            ),
            semanas_recomendadas=semanas,
        )

    if pico_por_forma < perfil["km_pico"] * 0.8:
        return Viabilidad(
            veredicto="ajustado",
            razon=(
                f"Se llega a la meta, pero con margen justo: el pico será de "
                f"~{pico_alcanzable:.0f} km/semana en vez de los "
                f"{perfil['km_pico']} km/semana ideales. Plan conservador, "
                f"orientado a terminar sin lesión más que a marca personal."
            ),
            km_pico_alcanzable=round(pico_alcanzable, 1),
            km_pico_recomendado=perfil["km_pico"],
            semanas_minimas_sugeridas=perfil["semanas_min"],
            semanas_recomendadas=semanas,
        )

    # El calendario solo se vuelve un problema real cuando recorta bastante
    # por debajo del pico ideal; 70 km en 5 días es un buen plan de maratón.
    if pico_por_dias < perfil["km_pico"] * 0.7:
        return Viabilidad(
            veredicto="ajustado",
            razon=(
                f"Con {dias_por_semana} días por semana el techo práctico es "
                f"~{pico_por_dias:.0f} km/semana, por debajo de los "
                f"{perfil['km_pico']} km/semana ideales para {distancia}. "
                f"El limitante es el calendario, no tu forma física: con un "
                f"día más de carrera el plan mejora bastante."
            ),
            km_pico_alcanzable=round(pico_alcanzable, 1),
            km_pico_recomendado=perfil["km_pico"],
            semanas_minimas_sugeridas=perfil["semanas_min"],
            semanas_recomendadas=semanas,
        )

    return Viabilidad(
        veredicto="viable",
        razon=(
            f"{semanas} semanas desde {km_semanales_actuales:g} km/semana "
            f"da margen suficiente para {distancia}."
        ),
        km_pico_alcanzable=round(pico_alcanzable, 1),
        km_pico_recomendado=perfil["km_pico"],
        semanas_minimas_sugeridas=perfil["semanas_min"],
        semanas_recomendadas=semanas,
    )


def _semanas_para_llegar(km_actual: float, km_objetivo: float, taper: int) -> int:
    """Semanas necesarias para pasar de un volumen a otro al +10%, con descargas."""
    if km_actual >= km_objetivo:
        return taper + 1
    semanas_progresion = math.ceil(
        math.log(km_objetivo / km_actual) / math.log(INCREMENTO_SEMANAL_MAX)
    )
    # Reinsertar las semanas de descarga que no progresan
    total = semanas_progresion + semanas_progresion // (CADA_N_SEMANAS_DESCARGA - 1)
    return int(total + taper)


# --------------------------------------------------------------------------
# Generación del plan
# --------------------------------------------------------------------------

@dataclass
class Sesion:
    dia: int                  # 1 = lunes
    tipo: str                 # descanso | facil | largo | tempo | intervalos | repeticiones
    km: float
    zona: str
    detalle: str


@dataclass
class SemanaPlan:
    numero: int
    fase: Literal["base", "construccion", "descarga", "afinamiento", "carrera"]
    km_total: float
    km_fondo: float
    sesiones: list[Sesion] = field(default_factory=list)


@dataclass
class Plan:
    distancia: TipoDistancia
    semanas: int
    dias_por_semana: int
    vdot: float | None
    viabilidad: Viabilidad
    ritmos: dict[str, RangoRitmo]
    tiempo_objetivo_s: float | None
    semanas_plan: list[SemanaPlan] = field(default_factory=list)

    def a_dict(self) -> dict:
        return asdict(self)


def generar_plan(
    distancia: TipoDistancia,
    semanas: int,
    km_semanales_actuales: float,
    dias_por_semana: int = 4,
    marca_reciente_distancia_m: float | None = None,
    marca_reciente_tiempo_s: float | None = None,
) -> Plan:
    """Genera un plan semana a semana respetando las reglas de carga.

    Si se aporta una marca reciente se calculan ritmos personalizados y un
    tiempo objetivo; si no, el plan sale sin ritmos (solo volumen y estructura).
    """
    if distancia not in PERFIL_DISTANCIA:
        raise ValueError(f"distancia debe ser una de {list(PERFIL_DISTANCIA)}")
    if not 3 <= dias_por_semana <= 7:
        raise ValueError("dias_por_semana debe estar entre 3 y 7")
    if semanas < 1:
        raise ValueError("semanas debe ser al menos 1")

    perfil = PERFIL_DISTANCIA[distancia]
    viabilidad = evaluar_viabilidad(
        distancia, semanas, km_semanales_actuales, dias_por_semana
    )
    # Un bloque más largo de la cuenta no se genera: se recorta a lo útil y la
    # viabilidad ya explica por qué.
    semanas = min(semanas, perfil["semanas_max"])

    vdot = None
    ritmos: dict[str, RangoRitmo] = {}
    tiempo_objetivo = None
    if marca_reciente_distancia_m and marca_reciente_tiempo_s:
        vdot = vdot_de_marca(marca_reciente_distancia_m, marca_reciente_tiempo_s)
        ritmos = ritmos_de_entrenamiento(vdot)
        tiempo_objetivo = predecir_por_vdot(vdot, DISTANCIAS[distancia])

    semanas_taper = min(perfil["semanas_taper"], max(0, semanas - 1))
    semanas_construccion = semanas - semanas_taper
    tope = min(
        viabilidad.km_pico_alcanzable,
        perfil["km_pico"],
        dias_por_semana * KM_MAX_PROMEDIO_POR_DIA,
    )

    plan_semanas: list[SemanaPlan] = []
    rampa = km_semanales_actuales  # volumen "real" de progresión, ignora descargas

    for i in range(semanas_construccion):
        numero = i + 1
        es_descarga = (
            numero % CADA_N_SEMANAS_DESCARGA == 0 and numero != semanas_construccion
        )

        if es_descarga:
            km = rampa * FACTOR_DESCARGA
            fase = "descarga"
        else:
            if numero > 1:
                rampa = min(rampa * INCREMENTO_SEMANAL_MAX, tope)
            km = rampa
            fase = "base" if numero <= semanas_construccion / 3 else "construccion"

        plan_semanas.append(
            _construir_semana(numero, fase, km, distancia, dias_por_semana, perfil)
        )

    # Afinamiento: bajar volumen manteniendo algo de intensidad
    factores_taper = {1: [0.60], 2: [0.70, 0.50], 3: [0.80, 0.60, 0.45]}
    for j, factor in enumerate(factores_taper.get(semanas_taper, [])):
        numero = semanas_construccion + j + 1
        es_ultima = numero == semanas
        plan_semanas.append(
            _construir_semana(
                numero,
                "carrera" if es_ultima else "afinamiento",
                tope * factor,
                distancia,
                dias_por_semana,
                perfil,
                semana_de_carrera=es_ultima,
            )
        )

    return Plan(
        distancia=distancia,
        semanas=semanas,
        dias_por_semana=dias_por_semana,
        vdot=round(vdot, 1) if vdot else None,
        viabilidad=viabilidad,
        ritmos=ritmos,
        tiempo_objetivo_s=round(tiempo_objetivo) if tiempo_objetivo else None,
        semanas_plan=plan_semanas,
    )


def _construir_semana(
    numero: int,
    fase: str,
    km_total: float,
    distancia: TipoDistancia,
    dias: int,
    perfil: dict,
    semana_de_carrera: bool = False,
) -> SemanaPlan:
    """Reparte el volumen semanal en sesiones concretas respetando 80/20."""
    if semana_de_carrera:
        km_carrera = round(DISTANCIAS[distancia] / 1000, 1)
        sesiones = [
            Sesion(1, "descanso", 0, "-", "Descanso total."),
            Sesion(2, "facil", 5, "facil", "Trote suave de activación."),
            Sesion(3, "facil", 4, "facil",
                   "Trote suave + 4 rectas de 100 m a ritmo de carrera."),
            Sesion(4, "descanso", 0, "-", "Descanso. Hidratación y sueño."),
            Sesion(5, "facil", 3, "facil", "Trote muy corto para soltar piernas."),
            Sesion(6, "descanso", 0, "-", "Descanso previo a competencia."),
            Sesion(7, "carrera", km_carrera,
                   "maraton" if distancia == "42k" else "umbral",
                   f"DÍA DE CARRERA: {distancia}. Sal conservador los primeros km."),
        ]
        # En semana de carrera no hay fondo de entrenamiento: la carrera lo es.
        return SemanaPlan(
            numero, "carrera", round(sum(s.km for s in sesiones), 1), 0.0, sesiones
        )

    # El calendario se deriva de los días disponibles. El domingo siempre es
    # el fondo; el lunes es el último día en ocuparse (descanso por defecto).
    ORDEN_OCUPACION = [2, 4, 3, 5, 6, 1]
    dias_entre_semana = sorted(ORDEN_OCUPACION[: dias - 1])
    dias_activos = dias_entre_semana + [7]

    sesiones_calidad = 1 if dias <= 3 else 2
    if fase in ("base", "descarga"):
        sesiones_calidad = max(1, sesiones_calidad - 1)
    # Nunca más sesiones de calidad que huecos entre semana, dejando al menos
    # un rodaje fácil de por medio.
    sesiones_calidad = min(sesiones_calidad, max(0, len(dias_entre_semana) - 1))

    # El sábado (6) queda libre para no encadenar calidad con el fondo del domingo.
    candidatos = [d for d in dias_entre_semana if d != 6] or dias_entre_semana
    if sesiones_calidad == 1:
        dias_calidad = [candidatos[len(candidatos) // 2]]
    elif sesiones_calidad >= 2:
        paso = (len(candidatos) - 1) / (sesiones_calidad - 1)
        dias_calidad = sorted(
            {candidatos[round(i * paso)] for i in range(sesiones_calidad)}
        )
    else:
        dias_calidad = []

    # Los días fáciles salen de lo que sobra, no de una fórmula aparte: así el
    # reparto no puede desincronizarse del calendario que se genera después.
    dias_faciles = [d for d in dias_entre_semana if d not in dias_calidad]
    n_faciles = max(1, len(dias_faciles))

    km_por_calidad = round(km_total * 0.11, 1)
    km_calidad_total = km_por_calidad * len(dias_calidad)

    # El fondo se dimensiona para que el reparto cuadre sin recortar volumen:
    # ni por debajo de su fracción objetivo, ni tan bajo que un rodaje "fácil"
    # acabe siendo más largo que el propio fondo. El techo absoluto de la
    # distancia manda por encima de las dos cosas.
    fraccion = FRACCION_FONDO_POR_DIAS.get(dias, 0.32)
    fondo_minimo_para_cuadrar = (km_total - km_calidad_total) / (n_faciles + 1)
    km_fondo = round(
        min(
            perfil["fondo_max"],
            max(km_total * fraccion, fondo_minimo_para_cuadrar),
        ),
        1,
    )

    km_restantes = max(0.0, km_total - km_fondo - km_calidad_total)
    km_por_facil = round(km_restantes / n_faciles, 1)

    tipos_calidad = _calidad_para(distancia, fase)

    sesiones: list[Sesion] = []
    idx_calidad = 0
    for dia in [1, 2, 3, 4, 5, 6, 7]:
        if dia == 7:
            sesiones.append(
                Sesion(7, "largo", km_fondo, "facil",
                       f"Fondo de {km_fondo} km a ritmo fácil. "
                       "Si no puedes conversar, vas muy rápido.")
            )
        elif dia not in dias_activos:
            sesiones.append(Sesion(dia, "descanso", 0, "-", "Descanso o movilidad."))
        elif dia in dias_calidad:
            tipo, zona, describir = tipos_calidad[idx_calidad % len(tipos_calidad)]
            sesiones.append(
                Sesion(dia, tipo, km_por_calidad, zona, describir(km_por_calidad))
            )
            idx_calidad += 1
        else:
            sesiones.append(
                Sesion(dia, "facil", km_por_facil, "facil",
                       "Rodaje suave. Termina sintiendo que podrías seguir.")
            )

    # El total es lo que realmente suman las sesiones, no un número aparte:
    # así el plan nunca promete un volumen que su propio calendario no contiene.
    return SemanaPlan(
        numero, fase, round(sum(s.km for s in sesiones), 1), km_fondo, sesiones
    )


def _calidad_para(distancia: TipoDistancia, fase: str) -> list[tuple]:
    """Sesiones de calidad apropiadas para la distancia.

    Cada entrada es (tipo, zona, describir) donde `describir(km)` genera el
    detalle a partir de los km realmente asignados, para que el texto nunca
    contradiga la prescripción.
    """
    def intervalos(km: float) -> str:
        # ~45% de la sesión son series; el resto calentamiento y enfriamiento
        series = max(4, round(km * 0.45 / 0.8))
        return (
            f"{series} × 800 m a ritmo de intervalo con 2 min de trote entre "
            f"series. Incluye 2 km de calentamiento y 1.5 km de enfriamiento."
        )

    def tempo(km: float, bloques: int) -> str:
        km_umbral = round(km * 0.5, 1)
        por_bloque = round(km_umbral / bloques, 1)
        if bloques == 1:
            cuerpo = f"{km_umbral} km continuos a ritmo de umbral"
        else:
            cuerpo = (
                f"{bloques} × {por_bloque} km a ritmo de umbral "
                f"con 3 min de trote entre bloques"
            )
        return f"{cuerpo}. Calentamiento y enfriamiento suave alrededor."

    def ritmo_objetivo(km: float, nombre: str) -> str:
        km_obj = round(km * 0.6, 1)
        return (
            f"{km_obj} km a ritmo objetivo de {nombre} dentro del rodaje, "
            f"el resto suave."
        )

    if distancia in ("5k", "10k"):
        return [
            ("intervalos", "intervalo", intervalos),
            ("tempo", "umbral", lambda km: tempo(km, 1)),
        ]
    if distancia == "21k":
        return [
            ("tempo", "umbral", lambda km: tempo(km, 2)),
            ("ritmo_objetivo", "maraton", lambda km: ritmo_objetivo(km, "media")),
        ]
    return [
        ("tempo", "umbral", lambda km: tempo(km, 3)),
        ("ritmo_objetivo", "maraton", lambda km: ritmo_objetivo(km, "maratón")),
    ]
