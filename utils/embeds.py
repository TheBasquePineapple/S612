"""
RAISA — Builders de Embeds reutilizables
utils/embeds.py

Responsabilidad : Todas las respuestas del bot se emiten como Embeds.
                  Este módulo centraliza su construcción para coherencia visual.
Dependencias    : discord.py
Autor           : Proyecto RAISA
"""

import discord

# Paleta de colores institucional RAISA
COLOR_OK      = discord.Color.from_str("#2ecc71")   # Verde — éxito
COLOR_ERROR   = discord.Color.from_str("#e74c3c")   # Rojo — error / denegado
COLOR_AVISO   = discord.Color.from_str("#f39c12")   # Naranja — advertencia
COLOR_INFO    = discord.Color.from_str("#3498db")   # Azul — información
COLOR_NEUTRO  = discord.Color.from_str("#95a5a6")   # Gris — neutro / sistema
COLOR_MEDICO  = discord.Color.from_str("#c0392b")   # Rojo oscuro — médico
COLOR_RADIO   = discord.Color.from_str("#27ae60")   # Verde oscuro — radio
COLOR_EVENTO  = discord.Color.from_str("#8e44ad")   # Morado — evento

FOOTER_TEXT = "RAISA · Fundación SCP — Sistema de Gestión Operativa"
FOOTER_ICON = "https://i.imgur.com/placeholder.png"  # Reemplazar con icono real


def _base(titulo: str, descripcion: str, color: discord.Color) -> discord.Embed:
    """Crea un embed base con footer institucional."""
    e = discord.Embed(title=titulo, description=descripcion, color=color)
    e.set_footer(text=FOOTER_TEXT)
    return e


# ── SISTEMA ───────────────────────────────────────────────────────────────────

def embed_acceso_denegado(rango_requerido: str) -> discord.Embed:
    """Embed de acceso denegado con el rango mínimo requerido."""
    return _base(
        "🔒 Acceso denegado",
        f"No tienes autorización para ejecutar esta acción.\n"
        f"**Rango mínimo requerido:** `{rango_requerido}`",
        COLOR_ERROR,
    )


def embed_error(mensaje: str) -> discord.Embed:
    """Embed de error genérico."""
    return _base("❌ Error", mensaje, COLOR_ERROR)


def embed_ok(titulo: str, mensaje: str) -> discord.Embed:
    """Embed de confirmación / éxito."""
    return _base(f"✅ {titulo}", mensaje, COLOR_OK)


def embed_aviso(titulo: str, mensaje: str) -> discord.Embed:
    """Embed de advertencia."""
    return _base(f"⚠️ {titulo}", mensaje, COLOR_AVISO)


def embed_info(titulo: str, mensaje: str) -> discord.Embed:
    """Embed informativo."""
    return _base(f"ℹ️ {titulo}", mensaje, COLOR_INFO)


def embed_tienda_bloqueada() -> discord.Embed:
    """Embed específico para acceso a tienda en Evento-ON."""
    return _base(
        "🔒 Tienda bloqueada",
        "La tienda está **cerrada** durante los eventos activos.\n"
        "Vuelve cuando el evento haya concluido.",
        COLOR_AVISO,
    )


# ── PERSONAJE / REGISTRO ──────────────────────────────────────────────────────

def embed_ficha_personaje(datos: dict, avatar_url: str | None = None) -> discord.Embed:
    """
    Construye el embed de presentación de ficha para el canal de verificación.

    :param datos: Dict con los campos del personaje.
    :param avatar_url: URL de la imagen del personaje (opcional).
    """
    e = discord.Embed(
        title=f"📋 Nueva ficha — {datos.get('nombre')} {datos.get('apellidos')}",
        color=COLOR_INFO,
    )
    e.add_field(name="Edad", value=str(datos.get("edad", "—")), inline=True)
    e.add_field(name="Género", value=datos.get("genero", "—"), inline=True)
    e.add_field(name="Nacionalidad", value=datos.get("nacionalidad", "—"), inline=True)
    e.add_field(name="Clase", value=datos.get("clase", "—"), inline=True)
    e.add_field(name="Estudios", value=datos.get("estudios", "—"), inline=False)
    e.add_field(name="Ocupaciones previas", value=datos.get("ocupaciones", "—"), inline=False)

    if datos.get("servicio_previo"):
        e.add_field(name="Servicio previo", value=datos["servicio_previo"], inline=False)
    if datos.get("destinos"):
        e.add_field(name="Destinos / Operaciones", value=datos["destinos"], inline=False)

    e.add_field(name="Trasfondo", value=datos.get("trasfondo", "—")[:1024], inline=False)

    # Examen psicotécnico — mostrar todas las respuestas para revisión manual
    psi_respuestas: list[str] = datos.get("psi_respuestas", [])
    if psi_respuestas:
        from cogs.registro import PREGUNTAS_PSI  # importación local para evitar ciclos
        e.add_field(
            name="🧠 Examen Psicotécnico",
            value="*(Respuestas de elaboración libre — revisar manualmente)*",
            inline=False,
        )
        for idx, (pregunta, respuesta) in enumerate(
            zip(PREGUNTAS_PSI, psi_respuestas), start=1
        ):
            # Truncar pregunta a ~80 chars para que quepa como label
            label = f"P{idx}: {pregunta[:75]}…" if len(pregunta) > 75 else f"P{idx}: {pregunta}"
            e.add_field(name=label, value=respuesta[:512] or "*(sin respuesta)*", inline=False)

    if avatar_url:
        e.set_image(url=avatar_url)

    e.set_footer(text=f"RAISA · ID: {datos.get('user_id')} — Pendiente de verificación")
    return e



