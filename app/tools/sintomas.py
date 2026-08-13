"""Triaje de molestias y lesiones.

Un coach de voz que improvise sobre dolor es un riesgo real. Esta lógica
es determinista a propósito: el LLM extrae los hechos de la conversación
y esta función decide, no al revés.

NO es diagnóstico médico. Su único trabajo es distinguir tres cosas:
  1. lo que necesita atención médica ya,
  2. lo que necesita a un profesional antes de seguir entrenando,
  3. lo que es molestia normal de adaptación.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Literal

Nivel = Literal["emergencia", "parar_y_consultar", "precaucion", "molestia_normal"]

# Síntomas que no admiten "descansa dos días y vemos"
SINTOMAS_SISTEMICOS = {
    "dolor_pecho": "dolor u opresión en el pecho",
    "desmayo": "desmayo o pérdida de conocimiento",
    "mareo_intenso": "mareo intenso o visión borrosa",
    "palpitaciones": "palpitaciones o ritmo cardiaco irregular",
    "falta_de_aire_en_reposo": "falta de aire estando en reposo",
}


@dataclass
class Evaluacion:
    nivel: Nivel
    mensaje: str
    puede_entrenar: bool
    ajuste_sugerido: str
    motivos: list[str]

    def a_dict(self) -> dict:
        return asdict(self)


def evaluar_sintoma(
    zona: str,
    dias_con_sintoma: int = 1,
    duele_en_reposo: bool = False,
    cambia_la_pisada: bool = False,
    dolor_punzante_localizado: bool = False,
    mejora_al_calentar: bool = True,
    hinchazon: bool = False,
    sintomas_sistemicos: list[str] | None = None,
) -> Evaluacion:
    """Clasifica una molestia reportada por el corredor.

    Args:
        zona: parte del cuerpo ("rodilla derecha", "tibia", "tendón de Aquiles").
        dias_con_sintoma: cuántos días lleva con la molestia.
        duele_en_reposo: si duele estando sentado o acostado.
        cambia_la_pisada: si lo hace cojear o modificar la técnica.
        dolor_punzante_localizado: dolor de punta de dedo sobre un punto óseo.
        mejora_al_calentar: si se alivia tras los primeros minutos de trote.
        hinchazon: hinchazón, calor o enrojecimiento visibles.
        sintomas_sistemicos: claves de SINTOMAS_SISTEMICOS presentes.
    """
    sistemicos = [s for s in (sintomas_sistemicos or []) if s in SINTOMAS_SISTEMICOS]

    if sistemicos:
        descripciones = ", ".join(SINTOMAS_SISTEMICOS[s] for s in sistemicos)
        return Evaluacion(
            nivel="emergencia",
            mensaje=(
                f"Para de entrenar ahora mismo. Lo que describes ({descripciones}) "
                f"necesita valoración médica inmediata, no ajustes de plan. "
                f"Busca atención médica hoy."
            ),
            puede_entrenar=False,
            ajuste_sugerido="Entrenamiento suspendido hasta alta médica.",
            motivos=sistemicos,
        )

    motivos: list[str] = []
    if duele_en_reposo:
        motivos.append("duele en reposo")
    if cambia_la_pisada:
        motivos.append("altera la forma de correr")
    if dolor_punzante_localizado:
        motivos.append("dolor punzante en un punto concreto")
    if hinchazon:
        motivos.append("hay hinchazón o calor")
    if not mejora_al_calentar:
        motivos.append("no mejora al calentar")
    if dias_con_sintoma >= 10:
        motivos.append(f"lleva {dias_con_sintoma} días")

    # Fractura por estrés: punto óseo doloroso al tacto que empeora al correr.
    sospecha_fractura = dolor_punzante_localizado and not mejora_al_calentar

    if sospecha_fractura or cambia_la_pisada or duele_en_reposo:
        return Evaluacion(
            nivel="parar_y_consultar",
            mensaje=(
                f"No voy a darte una rutina con esto. Lo que cuentas de "
                f"{zona} ({', '.join(motivos)}) tiene señales que un "
                f"fisioterapeuta o médico del deporte debe revisar antes de "
                f"que sigas corriendo. Seguir empeora el pronóstico."
            ),
            puede_entrenar=False,
            ajuste_sugerido=(
                "Suspende la carrera. Mantén actividad sin impacto (natación, "
                "bici suave) solo si no genera dolor."
            ),
            motivos=motivos,
        )

    if len(motivos) >= 2 or dias_con_sintoma >= 7:
        return Evaluacion(
            nivel="precaucion",
            mensaje=(
                f"La molestia en {zona} merece cautela ({', '.join(motivos)}). "
                f"Bajemos la carga unos días y observemos. Si no mejora en una "
                f"semana o aparece cojera, toca revisión profesional."
            ),
            puede_entrenar=True,
            ajuste_sugerido=(
                "Reduce el volumen ~40% esta semana, quita las sesiones de "
                "calidad y corre solo en ritmo fácil sin dolor. Nada de fondo largo."
            ),
            motivos=motivos,
        )

    return Evaluacion(
        nivel="molestia_normal",
        mensaje=(
            f"Suena a molestia de adaptación en {zona}: mejora al calentar, sin "
            f"cojera ni dolor en reposo. Es común al subir carga."
        ),
        puede_entrenar=True,
        ajuste_sugerido=(
            "Sigue el plan pero sin subir volumen esta semana. Si el dolor "
            "aumenta durante una sesión, córtala ahí mismo."
        ),
        motivos=motivos or ["sin señales de alarma"],
    )
