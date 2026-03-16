"""
utils/embeds.py — Constructores de Embeds reutilizables de RAISA
================================================================
Responsabilidad : Centralizar la creación de todos los Embeds del bot.
                  NUNCA responder fuera de Embeds (salvo mensajes técnicos
                  internos de Discord como errors de rate-limit).
Dependencias    : discord.py >= 2.0
Autor           : RAISA Dev

Paleta de colores
-----------------
  VERDE    0x2ECC71  — éxito, aprobación, operativo
  ROJO     0xE74C3C  — error, denegado, crítico
  NARANJA  0xE67E22  — advertencia, pendiente
  AZUL     0x3498DB  — información general
  GRIS     0x95A5A6  — estado neutral / desactivado
  NEGRO    0x2C2F33  — embeds oscuros / narrativos
  DORADO   0xF1C40F  — económico, recompensa
  CIAN     0x1ABC9C  — médico, sanitario
  MORADO   0x9B59B6  — SUDO / seguridad
"""

import discord

# ---------------------------------------------------------------------------
# Colores
# ---------------------------------------------------------------------------
C_OK      = 0x2ECC71
C_ERROR   = 0xE74C3C
C_WARN    = 0xE67E22
C_INFO    = 0x3498DB
C_NEUTRAL = 0x95A5A6
C_DARK    = 0x2C2F33
C_GOLD    = 0xF1C40F
C_TEAL    = 0x1ABC9C
C_PURPLE  = 0x9B59B6

FOOTER_TEXT = "RAISA · Sistema de Gestión Operativa · Fundación SCP"


def _base(title: str, description: str, color: int) -> discord.Embed:
    """Crea un embed base con footer estándar de RAISA."""
    e = discord.Embed(title=title, description=description, color=color)
    e.set_footer(text=FOOTER_TEXT)
    return e


# ---------------------------------------------------------------------------
# Embeds genéricos
# ---------------------------------------------------------------------------

def ok(titulo: str, descripcion: str = "") -> discord.Embed:
    """Embed de éxito (verde)."""
    return _base(f"✅  {titulo}", descripcion, C_OK)


def error(titulo: str, descripcion: str = "") -> discord.Embed:
    """Embed de error (rojo)."""
    return _base(f"❌  {titulo}", descripcion, C_ERROR)


def advertencia(titulo: str, descripcion: str = "") -> discord.Embed:
    """Embed de advertencia (naranja)."""
    return _base(f"⚠️  {titulo}", descripcion, C_WARN)


def info(titulo: str, descripcion: str = "") -> discord.Embed:
    """Embed informativo (azul)."""
    return _base(f"ℹ️  {titulo}", descripcion, C_INFO)


def acceso_denegado(rango_requerido: str) -> discord.Embed:
    """
    Embed de acceso denegado. Indica el rango mínimo sin exponer detalles técnicos.

    Args:
        rango_requerido: Nombre del rango mínimo requerido para la acción.
    """
    return _base(
        "Acceso denegado",
        f"No tienes autorización para ejecutar esta acción.\n"
        f"**Rango mínimo requerido:** {rango_requerido}",
        C_ERROR,
    )


def evento_bloqueado(accion: str) -> discord.Embed:
    """Embed para operaciones bloqueadas durante Evento-ON."""
    return _base(
        "Operación no disponible",
        f"**{accion}** no está disponible mientras hay un Evento activo.\n"
        "Espera a que el Narrador declare Evento-OFF.",
        C_WARN,
    )


# ---------------------------------------------------------------------------
# Embeds de registro
# ---------------------------------------------------------------------------

def formulario_inicio(paso: int, total: int, pregunta: str) -> discord.Embed:
    """
    Embed para cada pregunta del formulario de registro.

    Args:
        paso     : Número de paso actual (1-12).
        total    : Total de pasos.
        pregunta : Texto de la pregunta a mostrar.
    """
    e = discord.Embed(
        title=f"📋  Formulario de Registro — Paso {paso}/{total}",
        description=pregunta,
        color=C_INFO,
    )
    e.set_footer(text=f"{FOOTER_TEXT} | Tienes 20 minutos para responder.")
    return e


def formulario_suspendido() -> discord.Embed:
    """Embed para notificar que el formulario fue suspendido por inactividad."""
    return _base(
        "Formulario suspendido",
        "Tu formulario de registro quedó suspendido por inactividad (20 minutos).\n"
        "Usa `/registro` para retomarlo desde donde lo dejaste.",
        C_WARN,
    )


