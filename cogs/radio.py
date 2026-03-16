"""
cogs/radio.py — Sistema de radio de RAISA
==========================================
Responsabilidad : Gestión del sistema de comunicaciones por radio.
                  Webhooks dinámicos, frecuencias, estática y unidades.
Dependencias    : db.repository, utils.embeds, utils.permisos, utils.logger
Autor           : RAISA Dev

Comandos
--------
  /radio encendida             — Activar radio (Usuario+)
  /radio apagada               — Apagar radio (Usuario+)
  /radio canal [freq]          — Cambiar canal activo (Usuario+)
  /radio toda-malla [msg]      — Enviar a Intercom (Usuario+)
  /radio estado                — Ver estado de la radio propia (Usuario+)
  /radio estatica [canal] [on/off] — Activar/desactivar estática (Narrador+)
  /radio unidad [usuario] [unidad] — Asignar unidad radio (Narrador+)
"""

import json
import random
import re
import string
from pathlib import Path

import aiohttp
import discord
from discord import Interaction, app_commands
from discord.ext import commands

from db import repository as repo
from utils import embeds as emb
from utils.logger import audit, log_info, log_error, log_warning
from utils.permisos import NARRADOR, USUARIO, get_user_level, require_role

# ---------------------------------------------------------------------------
# Carga de configuración de radio
# ---------------------------------------------------------------------------

def _cargar_radio_config() -> dict:
    """Carga config/radio.json con fallback a config vacía."""
    try:
        with Path("config/radio.json").open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"frecuencias": {}, "intercom": {}}


# ---------------------------------------------------------------------------
# Filtro de estática — corrompe texto parcialmente
# ---------------------------------------------------------------------------

_STATIC_CHARS = ["█", "▓", "░", "▒", "■", "□", "?", "#", "*"]


def _aplicar_estatica(texto: str, intensidad: float = 0.3) -> str:
    """
    Aplica un filtro de corrupción de texto simulando interferencia de radio.

    Args:
        texto      : Texto original del mensaje.
        intensidad : Proporción de caracteres a corromper (0.0-1.0).

    Returns:
        Texto con caracteres aleatorios sustituidos.
    """
    chars = list(texto)
    n_corrupto = int(len(chars) * intensidad)
    indices = random.sample(range(len(chars)), min(n_corrupto, len(chars)))
    for i in indices:
        if chars[i] != " ":
            chars[i] = random.choice(_STATIC_CHARS)
    return "".join(chars)


# ---------------------------------------------------------------------------
# Gestión de webhooks
# ---------------------------------------------------------------------------

