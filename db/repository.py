"""
=============================================================================
RAISA — Capa de Repositorio (db/repository.py)
=============================================================================
Responsabilidad : Único punto de acceso a la base de datos SQLite.
                  Ningún cog debe abrir conexiones directamente; todo pasa
                  por este módulo.
Dependencias    : sqlite3, asyncio, functools, pathlib
Autor           : RAISA Dev
=============================================================================
"""

import sqlite3
import asyncio
import json
import logging
from pathlib import Path
from functools import lru_cache
from collections import OrderedDict
from typing import Any, Optional
from datetime import datetime, timezone

log = logging.getLogger("raisa.db")

# ---------------------------------------------------------------------------
# Rutas absolutas
# ---------------------------------------------------------------------------
BASE_DIR  = Path(__file__).parent.parent
DB_PATH   = BASE_DIR / "data" / "raisa.db"
SCHEMA    = Path(__file__).parent / "schema.sql"


# ---------------------------------------------------------------------------
# LRU Cache manual (50 entradas máx.) para datos de usuario frecuentes
# ---------------------------------------------------------------------------
class LRUCache:
    """
    Caché LRU con tamaño máximo para evitar acumulación en RAM.
    Hilo-seguro para uso con asyncio (no multihilo).
    """
    def __init__(self, maxsize: int = 50):
        self._cache: OrderedDict = OrderedDict()
        self._maxsize = maxsize

    def get(self, key: str) -> Optional[Any]:
        """Retorna el valor o None si no existe / ha expirado."""
        if key not in self._cache:
            return None
        self._cache.move_to_end(key)
        return self._cache[key]

    def set(self, key: str, value: Any) -> None:
        """Inserta o actualiza una entrada. Expulsa la más antigua si está lleno."""
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = value
        if len(self._cache) > self._maxsize:
            evicted = self._cache.popitem(last=False)
            log.debug("LRU evict: %s", evicted[0])

    def invalidate(self, key: str) -> None:
        """Elimina una entrada del caché (llamar al modificar datos)."""
        self._cache.pop(key, None)

    def clear(self) -> None:
        """Limpia todo el caché."""
        self._cache.clear()


# Instancia global del caché
_cache = LRUCache(maxsize=50)


# ---------------------------------------------------------------------------
# Gestión de conexión (thread-local no necesario con asyncio single-thread)
# ---------------------------------------------------------------------------
_conn: Optional[sqlite3.Connection] = None


def get_connection() -> sqlite3.Connection:
    """
    Retorna la conexión SQLite activa, creándola si no existe.
    WAL mode activado para lecturas concurrentes sin bloqueo de escritura.

    Returns:
        sqlite3.Connection: Conexión activa a la BBDD.
    """
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row           # Acceso por nombre de columna
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.execute("PRAGMA synchronous=NORMAL")  # Equilibrio seguridad/velocidad
        _init_schema(_conn)
        log.info("Conexión SQLite establecida: %s (WAL)", DB_PATH)
    return _conn


def _init_schema(conn: sqlite3.Connection) -> None:
    """
    Inicializa el esquema de BBDD si las tablas no existen.

    Args:
        conn: Conexión SQLite activa.
    """
    if SCHEMA.exists():
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        conn.commit()
        log.info("Esquema aplicado desde schema.sql")
    else:
        log.error("schema.sql no encontrado en %s", SCHEMA)


def close_connection() -> None:
    """Cierra la conexión limpiamente al apagar el bot."""
    global _conn
    if _conn:
        _conn.close()
        _conn = None
        log.info("Conexión SQLite cerrada.")