def ficha_verificacion(datos: dict) -> discord.Embed:
    """
    Embed de ficha de personaje para el canal de verificación.

    Args:
        datos: Dict con todos los campos del personaje.
    """
    e = discord.Embed(
        title="📄  Ficha pendiente de verificación",
        color=C_DARK,
    )
    e.add_field(name="Nombre",        value=datos.get("nombre_completo", "—"), inline=True)
    e.add_field(name="Edad",          value=str(datos.get("edad", "—")),       inline=True)
    e.add_field(name="Género",        value=datos.get("genero", "—"),          inline=True)
    e.add_field(name="Nacionalidad",  value=datos.get("nacionalidad", "—"),    inline=True)
    e.add_field(name="Clase",         value=datos.get("clase", "—"),           inline=True)
    e.add_field(name="Psicotécnico",  value=datos.get("resultado_psico", "—"), inline=True)

    if datos.get("servicio_previo"):
        e.add_field(name="Servicio previo",   value=datos["servicio_previo"],       inline=False)
    if datos.get("destinos_ops"):
        e.add_field(name="Destinos/Ops",      value=datos["destinos_ops"],          inline=False)

    e.add_field(name="Estudios",             value=datos.get("estudios", "—"),       inline=False)
    e.add_field(name="Ocupaciones previas",  value=datos.get("ocupaciones_previas","—"), inline=False)
    e.add_field(name="Trasfondo",            value=datos.get("trasfondo", "—")[:1024], inline=False)

    if datos.get("avatar_path"):
        e.set_thumbnail(url=f"attachment://avatar.png")

    e.set_footer(text=f"{FOOTER_TEXT} | Discord ID: {datos.get('user_id', '—')}")
    return e


# ---------------------------------------------------------------------------
# Embeds de inventario
# ---------------------------------------------------------------------------

def loadout(personaje: str, slots: dict) -> discord.Embed:
    """
    Embed mostrando el loadout completo de un personaje.

    Args:
        personaje : Nombre del personaje.
        slots     : Dict {nombre_slot: nombre_item_o_vacío}.
    """
    e = discord.Embed(title=f"🎒  Loadout — {personaje}", color=C_DARK)

    armas = {
        "Primaria":   slots.get("primaria",   "— Vacío"),
        "Secundaria": slots.get("secundaria", "— Vacío"),
        "Terciaria":  slots.get("terciaria",  "— Vacío"),
    }
    e.add_field(
        name="🔫 Armas",
        value="\n".join(f"**{k}:** {v}" for k, v in armas.items()),
        inline=False,
    )

    prot = {
        "Chaleco":      slots.get("chaleco",      "— Vacío"),
        "Portaplacas":  slots.get("portaplacas",  "— Vacío"),
        "Placas":       slots.get("placas",       "— Vacío"),
        "Soporte":      slots.get("soporte",      "— Vacío"),
        "Casco":        slots.get("casco",        "— Vacío"),
    }
    e.add_field(
        name="🛡️ Protecciones",
        value="\n".join(f"**{k}:** {v}" for k, v in prot.items()),
        inline=False,
    )

    uniforme = {
        "Pantalón": slots.get("pantalon",  "—"),
        "Camisa":   slots.get("camisa",    "—"),
        "Chaqueta": slots.get("chaqueta",  "—"),
        "Botas":    slots.get("botas",     "—"),
        "Guantes":  slots.get("guantes",   "—"),
        "Reloj":    slots.get("reloj",     "—"),
    }
    e.add_field(
        name="👕 Uniformidad",
        value="\n".join(f"**{k}:** {v}" for k, v in uniforme.items()),
        inline=True,
    )

    accesorios = {
        "Mochila":   slots.get("mochila",   "—"),
        "Cinturón":  slots.get("cinturon",  "—"),
        "Radio":     slots.get("radio",     "—"),
    }
    e.add_field(
        name="🎽 Accesorios",
        value="\n".join(f"**{k}:** {v}" for k, v in accesorios.items()),
        inline=True,
    )

    if slots.get("parche_url"):
        e.set_image(url=slots["parche_url"])

    e.set_footer(text=FOOTER_TEXT)
    return e


def inventario_general(personaje: str, items: list[dict], peso: float, vol: int,
                        peso_max: float, vol_max: int) -> discord.Embed:
    """
    Embed del inventario general (almacén personal).

    Args:
        personaje : Nombre del personaje.
        items     : Lista de dicts {nombre, cantidad, peso_kg, estado}.
        peso      : Peso total actual en kg.
        vol       : Volumen total actual en unidades.
        peso_max  : Límite de peso.
        vol_max   : Límite de volumen.
    """
    e = discord.Embed(
        title=f"📦  Inventario General — {personaje}",
        color=C_INFO,
    )
    if not items:
        e.description = "*Inventario vacío.*"
    else:
        lineas = [
            f"• **{it['nombre']}** ×{it['cantidad']} — {it['peso_kg']*it['cantidad']:.2f}kg"
            + (f" _(dañado)_" if it.get("estado") == "dañado" else "")
            for it in items
        ]
        e.description = "\n".join(lineas)

    e.add_field(
        name="Capacidad",
        value=f"Peso: **{peso:.1f}** / {peso_max} kg\nVolumen: **{vol}** / {vol_max} u",
        inline=False,
    )
    e.set_footer(text=FOOTER_TEXT)
    return e