class RadioCog(commands.Cog, name="Radio"):
    """Cog del sistema de radio."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot       = bot
        self.cfg       = _cargar_radio_config()
        # Caché en memoria de webhooks: {channel_id: discord.Webhook}
        # Sincronizado con BBDD. Ver REVIEW.md §2.3
        self._wh_cache: dict[int, discord.Webhook] = {}

    # -----------------------------------------------------------------------
    # Helper: obtener o crear webhook para un canal
    # Ver REVIEW.md §1.6 — límite de 10 webhooks por canal
    # -----------------------------------------------------------------------

    async def _get_or_create_webhook(
        self, channel: discord.TextChannel
    ) -> discord.Webhook | None:
        """
        Obtiene el webhook del bot para un canal. Lo crea si no existe.
        Gestiona el límite de 10 webhooks por canal de Discord.

        Args:
            channel: Canal de texto de Discord.

        Returns:
            Objeto Webhook o None si no fue posible obtenerlo.
        """
        channel_id = channel.id

        # 1. Buscar en caché en memoria
        if channel_id in self._wh_cache:
            return self._wh_cache[channel_id]

        # 2. Buscar en BBDD
        async with await repo.get_conn() as conn:
            cached = await repo.get_webhook_cache(conn, channel_id)

        if cached:
            try:
                wh = discord.Webhook.from_url(
                    cached["webhook_url"],
                    session=self.bot.http._HTTPClient__session,
                )
                self._wh_cache[channel_id] = wh
                return wh
            except Exception:
                # URL inválida → limpiar caché y recrear
                async with await repo.get_conn() as conn:
                    await repo.delete_webhook_cache(conn, channel_id)

        # 3. Buscar webhook del bot en el canal
        try:
            webhooks = await channel.webhooks()
        except discord.Forbidden:
            log_warning(f"[RADIO] Sin permisos para listar webhooks en {channel.id}")
            return None

        bot_webhook = next(
            (wh for wh in webhooks if wh.user and wh.user.id == self.bot.user.id),
            None,
        )

        if bot_webhook:
            self._wh_cache[channel_id] = bot_webhook
            async with await repo.get_conn() as conn:
                await repo.upsert_webhook_cache(conn, channel_id,
                                                 bot_webhook.id, bot_webhook.url)
            return bot_webhook

        # 4. No existe → crear nuevo
        # Verificar límite de 10 webhooks por canal (límite de Discord API)
        if len(webhooks) >= 10:
            # Intentar eliminar el más antiguo que sea del bot (no debe haber si llegamos aquí)
            # Si todos son de terceros, emitir error
            bot_webhooks = [wh for wh in webhooks if wh.user and wh.user.id == self.bot.user.id]
            if not bot_webhooks:
                log_error(
                    f"[RADIO] Canal {channel.id} tiene 10 webhooks externos. "
                    "No se puede crear webhook de RAISA."
                )
                return None

        try:
            avatar_bytes = None
            avatar_path = Path("assets/radio/avatar_default.png")
            if avatar_path.exists():
                avatar_bytes = avatar_path.read_bytes()

            new_wh = await channel.create_webhook(
                name="RAISA-Radio",
                avatar=avatar_bytes,
                reason="Sistema de radio RAISA — creación automática",
            )
            self._wh_cache[channel_id] = new_wh
            async with await repo.get_conn() as conn:
                await repo.upsert_webhook_cache(conn, channel_id, new_wh.id, new_wh.url)
            log_info(f"[RADIO] Webhook creado en canal {channel_id}")
            return new_wh

        except discord.Forbidden:
            log_error(f"[RADIO] Sin permisos para crear webhook en canal {channel_id}")
            return None
        except Exception as exc:
            log_error(f"[RADIO] Error creando webhook en {channel_id}: {exc}")
            return None

    # -----------------------------------------------------------------------
    # Helper: enviar mensaje por radio como webhook
    # -----------------------------------------------------------------------

    async def _enviar_por_radio(
        self,
        channel: discord.TextChannel,
        user: discord.Member,
        mensaje: str,
        char_nombre: str,
        avatar_url: str | None,
        unidad: str | None,
        estatica: bool,
    ) -> bool:
        """
        Envía un mensaje de radio usando webhook con la identidad del personaje.

        Args:
            channel    : Canal de Discord donde enviar.
            user       : Miembro de Discord que envía.
            mensaje    : Texto del mensaje.
            char_nombre: Nombre del personaje (usado como nombre del webhook).
            avatar_url : URL del avatar del personaje (None = avatar del bot).
            unidad     : Designación de unidad radio (ej: 'Bravo 5-3').
            estatica   : Si True, aplica filtro de corrupción al texto.

        Returns:
            True si el envío fue exitoso.
        """
        webhook = await self._get_or_create_webhook(channel)
        if not webhook:
            return False

        texto_final = mensaje
        if estatica:
            texto_final = _aplicar_estatica(mensaje, intensidad=0.35)

        prefijo = f"[**{unidad}**] " if unidad else ""
        contenido = f"{prefijo}{texto_final}"

        # Nombre del webhook = apodo del usuario en el servidor
        nombre_webhook = user.display_name
        if char_nombre:
            nombre_webhook = char_nombre

        try:
            await webhook.send(
                content  = contenido,
                username = nombre_webhook,
                avatar_url = avatar_url or discord.utils.MISSING,
            )
            return True
        except discord.HTTPException as exc:
            if exc.status == 404:
                # Webhook fue eliminado externamente → limpiar caché
                self._wh_cache.pop(channel.id, None)
                async with await repo.get_conn() as conn:
                    await repo.delete_webhook_cache(conn, channel.id)
                log_warning(f"[RADIO] Webhook eliminado externamente en canal {channel.id}")
            else:
                log_error(f"[RADIO] Error enviando mensaje de radio: {exc}")
            return False

    # -----------------------------------------------------------------------
    # Helper: verificar que el usuario tiene radio equipada
    # -----------------------------------------------------------------------

    async def _verificar_radio(self, user_id: int) -> tuple[bool, dict | None]:
        """
        Verifica que el usuario tiene radio equipada y activa.

        Args:
            user_id: discord user_id.

        Returns:
            Tupla (tiene_radio_encendida, radio_state_dict | None).
        """
        async with await repo.get_conn() as conn:
            radio_state = await repo.get_radio_state(conn, user_id)

        if not radio_state:
            return False, None
        return (
            bool(radio_state["tiene_radio"]) and bool(radio_state["encendida"]),
            dict(radio_state),
        )

    # -----------------------------------------------------------------------
    # Grupo de comandos /radio
    # -----------------------------------------------------------------------

    radio_group = app_commands.Group(
        name="radio", description="Sistema de comunicaciones por radio"
    )

    @radio_group.command(name="encendida", description="Activar la radio")
    @require_role(USUARIO)
    async def radio_on(self, interaction: Interaction) -> None:
        """
        Enciende la radio del personaje. Requiere radio equipada en el loadout.

        Args:
            interaction: Contexto de Discord.
        """
        async with await repo.get_conn() as conn:
            radio_state = await repo.get_radio_state(conn, interaction.user.id)

            if not radio_state or not radio_state["tiene_radio"]:
                await interaction.response.send_message(
                    embed=emb.radio_sin_equipo(), ephemeral=True
                )
                return

            if radio_state["encendida"]:
                await interaction.response.send_message(
                    embed=emb.advertencia("Radio ya encendida",
                                          "Tu radio ya está encendida."),
                    ephemeral=True,
                )
                return

            # Restaurar último canal activo si existe
            canal_restaurado = radio_state["canal_activo"] or "intercom"
            await repo.upsert_radio_state(
                conn, interaction.user.id,
                campos={"encendida": 1, "canal_activo": canal_restaurado},
            )

        # Asignar rol de radio en Discord si está configurado
        await self._sync_radio_roles(interaction.user, canal_restaurado, activo=True)

        await interaction.response.send_message(
            embed=emb.radio_estado(True, canal_restaurado, True),
            ephemeral=True,
        )

    @radio_group.command(name="apagada", description="Apagar la radio")
    @require_role(USUARIO)
    async def radio_off(self, interaction: Interaction) -> None:
        """
        Apaga la radio y retira los roles de canal e Intercom.

        Args:
            interaction: Contexto de Discord.
        """
        async with await repo.get_conn() as conn:
            radio_state = await repo.get_radio_state(conn, interaction.user.id)
            if not radio_state or not radio_state["encendida"]:
                await interaction.response.send_message(
                    embed=emb.advertencia("Radio ya apagada", "Tu radio ya está apagada."),
                    ephemeral=True,
                )
                return

            await repo.upsert_radio_state(
                conn, interaction.user.id,
                campos={"encendida": 0},
            )

        await self._sync_radio_roles(interaction.user, None, activo=False)

        await interaction.response.send_message(
            embed=emb.radio_estado(False, None, True), ephemeral=True
        )

    @radio_group.command(name="canal", description="Cambiar de canal de radio")
    @app_commands.describe(frecuencia="Nombre de la frecuencia (ej: freq_1, intercom)")
    @require_role(USUARIO)
    async def radio_canal(self, interaction: Interaction, frecuencia: str) -> None:
        """
        Cambia el canal activo de la radio del personaje.

        Args:
            interaction: Contexto de Discord.
            frecuencia : Clave de la frecuencia en config/radio.json.
        """
        activo, radio_state = await self._verificar_radio(interaction.user.id)
        if not activo:
            await interaction.response.send_message(
                embed=emb.radio_sin_equipo() if not radio_state or not radio_state["tiene_radio"]
                else emb.error("Radio apagada", "Enciende la radio primero con `/radio encendida`."),
                ephemeral=True,
            )
            return

        # Verificar que la frecuencia existe en config
        frecuencias_validas = list(self.cfg.get("frecuencias", {}).keys()) + ["intercom"]
        if frecuencia not in frecuencias_validas:
            await interaction.response.send_message(
                embed=emb.error(
                    "Frecuencia inválida",
                    f"Frecuencias disponibles: {', '.join(frecuencias_validas)}",
                ),
                ephemeral=True,
            )
            return

        async with await repo.get_conn() as conn:
            await repo.upsert_radio_state(
                conn, interaction.user.id,
                campos={"canal_activo": frecuencia},
            )

        await self._sync_radio_roles(interaction.user, frecuencia, activo=True)

        nombre_canal = (
            self.cfg.get("frecuencias", {}).get(frecuencia, {}).get("nombre", frecuencia)
            if frecuencia != "intercom"
            else "INTERCOM"
        )
        await interaction.response.send_message(
            embed=emb.radio_estado(True, nombre_canal, True), ephemeral=True
        )

    @radio_group.command(name="toda-malla", description="Enviar mensaje a toda la malla (Intercom)")
    @app_commands.describe(mensaje="Mensaje a transmitir por Intercom")
    @require_role(USUARIO)
    async def radio_toda_malla(self, interaction: Interaction, mensaje: str) -> None:
        """
        Envía un mensaje por Intercom a todos los operadores con radio encendida.

        Args:
            interaction: Contexto de Discord.
            mensaje    : Texto del mensaje.
        """
        activo, radio_state = await self._verificar_radio(interaction.user.id)
        if not activo:
            await interaction.response.send_message(
                embed=emb.radio_sin_equipo() if not radio_state or not radio_state["tiene_radio"]
                else emb.error("Radio apagada", "Enciende la radio primero."),
                ephemeral=True,
            )
            return

        # Obtener datos del personaje para el webhook
        async with await repo.get_conn() as conn:
            char = await repo.get_character(conn, interaction.user.id)

        char_nombre = char["nombre_completo"] if char else interaction.user.display_name
        unidad      = char["unidad_radio"]   if char else None
        avatar_url  = None

        # Intentar usar avatar del personaje
        avatar_path = Path(f"data/characters/{interaction.user.id}/avatar.png")
        if avatar_path.exists():
            # Para webhooks necesitamos URL pública — usar avatar de Discord si no hay URL
            avatar_url = interaction.user.display_avatar.url

        # Enviar al canal de Intercom
        intercom_cfg     = self.cfg.get("intercom", {})
        intercom_chan_id = intercom_cfg.get("channel_id", 0)
        canal            = interaction.guild.get_channel(intercom_chan_id) if interaction.guild else None

        if not canal:
            await interaction.response.send_message(
                embed=emb.error("Canal Intercom no configurado",
                                "El canal de Intercom no está configurado."),
                ephemeral=True,
            )
            return

        # Estática del canal (radio_state ya disponible)
        estatica = bool(radio_state and radio_state.get("estatica_activa", 0))

        enviado = await self._enviar_por_radio(
            channel    = canal,
            user       = interaction.user,
            mensaje    = mensaje,
            char_nombre= char_nombre,
            avatar_url = avatar_url,
            unidad     = unidad,
            estatica   = estatica,
        )

        if enviado:
            await interaction.response.send_message(
                embed=emb.ok("Transmisión enviada", "Mensaje enviado por Intercom."),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=emb.error("Error de transmisión", "No se pudo enviar el mensaje."),
                ephemeral=True,
            )

    @radio_group.command(name="estado", description="Ver el estado de tu radio")
    @require_role(USUARIO)
    async def radio_estado_cmd(self, interaction: Interaction) -> None:
        """
        Muestra el estado actual de la radio del usuario.

        Args:
            interaction: Contexto de Discord.
        """
        async with await repo.get_conn() as conn:
            radio_state = await repo.get_radio_state(conn, interaction.user.id)

        if not radio_state:
            await interaction.response.send_message(
                embed=emb.radio_estado(False, None, False), ephemeral=True
            )
            return

        nombre_canal = radio_state["canal_activo"] or "—"
        # Resolver nombre de frecuencia si existe
        if nombre_canal in self.cfg.get("frecuencias", {}):
            nombre_canal = self.cfg["frecuencias"][nombre_canal].get("nombre", nombre_canal)

        await interaction.response.send_message(
            embed=emb.radio_estado(
                encendida   = bool(radio_state["encendida"]),
                canal       = nombre_canal,
                tiene_radio = bool(radio_state["tiene_radio"]),
            ),
            ephemeral=True,
        )

    @radio_group.command(name="estatica",
                         description="Activar/desactivar estática en un canal (Narrador+)")
    @app_commands.describe(canal_id="ID del canal de Discord", activa="True para activar")
    @require_role(NARRADOR)
    async def radio_estatica(self, interaction: Interaction,
                              canal_id: str, activa: bool) -> None:
        """
        Activa o desactiva la estática en un canal de radio.
        Cuando está activa, los mensajes enviados se corrompen parcialmente.

        Args:
            interaction: Contexto de Discord.
            canal_id   : ID del canal de Discord (como string).
            activa     : True para activar, False para desactivar.
        """
        try:
            cid = int(canal_id)
        except ValueError:
            await interaction.response.send_message(
                embed=emb.error("ID inválido", "El ID del canal debe ser numérico."),
                ephemeral=True,
            )
            return

        async with await repo.get_conn() as conn:
            await repo.set_static(conn, cid, activa)

        estado_txt = "activada" if activa else "desactivada"
        await interaction.response.send_message(
            embed=emb.ok(f"Estática {estado_txt}",
                          f"Estática {estado_txt} en canal `{canal_id}`.")
        )

    @radio_group.command(name="unidad",
                         description="Asignar unidad radio a un personaje (Narrador+)")
    @app_commands.describe(usuario="Personaje", designacion="Ej: Bravo 5-3")
    @require_role(NARRADOR)
    async def radio_unidad(self, interaction: Interaction,
                            usuario: discord.Member, designacion: str) -> None:
        """
        Asigna una unidad radio al personaje indicado.
        Formato esperado: [Grupo] [NºUnidad]-[Designación] — Ej: Bravo 5-3

        Args:
            interaction : Contexto de Discord.
            usuario     : Miembro destino.
            designacion : Identificador de unidad.
        """
        async with await repo.get_conn() as conn:
            char = await repo.get_character(conn, usuario.id)
            if not char or char["estado"] != "activo":
                await interaction.response.send_message(
                    embed=emb.error("Sin personaje", "El usuario no tiene personaje activo."),
                    ephemeral=True,
                )
                return
            await repo.update_character_unidad_radio(conn, usuario.id, designacion)
            await audit(
                conn,
                tipo        = "asignacion_unidad",
                descripcion = f"Unidad radio '{designacion}' asignada a {char['nombre_completo']}",
                actor_id    = interaction.user.id,
                target_id   = usuario.id,
            )

        await interaction.response.send_message(
            embed=emb.ok("Unidad asignada",
                          f"**{char['nombre_completo']}** → unidad `{designacion}`")
        )

    # -----------------------------------------------------------------------
    # Helper: sincronizar roles de radio con Discord
    # -----------------------------------------------------------------------

    async def _sync_radio_roles(self, member: discord.Member,
                                 canal: str | None, activo: bool) -> None:
        """
        Añade o retira los roles de radio de Discord según el canal activo.
        Los roles de radio se mapean desde config/roles.json → roles_radio.

        Esta función no lanza excepciones — si falla, solo registra el error.

        Args:
            member : Miembro de Discord.
            canal  : Clave de canal (None = retirar todos).
            activo : True para añadir rol, False para retirar todos.
        """
        # Simplificado: en implementación completa mapear canal → rol_id en config
        # Por ahora solo registramos la operación para no bloquear si los roles no están configurados
        log_info(
            f"[RADIO] sync_roles: {member.display_name} canal={canal} activo={activo}"
        )


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot) -> None:
    """Registra el cog en el bot."""
    await bot.add_cog(RadioCog(bot))