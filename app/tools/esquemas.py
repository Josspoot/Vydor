"""Declaraciones de funciones para Gemini y despacho hacia el motor.

El LLM no calcula nada: extrae los datos de la conversación, llama a estas
funciones y narra el resultado. Cualquier número que diga el coach sale de aquí.
"""

from __future__ import annotations

import logging

from app.tools import sintomas, training

log = logging.getLogger(__name__)


DECLARACIONES = [
    {
        "name": "evaluar_viabilidad",
        "description": (
            "Determina si un corredor puede prepararse con seguridad para una "
            "distancia en el tiempo disponible. Llámala ANTES de generar un plan "
            "y siempre que el corredor mencione un objetivo y una fecha."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "distancia": {
                    "type": "STRING",
                    "enum": ["1k", "3k", "5k", "10k", "21k", "42k"],
                    "description": (
                        "Distancia objetivo. Vale una meta pequeña: para quien "
                        "empieza, correr 1 km seguido ya es una carrera."
                    ),
                },
                "semanas": {
                    "type": "INTEGER",
                    "description": "Semanas disponibles hasta la carrera.",
                },
                "km_semanales_actuales": {
                    "type": "NUMBER",
                    "description": (
                        "Kilómetros que corre por semana actualmente. Cero es un "
                        "valor válido y frecuente: mucha gente empieza sin correr nada."
                    ),
                },
                "dias_por_semana": {
                    "type": "INTEGER",
                    "description": "Días por semana que puede entrenar (3 a 7).",
                },
                "fecha_fija": {
                    "type": "BOOLEAN",
                    "description": "True si la carrera ya tiene fecha inamovible.",
                },
            },
            "required": ["distancia", "semanas", "km_semanales_actuales"],
        },
    },
    {
        "name": "generar_plan",
        "description": (
            "Genera el plan de entrenamiento completo semana a semana, con "
            "volúmenes, sesiones y ritmos. Si el corredor da una marca reciente "
            "inclúyela: sin ella el plan sale sin ritmos personalizados."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "distancia": {
                    "type": "STRING",
                    "enum": ["1k", "3k", "5k", "10k", "21k", "42k"],
                },
                "semanas": {"type": "INTEGER"},
                "km_semanales_actuales": {"type": "NUMBER"},
                "dias_por_semana": {
                    "type": "INTEGER",
                    "description": "Días por semana disponibles (3 a 7). Por defecto 4.",
                },
                "marca_reciente_distancia_m": {
                    "type": "NUMBER",
                    "description": "Distancia en metros de una carrera reciente.",
                },
                "marca_reciente_tiempo_s": {
                    "type": "NUMBER",
                    "description": "Tiempo en segundos de esa carrera reciente.",
                },
                "fecha_fija": {
                    "type": "BOOLEAN",
                    "description": (
                        "True si la carrera ya tiene fecha y no se puede mover. "
                        "Entonces el plan se ajusta a ese plazo aunque sea corto, "
                        "y el objetivo pasa a ser terminar en vez de hacer marca."
                    ),
                },
            },
            "required": ["distancia", "semanas", "km_semanales_actuales"],
        },
    },
    {
        "name": "calcular_ritmos",
        "description": (
            "Calcula las zonas de ritmo de entrenamiento a partir de una marca "
            "reciente. Úsala cuando pregunten '¿a qué ritmo debo correr X?' sin "
            "necesitar un plan completo."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "marca_distancia_m": {"type": "NUMBER"},
                "marca_tiempo_s": {"type": "NUMBER"},
            },
            "required": ["marca_distancia_m", "marca_tiempo_s"],
        },
    },
    {
        "name": "predecir_marca",
        "description": (
            "Predice el tiempo en una distancia a partir de una marca en otra. "
            "Úsala para '¿qué tiempo podría hacer en maratón si corro 10k en 45?'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "marca_distancia_m": {"type": "NUMBER"},
                "marca_tiempo_s": {"type": "NUMBER"},
                "distancia_objetivo_m": {"type": "NUMBER"},
            },
            "required": [
                "marca_distancia_m",
                "marca_tiempo_s",
                "distancia_objetivo_m",
            ],
        },
    },
    {
        "name": "evaluar_sintoma",
        "description": (
            "OBLIGATORIA cuando el corredor mencione cualquier dolor, molestia o "
            "lesión. Nunca des consejo sobre dolor sin llamar a esta función. "
            "Necesita saber si duele en reposo, si le hace cojear y si mejora al "
            "calentar: pregúntaselo al corredor de una pregunta a la vez. Sin "
            "esos datos la función no evalúa y te pedirá que preguntes."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "zona": {
                    "type": "STRING",
                    "description": "Parte del cuerpo afectada, ej. 'rodilla derecha'.",
                },
                "dias_con_sintoma": {"type": "INTEGER"},
                "duele_en_reposo": {
                    "type": "BOOLEAN",
                    "description": "Si duele sentado o acostado, sin correr.",
                },
                "cambia_la_pisada": {
                    "type": "BOOLEAN",
                    "description": "Si lo hace cojear o alterar la técnica.",
                },
                "dolor_punzante_localizado": {
                    "type": "BOOLEAN",
                    "description": "Dolor de punta de dedo sobre un punto óseo.",
                },
                "mejora_al_calentar": {
                    "type": "BOOLEAN",
                    "description": "Si se alivia tras los primeros minutos de trote.",
                },
                "hinchazon": {"type": "BOOLEAN"},
                "sintomas_sistemicos": {
                    "type": "ARRAY",
                    "items": {
                        "type": "STRING",
                        "enum": sorted(sintomas.SINTOMAS_SISTEMICOS),
                    },
                    "description": "Señales de alarma general presentes.",
                },
            },
            "required": [
                "zona", "duele_en_reposo", "cambia_la_pisada", "mejora_al_calentar",
            ],
        },
    },
]