# ---------------------------------------------------------------------------
# Helpers de ejecución asíncrona (evitar bloquear el event loop)
# ---------------------------------------------------------------------------
async def _run_in_executor(func, *args):
    """
    Ejecuta una función SQLite síncrona en un executor para no bloquear asyncio.
    Todas las operaciones de BBDD pasan por aquí.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, func, *args)


def _fetchone(query: str, params: tuple = ()) -> Optional[sqlite3.Row]:
    """Ejecuta una consulta y retorna una fila."""
    conn = get_connection()
    cur = conn.execute(query, params)
    return cur.fetchone()


def _fetchall(query: str, params: tuple = ()) -> list:
    """Ejecuta una consulta y retorna todas las filas."""
    conn = get_connection()
    cur = conn.execute(query, params)
    return cur.fetchall()


def _execute(query: str, params: tuple = ()) -> sqlite3.Cursor:
    """Ejecuta una escritura y hace commit."""
    conn = get_connection()
    cur = conn.execute(query, params)
    conn.commit()
    return cur


async def _execute_async(query: str, params: tuple = ()) -> sqlite3.Cursor:
    """Versión asíncrona de _execute."""
    return await _run_in_executor(_execute, query, params)


async def _fetchone_async(query: str, params: tuple = ()) -> Optional[sqlite3.Row]:
    """Versión asíncrona de _fetchone."""
    return await _run_in_executor(_fetchone, query, params)


async def _fetchall_async(query: str, params: tuple = ()) -> list:
    """Versión asíncrona de _fetchall."""
    return await _run_in_executor(_fetchall, query, params)


async def get_statica_canal(canal_key: str) -> bool:
    """Retorna si la estática está activa en un canal."""
    row = await _run_in_executor(
        _fetchone,
        "SELECT activa FROM radio_statica WHERE canal_key = ?",
        (canal_key,)
    )
    return bool(row["activa"]) if row else False


async def set_statica_canal(canal_key: str, activa: bool) -> None:
    """Activa o desactiva la estática en un canal."""
    await _run_in_executor(
        _execute,
        "INSERT INTO radio_statica (canal_key, activa) VALUES (?, ?) ON CONFLICT(canal_key) DO UPDATE SET activa = excluded.activa",
        (canal_key, 1 if activa else 0)
    )


def invalidar_cache(key: str) -> None:
    """Invalida una entrada específica de la caché."""
    _cache.invalidate(key)


# ---------------------------------------------------------------------------
# REPOSITORIO — USUARIOS
# ---------------------------------------------------------------------------
async def get_usuario(user_id: int) -> Optional[sqlite3.Row]:
    """
    Retorna los datos del usuario desde caché o BBDD.

    Args:
        user_id: Discord user ID.
    Returns:
        Row con datos del usuario o None si no existe.
    """
    cache_key = f"usuario:{user_id}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    row = await _run_in_executor(
        _fetchone,
        "SELECT * FROM usuarios WHERE id = ?",
        (user_id,)
    )
    if row:
        _cache.set(cache_key, row)
    return row


async def upsert_usuario(user_id: int, guild_id: int, **kwargs) -> None:
    """
    Inserta o actualiza datos del usuario. Invalida caché al escribir.

    Args:
        user_id : Discord user ID.
        guild_id: Discord guild ID.
        **kwargs: Campos a actualizar (apodo, rol_nivel, dinero, etc.)
    """
    existing = await _run_in_executor(
        _fetchone,
        "SELECT id FROM usuarios WHERE id = ?",
        (user_id,)
    )
    if existing:
        if kwargs:
            sets = ", ".join(f"{k} = ?" for k in kwargs)
            vals = list(kwargs.values()) + [user_id]
            await _run_in_executor(
                _execute,
                f"UPDATE usuarios SET {sets} WHERE id = ?",
                tuple(vals)
            )
    else:
        campos = ["id", "guild_id"] + list(kwargs.keys())
        valores = [user_id, guild_id] + list(kwargs.values())
        placeholders = ", ".join("?" * len(campos))
        await _run_in_executor(
            _execute,
            f"INSERT INTO usuarios ({', '.join(campos)}) VALUES ({placeholders})",
            tuple(valores)
        )
    _cache.invalidate(f"usuario:{user_id}")


async def get_dinero(user_id: int) -> float:
    """Retorna el saldo del usuario."""
    row = await get_usuario(user_id)
    return float(row["dinero"]) if row else 0.0


async def modificar_dinero(user_id: int, cantidad: float) -> float:
    """
    Suma (o resta si negativo) dinero al usuario.

    Args:
        user_id : Discord user ID.
        cantidad: Cantidad a sumar/restar.
    Returns:
        Nuevo saldo.
    Raises:
        ValueError: Si el saldo resultante sería negativo.
    """
    saldo_actual = await get_dinero(user_id)
    nuevo_saldo = saldo_actual + cantidad
    if nuevo_saldo < 0:
        raise ValueError(f"Saldo insuficiente. Actual: {saldo_actual:.2f}")
    await _run_in_executor(
        _execute,
        "UPDATE usuarios SET dinero = ? WHERE id = ?",
        (nuevo_saldo, user_id)
    )
    _cache.invalidate(f"usuario:{user_id}")
    return nuevo_saldo


# ---------------------------------------------------------------------------
# REPOSITORIO — FICHAS DE REGISTRO
# ---------------------------------------------------------------------------
async def get_ficha(user_id: int) -> Optional[sqlite3.Row]:
    """Retorna la ficha de registro en progreso."""
    return await _run_in_executor(
        _fetchone,
        "SELECT * FROM fichas_registro WHERE user_id = ?",
        (user_id,)
    )


async def save_ficha(user_id: int, guild_id: int, **kwargs) -> None:
    """
    Guarda progreso del formulario de registro.
    Actualiza ultima_actividad automáticamente.

    Args:
        user_id : Discord user ID.
        guild_id: Discord guild ID.
        **kwargs: Campos del formulario a actualizar.
    """
    kwargs["ultima_actividad"] = datetime.now(timezone.utc).isoformat()

    existing = await _run_in_executor(
        _fetchone,
        "SELECT user_id FROM fichas_registro WHERE user_id = ?",
        (user_id,)
    )
    if existing:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [user_id]
        await _run_in_executor(
            _execute,
            f"UPDATE fichas_registro SET {sets} WHERE user_id = ?",
            tuple(vals)
        )
    else:
        campos = ["user_id", "guild_id"] + list(kwargs.keys())
        valores = [user_id, guild_id] + list(kwargs.values())
        placeholders = ", ".join("?" * len(campos))
        await _run_in_executor(
            _execute,
            f"INSERT INTO fichas_registro ({', '.join(campos)}) VALUES ({placeholders})",
            tuple(valores)
        )


async def delete_ficha(user_id: int) -> None:
    """Elimina la ficha de registro (tras aceptar o rechazar definitivamente)."""
    await _run_in_executor(
        _execute,
        "DELETE FROM fichas_registro WHERE user_id = ?",
        (user_id,)
    )



# ---------------------------------------------------------------------------
# REPOSITORIO — PERSONAJES
# ---------------------------------------------------------------------------
async def get_personaje(user_id: int) -> Optional[sqlite3.Row]:
    """Retorna el personaje activo del usuario."""
    cache_key = f"personaje:{user_id}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    row = await _run_in_executor(
        _fetchone,
        "SELECT * FROM personajes WHERE user_id = ?",
        (user_id,)
    )
    if row:
        _cache.set(cache_key, row)
    return row


async def crear_personaje(user_id: int, datos: dict) -> None:
    """
    Crea el registro del personaje a partir de la ficha aceptada.

    Args:
        user_id: Discord user ID.
        datos  : Diccionario con todos los campos del personaje.
    """
    campos = ["user_id"] + list(datos.keys())
    valores = [user_id] + list(datos.values())
    placeholders = ", ".join("?" * len(campos))
    await _run_in_executor(
        _execute,
        f"INSERT OR REPLACE INTO personajes ({', '.join(campos)}) VALUES ({placeholders})",
        tuple(valores)
    )
    _cache.invalidate(f"personaje:{user_id}")


async def update_personaje(user_id: int, **kwargs) -> None:
    """Actualiza campos del personaje. Invalida caché."""
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [user_id]
    await _run_in_executor(
        _execute,
        f"UPDATE personajes SET {sets} WHERE user_id = ?",
        tuple(vals)
    )
    _cache.invalidate(f"personaje:{user_id}")


# ---------------------------------------------------------------------------
# REPOSITORIO — INVENTARIO
# ---------------------------------------------------------------------------
async def get_inventario_personal(user_id: int) -> list:
    """Retorna todos los ítems del loadout personal del usuario."""
    return await _run_in_executor(
        _fetchall,
        """SELECT ip.*, i.nombre, i.categoria, i.subcategoria, i.peso,
                  i.volumen, i.calibre, i.slots_pouches, i.imagen_url
           FROM inventario_personal ip
           JOIN items i ON ip.item_id = i.id
           WHERE ip.user_id = ?""",
        (user_id,)
    )


async def get_inventario_general(user_id: int) -> list:
    """Retorna todos los ítems del inventario general del usuario."""
    return await _run_in_executor(
        _fetchall,
        """SELECT ig.*, i.nombre, i.categoria, i.peso_kg,
                  i.volumen, i.precio_base
           FROM inventario_general ig
           JOIN items i ON ig.item_uuid = i.item_uuid
           WHERE ig.user_id = ?""",
        (user_id,)
    )


async def get_peso_total_personal(user_id: int) -> float:
    """Calcula el peso total del loadout personal."""
    row = await _run_in_executor(
        _fetchone,
        """SELECT COALESCE(SUM(i.peso * ip.cantidad), 0) as total
           FROM inventario_personal ip
           JOIN items i ON ip.item_id = i.id
           WHERE ip.user_id = ?""",
        (user_id,)
    )
    return float(row["total"]) if row else 0.0


async def add_item_personal(user_id: int, item_id: int, slot: str,
                             cantidad: int = 1, datos_extra: dict = None) -> None:
    """
    Añade o actualiza un ítem en el loadout personal.

    Args:
        user_id    : Discord user ID.
        item_id    : ID del ítem.
        slot       : Slot de equipamiento (primaria, chaleco, etc.)
        cantidad   : Cantidad a añadir.
        datos_extra: Datos adicionales (munición actual, accesorios, etc.)
    """
    extra_json = json.dumps(datos_extra or {})
    await _run_in_executor(
        _execute,
        """INSERT INTO inventario_personal (user_id, item_id, slot, cantidad, datos_extra)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, item_id, slot, cantidad, extra_json)
    )