# ---------------------------------------------------------------------------
# Embeds médicos
# ---------------------------------------------------------------------------

_ESTADO_COLOR = {
    "Operativo":      C_OK,
    "Lesionado":      C_WARN,
    "Herido":         C_WARN,
    "Herido grave":   C_ERROR,
    "Grave":          C_ERROR,
    "Crítico":        C_ERROR,
    "Muerte clínica": 0x000000,
}


def estado_medico(personaje: str, estado: dict) -> discord.Embed:
    """
    Embed del estado médico completo de un personaje.

    Args:
        personaje : Nombre del personaje.
        estado    : Dict con campos de medical_state + estado_general calculado.
    """
    general = estado.get("estado_general", "Desconocido")
    color   = _ESTADO_COLOR.get(general, C_NEUTRAL)

    e = discord.Embed(
        title=f"🏥  Estado médico — {personaje}",
        color=color,
    )
    e.add_field(name="Estado general",  value=f"**{general}**",                   inline=True)
    e.add_field(name="Consciencia",     value=estado.get("consciencia", "—"),     inline=True)
    e.add_field(name="Sangre",          value=f"{estado.get('sangre', 0)}%",      inline=True)

    heridas = estado.get("heridas", [])
    if heridas:
        texto_heridas = "\n".join(
            f"• {h.get('tipo','?')} en {h.get('localizacion','?')} "
            f"({h.get('gravedad','?')}) — {h.get('estado_tratamiento','sin tratar')}"
            for h in heridas
        )
        e.add_field(name="🩹 Heridas activas", value=texto_heridas[:1024], inline=False)
    else:
        e.add_field(name="🩹 Heridas activas", value="*Ninguna*", inline=False)

    fracturas = estado.get("fracturas", [])
    if fracturas:
        texto_frac = "\n".join(
            f"• {f.get('miembro','?')} — {f.get('tipo','?')}"
            for f in fracturas
        )
        e.add_field(name="🦴 Fracturas", value=texto_frac, inline=False)

    e.set_footer(text=FOOTER_TEXT)
    return e


# ---------------------------------------------------------------------------
# Embeds de radio
# ---------------------------------------------------------------------------

def radio_estado(encendida: bool, canal: str | None, tiene_radio: bool) -> discord.Embed:
    """
    Embed del estado actual de la radio del usuario.

    Args:
        encendida   : True si la radio está encendida.
        canal       : Nombre del canal activo, o None.
        tiene_radio : True si el usuario tiene radio equipada.
    """
    if not tiene_radio:
        return _base("📻  Radio", "No tienes una radio equipada en tu loadout.", C_ERROR)

    estado = "🟢 **Encendida**" if encendida else "🔴 **Apagada**"
    canal_txt = canal if canal else "—"

    e = _base("📻  Estado de Radio", "", C_TEAL if encendida else C_NEUTRAL)
    e.add_field(name="Estado",  value=estado,    inline=True)
    e.add_field(name="Canal",   value=canal_txt, inline=True)
    return e


def radio_sin_equipo() -> discord.Embed:
    """Embed de error cuando el usuario intenta usar radio sin tenerla equipada."""
    return error(
        "Sin radio equipada",
        "Debes tener una radio en el slot de radio de tu loadout para usar este sistema.",
    )


# ---------------------------------------------------------------------------
# Embeds económicos
# ---------------------------------------------------------------------------

def saldo(personaje: str, cantidad: float, simbolo: str = "₢") -> discord.Embed:
    """
    Embed mostrando el saldo económico de un personaje.

    Args:
        personaje : Nombre del personaje.
        cantidad  : Saldo actual.
        simbolo   : Símbolo de la moneda.
    """
    e = _base(
        f"💰  Saldo — {personaje}",
        f"**{cantidad:,.2f} {simbolo}**",
        C_GOLD,
    )
    return e


def tienda_listado(items: list[dict], pagina: int, total_paginas: int,
                   simbolo: str = "₢") -> discord.Embed:
    """
    Embed con el catálogo de la tienda.

    Args:
        items         : Lista de dicts {nombre, precio, stock, descripcion}.
        pagina        : Página actual (1-indexed).
        total_paginas : Total de páginas.
        simbolo       : Símbolo de la moneda.
    """
    e = discord.Embed(
        title="🏪  Tienda — Catálogo",
        color=C_GOLD,
    )
    for it in items:
        stock_txt = "∞" if it.get("stock", -1) == -1 else str(it["stock"])
        e.add_field(
            name=f"{it['nombre']}",
            value=f"Precio: **{it['precio']:,.0f} {simbolo}** | Stock: {stock_txt}\n"
                  f"_{it.get('descripcion','')[:80]}_",
            inline=False,
        )
    e.set_footer(text=f"{FOOTER_TEXT} | Página {pagina}/{total_paginas}")
    return e


