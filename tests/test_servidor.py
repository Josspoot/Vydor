"""Tests de los endpoints del servidor.

TestClient se instancia sin usarlo como contexto: así no se dispara el ciclo
de vida y no arrancan ni el bot de Telegram ni el planificador.

Lo que toca la base lo hace sobre una temporal —ver el fixture `bd_temporal`—
para no escribir en el coach.db de quien ejecute la suite.
"""

import pytest
from fastapi.testclient import TestClient

from app import memoria
from app.main import app

cliente = TestClient(app)


def test_los_estaticos_obligan_a_revalidar():
    """Un app.js cacheado es un cambio que "no se refleja".

    Sin cabecera, el navegador decide por heurística y puede quedarse con una
    versión vieja aunque el servidor ya sirva la nueva. `no-cache` no desactiva
    la caché: obliga a preguntar, y si no cambió la respuesta es un 304.
    """
    r = cliente.get("/static/app.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"


def test_el_index_tambien_revalida():
    """Si el index se queda viejo, ni siquiera se pide el JS nuevo."""
    r = cliente.get("/")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"


def test_revalidar_no_vuelve_a_descargar():
    """La cabecera no debe costar ancho de banda en cada carga."""
    etag = cliente.get("/static/app.js").headers["etag"]
    r = cliente.get("/static/app.js", headers={"If-None-Match": etag})
    assert r.status_code == 304


# --------------------------------------------------------------------------
# Borrar una conversación desde la API
# --------------------------------------------------------------------------

@pytest.fixture
def bd_temporal(tmp_path, monkeypatch):
    """Apunta la app a una base de usar y tirar.

    Sin esto, los tests de estos endpoints escribirían en el coach.db real de
    quien ejecute la suite.
    """
    ruta = tmp_path / "servidor.db"
    monkeypatch.setattr(memoria, "RUTA_BD", ruta)
    return ruta


def test_borrar_una_conversacion_por_la_api(bd_temporal):
    charla = memoria.Memoria("josue", "c1", ruta=bd_temporal)
    charla.guardar_turno("user", "Quiero un 10K")
    charla.guardar_plan({"distancia": "10k", "semanas": 12})

    r = cliente.delete("/api/conversaciones/c1?corredor=josue")
    assert r.status_code == 200
    assert charla.historial() == []
    assert cliente.get("/api/conversaciones?corredor=josue").json()["conversaciones"] == []


def test_borrar_algo_que_no_existe_da_404(bd_temporal):
    assert cliente.delete("/api/conversaciones/nada?corredor=josue").status_code == 404


def test_no_se_puede_borrar_la_conversacion_de_otro(bd_temporal):
    """La comprobación vive en la consulta, no en la interfaz."""
    ajena = memoria.Memoria("otro", "suya", ruta=bd_temporal)
    ajena.guardar_turno("user", "mi charla")

    assert cliente.delete("/api/conversaciones/suya?corredor=josue").status_code == 404
    assert len(ajena.historial()) == 1