async def remove_item_personal(user_id: int, inv_id: int) -> None:
    """Elimina un ítem del loadout personal por su ID de fila."""
    await _run_in_executor(
        _execute,
        "DELETE FROM inventario_personal WHERE id = ? AND user_id = ?",
        (inv_id, user_id)
    )


async def add_item_general(user_id: int, item_uuid: int, cantidad: int = 1) -> None:
    """Añade un ítem al inventario general. Agrupa si ya existe."""
    existing = await _run_in_executor(
        _fetchone,
        "SELECT id, cantidad FROM inventario_general WHERE user_id = ? AND item_uuid = ?",
        (user_id, item_uuid)
    )
    if existing:
        await _run_in_executor(
            _execute,
            "UPDATE inventario_general SET cantidad = cantidad + ? WHERE id = ?",
            (cantidad, existing["id"])
        )
    else:
        await _run_in_executor(
            _execute,
            "INSERT INTO inventario_general (user_id, item_uuid, cantidad) VALUES (?, ?, ?)",
            (user_id, item_uuid, cantidad)
        )


async def remove_item_general(user_id: int, item_uuid: int, cantidad: int = 1) -> bool:
    """
    Retira ítems del inventario general.

    Returns:
        True si se realizó correctamente, False si no había suficiente stock.
    """
    existing = await _run_in_executor(
        _fetchone,
        "SELECT id, cantidad FROM inventario_general WHERE user_id = ? AND item_uuid = ?",
        (user_id, item_uuid)
    )
    if not existing or existing["cantidad"] < cantidad:
        return False
    if existing["cantidad"] == cantidad:
        await _run_in_executor(
            _execute,
            "DELETE FROM inventario_general WHERE id = ?",
            (existing["id"],)
        )
    else:
        await _run_in_executor(
            _execute,
            "UPDATE inventario_general SET cantidad = cantidad - ? WHERE id = ?",
            (cantidad, existing["id"])
        )
    return True


