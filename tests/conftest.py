"""Fixtures compartidos.

El plan de ejemplo se genera con el motor de verdad, no a mano: un plan
inventado en el test podría cumplir aserciones que un plan real nunca
cumpliría, y entonces el test dejaría de decir nada sobre el producto.
"""

import pytest

from app.tools import esquemas


@pytest.fixture
def plan():
    return esquemas.ejecutar("generar_plan", {
        "distancia": "10k", "semanas": 12, "km_semanales_actuales": 25,
        "dias_por_semana": 4,
        "marca_reciente_distancia_m": 10000, "marca_reciente_tiempo_s": 3000,
    })
