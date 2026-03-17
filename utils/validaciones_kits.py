"""
utils/validaciones_kits.py — Validaciones para sistema de KITs
===============================================================
Responsabilidad : Funciones de validación reutilizables para KITs médicos.
Dependencias    : None (funciones puras)
Autor           : RAISA Dev

Funciones
---------
- validar_espacio_kit()      : Valida espacio disponible en un KIT
- validar_peso_kit()          : Valida peso máximo de un KIT
- validar_compatibilidad_kit(): Valida si un ítem puede ir en un KIT
- calcular_espacio_maximo()   : Calcula espacio máximo de un KIT
"""

from dataclasses import dataclass


@dataclass
class ResultadoValidacion:
    """Resultado de una validación."""
    valido: bool
    motivo: str = ""


# ---------------------------------------------------------------------------
# Validaciones de espacio
# ---------------------------------------------------------------------------

def calcular_espacio_maximo(volumen_base: int, espacio_libre_pct: int) -> float:
    """
    Calcula el espacio máximo permitido en un KIT.
    
    Args:
        volumen_base    : Volumen base del KIT en unidades.
        espacio_libre_pct: Porcentaje de espacio libre permitido (5-20).
        
    Returns:
        Espacio máximo en unidades (float).
        
    Example:
        >>> calcular_espacio_maximo(10, 15)
        11.5
    """
    return volumen_base * (1 + espacio_libre_pct / 100.0)


def validar_espacio_kit(
    volumen_base: int,
    espacio_libre_pct: int,
    volumen_usado: int,
    volumen_adicional: int,
) -> ResultadoValidacion:
    """
    Valida si hay espacio disponible en un KIT para añadir más ítems.
    
    Args:
        volumen_base     : Volumen base del KIT.
        espacio_libre_pct: Porcentaje de espacio libre.
        volumen_usado    : Volumen actualmente usado.
        volumen_adicional: Volumen que se quiere añadir.
        
    Returns:
        ResultadoValidacion con el resultado.
        
    Example:
        >>> validar_espacio_kit(10, 15, 8, 2)
        ResultadoValidacion(valido=True, motivo='')
        >>> validar_espacio_kit(10, 15, 10, 2)
        ResultadoValidacion(valido=False, motivo='Sin espacio. Disponible: 1.5u, necesitas: 2u')
    """
    volumen_max = calcular_espacio_maximo(volumen_base, espacio_libre_pct)
    volumen_futuro = volumen_usado + volumen_adicional
    
    if volumen_futuro > volumen_max:
        disponible = volumen_max - volumen_usado
        return ResultadoValidacion(
            valido=False,
            motivo=f"Sin espacio. Disponible: {disponible:.1f}u, necesitas: {volumen_adicional}u",
        )
    
    return ResultadoValidacion(valido=True)


# ---------------------------------------------------------------------------
# Validaciones de peso
# ---------------------------------------------------------------------------

def validar_peso_kit(
    peso_max_global: float,
    peso_loadout_actual: float,
    peso_kit: float,
) -> ResultadoValidacion:
    """
    Valida si añadir un KIT al loadout no excede el límite de peso global.
    
    Args:
        peso_max_global    : Peso máximo permitido en el loadout (kg).
        peso_loadout_actual: Peso actual del loadout (kg).
        peso_kit           : Peso del KIT a añadir (kg).
        
    Returns:
        ResultadoValidacion con el resultado.
        
    Example:
        >>> validar_peso_kit(40.0, 38.5, 2.0)
        ResultadoValidacion(valido=False, motivo='Excede límite. Disponible: 1.5kg, necesitas: 2.0kg')
    """
    peso_futuro = peso_loadout_actual + peso_kit
    
    if peso_futuro > peso_max_global:
        disponible = peso_max_global - peso_loadout_actual
        return ResultadoValidacion(
            valido=False,
            motivo=f"Excede límite. Disponible: {disponible:.2f}kg, necesitas: {peso_kit:.2f}kg",
        )
    
    return ResultadoValidacion(valido=True)


