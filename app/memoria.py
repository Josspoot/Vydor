"""Persistencia de la conversación y del perfil del corredor.

Cumple dos funciones a la vez:

1. **Dentro de una conversación**: la Live API se queda inerte tras el primer
   turno del modelo, así que se abre una sesión por intervención. El historial
   que se guarda aquí es lo que se reinyecta en cada sesión nueva para que el
   coach no pierda el hilo.

2. **Entre conversaciones**: el perfil estructurado del corredor sobrevive al
   cierre del navegador, para que la siguiente charla empiece por donde
   quedó la anterior.

No se guarda audio, solo texto: las transcripciones son suficientes como
contexto y ocupan una fracción.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

RUTA_BD = Path(__file__).resolve().parent.parent / "coach.db"

ESQUEMA = """
CREATE TABLE IF NOT EXISTS corredores (
    id           TEXT PRIMARY KEY,
    nombre       TEXT,
    perfil       TEXT NOT NULL DEFAULT '{}',   -- JSON con marca, volumen, objetivo...
    actualizado  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turnos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    corredor_id  TEXT NOT NULL REFERENCES corredores(id),
    conversacion TEXT NOT NULL,                -- agrupa los turnos de una charla
    rol          TEXT NOT NULL CHECK (rol IN ('user', 'model')),
    texto        TEXT NOT NULL,
    creado       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_turnos_corredor ON turnos(corredor_id, id);

CREATE TABLE IF NOT EXISTS planes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    corredor_id  TEXT NOT NULL REFERENCES corredores(id),
    conversacion TEXT,                         -- de qué charla salió
    plan         TEXT NOT NULL,                -- JSON del plan generado
    creado       TEXT NOT NULL
);
"""

# Bases creadas antes de que los planes se ligaran a su conversación.
MIGRACIONES = [
    ("planes", "conversacion", "ALTER TABLE planes ADD COLUMN conversacion TEXT"),
]


def _migrar(con) -> None:
    for tabla, columna, sentencia in MIGRACIONES:
        columnas = {f["name"] for f in con.execute(f"PRAGMA table_info({tabla})")}
        if columnas and columna not in columnas:
            con.execute(sentencia)


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def conectar(ruta: Path | str | None = None):
    con = sqlite3.connect(ruta or RUTA_BD)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(ESQUEMA)
        _migrar(con)
        yield con
        con.commit()
    finally:
        con.close()


def corredores_notificables(ruta: Path | str | None = None) -> list[str]:
    """Ids de corredores que vincularon Telegram y tienen un plan vigente."""
    with conectar(ruta or RUTA_BD) as con:
        filas = con.execute(
            "SELECT DISTINCT c.id FROM corredores c "
            "JOIN planes p ON p.corredor_id = c.id "
            "WHERE json_extract(c.perfil, '$.telegram_chat_id') IS NOT NULL"
        ).fetchall()
    return [f["id"] for f in filas]


def corredor_por_codigo(codigo: str, ruta: Path | str | None = None) -> str | None:
    """Traduce el código que el corredor envía al bot a su id interno."""
    with conectar(ruta or RUTA_BD) as con:
        fila = con.execute(
            "SELECT id FROM corredores "
            "WHERE json_extract(perfil, '$.codigo_telegram') = ?",
            (codigo,),
        ).fetchone()
    return fila["id"] if fila else None


def conversaciones(corredor_id: str, ruta: Path | str | None = None) -> list[dict]:
    """Las charlas de un corredor, de la más reciente a la más antigua.

    El título es lo primero que dijo el corredor en esa charla: describe de qué
    iba mucho mejor que una fecha.
    """
    with conectar(ruta or RUTA_BD) as con:
        filas = con.execute(
            """
            SELECT t.conversacion            AS id,
                   MIN(t.creado)             AS inicio,
                   MAX(t.creado)             AS fin,
                   COUNT(*)                  AS turnos,
                   (SELECT texto FROM turnos
                     WHERE conversacion = t.conversacion AND rol = 'user'
                     ORDER BY id LIMIT 1)    AS titulo,
                   (SELECT COUNT(*) FROM planes
                     WHERE conversacion = t.conversacion) AS planes
              FROM turnos t
             WHERE t.corredor_id = ?
          GROUP BY t.conversacion
          ORDER BY MAX(t.id) DESC
            """,
            (corredor_id,),
        ).fetchall()
    return [dict(f) for f in filas]


def planes_de(corredor_id: str, ruta: Path | str | None = None) -> list[dict]:
    """Resumen de cada plan guardado, sin arrastrar el JSON entero."""
    with conectar(ruta or RUTA_BD) as con:
        filas = con.execute(
            "SELECT id, conversacion, creado, plan FROM planes "
            "WHERE corredor_id = ? ORDER BY id DESC",
            (corredor_id,),
        ).fetchall()
    resumen = []
    for f in filas:
        datos = json.loads(f["plan"])
        resumen.append({
            "id": f["id"],
            "conversacion": f["conversacion"],
            "creado": f["creado"],
            "distancia": datos.get("distancia"),
            "semanas": datos.get("semanas"),
            "dias_por_semana": datos.get("dias_por_semana"),
            "vdot": datos.get("vdot"),
        })
    return resumen


def transcripcion(conversacion: str, corredor_id: str,
                 ruta: Path | str | None = None) -> list[dict]:
    """Los turnos de una charla, para volver a pintarla en pantalla."""
    with conectar(ruta or RUTA_BD) as con:
        filas = con.execute(
            "SELECT rol, texto, creado FROM turnos "
            "WHERE conversacion = ? AND corredor_id = ? ORDER BY id",
            (conversacion, corredor_id),
        ).fetchall()
    return [
        {"quien": "corredor" if f["rol"] == "user" else "coach",
         "texto": f["texto"], "creado": f["creado"]}
        for f in filas
    ]


def plan_por_id(plan_id: int, corredor_id: str, ruta: Path | str | None = None) -> dict | None:
    """Un plan concreto. Pide el corredor para que nadie lea planes ajenos."""
    with conectar(ruta or RUTA_BD) as con:
        fila = con.execute(
            "SELECT plan FROM planes WHERE id = ? AND corredor_id = ?",
            (plan_id, corredor_id),
        ).fetchone()
    return json.loads(fila["plan"]) if fila else None


class Memoria:
    """Acceso a la memoria de un corredor concreto."""

    def __init__(self, corredor_id: str, conversacion: str, ruta: Path | str | None = None):
        self.corredor_id = corredor_id
        self.conversacion = conversacion
        self.ruta = ruta or RUTA_BD
        with conectar(self.ruta) as con:
            con.execute(
                "INSERT INTO corredores (id, perfil, actualizado) VALUES (?, '{}', ?) "
                "ON CONFLICT(id) DO NOTHING",
                (corredor_id, _ahora()),
            )

    # ---------------------------------------------------------------- turnos

    def guardar_turno(self, rol: str, texto: str) -> None:
        if not texto.strip():
            return
        with conectar(self.ruta) as con:
            con.execute(
                "INSERT INTO turnos (corredor_id, conversacion, rol, texto, creado) "
                "VALUES (?, ?, ?, ?, ?)",
                (self.corredor_id, self.conversacion, rol, texto.strip(), _ahora()),
            )

    def historial(self, max_turnos: int = 20) -> list[dict]:
        """Turnos de ESTA conversación, en el formato que espera la Live API."""
        with conectar(self.ruta) as con:
            filas = con.execute(
                "SELECT rol, texto FROM turnos WHERE corredor_id = ? AND conversacion = ? "
                "ORDER BY id DESC LIMIT ?",
                (self.corredor_id, self.conversacion, max_turnos),
            ).fetchall()
        return [
            {"role": f["rol"], "parts": [{"text": f["texto"]}]}
            for f in reversed(filas)
        ]

    # ---------------------------------------------------------------- perfil

    def perfil(self) -> dict:
        with conectar(self.ruta) as con:
            fila = con.execute(
                "SELECT nombre, perfil FROM corredores WHERE id = ?", (self.corredor_id,)
            ).fetchone()
        if not fila:
            return {}
        datos = json.loads(fila["perfil"] or "{}")
        if fila["nombre"]:
            datos.setdefault("nombre", fila["nombre"])
        return datos

    def actualizar_perfil(self, **campos) -> dict:
        """Fusiona campos nuevos sobre el perfil existente."""
        datos = self.perfil()
        datos.update({k: v for k, v in campos.items() if v is not None})
        with conectar(self.ruta) as con:
            con.execute(
                "UPDATE corredores SET perfil = ?, nombre = COALESCE(?, nombre), "
                "actualizado = ? WHERE id = ?",
                (json.dumps(datos, ensure_ascii=False), datos.get("nombre"),
                 _ahora(), self.corredor_id),
            )
        return datos

    def borrar_del_perfil(self, *claves: str) -> dict:
        """Elimina campos del perfil.

        Hace falta un método aparte porque actualizar_perfil ignora los None
        a propósito, para que un dato que no se conoce no borre el que ya
        había. Aquí la intención es justo la contraria.
        """
        datos = self.perfil()
        for clave in claves:
            datos.pop(clave, None)
        with conectar(self.ruta) as con:
            con.execute(
                "UPDATE corredores SET perfil = ?, actualizado = ? WHERE id = ?",
                (json.dumps(datos, ensure_ascii=False), _ahora(), self.corredor_id),
            )
        return datos

    def guardar_plan(self, plan: dict) -> None:
        with conectar(self.ruta) as con:
            con.execute(
                "INSERT INTO planes (corredor_id, conversacion, plan, creado) "
                "VALUES (?, ?, ?, ?)",
                (self.corredor_id, self.conversacion,
                 json.dumps(plan, ensure_ascii=False), _ahora()),
            )
        v = plan.get("viabilidad", {})
        self.actualizar_perfil(
            distancia_objetivo=plan.get("distancia"),
            semanas_plan=plan.get("semanas"),
            dias_por_semana=plan.get("dias_por_semana"),
            vdot=plan.get("vdot"),
            km_pico=v.get("km_pico_alcanzable"),
        )

    def ultimo_plan(self) -> dict | None:
        con_fecha = self.ultimo_plan_con_fecha()
        return con_fecha[0] if con_fecha else None

    def ultimo_plan_con_fecha(self) -> tuple[dict, date] | None:
        """El plan y el día en que se generó.

        La fecha es imprescindible para los recordatorios: sin ella no se
        puede saber en qué semana del plan va el corredor.
        """
        with conectar(self.ruta) as con:
            fila = con.execute(
                "SELECT plan, creado FROM planes WHERE corredor_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (self.corredor_id,),
            ).fetchone()
        if not fila:
            return None
        return json.loads(fila["plan"]), datetime.fromisoformat(fila["creado"]).date()

    # ------------------------------------------------------------- resúmenes

    def resumen_para_prompt(self) -> str | None:
        """Lo que el coach debe recordar de este corredor al empezar a hablar.

        Va en la instrucción de sistema, así que se escribe en prosa corta:
        una lista de campos haría que el modelo la recite como un expediente.
        """
        perfil = self.perfil()
        ultima = self._ultima_charla()
        if not perfil and not ultima:
            return None

        partes = []
        if nombre := perfil.get("nombre"):
            partes.append(f"Se llama {nombre}.")
        if perfil.get("distancia_objetivo"):
            frase = f"Prepara un {perfil['distancia_objetivo']}"
            if perfil.get("semanas_plan"):
                frase += f" con un plan de {perfil['semanas_plan']} semanas"
            if perfil.get("dias_por_semana"):
                frase += f", entrenando {perfil['dias_por_semana']} días por semana"
            partes.append(frase + ".")
        if perfil.get("vdot"):
            partes.append(f"Su VDOT calculado es {perfil['vdot']}.")
        if lesion := perfil.get("molestia_reciente"):
            partes.append(f"Reportó una molestia en {lesion}; pregúntale cómo sigue.")
        if ultima:
            dias = ultima["dias"]
            cuando = "hoy" if dias == 0 else "ayer" if dias == 1 else f"hace {dias} días"
            partes.append(f"Hablaron por última vez {cuando}. Terminaron así: {ultima['texto']}")
        return " ".join(partes) or None

    def _ultima_charla(self) -> dict | None:
        with conectar(self.ruta) as con:
            fila = con.execute(
                "SELECT texto, creado, conversacion FROM turnos "
                "WHERE corredor_id = ? AND conversacion != ? AND rol = 'model' "
                "ORDER BY id DESC LIMIT 1",
                (self.corredor_id, self.conversacion),
            ).fetchone()
        if not fila:
            return None
        cuando = datetime.fromisoformat(fila["creado"]).date()
        return {
            "texto": fila["texto"][:200],
            "dias": (date.today() - cuando).days,
        }
