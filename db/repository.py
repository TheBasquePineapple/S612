"""
db/repository.py — Capa de acceso a base de datos de RAISA
===========================================================
Responsabilidad : ÚNICO punto de acceso a SQLite. Ningún cog ni utils
                  debe ejecutar SQL directamente; todo pasa por aquí.
Dependencias    : aiosqlite (async SQLite), json (stdlib)
Autor           : RAISA Dev

Secciones
---------
  1. Conexión y pool
  2. Personajes y formularios
  3. Inventario (loadout, general, pouches)
  4. Estado médico
  5. Radio
  6. Economía
  7. Tienda
  8. Vehículos
  9. Eventos
  10. Auditoría (escritura)
  11. Webhooks
  12. Ítems (consultas de catálogo)

NOTA: Todas las funciones son async. Usar `await` siempre.
"""

import json
import os
from pathlib import Path
from typing import Any

import aiosqlite

DB_PATH = Path(os.getenv("DB_PATH", "data/raisa.db"))


# ---------------------------------------------------------------------------
# 1. Conexión
# ---------------------------------------------------------------------------

class ConnWrapper:
    """
    Wrapper para permitir doble 'await' y 'async with' en la misma conexión
    sin reiniciar el thread interno de aiosqlite, evitando el error:
    'threads can only be started once'.
    """
    def __init__(self, conn: aiosqlite.Connection):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.conn.close()

async def get_conn() -> ConnWrapper:
    """
    Abre y devuelve un wrapper de conexión aiosqlite con pragmas de producción.
    El llamador puede hacer `async with await get_conn() as conn:` 
    de forma segura.
    """
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode = WAL")
    await conn.execute("PRAGMA foreign_keys = ON")
    await conn.execute("PRAGMA synchronous = NORMAL")
    return ConnWrapper(conn)


async def _one(conn: aiosqlite.Connection, sql: str, params: tuple = ()) -> aiosqlite.Row | None:
    """Ejecuta una consulta y devuelve la primera fila o None."""
    async with conn.execute(sql, params) as cur:
        return await cur.fetchone()