# ---------------------------------------------------------------------------
# Validaciones de compatibilidad
# ---------------------------------------------------------------------------

def validar_compatibilidad_kit(
    item_categoria: str,
    kits_permitidos: list[str] | None = None,
) -> ResultadoValidacion:
    """
    Valida si un ítem puede insertarse en un KIT basándose en su categoría.
    
    Por defecto, solo ítems médicos pueden ir en KITs médicos.
    
    Args:
        item_categoria  : Categoría del ítem (ej: 'medico', 'radio', 'misc').
        kits_permitidos : Lista de categorías permitidas (default: ['medico']).
        
    Returns:
        ResultadoValidacion con el resultado.
        
    Example:
        >>> validar_compatibilidad_kit('medico')
        ResultadoValidacion(valido=True, motivo='')
        >>> validar_compatibilidad_kit('radio')
        ResultadoValidacion(valido=False, motivo='Solo ítems médicos pueden ir en KITs médicos')
    """
    if kits_permitidos is None:
        kits_permitidos = ["medico"]
    
    if item_categoria not in kits_permitidos:
        categorias_str = ", ".join(kits_permitidos)
        return ResultadoValidacion(
            valido=False,
            motivo=f"Solo ítems de categorías [{categorias_str}] pueden ir en KITs médicos",
        )
    
    return ResultadoValidacion(valido=True)


# ---------------------------------------------------------------------------
# Validaciones de slots de pouches
# ---------------------------------------------------------------------------

def validar_kit_como_pouch(
    slots_totales: int,
    slots_usados: int,
    slots_kit: int,
) -> ResultadoValidacion:
    """
    Valida si un KIT puede asignarse como pouch a una protección.
    
    Args:
        slots_totales: Slots totales disponibles en la protección.
        slots_usados : Slots ya ocupados por otros pouches.
        slots_kit    : Slots que ocuparía el KIT.
        
    Returns:
        ResultadoValidacion con el resultado.
        
    Example:
        >>> validar_kit_como_pouch(10, 8, 2)
        ResultadoValidacion(valido=True, motivo='')
        >>> validar_kit_como_pouch(10, 9, 2)
        ResultadoValidacion(valido=False, motivo='Sin slots. Disponibles: 1, necesitas: 2')
    """
    slots_disponibles = slots_totales - slots_usados
    
    if slots_kit > slots_disponibles:
        return ResultadoValidacion(
            valido=False,
            motivo=f"Sin slots. Disponibles: {slots_disponibles}, necesitas: {slots_kit}",
        )
    
    return ResultadoValidacion(valido=True)


# ---------------------------------------------------------------------------
# Cálculos auxiliares
# ---------------------------------------------------------------------------

def calcular_peso_total_kit(
    peso_contenedor: float,
    peso_contenido: float,
) -> float:
    """
    Calcula el peso total de un KIT (contenedor + contenido).
    
    Args:
        peso_contenedor: Peso del contenedor vacío (kg).
        peso_contenido : Peso del contenido actual (kg).
        
    Returns:
        Peso total en kg.
        
    Example:
        >>> calcular_peso_total_kit(0.5, 2.0)
        2.5
    """
    return round(peso_contenedor + peso_contenido, 3)


def calcular_porcentaje_ocupacion(
    volumen_usado: int,
    volumen_base: int,
    espacio_libre_pct: int,
) -> float:
    """
    Calcula el porcentaje de ocupación de un KIT.
    
    Args:
        volumen_usado    : Volumen actualmente usado.
        volumen_base     : Volumen base del KIT.
        espacio_libre_pct: Porcentaje de espacio libre.
        
    Returns:
        Porcentaje de ocupación (0-100+).
        
    Example:
        >>> calcular_porcentaje_ocupacion(10, 10, 15)
        86.96
    """
    volumen_max = calcular_espacio_maximo(volumen_base, espacio_libre_pct)
    if volumen_max == 0:
        return 0.0
    return round((volumen_usado / volumen_max) * 100, 2)