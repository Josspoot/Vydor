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


def _fecha_local(marca: str) -> date:
    """El día natural del corredor en que ocurrió algo guardado en UTC.

    Se guarda en UTC para que la base no dependa de dónde corra el servidor,
    pero "qué día fue" solo tiene sentido en la hora local: al este de UTC-0
    la fecha UTC se adelanta por la noche, y comparar contra date.today()
    daba diferencias negativas ("hablaron hace -1 días") y desplazaba en un
    día la semana del plan que usan los recordatorios.
    """
    momento = datetime.fromisoformat(marca)
    if momento.tzinfo is None:                 # filas antiguas, sin zona
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.astimezone().date()


def _enumerar(cosas: list[str]) -> str:
    """"10K", "10K y 21K", "10K, 21K y 42K" — para que el prompt suene a prosa."""
    if len(cosas) == 1:
        return cosas[0]
    return ", ".join(cosas[:-1]) + " y " + cosas[-1]


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

    def guardar_plan(self, plan: dict) -> int:
        """Guarda el plan en esta conversación y devuelve su id.

        Solo se copian al perfil del corredor los datos que son de la persona
        y no de la meta. La distancia, las semanas y los días por semana viven
        en el plan, y el plan vive en su conversación: si se guardaran aquí,
        preparar un 21K en otra charla reescribiría lo que el coach recuerda
        del 10K que ya estabas corriendo.
        """
        with conectar(self.ruta) as con:
            cursor = con.execute(
                "INSERT INTO planes (corredor_id, conversacion, plan, creado) "
                "VALUES (?, ?, ?, ?)",
                (self.corredor_id, self.conversacion,
                 json.dumps(plan, ensure_ascii=False), _ahora()),
            )
            plan_id = int(cursor.lastrowid)

        self.actualizar_perfil(vdot=plan.get("vdot"))

        # Mientras el corredor no elija, los recordatorios usan el plan más
        # reciente; por eso aquí no se fija nada, o un valor por defecto
        # pasaría por una decisión suya. Si ya eligió, rehacer el plan de esa
        # misma charla es corregir la misma meta y el activo lo sigue; un plan
        # de otra charla es otra meta y no le roba el puesto.
        activo = self.perfil().get("plan_activo")
        if activo is not None and self._conversacion_del_plan(activo) == self.conversacion:
            self.actualizar_perfil(plan_activo=plan_id)
        return plan_id

    def _conversacion_del_plan(self, plan_id: int) -> str | None:
        with conectar(self.ruta) as con:
            fila = con.execute(
                "SELECT conversacion FROM planes WHERE id = ? AND corredor_id = ?",
                (plan_id, self.corredor_id),
            ).fetchone()
        return fila["conversacion"] if fila else None

    def activar_plan(self, plan_id: int) -> bool:
        """Marca de qué plan quiere recibir recordatorios el corredor."""
        if self._conversacion_del_plan(plan_id) is None:
            return False
        self.actualizar_perfil(plan_activo=plan_id)
        return True

    def ultimo_plan(self) -> dict | None:
        con_fecha = self.ultimo_plan_con_fecha()
        return con_fecha[0] if con_fecha else None

    def plan_de_esta_conversacion(self) -> dict | None:
        """El plan de la charla abierta, que es del que se está hablando."""
        with conectar(self.ruta) as con:
            fila = con.execute(
                "SELECT plan FROM planes WHERE corredor_id = ? AND conversacion = ? "
                "ORDER BY id DESC LIMIT 1",
                (self.corredor_id, self.conversacion),
            ).fetchone()
        return json.loads(fila["plan"]) if fila else None

    def _fila_del_plan_activo(self):
        """El plan del que salen los recordatorios.

        Es el que el corredor marcó; si no marcó ninguno, o si el que marcó
        ya no existe, el más reciente.
        """
        activo = self.perfil().get("plan_activo")
        with conectar(self.ruta) as con:
            fila = None
            if activo is not None:
                fila = con.execute(
                    "SELECT id, plan, creado FROM planes "
                    "WHERE id = ? AND corredor_id = ?",
                    (activo, self.corredor_id),
                ).fetchone()
            if fila is None:
                fila = con.execute(
                    "SELECT id, plan, creado FROM planes WHERE corredor_id = ? "
                    "ORDER BY id DESC LIMIT 1",
                    (self.corredor_id,),
                ).fetchone()
        return fila

    def id_del_plan_activo(self) -> int | None:
        fila = self._fila_del_plan_activo()
        return int(fila["id"]) if fila else None

    def ultimo_plan_con_fecha(self) -> tuple[dict, date] | None:
        """El plan activo y el día en que se generó.

        La fecha es imprescindible: sin ella no se puede saber en qué semana
        del plan va el corredor.
        """
        fila = self._fila_del_plan_activo()
        if not fila:
            return None
        return json.loads(fila["plan"]), _fecha_local(fila["creado"])

    # ------------------------------------------------------------- resúmenes

    def resumen_para_prompt(self) -> str | None:
        """Lo que el coach debe recordar de este corredor al empezar a hablar.

        Va en la instrucción de sistema, así que se escribe en prosa corta:
        una lista de campos haría que el modelo la recite como un expediente.
        """
        perfil = self.perfil()
        ultima = self._ultima_charla()
        plan = self.plan_de_esta_conversacion()
        otras = self._otras_metas()
        if not perfil and not ultima and not plan:
            return None

        partes = []
        if nombre := perfil.get("nombre"):
            partes.append(f"Se llama {nombre}.")

        # La meta sale del plan de esta charla, no del perfil: es lo que hace
        # que dos objetivos a la vez no se pisen. Los de otras charlas se
        # nombran como lo que son, para que el coach pueda preguntar de cuál
        # se habla en vez de dar uno por hecho.
        if plan:
            frase = f"En esta charla prepara un {plan.get('distancia')}"
            if plan.get("semanas"):
                frase += f" con un plan de {plan['semanas']} semanas"
            if plan.get("dias_por_semana"):
                frase += f", entrenando {plan['dias_por_semana']} días por semana"
            partes.append(frase + ".")
        if otras:
            partes.append(
                f"En otras conversaciones tiene planes de {_enumerar(otras)}; "
                "son metas aparte y no se mezclan con esta."
            )
        if perfil.get("vdot"):
            partes.append(f"Su VDOT calculado es {perfil['vdot']}.")
        if lesion := perfil.get("molestia_reciente"):
            partes.append(f"Reportó una molestia en {lesion}; pregúntale cómo sigue.")
        if ultima:
            dias = ultima["dias"]
            cuando = "hoy" if dias == 0 else "ayer" if dias == 1 else f"hace {dias} días"
            partes.append(f"Hablaron por última vez {cuando}. Terminaron así: {ultima['texto']}")
        return " ".join(partes) or None

    def _otras_metas(self) -> list[str]:
        """Distancias que el corredor prepara en otras charlas."""
        with conectar(self.ruta) as con:
            filas = con.execute(
                "SELECT plan FROM planes WHERE corredor_id = ? AND conversacion != ? "
                "ORDER BY id DESC",
                (self.corredor_id, self.conversacion),
            ).fetchall()
        vistas: list[str] = []
        for f in filas:
            distancia = json.loads(f["plan"]).get("distancia")
            if distancia and distancia not in vistas:
                vistas.append(distancia)
        return vistas

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
        return {
            "texto": fila["texto"][:200],
            "dias": (date.today() - _fecha_local(fila["creado"])).days,
        }