# --------------------------------------------------------------------------
# Despacho
# --------------------------------------------------------------------------

def _evaluar_viabilidad(**kw) -> dict:
    v = training.evaluar_viabilidad(
        distancia=kw["distancia"],
        semanas=int(kw["semanas"]),
        km_semanales_actuales=float(kw["km_semanales_actuales"]),
        dias_por_semana=int(kw.get("dias_por_semana") or 5),
        solo_terminar=bool(kw.get("fecha_fija", False)),
    )
    return {
        "veredicto": v.veredicto,
        "razon": v.razon,
        "km_pico_alcanzable": v.km_pico_alcanzable,
        "km_pico_recomendado": v.km_pico_recomendado,
        "semanas_minimas_sugeridas": v.semanas_minimas_sugeridas,
        "semanas_recomendadas": v.semanas_recomendadas,
        # El propio resultado dice qué hacer a continuación. En pruebas el
        # modelo se quedaba aquí y anunciaba un plan que nunca había generado.
        "siguiente_paso": (
            "Esto solo evalúa; NO existe ningún plan todavía. Llama ahora a "
            "generar_plan con los mismos datos. No digas que el plan está listo "
            "ni que se ve en pantalla hasta haberlo hecho."
        ),
    }


def _generar_plan(**kw) -> dict:
    plan = training.generar_plan(
        distancia=kw["distancia"],
        semanas=int(kw["semanas"]),
        km_semanales_actuales=float(kw["km_semanales_actuales"]),
        dias_por_semana=int(kw.get("dias_por_semana") or 4),
        marca_reciente_distancia_m=kw.get("marca_reciente_distancia_m"),
        marca_reciente_tiempo_s=kw.get("marca_reciente_tiempo_s"),
        fecha_fija=bool(kw.get("fecha_fija", False)),
    )
    datos = plan.a_dict()
    # El audio no puede recitar 18 semanas: se le da al modelo un resumen
    # hablado y el detalle completo viaja aparte hacia la interfaz.
    datos["resumen_hablado"] = _resumir_para_voz(plan)
    return datos


def _resumir_para_voz(plan: training.Plan) -> str:
    """Resumen corto y decible del plan; el detalle se muestra en pantalla."""
    picos = [s for s in plan.semanas_plan if s.fase != "carrera"]
    pico = max((s.km_total for s in picos), default=0)
    fondo_max = max((s.km_fondo for s in picos), default=0)
    partes = [
        f"Plan de {plan.semanas} semanas para {plan.distancia}, "
        f"{plan.dias_por_semana} días por semana.",
        f"Empiezas en {plan.semanas_plan[0].km_total:.0f} kilómetros semanales "
        f"y llegas a un pico de {pico:.0f}, con un fondo máximo de {fondo_max:.0f}.",
    ]
    if plan.tiempo_objetivo_s:
        partes.append(
            f"Con tu marca actual el objetivo realista es "
            f"{training.mmss(plan.tiempo_objetivo_s)}."
        )
    if plan.ritmos:
        facil = plan.ritmos["facil"].formato()
        umbral = plan.ritmos["umbral"].formato()
        partes.append(
            f"Tus rodajes fáciles van a {facil} por kilómetro y el umbral a {umbral}."
        )
    if plan.objetivo_realista == "terminar_o_caminar":
        partes.append(
            "Con este plazo el objetivo honesto es cruzar la meta, alternando "
            "carrera y caminata si hace falta, no hacer un buen tiempo."
        )
    elif plan.objetivo_realista == "terminar":
        partes.append("El objetivo de este plan es terminar cómodo, no marcar tiempo.")
    if plan.desde_cero:
        partes.append(
            "Como partes de cero, las primeras semanas alternan carrera y caminata."
        )
    if plan.viabilidad.veredicto != "viable":
        partes.append(plan.viabilidad.razon)
    return " ".join(partes)


