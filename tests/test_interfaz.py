"""Cuelga los tests de interfaz de pytest.

Van en node porque prueban JavaScript, pero se lanzan desde aquí a propósito:
un test que hay que acordarse de ejecutar aparte es un test que nadie ejecuta.
Si no hay node, se saltan en vez de fallar: el producto no lo necesita para
funcionar.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).parent / "frontend"
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node no está instalado")
@pytest.mark.parametrize("guion", sorted(p.name for p in FRONTEND.glob("*.js")))
def test_interfaz(guion):
    resultado = subprocess.run(
        [NODE, str(FRONTEND / guion)],
        capture_output=True, text=True, timeout=60,
    )
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
