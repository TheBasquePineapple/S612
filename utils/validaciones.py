"""
RAISA — Validaciones reutilizables centralizadas
utils/validaciones.py

Responsabilidad : Lógica de validación compartida entre cogs.
                  Compatibilidad de munición, límites de peso/volumen, etc.
Dependencias    : db/repository
Autor           : Proyecto RAISA
"""

import logging

from db.repository import Repository  # noqa: F401 – class defined in db/repository

log = logging.getLogger("raisa.validaciones")


# ──────────────────────────────────────────────────────────────────────────────
# MUNICIÓN
# ──────────────────────────────────────────────────────────────────────────────

async def validar_municion(
    repo: Repository,
    arma_item_id: int,
    cargador_item_id: int,
) -> tuple[bool, str]:
    """
    REGLA ABSOLUTA: La munición debe coincidir en calibre Y en id_compatibilidad.
    Esta función centraliza la validación en todos los puntos de recarga.

    :param repo: Repositorio de datos.
    :param arma_item_id: UUID del arma a recargar.
    :param cargador_item_id: UUID del cargador a usar.
    :returns: (True, "") si es válido, (False, mensaje_error) si no.
    """
    arma     = await repo.get_item(arma_item_id)
    cargador = await repo.get_item(cargador_item_id)

    if not arma:
        return False, f"El arma con ID `{arma_item_id}` no existe en el sistema."
    if not cargador:
        return False, f"El cargador con ID `{cargador_item_id}` no existe en el sistema."

    # Verificación de calibre
    if arma.get("calibre") and cargador.get("calibre"):
        if arma["calibre"] != cargador["calibre"]:
            return (
                False,
                f"**Calibre incompatible.** "
                f"El arma usa `{arma['calibre']}` pero el cargador es `{cargador['calibre']}`."
            )

    # Verificación de ID de compatibilidad (independiente al UUID del ítem)
    if arma.get("id_compatibilidad") and cargador.get("id_compatibilidad"):
        if arma["id_compatibilidad"] != cargador["id_compatibilidad"]:
            return (
                False,
                f"**Cargador incompatible.** "
                f"El arma requiere compatibilidad `{arma['id_compatibilidad']}` "
                f"pero el cargador es `{cargador['id_compatibilidad']}`."
            )

    return True, ""


# ──────────────────────────────────────────────────────────────────────────────
# INVENTARIO — PESO Y VOLUMEN
# ──────────────────────────────────────────────────────────────────────────────

async def validar_capacidad_inventario(
    repo: Repository,
    user_id: int,
    item_uuid: int,
    cantidad: int = 1,
) -> tuple[bool, str]:
    """
    Verifica si añadir `cantidad` unidades de un ítem al loadout supera
    los límites de peso (40 kg) y volumen configurados.

    Solo aplica al inventario personal/loadout.
    El inventario general y de vehículos tienen sus propios límites.

    :param repo: Repositorio de datos.
    :param user_id: ID del usuario.
    :param item_uuid: UUID del ítem a añadir.
    :param cantidad: Unidades a añadir.
    :returns: (True, "") si cabe, (False, mensaje_error) si no.
    """
    import json
    from pathlib import Path

    item = await repo.get_item(item_uuid)
    if not item:
        return False, f"Ítem con ID `{item_uuid}` no encontrado."

    # Leer límite de volumen desde config
    cfg_path = Path("config/inventario.json")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    peso_max   = cfg.get("peso_maximo_kg", 40)
    volumen_max = cfg.get("volumen_maximo_unidades", 100)

    # Calcular peso/volumen actual del loadout
    peso_actual, volumen_actual = await _calcular_carga_loadout(repo, user_id)

    peso_nuevo   = peso_actual   + item["peso_kg"]   * cantidad
    volumen_nuevo = volumen_actual + item["volumen"]   * cantidad

    if peso_nuevo > peso_max:
        return (
            False,
            f"**Límite de peso superado.**\n"
            f"Peso actual: `{peso_actual:.2f} kg` · Añadir: `{item['peso_kg'] * cantidad:.2f} kg`\n"
            f"Máximo: `{peso_max} kg`"
        )

    if volumen_nuevo > volumen_max:
        return (
            False,
            f"**Límite de volumen superado.**\n"
            f"Volumen actual: `{volumen_actual:.2f}` · Añadir: `{item['volumen'] * cantidad:.2f}`\n"
            f"Máximo: `{volumen_max}`"
        )

    return True, ""


async def _calcular_carga_loadout(repo: Repository, user_id: int) -> tuple[float, float]:
    """
    Calcula el peso y volumen total actual del loadout de un usuario.

    :returns: (peso_total_kg, volumen_total)
    """
    loadout = await repo.get_loadout(user_id)
    if not loadout:
        return 0.0, 0.0

    SLOTS_ITEM = [
        "arma_primaria_id", "arma_secundaria_id", "arma_terciaria_id",
        "chaleco_id", "portaplacas_id", "placas_id", "soportes_id", "casco_id",
        "pantalon_id", "camisa_id", "chaqueta_id", "botas_id",
        "guantes_id", "reloj_id", "mochila_id", "cinturon_id", "radio_id",
    ]

    peso_total = 0.0
    volumen_total = 0.0

    for slot in SLOTS_ITEM:
        item_id = loadout.get(slot)
        if item_id:
            item = await repo.get_item(item_id)
            if item:
                peso_total    += item.get("peso_kg", 0)
                volumen_total += item.get("volumen", 0)

    # Sumar pouches
    for contenedor in ("chaleco", "portaplacas", "soportes"):
        pouches = await repo.get_pouches(user_id, contenedor)
        for pouch in pouches:
            peso_total    += pouch.get("peso_kg", 0)
            volumen_total += pouch.get("volumen", 0)

    return peso_total, volumen_total