# ── INVENTARIO ────────────────────────────────────────────────────────────────

def embed_loadout(personaje: dict, loadout: dict, items: dict[int, dict]) -> discord.Embed:
    """
    Construye el embed de visualización del loadout.

    :param personaje: Dict del personaje.
    :param loadout: Dict de la fila de loadout.
    :param items: Dict {item_uuid: datos_item} para resolver nombres.
    """
    def nombre_item(item_id: int | None) -> str:
        if not item_id:
            return "*Vacío*"
        item = items.get(item_id)
        return item["nombre"] if item else f"[ID:{item_id}]"

    e = discord.Embed(
        title=f"🎒 Loadout — {personaje['nombre']} {personaje['apellidos']}",
        color=COLOR_NEUTRO,
    )

    # Armas
    e.add_field(
        name="🔫 Armas",
        value=(
            f"**Primaria:** {nombre_item(loadout.get('arma_primaria_id'))}\n"
            f"**Secundaria:** {nombre_item(loadout.get('arma_secundaria_id'))}\n"
            f"**Terciaria:** {nombre_item(loadout.get('arma_terciaria_id'))}"
        ),
        inline=True,
    )

    # Protecciones
    e.add_field(
        name="🛡️ Protecciones",
        value=(
            f"**Chaleco:** {nombre_item(loadout.get('chaleco_id'))}\n"
            f"**Portaplacas:** {nombre_item(loadout.get('portaplacas_id'))}\n"
            f"**Placas:** {nombre_item(loadout.get('placas_id'))}\n"
            f"**Soportes:** {nombre_item(loadout.get('soportes_id'))}\n"
            f"**Casco:** {nombre_item(loadout.get('casco_id'))}"
        ),
        inline=True,
    )

    # Uniformidad
    e.add_field(
        name="👕 Uniformidad",
        value=(
            f"**Pantalón:** {nombre_item(loadout.get('pantalon_id'))}\n"
            f"**Camisa:** {nombre_item(loadout.get('camisa_id'))}\n"
            f"**Chaqueta:** {nombre_item(loadout.get('chaqueta_id'))}\n"
            f"**Botas:** {nombre_item(loadout.get('botas_id'))}\n"
            f"**Guantes:** {nombre_item(loadout.get('guantes_id'))}\n"
            f"**Reloj:** {nombre_item(loadout.get('reloj_id'))}"
        ),
        inline=False,
    )

    # Accesorios
    e.add_field(
        name="🎽 Accesorios",
        value=(
            f"**Mochila:** {nombre_item(loadout.get('mochila_id'))}\n"
            f"**Cinturón:** {nombre_item(loadout.get('cinturon_id'))}\n"
            f"**Radio:** {nombre_item(loadout.get('radio_id'))}"
        ),
        inline=True,
    )

    # Parche (imagen si hay URL)
    if loadout.get("parche_url"):
        e.add_field(name="🔖 Parche", value=loadout["parche_url"], inline=True)
        e.set_thumbnail(url=loadout["parche_url"])

    e.set_footer(text=FOOTER_TEXT)
    return e


# ── ESTADO MÉDICO ─────────────────────────────────────────────────────────────

