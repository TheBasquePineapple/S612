"""
utils/validaciones.py — Validaciones centralizadas y reutilizables de RAISA
============================================================================
Responsabilidad : Todas las reglas de negocio que se aplican en múltiples
                  módulos. Importar desde aquí, nunca duplicar lógica.
Dependencias    : stdlib únicamente (no importar cogs ni discord aquí)
Autor           : RAISA Dev

Funciones exportadas
--------------------
  validar_compatibilidad_municion  — Regla absoluta de calibre + id_compat
  validar_peso_volumen             — Límites de inventario personal
  calcular_estado_general          — Estado médico calculado en runtime
  validar_nombre_banlist           — Filtro de palabras prohibidas en nombres
  validar_edad                     — Rango de edad del formulario
  validar_url_imagen               — Formato y existencia de URL de imagen
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Resultado genérico de validación
# ---------------------------------------------------------------------------

@dataclass
class ResultadoValidacion:
    """
    Resultado inmutable de cualquier validación.

    Attributes:
        ok      : True si la validación pasó.
        motivo  : Mensaje de error legible para el usuario (vacío si ok=True).
    """
    ok: bool
    motivo: str = ""

    def __bool__(self) -> bool:
        return self.ok


OK    = ResultadoValidacion(ok=True)
_FAIL = lambda motivo: ResultadoValidacion(ok=False, motivo=motivo)


# ---------------------------------------------------------------------------
# 1. Validación de compatibilidad de munición — REGLA ABSOLUTA
# ---------------------------------------------------------------------------

def validar_compatibilidad_municion(arma: Any, cargador: Any) -> ResultadoValidacion:
    """
    Verifica que un cargador sea compatible con un arma.

    REGLA ABSOLUTA: Ambas condiciones deben cumplirse simultáneamente:
      1. cargador.calibre == arma.calibre
      2. cargador.id_compatibilidad == arma.id_compatibilidad

    Si cualquiera falla → devuelve error con descripción explícita.
    Esta función no tiene excepciones ni bypass posibles.

    Args:
        arma     : Objeto o dict con campos 'calibre' e 'id_compatibilidad'.
        cargador : Objeto o dict con campos 'calibre' e 'id_compatibilidad'.

    Returns:
        ResultadoValidacion con ok=True si compatible, ok=False + motivo si no.
    """
    def _get(obj, campo):
        if isinstance(obj, dict):
            return obj.get(campo)
        return getattr(obj, campo, None)

    calibre_arma   = _get(arma,    "calibre")
    calibre_carg   = _get(cargador,"calibre")
    compat_arma    = _get(arma,    "id_compatibilidad")
    compat_carg    = _get(cargador,"id_compatibilidad")

    if calibre_arma is None or calibre_carg is None:
        return _FAIL("No se pudo determinar el calibre del arma o del cargador.")

    if calibre_arma != calibre_carg:
        return _FAIL(
            f"Calibre incompatible: el arma usa **{calibre_arma}** "
            f"pero el cargador es **{calibre_carg}**."
        )

    if compat_arma and compat_carg and compat_arma != compat_carg:
        return _FAIL(
            f"Cargador incompatible: el arma requiere compatibilidad "
            f"**{compat_arma}** pero el cargador tiene **{compat_carg}**."
        )

    return OK


# ---------------------------------------------------------------------------
# 2. Validación de peso y volumen de inventario
# ---------------------------------------------------------------------------

def validar_peso_volumen(
    peso_actual: float,
    volumen_actual: int,
    peso_item: float,
    volumen_item: int,
    cantidad: int = 1,
    peso_max: float = 40.0,
    volumen_max: int = 80,
) -> ResultadoValidacion:
    """
    Verifica que añadir un ítem no supere los límites del inventario.

    Args:
        peso_actual   : Peso actual del inventario en kg.
        volumen_actual: Volumen actual en unidades.
        peso_item     : Peso del ítem a añadir en kg.
        volumen_item  : Volumen del ítem a añadir en unidades.
        cantidad      : Cuántas unidades del ítem se añaden.
        peso_max      : Límite de peso máximo (default 40 kg).
        volumen_max   : Límite de volumen máximo.

    Returns:
        ResultadoValidacion.
    """
    nuevo_peso    = peso_actual    + (peso_item    * cantidad)
    nuevo_volumen = volumen_actual + (volumen_item * cantidad)

    errores = []
    if nuevo_peso > peso_max:
        exceso = nuevo_peso - peso_max
        errores.append(f"Peso: **{nuevo_peso:.2f} kg** excede el límite de **{peso_max} kg** (+{exceso:.2f} kg)")

    if nuevo_volumen > volumen_max:
        exceso = nuevo_volumen - volumen_max
        errores.append(f"Volumen: **{nuevo_volumen}u** excede el límite de **{volumen_max}u** (+{exceso}u)")

    if errores:
        return _FAIL("No hay espacio suficiente:\n" + "\n".join(errores))

    return OK


# ---------------------------------------------------------------------------
# 3. Cálculo de estado médico general
# ---------------------------------------------------------------------------

# Orden de evaluación: del más grave al más leve (primera coincidencia gana)
_ESTADOS_MEDICOS = [
    "Muerte clínica",
    "Crítico",
    "Grave",
    "Herido grave",
    "Herido",
    "Lesionado",
    "Operativo",
]


def calcular_estado_general(
    sangre: int,
    consciencia: str,
    heridas: list[dict],
    fracturas: list[dict],
) -> str:
    """
    Calcula el estado médico general en tiempo de lectura.

    NO se persiste en BBDD para evitar inconsistencias con los campos fuente.
    Ver REVIEW.md §1.3 para la justificación completa.

    Args:
        sangre       : Valor 0-100.
        consciencia  : 'Consciente' | 'Semiconsciente' | 'Inconsciente' | 'Clínico'.
        heridas      : Lista de dicts {tipo, localizacion, gravedad, estado_tratamiento}.
        fracturas    : Lista de dicts {miembro, tipo}.

    Returns:
        String con el estado general calculado.
    """
    # Muerte clínica
    if sangre == 0 or consciencia == "Clínico":
        return "Muerte clínica"

    # Crítico
    if sangre < 20 or consciencia == "Inconsciente":
        return "Crítico"

    # Grave
    if sangre < 40 or consciencia == "Semiconsciente":
        return "Grave"

    # Herido grave (fractura expuesta)
    if any(f.get("tipo") == "expuesta" for f in fracturas):
        return "Herido grave"

    # Herido (herida grave activa)
    if any(h.get("gravedad") == "grave" for h in heridas):
        return "Herido"

    # Lesionado (cualquier herida o fractura)
    if heridas or fracturas:
        return "Lesionado"

    return "Operativo"


# ---------------------------------------------------------------------------
# 4. Validación de nombre contra banlist
# ---------------------------------------------------------------------------

_banlist_cache: list[str] | None = None
_BANLIST_PATH = Path("config/banlist.json")


def _cargar_banlist() -> list[str]:
    """Carga y cachea la banlist desde config/banlist.json."""
    global _banlist_cache
    if _banlist_cache is not None:
        return _banlist_cache
    try:
        with _BANLIST_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
        _banlist_cache = [p.lower() for p in data.get("palabras", [])]
    except Exception:
        _banlist_cache = []
    return _banlist_cache


def invalidar_cache_banlist() -> None:
    """Invalida el caché de banlist (llamar si se modifica el archivo)."""
    global _banlist_cache
    _banlist_cache = None


def validar_nombre_banlist(nombre: str) -> ResultadoValidacion:
    """
    Verifica que el nombre de personaje no contenga palabras prohibidas.

    Args:
        nombre: Nombre completo del personaje.

    Returns:
        ResultadoValidacion.
    """
    nombre_lower = nombre.lower()
    banlist = _cargar_banlist()
    for palabra in banlist:
        if palabra in nombre_lower:
            return _FAIL(
                f"El nombre contiene una palabra no permitida. "
                f"Por favor elige otro nombre."
            )
    return OK


# ---------------------------------------------------------------------------
# 5. Validación de edad
# ---------------------------------------------------------------------------

def validar_edad(valor: str) -> ResultadoValidacion:
    """
    Valida que la edad sea un entero en el rango 18-60.
    El límite superior (60) NO se menciona en el mensaje de error al usuario.

    Args:
        valor: Texto introducido por el usuario.

    Returns:
        ResultadoValidacion.
    """
    try:
        edad = int(valor.strip())
    except ValueError:
        return _FAIL("La edad debe ser un número entero.")

    if edad < 18:
        return _FAIL("Debes tener al menos **18 años** para registrarte.")

    # Límite superior sin mencionar al usuario (spec explícito)
    if edad > 60:
        return _FAIL("La edad introducida no es válida para el registro.")

    return OK


# ---------------------------------------------------------------------------
# 6. Validación de URL de imagen
# ---------------------------------------------------------------------------

_URL_PATTERN = re.compile(
    r"^https?://.+\.(png|jpg|jpeg|webp|gif)(\?.*)?$",
    re.IGNORECASE,
)


def validar_url_imagen(url: str) -> ResultadoValidacion:
    """
    Valida el formato de una URL de imagen (extensión + protocolo).
    No hace request HTTP — solo valida formato.
    La verificación real de existencia debe hacerse con aiohttp en el cog.

    Args:
        url: URL a validar.

    Returns:
        ResultadoValidacion.
    """
    if not url or not isinstance(url, str):
        return _FAIL("No se proporcionó una URL.")

    url = url.strip()
    if not _URL_PATTERN.match(url):
        return _FAIL(
            "La URL no parece ser una imagen válida.\n"
            "Formatos admitidos: `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`\n"
            "La URL debe comenzar con `https://`."
        )
    return OK


# ---------------------------------------------------------------------------
# 7. Validación de slots de pouches
# ---------------------------------------------------------------------------

def validar_slots_pouches(
    slots_disponibles: int,
    slots_usados: int,
    slots_que_ocupa: int,
) -> ResultadoValidacion:
    """
    Verifica que haya slots suficientes en una protección antes de asignar un pouch.

    Args:
        slots_disponibles : Total de slots de la protección.
        slots_usados      : Slots ya ocupados.
        slots_que_ocupa   : Slots que consumiría el nuevo pouch.

    Returns:
        ResultadoValidacion.
    """
    libres = slots_disponibles - slots_usados
    if slots_que_ocupa > libres:
        return _FAIL(
            f"No hay suficientes slots libres.\n"
            f"Slots libres: **{libres}** | Slots requeridos: **{slots_que_ocupa}**"
        )
    return OK