# ---------------------------------------------------------------------------
# REPOSITORIO — ÍTEMS Y TIENDA
# ---------------------------------------------------------------------------
async def get_item(item_uuid: int) -> Optional[sqlite3.Row]:
    """Retorna datos de un ítem por su UUID."""
    cache_key = f"item:{item_uuid}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached
    row = await _run_in_executor(
        _fetchone,
        "SELECT * FROM items WHERE item_uuid = ?",
        (item_uuid,)
    )
    if row:
        _cache.set(cache_key, row)
    return row


async def get_tienda(solo_disponibles: bool = True) -> list:
    """Retorna los ítems de la tienda con sus precios."""
    query = """SELECT t.id, t.precio_actual, t.stock, i.*
               FROM tienda t JOIN items i ON t.item_uuid = i.item_uuid"""
    if solo_disponibles:
        query += " WHERE t.activo = 1"
    return await _run_in_executor(_fetchall, query)


# ---------------------------------------------------------------------------
# REPOSITORIO — ESTADO DE EVENTO
# ---------------------------------------------------------------------------
async def get_estado_evento() -> str:
    """Retorna el modo del evento global ('on' o 'off')."""
    cache_key = "estado_evento"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    row = await _run_in_executor(
        _fetchone,
        "SELECT modo FROM estado_evento WHERE id = 1"
    )
    modo = row["modo"].upper() if row else "OFF"
    _cache.set(cache_key, modo)
    return modo


async def set_estado_evento(modo: str, activado_por: int) -> None:
    """
    Cambia el estado del evento global.

    Args:
        modo        : 'ON' o 'OFF'.
        activado_por: user_id del narrador que lo activó.
    """
    ts = datetime.now(timezone.utc).isoformat()
    await _run_in_executor(
        _execute,
        """UPDATE estado_evento
           SET modo = ?, activado_por = ?, activado_en = ?
           WHERE id = 1""",
        (modo.upper(), activado_por, ts)
    )
    _cache.invalidate("estado_evento")


# ---------------------------------------------------------------------------
# REPOSITORIO — ESTADO MÉDICO
# ---------------------------------------------------------------------------
async def get_estado_medico(user_id: int) -> Optional[dict]:
    """Retorna el estado médico del personaje."""
    res = await _run_in_executor(
        _fetchone,
        "SELECT * FROM estado_medico WHERE user_id = ?",
        (user_id,)
    )
    if res:
        # Convertir JSON a listas
        res_dict = dict(res)
        res_dict["heridas"] = json.loads(res_dict["heridas"])
        res_dict["fracturas"] = json.loads(res_dict["fracturas"])
        return res_dict
    return None


async def actualizar_estado_medico(user_id: int, datos: dict) -> None:
    """Actualiza campos del estado médico (heridas, sangre, etc.)."""
    # Asegurar que existe el registro
    existing = await _run_in_executor(
        _fetchone,
        "SELECT user_id FROM estado_medico WHERE user_id = ?",
        (user_id,)
    )
    if not existing:
        await _run_in_executor(
            _execute,
            "INSERT INTO estado_medico (user_id) VALUES (?)",
            (user_id,)
        )

    # Preparar campos serializando los que sean listas/dicts
    campos = []
    valores = []
    for k, v in datos.items():
        campos.append(f"{k} = ?")
        if isinstance(v, (list, dict)):
            valores.append(json.dumps(v, ensure_ascii=False))
        else:
            valores.append(v)
    
    valores.append(user_id)
    query = f"UPDATE estado_medico SET {', '.join(campos)}, ultima_actualizacion = datetime('now') WHERE user_id = ?"
    await _run_in_executor(_execute, query, tuple(valores))


