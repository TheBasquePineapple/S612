"""
db/kits_repository.py — Repositorio de KITs Médicos
====================================================
Responsabilidad : Capa de acceso a datos para sistema de KITs médicos.
Dependencias    : aiosqlite
Autor           : RAISA Dev

Funciones principales
---------------------
- seed_kits_catalogo()           : Carga KITs desde JSON a BBDD
- create_kit_instance()          : Crea instancia de KIT para usuario
- get_kit_instance()             : Obtiene instancia de KIT por ID
- get_user_kits()                : Lista todos los KITs de un usuario
- get_kit_contents()             : Obtiene contenido actual de un KIT
- extract_item_from_kit()        : Extrae ítem de un KIT
- insert_item_into_kit()         : Inserta ítem en un KIT
- delete_kit_instance()          : Elimina instancia de KIT
- calculate_kit_weight()         : Calcula peso total del KIT
- validate_kit_space()           : Valida espacio disponible en KIT

Optimizaciones
--------------
- Uso de prepared statements con parámetros
- Índices en columnas de búsqueda frecuente
- Triggers automáticos para timestamps
- Caching de catálogo de KITs en memoria
"""

import json
from pathlib import Path
from typing import Any

import aiosqlite

# ---------------------------------------------------------------------------
# Cache en memoria del catálogo de KITs (invalidar al modificar)
# ---------------------------------------------------------------------------
_KITS_CACHE: dict[str, dict[str, Any]] | None = None


# ---------------------------------------------------------------------------
# Funciones auxiliares
# ---------------------------------------------------------------------------

def _invalidate_kits_cache() -> None:
    """Invalida el cache de KITs en memoria."""
    global _KITS_CACHE
    _KITS_CACHE = None


async def _load_kits_cache(conn: aiosqlite.Connection) -> dict[str, dict[str, Any]]:
    """
    Carga el catálogo de KITs en memoria.
    
    Args:
        conn: Conexión aiosqlite activa.
        
    Returns:
        Dict indexado por código de KIT.
    """
    global _KITS_CACHE
    if _KITS_CACHE is not None:
        return _KITS_CACHE
    
    cursor = await conn.execute("""
        SELECT id, codigo, nombre, descripcion, categoria, subcategoria,
               peso_kg, volumen_u, slots_pouch, espacio_libre_pct, precio_base
        FROM kits_catalogo
    """)
    rows = await cursor.fetchall()
    
    _KITS_CACHE = {}
    for row in rows:
        _KITS_CACHE[row[1]] = {  # Indexado por 'codigo'
            "id": row[0],
            "codigo": row[1],
            "nombre": row[2],
            "descripcion": row[3],
            "categoria": row[4],
            "subcategoria": row[5],
            "peso_kg": row[6],
            "volumen_u": row[7],
            "slots_pouch": row[8],
            "espacio_libre_pct": row[9],
            "precio_base": row[10],
        }
    
    return _KITS_CACHE


# ---------------------------------------------------------------------------
# Seed: Carga catálogo desde JSON
# ---------------------------------------------------------------------------

