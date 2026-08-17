"""Qué entrenamiento toca hoy y cómo contárselo al corredor.

Separado a propósito del envío por Telegram: aquí no hay red ni tokens, solo
fechas y texto, que es lo que conviene poder probar sin depender de nadie.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.memoria import Memoria

NOMBRE_DIA = ["", "lunes", "martes", "miércoles", "jueves", "viernes",
              "sábado", "domingo"]

# El vocabulario del motor no es el del corredor. Mismo criterio que la interfaz.
NOMBRE_TIPO = {
    "descanso": "Descanso",
    "facil": "Rodaje suave",
    "largo": "Fondo largo",
    "tempo": "Ritmo controlado",
    "intervalos": "Series rápidas",
    "ritmo_objetivo": "Ritmo de carrera",
    "carrera": "DÍA DE CARRERA",
}

NOMBRE_ZONA = {
    "facil": "fácil", "maraton": "ritmo de maratón", "umbral": "umbral",
    "intervalo": "intervalo", "repeticion": "repetición",
}


def semana_del_plan(inicio: date, hoy: date) -> int:
    """En qué semana del plan cae `hoy`. La semana 1 es la del día de inicio.

    Se cuenta desde el lunes de la semana en que se generó el plan: si alguien
    lo recibe un miércoles, el jueves sigue siendo su semana 1.
    """
    lunes_inicial = inicio - timedelta(days=inicio.weekday())
    return (hoy - lunes_inicial).days // 7 + 1


def sesion_de(plan: dict, inicio: date, hoy: date) -> dict | None:
    """La sesión que toca ese día, o None si el plan ya terminó o no empezó."""
    numero = semana_del_plan(inicio, hoy)
    if numero < 1 or numero > len(plan["semanas_plan"]):
        return None
    semana = plan["semanas_plan"][numero - 1]
    for sesion in semana["sesiones"]:
        if sesion["dia"] == hoy.isoweekday():
            return {**sesion, "semana": semana["numero"], "fase": semana["fase"]}
    return None


def _ritmo_de(plan: dict, zona: str) -> str | None:
    r = (plan.get("ritmos") or {}).get(zona)
    if not r:
        return None
    rapido, lento = r["seg_por_km_rapido"], r["seg_por_km_lento"]
    fmt = lambda s: f"{s // 60}:{s % 60:02d}"
    return fmt(rapido) if rapido == lento else f"{fmt(rapido)}–{fmt(lento)}"


def redactar(plan: dict, sesion: dict, nombre: str | None = None) -> str:
    """Mensaje de Telegram para la sesión de hoy.

    Corto a propósito: llega al teléfono, no a una pantalla de escritorio.
    """
    saludo = f"Buenos días{', ' + nombre if nombre else ''}."
    total = len(plan["semanas_plan"])
    # Nombrar el plan importa cuando hay más de uno: sin esto, un mensaje
    # suelto en el teléfono no dice si es del 10K o de la media.
    donde = (f"Plan {plan['distancia'].upper()} · semana " if plan.get("distancia")
             else "Semana ") + f"{sesion['semana']} de {total}."

    if sesion["tipo"] == "descanso":
        return (f"{saludo} Hoy toca descanso. El descanso es parte del plan, no "
                f"una pausa en él.\n\n{donde}")

    titulo = NOMBRE_TIPO.get(sesion["tipo"], sesion["tipo"])
    lineas = [
        saludo,
        f"Hoy {NOMBRE_DIA[sesion['dia']]} toca *{titulo}*"
        + (f" · {sesion['km']:g} km" if sesion["km"] else ""),
        "",
        sesion["detalle"],
    ]

    ritmo = _ritmo_de(plan, sesion["zona"])
    if ritmo:
        lineas.append(f"\nRitmo objetivo: {ritmo} por km ({NOMBRE_ZONA.get(sesion['zona'], sesion['zona'])}).")

    lineas.append(f"\n{donde}")
    if sesion["tipo"] == "carrera":
        lineas.append("Sal conservador los primeros kilómetros. Mucha suerte.")
    return "\n".join(lineas)


def recordatorio_para(
    corredor_id: str, hoy: date | None = None, ruta=None
) -> str | None:
    """Mensaje de hoy para un corredor, o None si no hay nada que decirle."""
    hoy = hoy or date.today()
    memoria = Memoria(corredor_id, conversacion="recordatorio", ruta=ruta)

    guardado = memoria.ultimo_plan_con_fecha()
    if not guardado:
        return None
    plan, inicio = guardado

    sesion = sesion_de(plan, inicio, hoy)
    if not sesion:
        return None

    perfil = memoria.perfil()
    mensaje = redactar(plan, sesion, perfil.get("nombre"))

    # Si quedó una molestia sin cerrar, preguntar pesa más que el entrenamiento.
    if molestia := perfil.get("molestia_reciente"):
        mensaje += (f"\n\nPor cierto, ¿cómo va esa molestia en {molestia}? "
                    f"Si sigue igual, cuéntamelo antes de entrenar.")
    return mensaje