# ---------------------------------------------------------------------------
# REPOSITORIO — VEHÍCULOS
# ---------------------------------------------------------------------------
async def get_vehiculo(vehiculo_id: int) -> Optional[sqlite3.Row]:
    """Retorna datos de un vehículo."""
    return await _run_in_executor(
        _fetchone,
        "SELECT * FROM vehiculos WHERE vehiculo_id = ?",
        (vehiculo_id,)
    )


async def update_vehiculo(vehiculo_id: int, **kwargs) -> None:
    """Actualiza campos de un vehículo."""
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [vehiculo_id]
    await _run_in_executor(
        _execute,
        f"UPDATE vehiculos SET {sets} WHERE vehiculo_id = ?",
        (tuple(vals))
    )


async def get_ocupantes(vehiculo_id: int) -> list:
    """Retorna la lista de ocupantes de un vehículo."""
    return await _run_in_executor(
        _fetchall,
        "SELECT * FROM vehiculo_ocupantes WHERE vehiculo_id = ?",
        (vehiculo_id,)
    )


async def subir_vehiculo(vehiculo_id: int, user_id: int, asiento: int) -> None:
    """Registra a un usuario en un asiento de un vehículo."""
    await _run_in_executor(
        _execute,
        "INSERT OR REPLACE INTO vehiculo_ocupantes (vehiculo_id, user_id, asiento) VALUES (?, ?, ?)",
        (vehiculo_id, user_id, asiento)
    )


async def bajar_vehiculo(user_id: int) -> None:
    """Retira a un usuario de cualquier vehículo en que esté."""
    await _run_in_executor(
        _execute,
        "DELETE FROM vehiculo_ocupantes WHERE user_id = ?",
        (user_id,)
    )


async def crear_vehiculo(datos: dict) -> int:
    """Crea un nuevo vehículo en la BBDD."""
    campos = list(datos.keys())
    valores = list(datos.values())
    placeholders = ", ".join("?" * len(campos))
    cur = await _run_in_executor(
        _execute,
        f"INSERT INTO vehiculos ({', '.join(campos)}) VALUES ({placeholders})",
        tuple(valores)
    )
    return cur.lastrowid


async def get_vehiculos_activos() -> list:
    """Retorna todos los vehículos activos."""
    return await _run_in_executor(
        _fetchall,
        "SELECT * FROM vehiculos WHERE destruido = 0"
    )


# ---------------------------------------------------------------------------
# REPOSITORIO — LOG DE ACCIONES CRÍTICAS
# ---------------------------------------------------------------------------
async def registrar_log(accion: str, ejecutor_id: Optional[int],
                         objetivo_id: Optional[int] = None,
                         detalle: dict = None) -> None:
    """
    Registra una acción crítica en el log permanente (log_critico).

    Args:
        accion     : Categoría (sudo|muerte|medico|evento|config|ban).
        ejecutor_id: User ID del ejecutor.
        objetivo_id: User ID del afectado (opcional).
        detalle    : Datos adicionales en formato dict.
    """
    ts = datetime.now(timezone.utc).isoformat()
    det_json = json.dumps(detalle or {})
    await _run_in_executor(
        _execute,
        """INSERT INTO log_critico
           (accion, ejecutor_id, objetivo_id, detalle, timestamp)
           VALUES (?, ?, ?, ?, ?)""",
        (accion, ejecutor_id, objetivo_id, det_json, ts)
    )


# ---------------------------------------------------------------------------
# REPOSITORIO — RADIO
# ---------------------------------------------------------------------------
async def get_radio_canal(canal_discord_id: int) -> Optional[sqlite3.Row]:
    """Retorna configuración de un canal de radio."""
    return await _run_in_executor(
        _fetchone,
        "SELECT * FROM radio_estado WHERE canal_discord_id = ?",
        (canal_discord_id,)
    )


async def set_estatica(canal_discord_id: int, activa: bool) -> None:
    """Activa o desactiva la estática de un canal de radio."""
    await _run_in_executor(
        _execute,
        "UPDATE radio_estado SET estatica_activa = ? WHERE canal_discord_id = ?",
        (1 if activa else 0, canal_discord_id)
    )


# ---------------------------------------------------------------------------
# REPOSITORIO — WEBHOOKS CACHÉ
# ---------------------------------------------------------------------------
async def get_webhook_cache(canal_id: int) -> Optional[sqlite3.Row]:
    """Retorna el webhook cacheado de un canal (si existe)."""
    return await _run_in_executor(
        _fetchone,
        "SELECT * FROM webhooks_cache WHERE canal_id = ?",
        (canal_id,)
    )


async def save_webhook_cache(canal_id: int, webhook_id: int, webhook_url: str) -> None:
    """Guarda o actualiza el webhook de un canal en caché."""
    ts = datetime.now(timezone.utc).isoformat()
    await _run_in_executor(
        _execute,
        """INSERT OR REPLACE INTO webhooks_cache
           (canal_id, webhook_id, webhook_url, creado_en)
           VALUES (?, ?, ?, ?)""",
        (canal_id, webhook_id, webhook_url, ts)
    )