async def seed_kits_catalogo(conn: aiosqlite.Connection, json_path: str = "protecciones_y_equipo.json") -> int:
    """
    Carga el catálogo de KITs desde el archivo JSON a la BBDD.
    
    Args:
        conn     : Conexión aiosqlite activa.
        json_path: Ruta al archivo JSON con los KITs.
        
    Returns:
        Cantidad de KITs insertados.
        
    Raises:
        FileNotFoundError: Si el archivo JSON no existe.
        json.JSONDecodeError: Si el JSON es inválido.
    """
    path = Path(json_path)
    if not path.exists():
        raise FileNotFoundError(f"Archivo no encontrado: {json_path}")
    
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    
    # Filtrar solo ítems con es_kit=true o categoria='kit'
    kits = [item for item in data if item.get("es_kit") or item.get("categoria") == "kit"]
    
    count = 0
    for kit in kits:
        # Insertar en kits_catalogo
        cursor = await conn.execute("""
            INSERT OR IGNORE INTO kits_catalogo 
            (codigo, nombre, descripcion, categoria, subcategoria, peso_kg, volumen_u,
             slots_pouch, espacio_libre_pct, precio_base, es_kit)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (
            kit["codigo"],
            kit["nombre"],
            kit.get("descripcion", ""),
            kit.get("categoria", "kit"),
            kit.get("subcategoria", ""),
            kit["peso_kg"],
            kit["volumen_u"],
            kit.get("slots_pouch", 1),
            kit.get("espacio_libre_pct", 10),
            kit["precio_base"],
        ))
        
        if cursor.rowcount > 0:
            count += 1
            kit_id = cursor.lastrowid
            
            # Insertar contenido default
            contenido = kit.get("contenido", [])
            for item in contenido:
                await conn.execute("""
                    INSERT INTO kits_contenido_default (kit_id, item_codigo, cantidad)
                    VALUES (?, ?, ?)
                """, (kit_id, item["item_codigo"], item["cantidad"]))
    
    await conn.commit()
    _invalidate_kits_cache()
    
    return count


# ---------------------------------------------------------------------------
# CRUD: Instancias de KITs
# ---------------------------------------------------------------------------

async def create_kit_instance(
    conn: aiosqlite.Connection,
    user_id: int,
    kit_codigo: str,
    ubicacion: str = "general",
    slot_destino: str | None = None,
) -> int:
    """
    Crea una nueva instancia de KIT para un usuario.
    Copia el contenido default del catálogo a la instancia.
    
    Args:
        conn        : Conexión aiosqlite activa.
        user_id     : Discord user ID.
        kit_codigo  : Código del KIT (ej: KIT-001).
        ubicacion   : 'general', 'loadout_slot', 'pouch_ID', 'vehiculo_ID'.
        slot_destino: Nombre del slot si ubicacion='loadout_slot'.
        
    Returns:
        ID de la instancia creada.
        
    Raises:
        ValueError: Si el KIT no existe en el catálogo.
    """
    # Obtener KIT del catálogo
    cache = await _load_kits_cache(conn)
    kit = cache.get(kit_codigo)
    if not kit:
        raise ValueError(f"KIT no encontrado en catálogo: {kit_codigo}")
    
    # Crear instancia
    cursor = await conn.execute("""
        INSERT INTO kits_instancias 
        (user_id, kit_catalogo_id, ubicacion, slot_destino, peso_contenido_actual, volumen_usado)
        VALUES (?, ?, ?, ?, 0.0, 0)
    """, (user_id, kit["id"], ubicacion, slot_destino))
    
    instancia_id = cursor.lastrowid
    
    # Copiar contenido default a contenido actual
    cursor = await conn.execute("""
        SELECT item_codigo, cantidad
        FROM kits_contenido_default
        WHERE kit_id = ?
    """, (kit["id"],))
    
    items_default = await cursor.fetchall()
    
    for item_codigo, cantidad in items_default:
        await conn.execute("""
            INSERT INTO kits_contenido_actual (kit_instancia_id, item_codigo, cantidad)
            VALUES (?, ?, ?)
        """, (instancia_id, item_codigo, cantidad))
    
    # Calcular peso inicial
    peso_total = await calculate_kit_weight(conn, instancia_id)
    await conn.execute("""
        UPDATE kits_instancias 
        SET peso_contenido_actual = ?
        WHERE id = ?
    """, (peso_total, instancia_id))
    
    await conn.commit()
    return instancia_id


async def get_kit_instance(conn: aiosqlite.Connection, instancia_id: int) -> dict[str, Any] | None:
    """
    Obtiene los datos de una instancia de KIT.
    
    Args:
        conn        : Conexión aiosqlite activa.
        instancia_id: ID de la instancia.
        
    Returns:
        Dict con datos de la instancia o None si no existe.
    """
    cursor = await conn.execute("""
        SELECT ki.id, ki.user_id, ki.ubicacion, ki.slot_destino,
               ki.peso_contenido_actual, ki.volumen_usado,
               kc.codigo, kc.nombre, kc.volumen_u, kc.espacio_libre_pct
        FROM kits_instancias ki
        JOIN kits_catalogo kc ON ki.kit_catalogo_id = kc.id
        WHERE ki.id = ?
    """, (instancia_id,))
    
    row = await cursor.fetchone()
    if not row:
        return None
    
    return {
        "id": row[0],
        "user_id": row[1],
        "ubicacion": row[2],
        "slot_destino": row[3],
        "peso_contenido_actual": row[4],
        "volumen_usado": row[5],
        "kit_codigo": row[6],
        "kit_nombre": row[7],
        "volumen_total": row[8],
        "espacio_libre_pct": row[9],
    }


async def get_user_kits(
    conn: aiosqlite.Connection,
    user_id: int,
    ubicacion: str | None = None,
) -> list[dict[str, Any]]:
    """
    Obtiene todos los KITs de un usuario, opcionalmente filtrados por ubicación.
    
    Args:
        conn     : Conexión aiosqlite activa.
        user_id  : Discord user ID.
        ubicacion: Filtro opcional por ubicación.
        
    Returns:
        Lista de diccionarios con datos de instancias.
    """
    if ubicacion:
        cursor = await conn.execute("""
            SELECT ki.id, ki.ubicacion, ki.slot_destino, ki.peso_contenido_actual,
                   kc.codigo, kc.nombre, kc.volumen_u, kc.espacio_libre_pct
            FROM kits_instancias ki
            JOIN kits_catalogo kc ON ki.kit_catalogo_id = kc.id
            WHERE ki.user_id = ? AND ki.ubicacion = ?
            ORDER BY ki.created_at DESC
        """, (user_id, ubicacion))
    else:
        cursor = await conn.execute("""
            SELECT ki.id, ki.ubicacion, ki.slot_destino, ki.peso_contenido_actual,
                   kc.codigo, kc.nombre, kc.volumen_u, kc.espacio_libre_pct
            FROM kits_instancias ki
            JOIN kits_catalogo kc ON ki.kit_catalogo_id = kc.id
            WHERE ki.user_id = ?
            ORDER BY ki.created_at DESC
        """, (user_id,))
    
    rows = await cursor.fetchall()
    
    return [
        {
            "id": r[0],
            "ubicacion": r[1],
            "slot_destino": r[2],
            "peso_contenido": r[3],
            "kit_codigo": r[4],
            "kit_nombre": r[5],
            "volumen_total": r[6],
            "espacio_libre_pct": r[7],
        }
        for r in rows
    ]


async def delete_kit_instance(conn: aiosqlite.Connection, instancia_id: int, user_id: int) -> bool:
    """
    Elimina una instancia de KIT (solo si pertenece al usuario).
    
    Args:
        conn        : Conexión aiosqlite activa.
        instancia_id: ID de la instancia a eliminar.
        user_id     : Discord user ID (validación de pertenencia).
        
    Returns:
        True si se eliminó, False si no existe o no pertenece al usuario.
    """
    cursor = await conn.execute("""
        DELETE FROM kits_instancias
        WHERE id = ? AND user_id = ?
    """, (instancia_id, user_id))
    
    await conn.commit()
    return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Gestión de contenido
# ---------------------------------------------------------------------------

async def get_kit_contents(conn: aiosqlite.Connection, instancia_id: int) -> list[dict[str, Any]]:
    """
    Obtiene el contenido actual de un KIT.
    
    Args:
        conn        : Conexión aiosqlite activa.
        instancia_id: ID de la instancia.
        
    Returns:
        Lista de diccionarios con ítems y cantidades.
    """
    cursor = await conn.execute("""
        SELECT item_codigo, cantidad
        FROM kits_contenido_actual
        WHERE kit_instancia_id = ?
        ORDER BY item_codigo
    """, (instancia_id,))
    
    rows = await cursor.fetchall()
    return [{"item_codigo": r[0], "cantidad": r[1]} for r in rows]


async def extract_item_from_kit(
    conn: aiosqlite.Connection,
    instancia_id: int,
    item_codigo: str,
    cantidad: int = 1,
) -> bool:
    """
    Extrae un ítem de un KIT. Reduce la cantidad o elimina el registro.
    
    Args:
        conn        : Conexión aiosqlite activa.
        instancia_id: ID de la instancia.
        item_codigo : Código del ítem (MED-XXX).
        cantidad    : Cantidad a extraer.
        
    Returns:
        True si se extrajo, False si no hay suficiente cantidad.
    """
    # Verificar cantidad actual
    cursor = await conn.execute("""
        SELECT cantidad FROM kits_contenido_actual
        WHERE kit_instancia_id = ? AND item_codigo = ?
    """, (instancia_id, item_codigo))
    
    row = await cursor.fetchone()
    if not row or row[0] < cantidad:
        return False
    
    cantidad_actual = row[0]
    nueva_cantidad = cantidad_actual - cantidad
    
    if nueva_cantidad <= 0:
        # Eliminar ítem del KIT
        await conn.execute("""
            DELETE FROM kits_contenido_actual
            WHERE kit_instancia_id = ? AND item_codigo = ?
        """, (instancia_id, item_codigo))
    else:
        # Reducir cantidad
        await conn.execute("""
            UPDATE kits_contenido_actual
            SET cantidad = ?
            WHERE kit_instancia_id = ? AND item_codigo = ?
        """, (nueva_cantidad, instancia_id, item_codigo))
    
    # Recalcular peso
    peso_total = await calculate_kit_weight(conn, instancia_id)
    await conn.execute("""
        UPDATE kits_instancias
        SET peso_contenido_actual = ?
        WHERE id = ?
    """, (peso_total, instancia_id))
    
    await conn.commit()
    return True


async def insert_item_into_kit(
    conn: aiosqlite.Connection,
    instancia_id: int,
    item_codigo: str,
    item_peso: float,
    item_volumen: int,
    cantidad: int = 1,
) -> tuple[bool, str]:
    """
    Inserta un ítem en un KIT. Valida espacio disponible.
    
    Args:
        conn        : Conexión aiosqlite activa.
        instancia_id: ID de la instancia.
        item_codigo : Código del ítem.
        item_peso   : Peso unitario del ítem.
        item_volumen: Volumen unitario del ítem.
        cantidad    : Cantidad a insertar.
        
    Returns:
        Tupla (éxito: bool, motivo: str).
    """
    # Obtener datos del KIT
    kit = await get_kit_instance(conn, instancia_id)
    if not kit:
        return False, "Instancia de KIT no encontrada"
    
    # Calcular espacio máximo permitido
    volumen_max = kit["volumen_total"] * (1 + kit["espacio_libre_pct"] / 100.0)
    volumen_nuevo = item_volumen * cantidad
    volumen_futuro = kit["volumen_usado"] + volumen_nuevo
    
    if volumen_futuro > volumen_max:
        espacio_disponible = volumen_max - kit["volumen_usado"]
        return False, f"Sin espacio. Disponible: {espacio_disponible}u, necesitas: {volumen_nuevo}u"
    
    # Verificar si el ítem ya existe en el KIT
    cursor = await conn.execute("""
        SELECT cantidad FROM kits_contenido_actual
        WHERE kit_instancia_id = ? AND item_codigo = ?
    """, (instancia_id, item_codigo))
    
    row = await cursor.fetchone()
    
    if row:
        # Incrementar cantidad
        nueva_cantidad = row[0] + cantidad
        await conn.execute("""
            UPDATE kits_contenido_actual
            SET cantidad = ?
            WHERE kit_instancia_id = ? AND item_codigo = ?
        """, (nueva_cantidad, instancia_id, item_codigo))
    else:
        # Insertar nuevo ítem
        await conn.execute("""
            INSERT INTO kits_contenido_actual (kit_instancia_id, item_codigo, cantidad)
            VALUES (?, ?, ?)
        """, (instancia_id, item_codigo, cantidad))
    
    # Actualizar peso y volumen
    nuevo_peso = kit["peso_contenido_actual"] + (item_peso * cantidad)
    await conn.execute("""
        UPDATE kits_instancias
        SET peso_contenido_actual = ?, volumen_usado = ?
        WHERE id = ?
    """, (nuevo_peso, volumen_futuro, instancia_id))
    
    await conn.commit()
    return True, "Ítem insertado correctamente"


# ---------------------------------------------------------------------------
# Cálculos auxiliares
# ---------------------------------------------------------------------------

async def calculate_kit_weight(conn: aiosqlite.Connection, instancia_id: int) -> float:
    """
    Calcula el peso total del contenido actual de un KIT.
    Consulta el catálogo de ítems médicos para obtener pesos.
    
    Args:
        conn        : Conexión aiosqlite activa.
        instancia_id: ID de la instancia.
        
    Returns:
        Peso total en kg.
        
    Note:
        Esta función asume que existe una tabla 'items_catalogo' con columnas:
        - codigo: TEXT
        - peso_kg: REAL
        Ajustar según la estructura real de tu BBDD.
    """
    cursor = await conn.execute("""
        SELECT kca.item_codigo, kca.cantidad
        FROM kits_contenido_actual kca
        WHERE kca.kit_instancia_id = ?
    """, (instancia_id,))
    
    contenido = await cursor.fetchall()
    peso_total = 0.0
    
    for item_codigo, cantidad in contenido:
        # Buscar peso del ítem en el catálogo
        # NOTA: Ajustar según la estructura real de tu tabla de ítems
        cursor_item = await conn.execute("""
            SELECT peso_kg FROM items_catalogo WHERE codigo = ?
        """, (item_codigo,))
        
        row = await cursor_item.fetchone()
        if row:
            peso_total += row[0] * cantidad
    
    return round(peso_total, 3)


async def validate_kit_space(
    conn: aiosqlite.Connection,
    instancia_id: int,
    volumen_adicional: int,
) -> tuple[bool, str]:
    """
    Valida si hay espacio disponible en un KIT para añadir más ítems.
    
    Args:
        conn             : Conexión aiosqlite activa.
        instancia_id     : ID de la instancia.
        volumen_adicional: Volumen a añadir.
        
    Returns:
        Tupla (válido: bool, motivo: str).
    """
    kit = await get_kit_instance(conn, instancia_id)
    if not kit:
        return False, "Instancia de KIT no encontrada"
    
    volumen_max = kit["volumen_total"] * (1 + kit["espacio_libre_pct"] / 100.0)
    volumen_futuro = kit["volumen_usado"] + volumen_adicional
    
    if volumen_futuro > volumen_max:
        disponible = volumen_max - kit["volumen_usado"]
        return False, f"Sin espacio. Disponible: {disponible}u, necesitas: {volumen_adicional}u"
    
    return True, "Espacio disponible"


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

async def get_kit_by_codigo(conn: aiosqlite.Connection, codigo: str) -> dict[str, Any] | None:
    """
    Obtiene un KIT del catálogo por su código.
    
    Args:
        conn  : Conexión aiosqlite activa.
        codigo: Código del KIT (ej: KIT-001).
        
    Returns:
        Dict con datos del KIT o None si no existe.
    """
    cache = await _load_kits_cache(conn)
    return cache.get(codigo)