async def _all(conn: aiosqlite.Connection, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
    """Ejecuta una consulta y devuelve todas las filas."""
    async with conn.execute(sql, params) as cur:
        return await cur.fetchall()


async def _run(conn: aiosqlite.Connection, sql: str, params: tuple = ()) -> int:
    """Ejecuta una sentencia DML y devuelve el lastrowid."""
    async with conn.execute(sql, params) as cur:
        await conn.commit()
        return cur.lastrowid


# ---------------------------------------------------------------------------
# 2. Personajes y formularios de registro
# ---------------------------------------------------------------------------

async def get_character(conn, user_id: int) -> aiosqlite.Row | None:
    """
    Obtiene el personaje activo de un usuario.

    Args:
        conn    : Conexión aiosqlite.
        user_id : discord user_id.

    Returns:
        Fila de characters o None.
    """
    return await _one(conn, "SELECT * FROM characters WHERE user_id = ?", (user_id,))


async def get_character_by_id(conn, char_id: int) -> aiosqlite.Row | None:
    """Obtiene un personaje por su ID interno."""
    return await _one(conn, "SELECT * FROM characters WHERE id = ?", (char_id,))


async def get_characters_activos(conn) -> list[aiosqlite.Row]:
    """
    Devuelve todos los personajes con estado='activo'.
    
    Usada por el sistema de economía para el pago automático de salarios.
    
    Returns:
        Lista de filas de characters.
    """
    return await _all(conn, "SELECT * FROM characters WHERE estado='activo'")


async def create_character(conn, data: dict) -> int:
    """
    Inserta un nuevo personaje en estado 'pendiente'.

    Args:
        conn : Conexión aiosqlite.
        data : Dict con todos los campos del personaje.

    Returns:
        ID del registro insertado.
    """
    fields = [
        "user_id", "nombre_completo", "edad", "genero", "nacionalidad",
        "servicio_previo", "destinos_ops", "clase", "clase_compleja",
        "resultado_psico", "estudios", "ocupaciones_previas", "trasfondo",
        "avatar_path", "estado",
    ]
    cols   = ", ".join(fields)
    ph     = ", ".join("?" for _ in fields)
    values = tuple(data.get(f) for f in fields)
    return await _run(conn, f"INSERT INTO characters ({cols}) VALUES ({ph})", values)


async def update_character_estado(conn, user_id: int, estado: str,
                                   verificado_por: int | None = None,
                                   motivo: str | None = None) -> None:
    """
    Actualiza el estado de un personaje y opcionalmente el verificador.

    Args:
        conn          : Conexión aiosqlite.
        user_id       : discord user_id.
        estado        : Nuevo estado ('activo', 'denegado', 'baja', 'pendiente').
        verificado_por: discord user_id del verificador (Narrador+).
        motivo        : Motivo de denegación si aplica.
    """
    await conn.execute(
        """
        UPDATE characters
        SET estado=?, verificado_por=?, verificado_en=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
            motivo_denegacion=?
        WHERE user_id=?
        """,
        (estado, verificado_por, motivo, user_id),
    )
    await conn.commit()


async def update_character_unidad_radio(conn, user_id: int, unidad: str) -> None:
    """Actualiza la unidad radio asignada a un personaje."""
    await conn.execute(
        "UPDATE characters SET unidad_radio=? WHERE user_id=?", (unidad, user_id)
    )
    await conn.commit()


# -- Formularios --

async def get_form(conn, user_id: int) -> aiosqlite.Row | None:
    """Obtiene el formulario en progreso de un usuario."""
    return await _one(conn, "SELECT * FROM registration_forms WHERE user_id=?", (user_id,))


async def upsert_form(conn, user_id: int, paso: int, datos: dict) -> None:
    """
    Inserta o actualiza el progreso de un formulario de registro.

    Args:
        conn    : Conexión aiosqlite.
        user_id : discord user_id.
        paso    : Paso actual (1-12).
        datos   : Dict acumulado de respuestas.
    """
    datos_json = json.dumps(datos, ensure_ascii=False)
    await conn.execute(
        """
        INSERT INTO registration_forms (user_id, paso_actual, datos_json, suspendido)
        VALUES (?, ?, ?, 0)
        ON CONFLICT(user_id) DO UPDATE SET
            paso_actual      = excluded.paso_actual,
            datos_json       = excluded.datos_json,
            ultima_actividad = strftime('%Y-%m-%dT%H:%M:%fZ','now'),
            suspendido       = 0
        """,
        (user_id, paso, datos_json),
    )
    await conn.commit()


async def suspend_form(conn, user_id: int) -> None:
    """Marca un formulario como suspendido por inactividad."""
    await conn.execute(
        "UPDATE registration_forms SET suspendido=1 WHERE user_id=?", (user_id,)
    )
    await conn.commit()


async def delete_form(conn, user_id: int) -> None:
    """Elimina el formulario en progreso de un usuario."""
    await conn.execute("DELETE FROM registration_forms WHERE user_id=?", (user_id,))
    await conn.commit()


async def add_to_verification_queue(conn, char_id: int,
                                     message_id: int, channel_id: int) -> None:
    """Registra una ficha en la cola de verificación."""
    await conn.execute(
        """
        INSERT INTO verification_queue (character_id, message_id, channel_id)
        VALUES (?, ?, ?)
        """,
        (char_id, message_id, channel_id),
    )
    await conn.commit()


async def resolve_verification(conn, char_id: int) -> None:
    """Marca una ficha de verificación como resuelta."""
    await conn.execute(
        "UPDATE verification_queue SET resuelto=1 WHERE character_id=?", (char_id,)
    )
    await conn.commit()


async def get_pending_verifications(conn) -> list:
    """Devuelve todas las fichas pendientes de verificación (para reconstruir Views)."""
    return await _all(
        conn,
        """
        SELECT vq.*, c.user_id, c.nombre_completo
        FROM verification_queue vq
        JOIN characters c ON c.id = vq.character_id
        WHERE vq.resuelto = 0
        """
    )


# ---------------------------------------------------------------------------
# 3. Inventario
# ---------------------------------------------------------------------------

async def get_loadout(conn, user_id: int) -> list:
    """Devuelve todas las filas del loadout de un personaje."""
    return await _all(
        conn,
        """
        SELECT l.slot, l.parche_url, l.estado,
               i.id AS item_id, i.nombre AS item_nombre,
               i.calibre, i.id_compatibilidad, i.peso_kg, i.volumen_u,
               i.slots_pouches
        FROM loadout l
        LEFT JOIN items i ON i.id = l.item_id
        WHERE l.user_id = ?
        """,
        (user_id,),
    )


async def upsert_loadout_slot(conn, user_id: int, slot: str,
                               item_id: int | None,
                               parche_url: str | None = None) -> None:
    """
    Equipa o desquipa un ítem en un slot del loadout.

    Args:
        conn      : Conexión aiosqlite.
        user_id   : discord user_id.
        slot      : Nombre del slot.
        item_id   : ID del ítem a equipar, o None para vaciar el slot.
        parche_url: URL del parche si el slot es 'parche'.
    """
    await conn.execute(
        """
        INSERT INTO loadout (user_id, slot, item_id, parche_url)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, slot) DO UPDATE SET
            item_id    = excluded.item_id,
            parche_url = excluded.parche_url
        """,
        (user_id, slot, item_id, parche_url),
    )
    await conn.commit()


async def get_inventory_general(conn, user_id: int) -> list:
    """Devuelve el inventario general del usuario con datos del ítem."""
    return await _all(
        conn,
        """
        SELECT ig.id, ig.cantidad, ig.estado, ig.notas,
               i.nombre, i.peso_kg, i.volumen_u, i.categoria
        FROM inventory_general ig
        JOIN items i ON i.id = ig.item_id
        WHERE ig.user_id = ?
        ORDER BY i.categoria, i.nombre
        """,
        (user_id,),
    )


async def add_to_general_inventory(conn, user_id: int, item_id: int, cantidad: int = 1) -> None:
    """
    Añade un ítem al inventario general. Si ya existe, incrementa cantidad.

    Args:
        conn    : Conexión aiosqlite.
        user_id : discord user_id.
        item_id : ID del ítem.
        cantidad: Cuántas unidades añadir.
    """
    existing = await _one(
        conn,
        "SELECT id, cantidad FROM inventory_general WHERE user_id=? AND item_id=?",
        (user_id, item_id),
    )
    if existing:
        await conn.execute(
            "UPDATE inventory_general SET cantidad=cantidad+? WHERE id=?",
            (cantidad, existing["id"]),
        )
    else:
        await conn.execute(
            "INSERT INTO inventory_general (user_id, item_id, cantidad) VALUES (?,?,?)",
            (user_id, item_id, cantidad),
        )
    await conn.commit()


async def remove_from_general_inventory(conn, user_id: int, item_id: int,
                                         cantidad: int = 1) -> bool:
    """
    Reduce la cantidad de un ítem en el inventario general.
    Si la cantidad llega a 0, elimina la fila.

    Args:
        conn    : Conexión aiosqlite.
        user_id : discord user_id.
        item_id : ID del ítem.
        cantidad: Cuántas unidades retirar.

    Returns:
        True si la operación fue posible, False si no había suficiente stock.
    """
    row = await _one(
        conn,
        "SELECT id, cantidad FROM inventory_general WHERE user_id=? AND item_id=?",
        (user_id, item_id),
    )
    if not row or row["cantidad"] < cantidad:
        return False

    if row["cantidad"] == cantidad:
        await conn.execute("DELETE FROM inventory_general WHERE id=?", (row["id"],))
    else:
        await conn.execute(
            "UPDATE inventory_general SET cantidad=cantidad-? WHERE id=?",
            (cantidad, row["id"]),
        )
    await conn.commit()
    return True


async def get_inventory_totals(conn, user_id: int) -> tuple[float, int]:
    """
    Calcula el peso total (kg) y volumen total (u) del inventario general.

    Returns:
        Tupla (peso_total_kg, volumen_total_u).
    """
    row = await _one(
        conn,
        """
        SELECT
            COALESCE(SUM(i.peso_kg   * ig.cantidad), 0.0) AS peso_total,
            COALESCE(SUM(i.volumen_u * ig.cantidad), 0)   AS vol_total
        FROM inventory_general ig
        JOIN items i ON i.id = ig.item_id
        WHERE ig.user_id = ?
        """,
        (user_id,),
    )
    return (float(row["peso_total"]), int(row["vol_total"])) if row else (0.0, 0)


async def get_pouches(conn, user_id: int) -> list:
    """Devuelve todos los pouches asignados a las protecciones del personaje."""
    return await _all(
        conn,
        """
        SELECT p.id, p.slot_proteccion, p.contenido_json,
               i.nombre AS pouch_nombre, i.tipo_pouch,
               i.slots_ocupa, i.capacidad_pouch
        FROM pouches p
        JOIN items i ON i.id = p.pouch_item_id
        WHERE p.user_id = ?
        """,
        (user_id,),
    )


async def add_pouch(conn, user_id: int, slot_proteccion: str, pouch_item_id: int) -> int:
    """Añade un pouch a una protección del loadout. Devuelve el ID del registro."""
    return await _run(
        conn,
        "INSERT INTO pouches (user_id, slot_proteccion, pouch_item_id) VALUES (?,?,?)",
        (user_id, slot_proteccion, pouch_item_id),
    )


async def remove_pouch(conn, pouch_id: int, user_id: int) -> None:
    """Elimina un pouch del loadout verificando que pertenezca al usuario."""
    await conn.execute(
        "DELETE FROM pouches WHERE id=? AND user_id=?", (pouch_id, user_id)
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# 4. Estado médico
# ---------------------------------------------------------------------------

async def get_medical_state(conn, user_id: int) -> aiosqlite.Row | None:
    """Devuelve el estado médico de un personaje."""
    return await _one(conn, "SELECT * FROM medical_state WHERE user_id=?", (user_id,))


async def upsert_medical_state(conn, user_id: int, campos: dict,
                                 modificado_por: int | None = None) -> None:
    """
    Crea o actualiza el estado médico de un personaje.

    Args:
        conn          : Conexión aiosqlite.
        user_id       : discord user_id.
        campos        : Dict con los campos a actualizar (heridas, fracturas,
                        consciencia, sangre). Solo los presentes se actualizan.
        modificado_por: discord user_id del Narrador que realiza el cambio.
    """
    existing = await get_medical_state(conn, user_id)

    # Serializar listas a JSON si hace falta
    for campo in ("heridas", "fracturas"):
        if campo in campos and isinstance(campos[campo], list):
            campos[campo] = json.dumps(campos[campo], ensure_ascii=False)

    if existing:
        set_parts = ", ".join(f"{k}=?" for k in campos)
        set_parts += ", ultima_mod_por=?, ultima_mod_en=strftime('%Y-%m-%dT%H:%M:%fZ','now')"
        values    = list(campos.values()) + [modificado_por, user_id]
        await conn.execute(
            f"UPDATE medical_state SET {set_parts} WHERE user_id=?", values
        )
    else:
        # Inserción inicial con defaults
        base = {
            "user_id":       user_id,
            "heridas":       "[]",
            "fracturas":     "[]",
            "consciencia":   "Consciente",
            "sangre":        100,
            "ultima_mod_por": modificado_por,
        }
        base.update(campos)
        cols = ", ".join(base.keys())
        ph   = ", ".join("?" for _ in base)
        await conn.execute(f"INSERT INTO medical_state ({cols}) VALUES ({ph})", tuple(base.values()))

    await conn.commit()


# ---------------------------------------------------------------------------
# 5. Radio
# ---------------------------------------------------------------------------

async def get_radio_state(conn, user_id: int) -> aiosqlite.Row | None:
    """Devuelve el estado de radio de un personaje."""
    return await _one(conn, "SELECT * FROM radio_state WHERE user_id=?", (user_id,))


async def upsert_radio_state(conn, user_id: int, campos: dict) -> None:
    """
    Crea o actualiza el estado de radio de un personaje.

    Args:
        conn    : Conexión aiosqlite.
        user_id : discord user_id.
        campos  : Dict con campos a actualizar (encendida, canal_activo,
                  tiene_radio, estatica_activa).
    """
    existing = await get_radio_state(conn, user_id)
    if existing:
        set_parts = ", ".join(f"{k}=?" for k in campos)
        values    = list(campos.values()) + [user_id]
        await conn.execute(f"UPDATE radio_state SET {set_parts} WHERE user_id=?", values)
    else:
        base = {"user_id": user_id}
        base.update(campos)
        cols = ", ".join(base.keys())
        ph   = ", ".join("?" for _ in base)
        await conn.execute(f"INSERT INTO radio_state ({cols}) VALUES ({ph})", tuple(base.values()))
    await conn.commit()


async def set_static(conn, channel_id: int, activa: bool) -> None:
    """
    Activa o desactiva la estática en todos los personajes conectados a un canal.
    La estática se guarda a nivel de personaje para que sea individual.
    En la implementación real, la estática puede ser por canal (simplificado aquí).

    Args:
        conn       : Conexión aiosqlite.
        channel_id : ID del canal donde activar la estática.
        activa     : True para activar, False para desactivar.
    """
    # Simplificado: actualizar todos los usuarios conectados a ese canal
    # En implementación completa, habría una tabla canal→estatica
    await conn.execute(
        "UPDATE radio_state SET estatica_activa=? WHERE canal_activo=?",
        (1 if activa else 0, str(channel_id)),
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# 6. Economía
# ---------------------------------------------------------------------------

async def get_balance(conn, user_id: int) -> float:
    """Devuelve el saldo del usuario, o 0.0 si no tiene cuenta."""
    row = await _one(conn, "SELECT saldo FROM economy WHERE user_id=?", (user_id,))
    return float(row["saldo"]) if row else 0.0


async def update_balance(conn, user_id: int, delta: float,
                          tipo: str, descripcion: str,
                          ejecutado_por: int | None = None,
                          item_id: int | None = None) -> float:
    """
    Modifica el saldo de un usuario y registra la transacción.

    Args:
        conn          : Conexión aiosqlite.
        user_id       : discord user_id.
        delta         : Cantidad a sumar (positivo) o restar (negativo).
        tipo          : Tipo de transacción (ver transactions).
        descripcion   : Descripción legible.
        ejecutado_por : discord user_id del actor (None = sistema).
        item_id       : ID del ítem relacionado si aplica.

    Returns:
        Nuevo saldo tras la operación.

    Raises:
        ValueError: Si el saldo resultante sería negativo.
    """
    actual = await get_balance(conn, user_id)
    nuevo  = actual + delta

    if nuevo < 0:
        raise ValueError(
            f"Saldo insuficiente. Saldo actual: {actual:.2f}, requerido: {abs(delta):.2f}"
        )

    await conn.execute(
        """
        INSERT INTO economy (user_id, saldo)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            saldo = excluded.saldo,
            actualizado_en = strftime('%Y-%m-%dT%H:%M:%fZ','now')
        """,
        (user_id, nuevo),
    )
    # Registrar transacción (inmutable)
    await conn.execute(
        """
        INSERT INTO transactions
            (user_id, tipo, item_id, cantidad, descripcion, ejecutado_por)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, tipo, item_id, delta, descripcion, ejecutado_por),
    )
    await conn.commit()
    return nuevo


async def get_transactions(conn, user_id: int, limit: int = 10,
                            offset: int = 0) -> list:
    """Devuelve el historial de transacciones de un usuario (paginado)."""
    return await _all(
        conn,
        """
        SELECT * FROM transactions
        WHERE user_id = ?
        ORDER BY creado_en DESC
        LIMIT ? OFFSET ?
        """,
        (user_id, limit, offset),
    )


# ---------------------------------------------------------------------------
# 7. Tienda
# ---------------------------------------------------------------------------

async def get_shop_listings(conn, pagina: int = 1,
                             por_pagina: int = 8) -> tuple[list, int]:
    """
    Devuelve los ítems activos de la tienda (paginados).

    Args:
        conn      : Conexión aiosqlite.
        pagina    : Página solicitada (1-indexed).
        por_pagina: Ítems por página.

    Returns:
        Tupla (lista_de_items, total_paginas).
    """
    offset = (pagina - 1) * por_pagina

    total_row = await _one(
        conn,
        "SELECT COUNT(*) AS n FROM shop_listings WHERE activo=1 AND (stock=-1 OR stock>0)"
    )
    total = total_row["n"] if total_row else 0
    total_paginas = max(1, (total + por_pagina - 1) // por_pagina)

    rows = await _all(
        conn,
        """
        SELECT sl.id, sl.precio, sl.stock, i.nombre, i.descripcion,
               i.peso_kg, i.volumen_u, i.id AS item_id
        FROM shop_listings sl
        JOIN items i ON i.id = sl.item_id
        WHERE sl.activo=1 AND (sl.stock=-1 OR sl.stock>0)
        ORDER BY i.categoria, i.nombre
        LIMIT ? OFFSET ?
        """,
        (por_pagina, offset),
    )
    return list(rows), total_paginas


async def get_shop_item_by_name(conn, nombre: str) -> aiosqlite.Row | None:
    """Busca un ítem activo en la tienda por nombre (case-insensitive)."""
    return await _one(
        conn,
        """
        SELECT sl.id AS listing_id, sl.precio, sl.stock,
               i.id AS item_id, i.nombre, i.descripcion,
               i.peso_kg, i.volumen_u, i.categoria
        FROM shop_listings sl
        JOIN items i ON i.id = sl.item_id
        WHERE sl.activo=1 AND LOWER(i.nombre) = LOWER(?)
          AND (sl.stock=-1 OR sl.stock>0)
        """,
        (nombre,),
    )


async def reduce_shop_stock(conn, listing_id: int, cantidad: int = 1) -> None:
    """Reduce el stock de un listado de tienda. Si stock=-1, no lo toca."""
    await conn.execute(
        """
        UPDATE shop_listings
        SET stock = CASE WHEN stock = -1 THEN -1 ELSE stock - ? END
        WHERE id = ?
        """,
        (cantidad, listing_id),
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# 8. Vehículos
# ---------------------------------------------------------------------------

async def get_vehicles(conn, tipo: str | None = None,
                        activo: bool = True) -> list:
    """Devuelve vehículos, opcionalmente filtrados por tipo."""
    sql    = "SELECT * FROM vehicles WHERE activo=?"
    params: list = [1 if activo else 0]
    if tipo:
        sql    += " AND tipo=?"
        params.append(tipo)
    sql += " ORDER BY tipo, nombre"
    return await _all(conn, sql, tuple(params))


async def get_vehicle(conn, vehicle_id: int) -> aiosqlite.Row | None:
    """Obtiene un vehículo por ID."""
    return await _one(conn, "SELECT * FROM vehicles WHERE id=?", (vehicle_id,))


async def update_vehicle(conn, vehicle_id: int, campos: dict) -> None:
    """
    Actualiza campos de un vehículo.

    Args:
        conn       : Conexión aiosqlite.
        vehicle_id : ID del vehículo.
        campos     : Dict con los campos a actualizar.
    """
    # Serializar JSON si es necesario
    for campo in ("componentes", "municion_json", "hardpoints_json", "tripulacion_json"):
        if campo in campos and not isinstance(campos[campo], str):
            campos[campo] = json.dumps(campos[campo], ensure_ascii=False)

    set_parts = ", ".join(f"{k}=?" for k in campos)
    values    = list(campos.values()) + [vehicle_id]
    await conn.execute(f"UPDATE vehicles SET {set_parts} WHERE id=?", values)
    await conn.commit()


async def add_to_vehicle_inventory(conn, vehicle_id: int, item_id: int,
                                    cantidad: int = 1) -> None:
    """Añade ítems al inventario de un vehículo."""
    existing = await _one(
        conn,
        "SELECT id, cantidad FROM inventory_vehicle WHERE vehicle_id=? AND item_id=?",
        (vehicle_id, item_id),
    )
    if existing:
        await conn.execute(
            "UPDATE inventory_vehicle SET cantidad=cantidad+? WHERE id=?",
            (cantidad, existing["id"]),
        )
    else:
        await conn.execute(
            "INSERT INTO inventory_vehicle (vehicle_id, item_id, cantidad) VALUES (?,?,?)",
            (vehicle_id, item_id, cantidad),
        )
    await conn.commit()


async def get_vehicle_inventory(conn, vehicle_id: int) -> list:
    """Devuelve el inventario de un vehículo."""
    return await _all(
        conn,
        """
        SELECT iv.cantidad, iv.estado,
               i.nombre, i.peso_kg, i.volumen_u, i.categoria
        FROM inventory_vehicle iv
        JOIN items i ON i.id = iv.item_id
        WHERE iv.vehicle_id = ?
        """,
        (vehicle_id,),
    )


# ---------------------------------------------------------------------------
# 9. Estado del evento
# ---------------------------------------------------------------------------

async def get_event_state(conn) -> aiosqlite.Row:
    """
    Devuelve el estado del evento (singleton id=1).
    Siempre devuelve una fila gracias al INSERT OR IGNORE en schema.sql.

    Returns:
        Fila de event_state.
    """
    return await _one(conn, "SELECT * FROM event_state WHERE id=1")


async def set_event_state(conn, activo: bool, user_id: int,
                           descripcion: str | None = None) -> None:
    """
    Cambia el estado del evento global.

    Args:
        conn        : Conexión aiosqlite.
        activo      : True = Evento-ON, False = Evento-OFF.
        user_id     : discord user_id del Narrador que ejecuta el cambio.
        descripcion : Nota narrativa opcional.
    """
    await conn.execute(
        """
        UPDATE event_state
        SET evento_activo=?,
            activado_por=?,
            activado_en=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
            descripcion=?
        WHERE id=1
        """,
        (1 if activo else 0, user_id, descripcion),
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# 10. Auditoría (escritura directa desde el repositorio si es necesario)
# ---------------------------------------------------------------------------

async def write_audit(conn, tipo: str, descripcion: str,
                       actor_id: int | None = None,
                       target_id: int | None = None,
                       detalles: dict | None = None) -> None:
    """
    Inserta directamente en audit_log.
    Usar utils.logger.audit() en lugar de llamar esto directamente
    (el logger llama a esta función internamente).

    Args:
        conn        : Conexión aiosqlite.
        tipo        : Tipo de acción.
        descripcion : Texto descriptivo.
        actor_id    : discord user_id del actor.
        target_id   : discord user_id del afectado.
        detalles    : Dict adicional serializado como JSON.
    """
    dj = json.dumps(detalles, ensure_ascii=False) if detalles else None
    await conn.execute(
        """
        INSERT INTO audit_log (tipo, actor_id, target_id, descripcion, detalles_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (tipo, actor_id, target_id, descripcion, dj),
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# 11. Webhooks
# ---------------------------------------------------------------------------

async def get_webhook_cache(conn, channel_id: int) -> aiosqlite.Row | None:
    """Busca un webhook cacheado para el canal indicado."""
    return await _one(
        conn, "SELECT * FROM webhook_cache WHERE channel_id=?", (channel_id,)
    )


async def upsert_webhook_cache(conn, channel_id: int,
                                webhook_id: int, webhook_url: str) -> None:
    """Guarda o actualiza el webhook de un canal en caché."""
    await conn.execute(
        """
        INSERT INTO webhook_cache (channel_id, webhook_id, webhook_url)
        VALUES (?, ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET
            webhook_id  = excluded.webhook_id,
            webhook_url = excluded.webhook_url,
            creado_en   = strftime('%Y-%m-%dT%H:%M:%fZ','now')
        """,
        (channel_id, webhook_id, webhook_url),
    )
    await conn.commit()


async def delete_webhook_cache(conn, channel_id: int) -> None:
    """Elimina el webhook cacheado de un canal (si fue borrado externamente)."""
    await conn.execute(
        "DELETE FROM webhook_cache WHERE channel_id=?", (channel_id,)
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# 12. Ítems (consultas de catálogo)
# ---------------------------------------------------------------------------

async def get_item_by_id(conn, item_id: int) -> aiosqlite.Row | None:
    """Obtiene un ítem del catálogo maestro por ID."""
    return await _one(conn, "SELECT * FROM items WHERE id=?", (item_id,))


async def get_item_by_name(conn, nombre: str) -> aiosqlite.Row | None:
    """Obtiene un ítem del catálogo maestro por nombre (case-insensitive)."""
    return await _one(
        conn, "SELECT * FROM items WHERE LOWER(nombre)=LOWER(?)", (nombre,)
    )