# ---------------------------------------------------------------------------
# REPOSITORIO — SESIONES SUDO
# ---------------------------------------------------------------------------
async def get_sesion_sudo(user_id: int) -> Optional[sqlite3.Row]:
    """Retorna la sesión SUDO si existe y no ha expirado."""
    return await _run_in_executor(
        _fetchone,
        "SELECT * FROM sesiones_sudo WHERE user_id = ? AND expira > ?",
        (user_id, datetime.now(timezone.utc).isoformat())
    )


async def crear_sesion_sudo(user_id: int, duracion_min: int = 30) -> None:
    """Crea una sesión SUDO con duración especificada."""
    from datetime import timedelta
    ahora = datetime.now(timezone.utc)
    expira = ahora + timedelta(minutes=duracion_min)
    await _run_in_executor(
        _execute,
        "INSERT OR REPLACE INTO sesiones_sudo (user_id, inicio, expira) VALUES (?, ?, ?)",
        (user_id, ahora.isoformat(), expira.isoformat())
    )


async def revocar_sesion_sudo(user_id: int) -> None:
    """Revoca manualmente la sesión SUDO de un usuario."""
    await _run_in_executor(
        _execute,
        "DELETE FROM sesiones_sudo WHERE user_id = ?",
        (user_id,)
    )


async def limpiar_sesiones_sudo_expiradas() -> int:
    """
    Elimina sesiones SUDO expiradas. Llamada por la task periódica.

    Returns:
        Número de sesiones eliminadas.
    """
    cur = await _run_in_executor(
        _execute,
        "DELETE FROM sesiones_sudo WHERE expira <= ?",
        (datetime.now(timezone.utc).isoformat(),)
    )
    return cur.rowcount


# ---------------------------------------------------------------------------
# REPOSITORIO — FORMULARIOS DE REGISTRO
# ---------------------------------------------------------------------------
async def get_formulario(user_id: int) -> Optional[sqlite3.Row]:
    """Retorna el formulario de registro en progreso del usuario."""
    return await _run_in_executor(
        _fetchone,
        "SELECT * FROM formularios_registro WHERE user_id = ?",
        (user_id,)
    )


async def upsert_formulario(user_id: int, paso: int, datos: dict,
                             suspendido: bool = False) -> None:
    """
    Inserta o actualiza el formulario de registro.
    Serializa datos a JSON. Actualiza ultima_actividad automáticamente.
    """
    datos_json = json.dumps(datos, ensure_ascii=False)
    ts = datetime.now(timezone.utc).isoformat()
    await _run_in_executor(
        _execute,
        """INSERT INTO formularios_registro (user_id, paso_actual, datos_json, ultima_actividad, suspendido)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
               paso_actual       = excluded.paso_actual,
               datos_json        = excluded.datos_json,
               ultima_actividad  = excluded.ultima_actividad,
               suspendido        = excluded.suspendido""",
        (user_id, paso, datos_json, ts, 1 if suspendido else 0)
    )


async def borrar_formulario(user_id: int) -> None:
    """Elimina el formulario de registro (tras aceptar, denegar o reiniciar)."""
    await _run_in_executor(
        _execute,
        "DELETE FROM formularios_registro WHERE user_id = ?",
        (user_id,)
    )


async def suspender_formularios_inactivos(minutos: int = 20) -> int:
    """
    Marca como suspendidos todos los formularios sin actividad
    en los últimos `minutos` minutos.

    Returns:
        Número de formularios marcados.
    """
    from datetime import timedelta
    corte = (datetime.now(timezone.utc) - timedelta(minutes=minutos)).isoformat()
    cur = await _run_in_executor(
        _execute,
        "UPDATE formularios_registro SET suspendido = 1 WHERE ultima_actividad < ? AND suspendido = 0",
        (corte,)
    )
    return cur.rowcount


async def actualizar_verificacion(user_id: int, estado: int) -> None:
    """
    Actualiza el estado de verificación del personaje.

    Args:
        user_id: Discord user ID.
        estado : 0=pendiente, 1=aceptado, 2=denegado.
    """
    await _run_in_executor(
        _execute,
        "UPDATE personajes SET verificado = ? WHERE user_id = ?",
        (estado, user_id)
    )


# ---------------------------------------------------------------------------
# REPOSITORIO — LOADOUT
# ---------------------------------------------------------------------------
async def get_loadout(user_id: int) -> Optional[sqlite3.Row]:
    """Retorna el loadout del usuario (fila de la tabla loadout)."""
    return await _run_in_executor(
        _fetchone,
        "SELECT * FROM loadout WHERE user_id = ?",
        (user_id,)
    )