def embed_estado_medico(personaje: dict, medico: dict) -> discord.Embed:
    """
    Construye el embed del estado médico de un personaje.

    :param personaje: Dict del personaje.
    :param medico: Dict del estado médico (heridas y fracturas ya deserializadas).
    """
    e = discord.Embed(
        title=f"🏥 Estado Médico — {personaje['nombre']} {personaje['apellidos']}",
        color=COLOR_MEDICO,
    )

    # Indicadores vitales
    sangre = medico.get("sangre", 100)
    barra = _barra_progreso(sangre, 100, 10)
    e.add_field(
        name="🩸 Sangre",
        value=f"{barra} `{sangre}%`",
        inline=False,
    )
    e.add_field(name="🧠 Consciencia", value=medico.get("consciencia", "—"), inline=True)
    e.add_field(name="📊 Estado general", value=medico.get("estado_general", "—"), inline=True)

    # Heridas
    heridas = medico.get("heridas", [])
    if heridas:
        heridas_txt = "\n".join(
            f"• **{h.get('tipo','?')}** en {h.get('localizacion','?')} "
            f"— {h.get('gravedad','?')} [{h.get('estado_tratamiento','?')}]"
            for h in heridas
        )
        e.add_field(name=f"🩹 Heridas ({len(heridas)})", value=heridas_txt[:1024], inline=False)
    else:
        e.add_field(name="🩹 Heridas", value="Ninguna registrada", inline=False)

    # Fracturas
    fracturas = medico.get("fracturas", [])
    if fracturas:
        frac_txt = "\n".join(
            f"• {f.get('miembro','?')} — {f.get('tipo','?')}" for f in fracturas
        )
        e.add_field(name=f"🦴 Fracturas ({len(fracturas)})", value=frac_txt[:512], inline=False)

    e.set_footer(
        text=f"RAISA · Última actualización: {medico.get('ultima_actualizacion','—')}"
    )
    return e


# ── VEHÍCULOS ─────────────────────────────────────────────────────────────────

def embed_vehiculo(v: dict) -> discord.Embed:
    """Construye el embed de estado de un vehículo."""
    e = discord.Embed(
        title=f"🚗 {v['nombre']}  —  {v['tipo'].replace('_',' ').title()}",
        color=COLOR_INFO,
    )
    e.add_field(name="Matrícula", value=v.get("matricula") or "—", inline=True)
    e.add_field(name="Estado", value=v.get("estado_general", "—"), inline=True)
    e.add_field(name="Asientos", value=str(v.get("asientos", "—")), inline=True)

    comb_actual = v.get("combustible_actual", 0)
    comb_max    = v.get("combustible_max", 1)
    barra_comb  = _barra_progreso(comb_actual, comb_max, 10)
    e.add_field(
        name="⛽ Combustible",
        value=f"{barra_comb} `{comb_actual:.0f}/{comb_max:.0f} L`",
        inline=False,
    )

    componentes = v.get("componentes_json", {})
    if isinstance(componentes, dict) and componentes:
        comp_txt = "\n".join(f"• {k}: {val}" for k, val in componentes.items())
        e.add_field(name="🔧 Componentes", value=comp_txt[:1024], inline=False)

    e.set_footer(text=f"RAISA · ID vehículo: {v['vehiculo_id']}")
    return e


# ── RADIO ─────────────────────────────────────────────────────────────────────

def embed_radio_sin_equipo() -> discord.Embed:
    """Embed de error para usuario sin radio equipada."""
    return _base(
        "📻 Sin radio",
        "No tienes ninguna **radio** equipada en tu loadout.\n"
        "Equipa una radio en el slot correspondiente para acceder a las comunicaciones.",
        COLOR_ERROR,
    )


def embed_radio_canal(canal_nombre: str, unidad: str | None) -> discord.Embed:
    """Embed de confirmación de cambio de canal."""
    unit_txt = f" — Unidad: `{unidad}`" if unidad else ""
    return _base(
        "📻 Canal de radio activo",
        f"Conectado a **{canal_nombre}**{unit_txt}.",
        COLOR_RADIO,
    )


# ── EVENTOS ───────────────────────────────────────────────────────────────────

def embed_estado_evento(modo: str, activado_por: int | None = None) -> discord.Embed:
    """Embed de estado actual del evento."""
    if modo == "ON":
        titulo  = "🔴 EVENTO-ON — Activo"
        desc    = "El evento está en curso.\n• Tienda **bloqueada**\n• Zonas seguras **inactivas**\n• Inventario: solo personal y vehículos"
        color   = COLOR_ERROR
    else:
        titulo  = "🟢 EVENTO-OFF — Inactivo"
        desc    = "No hay evento en curso.\n• Tienda **operativa**\n• Zonas seguras **activas**\n• Inventario general **accesible**"
        color   = COLOR_OK

    e = _base(titulo, desc, color)
    if activado_por:
        e.set_footer(text=f"RAISA · Activado por ID: {activado_por}")
    return e


# ── UTILIDADES INTERNAS ───────────────────────────────────────────────────────

def _barra_progreso(actual: float, maximo: float, longitud: int = 10) -> str:
    """
    Genera una barra de progreso de texto.

    :param actual: Valor actual.
    :param maximo: Valor máximo.
    :param longitud: Número de bloques totales.
    :returns: Cadena tipo '██████░░░░'.
    """
    if maximo <= 0:
        return "░" * longitud
    fraccion = max(0.0, min(1.0, actual / maximo))
    llenos   = round(fraccion * longitud)
    return "█" * llenos + "░" * (longitud - llenos)
