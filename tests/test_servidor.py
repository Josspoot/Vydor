"""Tests del servidor que no tocan la base de datos.

Solo se prueban rutas que sirven archivos: se instancia TestClient sin usarlo
como contexto, así no se dispara el ciclo de vida y no arranca ni el bot de
Telegram ni el planificador.
"""

from fastapi.testclient import TestClient

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