async def upsert_loadout(user_id: int, **kwargs) -> None:
    """Inserta o actualiza el loadout del usuario."""
    existing = await _run_in_executor(
        _fetchone,
        "SELECT user_id FROM loadout WHERE user_id = ?",
        (user_id,)
    )
    if existing:
        if kwargs:
            sets = ", ".join(f"{k} = ?" for k in kwargs)
            vals = list(kwargs.values()) + [user_id]
            await _run_in_executor(
                _execute,
                f"UPDATE loadout SET {sets} WHERE user_id = ?",
                tuple(vals)
            )
    else:
        campos = ["user_id"] + list(kwargs.keys())
        valores = [user_id] + list(kwargs.values())
        placeholders = ", ".join("?" * len(campos))
        await _run_in_executor(
            _execute,
            f"INSERT INTO loadout ({', '.join(campos)}) VALUES ({placeholders})",
            tuple(valores)
        )


async def set_slot_loadout(user_id: int, slot: str, item_id: Optional[int]) -> None:
    """Establece un ítem en un slot específico del loadout."""
    # Aseguramos que el registro de loadout existe para el usuario
    await upsert_loadout(user_id)
    
    await _run_in_executor(
        _execute,
        f"UPDATE loadout SET {slot} = ? WHERE user_id = ?",
        (item_id, user_id)
    )


async def crear_item(datos: dict) -> int:
    """
    Inserta un nuevo ítem en el catálogo.
    Retorna el item_uuid generado.
    """
    campos = list(datos.keys())
    valores = list(datos.values())
    placeholders = ", ".join("?" * len(campos))
    cur = await _run_in_executor(
        _execute,
        f"INSERT INTO items ({', '.join(campos)}) VALUES ({placeholders})",
        tuple(valores)
    )
    return cur.lastrowid


# ---------------------------------------------------------------------------
# REPOSITORIO — POUCHES EQUIPADOS
# ---------------------------------------------------------------------------
async def get_pouches(user_id: int, contenedor: str) -> list:
    """Retorna los pouches equipados de un usuario en un contenedor dado."""
    return await _run_in_executor(
        _fetchall,
        """SELECT pe.*, i.nombre, i.peso_kg, i.volumen, i.slots_pouches, i.tipo_pouch
           FROM pouches_equipados pe
           JOIN items i ON pe.pouch_item_id = i.item_uuid
           WHERE pe.user_id = ? AND pe.contenedor = ?
           ORDER BY pe.slot_numero""",
        (user_id, contenedor)
    )


async def add_pouch(user_id: int, contenedor: str, slot_numero: int,
                    pouch_item_id: int) -> None:
    """Equipa un pouch en el slot indicado."""
    await _run_in_executor(
        _execute,
        """INSERT OR REPLACE INTO pouches_equipados
           (user_id, contenedor, slot_numero, pouch_item_id)
           VALUES (?, ?, ?, ?)""",
        (user_id, contenedor, slot_numero, pouch_item_id)
    )


async def remove_pouch(user_id: int, contenedor: str, slot_numero: int) -> None:
    """Desequipa un pouch del slot indicado."""
    await _run_in_executor(
        _execute,
        "DELETE FROM pouches_equipados WHERE user_id = ? AND contenedor = ? AND slot_numero = ?",
        (user_id, contenedor, slot_numero)
    )


async def truncate_all_tables() -> None:
    """
    Borra todos los datos de todas las tablas de la BBDD (Dev only).
    Útil para resetear el entorno de desarrollo rápidamente.
    """
    tablas = [
        "personajes", "formularios_registro", "fichas_registro", "loadout",
        "pouches_equipados", "inventario_general", "items", "tienda",
        "estado_medico", "vehiculos", "vehiculo_ocupantes", "inventario_vehiculo",
        "log_critico", "radio_statica", "radio_estado", 
        "sesiones_sudo", "webhooks_cache"
    ]
    for tabla in tablas:
        try:
            await _run_in_executor(_execute, f"DELETE FROM {tabla}")
        except Exception:
            pass
    
    # Resetear estado de evento a OFF por defecto
    try:
        await _run_in_executor(_execute, "UPDATE estado_evento SET modo = 'OFF', activado_por = NULL WHERE id = 1")
    except Exception:
        pass

    _cache.clear()
    log.warning("¡ATENCIÓN! La base de datos ha sido truncada completamente.")


# ---------------------------------------------------------------------------
# ALIAS PARA COMPATIBILIDAD
# ---------------------------------------------------------------------------
log_accion = registrar_log
get_modo_evento = get_estado_evento
set_modo_evento = set_estado_evento
añadir_item_inventario = add_item_general
retirar_item_inventario = remove_item_general
_fetch_one = _fetchone_async
_fetch_all = _fetchall_async
_execute_sql = _execute_async