async def validar_capacidad_vehiculo(
    repo: Repository,
    vehiculo_id: int,
    item_uuid: int,
    cantidad: int = 1,
) -> tuple[bool, str]:
    """
    Verifica si añadir un ítem al inventario de un vehículo supera sus límites.

    :returns: (True, "") si cabe, (False, mensaje_error) si no.
    """
    vehiculo = await repo.get_vehiculo(vehiculo_id)
    item     = await repo.get_item(item_uuid)

    if not vehiculo:
        return False, "Vehículo no encontrado."
    if not item:
        return False, "Ítem no encontrado."

    peso_actual   = vehiculo.get("inventario_peso_actual", 0)
    peso_max      = vehiculo.get("inventario_peso_max", 0)
    vol_actual    = vehiculo.get("inventario_volumen_actual", 0)
    vol_max       = vehiculo.get("inventario_volumen_max", 0)

    peso_nuevo = peso_actual + item["peso_kg"] * cantidad
    vol_nuevo  = vol_actual  + item["volumen"]  * cantidad

    if peso_nuevo > peso_max:
        return False, f"El vehículo no tiene capacidad de peso suficiente. (`{peso_actual:.1f}/{peso_max:.1f} kg`)"
    if vol_nuevo > vol_max:
        return False, f"El vehículo no tiene capacidad de volumen suficiente. (`{vol_actual:.1f}/{vol_max:.1f}`)"

    return True, ""


# ──────────────────────────────────────────────────────────────────────────────
# TRANSFERENCIA DE MUNICIÓN (Regla de vehículos)
# ──────────────────────────────────────────────────────────────────────────────

TIPOS_PERMITEN_TRANSFERENCIA = {
    "coche", "furgoneta", "blindado_ligero", "blindado_pesado", "mbt",
    "helo_transporte",
}

def validar_transferencia_municion(tipo_vehiculo: str) -> tuple[bool, str]:
    """
    Verifica si un tipo de vehículo permite transferencia de munición
    desde el inventario personal a sus armas.

    :param tipo_vehiculo: Tipo del vehículo (campo 'tipo' en BBDD).
    :returns: (True, "") si está permitido, (False, mensaje) si no.
    """
    if tipo_vehiculo in TIPOS_PERMITEN_TRANSFERENCIA:
        return True, ""
    return (
        False,
        f"La transferencia de munición **no está permitida** para vehículos de tipo `{tipo_vehiculo}`.\n"
        f"Solo está disponible en vehículos terrestres y helicópteros de transporte."
    )


# ──────────────────────────────────────────────────────────────────────────────
# SLOTS DE POUCHES
# ──────────────────────────────────────────────────────────────────────────────

async def validar_slot_pouch(
    repo: Repository,
    user_id: int,
    contenedor: str,
    pouch_tipo: str,
) -> tuple[bool, str, int]:
    """
    Verifica si hay espacio para añadir un pouch en el contenedor.

    :param repo: Repositorio de datos.
    :param user_id: ID del usuario.
    :param contenedor: 'chaleco' | 'portaplacas' | 'soportes'.
    :param pouch_tipo: 'simple' | 'dual' | 'doble'.
    :returns: (True, "", slot_libre) o (False, mensaje, -1).
    """
    import json
    from pathlib import Path

    cfg = json.loads(Path("config/inventario.json").read_text(encoding="utf-8"))
    slots_por_tipo = cfg.get("tipos_pouch", {})
    slots_requeridos = slots_por_tipo.get(pouch_tipo, {}).get("slots", 1)

    # Obtener el contenedor del loadout para saber cuántos slots tiene
    loadout = await repo.get_loadout(user_id)
    if not loadout:
        return False, "No tienes loadout inicializado.", -1

    contenedor_id = loadout.get(f"{contenedor}_id")
    if not contenedor_id:
        return False, f"No tienes ningún **{contenedor}** equipado.", -1

    contenedor_item = await repo.get_item(contenedor_id)
    if not contenedor_item:
        return False, "Error al obtener datos del contenedor.", -1

    slots_totales = contenedor_item.get("slots_pouches", 0)
    if slots_totales == 0:
        return False, f"El {contenedor} equipado no tiene slots de pouches.", -1

    # Ver cuántos slots están ocupados
    pouches = await repo.get_pouches(user_id, contenedor)
    slots_ocupados = sum(
        slots_por_tipo.get(p.get("tipo_pouch", "simple"), {}).get("slots", 1)
        for p in pouches
    )
    slots_libres = slots_totales - slots_ocupados

    if slots_libres < slots_requeridos:
        return (
            False,
            f"No hay suficientes slots libres en el {contenedor}.\n"
            f"Libres: `{slots_libres}` · Requeridos: `{slots_requeridos}`",
            -1,
        )

    # Encontrar el primer slot numérico libre
    ocupados_set = {p["slot_numero"] for p in pouches}
    slot_libre = next(i for i in range(1, slots_totales + 1) if i not in ocupados_set)
    return True, "", slot_libre