# ---------------------------------------------------------------------------
# Embeds de vehículos
# ---------------------------------------------------------------------------

def ficha_vehiculo(v: dict) -> discord.Embed:
    """
    Embed con la ficha técnica de un vehículo.

    Args:
        v: Dict con todos los campos del vehículo (de la BBDD).
    """
    import json as _json

    e = discord.Embed(title=f"🚗  {v.get('nombre','—')}", color=C_DARK)
    e.add_field(name="Tipo",          value=v.get("tipo","—"),          inline=True)
    e.add_field(name="Estado",        value=v.get("estado_general","—"),inline=True)
    e.add_field(name="Asientos",      value=str(v.get("asientos","—")), inline=True)
    e.add_field(
        name="Combustible",
        value=f"{v.get('combustible_actual',0):.0f} / {v.get('combustible_max',0):.0f} L",
        inline=True,
    )
    e.add_field(
        name="Inventario",
        value=f"Peso máx: {v.get('inv_peso_max_kg',0)} kg",
        inline=True,
    )

    comp = v.get("componentes") or {}
    if isinstance(comp, str):
        try:
            comp = _json.loads(comp)
        except Exception:
            comp = {}
    if comp:
        estado_comp = "\n".join(f"• {k}: {val}" for k, val in comp.items())
        e.add_field(name="🔧 Componentes", value=estado_comp[:1024], inline=False)

    mun = v.get("municion_json") or {}
    if isinstance(mun, str):
        try:
            mun = _json.loads(mun)
        except Exception:
            mun = {}
    if mun:
        mun_txt = "\n".join(
            f"• {arma}: {d.get('cargado',0)}/{d.get('max',0)} ({d.get('calibre','?')})"
            for arma, d in mun.items()
        )
        e.add_field(name="🔫 Munición", value=mun_txt[:1024], inline=False)

    e.set_footer(text=FOOTER_TEXT)
    return e


# ---------------------------------------------------------------------------
# Embeds de eventos
# ---------------------------------------------------------------------------

def evento_on(descripcion: str, activado_por: str) -> discord.Embed:
    """Embed de anuncio de inicio de Evento-ON."""
    e = _base(
        "🔴  EVENTO ACTIVO",
        f"**{descripcion}**\n\n"
        "• Tienda: **bloqueada**\n"
        "• Zonas seguras: **inactivas**\n"
        "• Acceso a inventario general: **restringido**",
        C_ERROR,
    )
    e.set_footer(text=f"{FOOTER_TEXT} | Activado por {activado_por}")
    return e


def evento_off(activado_por: str) -> discord.Embed:
    """Embed de anuncio de fin de evento (Evento-OFF)."""
    e = _base(
        "🟢  EVENTO FINALIZADO",
        "• Tienda: **operativa**\n"
        "• Zonas seguras: **activas**\n"
        "• Acceso a inventario: **completo**",
        C_OK,
    )
    e.set_footer(text=f"{FOOTER_TEXT} | Desactivado por {activado_por}")
    return e


# ---------------------------------------------------------------------------
# Embeds de SUDO
# ---------------------------------------------------------------------------

def sudo_solicitud() -> discord.Embed:
    """Embed enviado por MD cuando se solicita autenticación SUDO."""
    return _base(
        "🔐  Autenticación SUDO",
        "Responde a este mensaje con tu **clave SUDO**.\n"
        "Tienes 2 minutos. Este mensaje se autodestruirá tras la verificación.\n\n"
        "_Nunca compartas tu clave SUDO con nadie._",
        C_PURPLE,
    )


def sudo_ok(minutos: int = 30) -> discord.Embed:
    """Embed de confirmación de sesión SUDO activada."""
    return ok("Sesión SUDO activada", f"Tendrás acceso SUDO durante **{minutos} minutos**.")


def sudo_fail() -> discord.Embed:
    """Embed de error de autenticación SUDO."""
    return error("Autenticación fallida", "Clave incorrecta. El intento ha sido registrado.")


def sudo_activa(segundos_restantes: int) -> discord.Embed:
    """Embed informando del tiempo restante de la sesión SUDO activa."""
    minutos = segundos_restantes // 60
    segs    = segundos_restantes % 60
    return info(
        "Sesión SUDO activa",
        f"Tu sesión SUDO expira en **{minutos}m {segs}s**.",
    )