# ---------------------------------------------------------------------------
# CLASE REPOSITORIO — Wrapper orientado a objetos (compatibilidad con cogs)
# ---------------------------------------------------------------------------
class Repository:
    """
    Wrapper de clase sobre las funciones de módulo del repositorio.
    Permite a los cogs usar self.bot.repo.get_item(...) en lugar del módulo directo.
    Todas las llamadas delegan en las funciones async del módulo.
    """

    # -- Usuarios --
    get_usuario                  = staticmethod(get_usuario)
    upsert_usuario               = staticmethod(upsert_usuario)
    get_dinero                   = staticmethod(get_dinero)
    modificar_dinero             = staticmethod(modificar_dinero)

    # -- Fichas de registro --
    get_ficha                    = staticmethod(get_ficha)
    save_ficha                   = staticmethod(save_ficha)
    delete_ficha                 = staticmethod(delete_ficha)

    # -- Personajes --
    get_personaje                = staticmethod(get_personaje)
    crear_personaje              = staticmethod(crear_personaje)
    update_personaje             = staticmethod(update_personaje)

    # -- Inventario personal --
    get_inventario_personal      = staticmethod(get_inventario_personal)
    get_inventario_general       = staticmethod(get_inventario_general)
    get_peso_total_personal      = staticmethod(get_peso_total_personal)
    add_item_personal            = staticmethod(add_item_personal)
    remove_item_personal         = staticmethod(remove_item_personal)
    add_item_general             = staticmethod(add_item_general)
    remove_item_general          = staticmethod(remove_item_general)

    # -- Loadout --
    get_loadout                  = staticmethod(get_loadout)
    upsert_loadout               = staticmethod(upsert_loadout)
    set_slot_loadout             = staticmethod(set_slot_loadout)

    # -- Pouches --
    get_pouches                  = staticmethod(get_pouches)
    add_pouch                    = staticmethod(add_pouch)
    remove_pouch                 = staticmethod(remove_pouch)

    # -- Ítems y tienda --
    get_item                     = staticmethod(get_item)
    get_tienda                   = staticmethod(get_tienda)
    crear_item                   = staticmethod(crear_item)

    # -- Radio & Estática --
    get_statica_canal            = staticmethod(get_statica_canal)
    set_statica_canal            = staticmethod(set_statica_canal)
    get_radio_canal              = staticmethod(get_radio_canal)

    # -- Médico --
    get_estado_medico            = staticmethod(get_estado_medico)
    actualizar_estado_medico     = staticmethod(actualizar_estado_medico)

    # -- Vehículos --
    get_vehiculo                 = staticmethod(get_vehiculo)
    update_vehiculo              = staticmethod(update_vehiculo)
    get_vehiculos_activos        = staticmethod(get_vehiculos_activos)
    get_ocupantes                = staticmethod(get_ocupantes)
    subir_vehiculo               = staticmethod(subir_vehiculo)
    bajar_vehiculo               = staticmethod(bajar_vehiculo)
    crear_vehiculo               = staticmethod(crear_vehiculo)

    # -- Logs --
    registrar_log                = staticmethod(registrar_log)
    log_accion                   = staticmethod(registrar_log)  # Alias para compatibilidad

    # -- Estado de evento (aliases) --
    get_modo_evento              = staticmethod(get_estado_evento)
    set_modo_evento              = staticmethod(set_estado_evento)

    # -- Estado de evento (punteros base) --
    get_estado_evento            = staticmethod(get_estado_evento)
    set_estado_evento            = staticmethod(set_estado_evento)

    # -- Webhooks --
    get_webhook_cache            = staticmethod(get_webhook_cache)
    save_webhook_cache           = staticmethod(save_webhook_cache)

    # -- Sesiones SUDO --
    get_sesion_sudo              = staticmethod(get_sesion_sudo)
    crear_sesion_sudo            = staticmethod(crear_sesion_sudo)
    revocar_sesion_sudo          = staticmethod(revocar_sesion_sudo)
    limpiar_sesiones_sudo_expiradas = staticmethod(limpiar_sesiones_sudo_expiradas)
    truncate_all_tables          = staticmethod(truncate_all_tables)

    # -- Conexión --
    get_connection               = staticmethod(get_connection)
    close_connection             = staticmethod(close_connection)

    # -- Formularios de registro --
    get_formulario               = staticmethod(get_formulario)
    upsert_formulario            = staticmethod(upsert_formulario)
    borrar_formulario            = staticmethod(borrar_formulario)
    suspender_formularios_inactivos = staticmethod(suspender_formularios_inactivos)
    # -- Inventario general (aliases) --
    añadir_item_inventario       = staticmethod(add_item_general)
    retirar_item_inventario      = staticmethod(remove_item_general)

    # -- Ítems y tienda (métodos adicionales) --
    async def añadir_a_tienda(item_uuid: int, precio: float, stock: int = -1) -> None:
        await _run_in_executor(
            _execute,
            "INSERT INTO tienda (item_uuid, precio_actual, stock, activo) VALUES (?, ?, ?, 1)",
            (item_uuid, precio, stock)
        )

    async def retirar_de_tienda(tienda_id: int) -> None:
        await _run_in_executor(
            _execute,
            "UPDATE tienda SET activo = 0 WHERE id = ?",
            (tienda_id,)
        )

    añadir_a_tienda = staticmethod(añadir_a_tienda)
    retirar_de_tienda = staticmethod(retirar_de_tienda)

    # -- Métodos de ejecución directa (limitados/alias) --
    _execute                     = staticmethod(_execute_async)
    _fetch_one                   = staticmethod(_fetchone_async)
    _fetch_all                   = staticmethod(_fetchall_async)