def _calcular_ritmos(**kw) -> dict:
    vdot = training.vdot_de_marca(
        float(kw["marca_distancia_m"]), float(kw["marca_tiempo_s"])
    )
    ritmos = training.ritmos_de_entrenamiento(vdot)
    return {
        "vdot": round(vdot, 1),
        "ritmos": {
            zona: {
                "rango_por_km": r.formato(),
                "descripcion": r.descripcion,
            }
            for zona, r in ritmos.items()
        },
    }


def _predecir_marca(**kw) -> dict:
    vdot = training.vdot_de_marca(
        float(kw["marca_distancia_m"]), float(kw["marca_tiempo_s"])
    )
    objetivo = float(kw["distancia_objetivo_m"])
    por_vdot = training.predecir_por_vdot(vdot, objetivo)
    por_riegel = training.predecir_riegel(
        float(kw["marca_distancia_m"]), float(kw["marca_tiempo_s"]), objetivo
    )
    return {
        "vdot": round(vdot, 1),
        "prediccion": training.mmss(por_vdot),
        "prediccion_alternativa_riegel": training.mmss(por_riegel),
        "advertencia": (
            "La predicción asume entrenamiento específico para esa distancia. "
            "Sin volumen suficiente, el tiempo real será bastante peor."
        ),
    }


# Sin estos datos no hay triaje posible: decidirlos por defecto convierte
# cualquier dolor sin explorar en "molestia normal".
DATOS_MINIMOS_SINTOMA = ("duele_en_reposo", "cambia_la_pisada", "mejora_al_calentar")


def _evaluar_sintoma(**kw) -> dict:
    faltan = [c for c in DATOS_MINIMOS_SINTOMA if kw.get(c) is None]
    if faltan:
        preguntas = {
            "duele_en_reposo": "si le duele estando sentado o acostado, sin correr",
            "cambia_la_pisada": "si le hace cojear o cambiar la forma de correr",
            "mejora_al_calentar": "si mejora tras los primeros minutos de trote",
        }
        return {
            "faltan_datos": faltan,
            "instruccion": (
                "No puedes evaluar este dolor todavía. Pregúntale al corredor "
                + "; ".join(preguntas[c] for c in faltan)
                + ". Pregunta de una en una, en tono normal, y vuelve a llamar "
                "a esta función con las respuestas. No des ningún consejo ni "
                "rutina mientras tanto."
            ),
        }

    ev = sintomas.evaluar_sintoma(
        zona=kw["zona"],
        dias_con_sintoma=int(kw.get("dias_con_sintoma") or 1),
        duele_en_reposo=bool(kw["duele_en_reposo"]),
        cambia_la_pisada=bool(kw["cambia_la_pisada"]),
        dolor_punzante_localizado=bool(kw.get("dolor_punzante_localizado", False)),
        mejora_al_calentar=bool(kw["mejora_al_calentar"]),
        hinchazon=bool(kw.get("hinchazon", False)),
        sintomas_sistemicos=kw.get("sintomas_sistemicos") or [],
    )
    return ev.a_dict()


FUNCIONES = {
    "evaluar_viabilidad": _evaluar_viabilidad,
    "generar_plan": _generar_plan,
    "calcular_ritmos": _calcular_ritmos,
    "predecir_marca": _predecir_marca,
    "evaluar_sintoma": _evaluar_sintoma,
}


def ejecutar(nombre: str, argumentos: dict) -> dict:
    """Ejecuta una función pedida por el modelo.

    Nunca lanza: un error aquí cortaría la conversación de voz. En su lugar
    devuelve un objeto de error para que el modelo pueda repreguntar.
    """
    funcion = FUNCIONES.get(nombre)
    if funcion is None:
        return {"error": f"función desconocida: {nombre}"}

    try:
        return funcion(**(argumentos or {}))
    except (ValueError, KeyError, TypeError) as exc:
        log.warning("fallo en %s con %s: %s", nombre, argumentos, exc)
        return {
            "error": str(exc),
            "sugerencia": (
                "Faltan datos o son inválidos. Pregunta al corredor lo que falte "
                "y vuelve a intentar; no inventes los valores."
            ),
        }
