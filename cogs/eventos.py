"""
cogs/eventos.py — Sistema de control de eventos narrativos de RAISA
====================================================================
Responsabilidad : Gestionar el estado global Evento-ON / Evento-OFF.
                  Afecta directamente a tienda, inventario y sistema médico.
Dependencias    : db.repository, utils.embeds, utils.permisos, utils.logger
Autor           : RAISA Dev

Comandos
--------
  /evento on  [descripcion] — Activa el evento (Narrador+)
  /evento off               — Desactiva el evento (Narrador+)
  /evento estado            — Muestra el estado actual (Usuario+)
"""

import discord
from discord import Interaction, app_commands
from discord.ext import commands

import aiosqlite
from db import repository as repo
from utils import embeds as emb
from utils.logger import audit, log_info
from utils.permisos import NARRADOR, USUARIO, get_user_level, require_role


class EventosCog(commands.Cog, name="Eventos"):
    """Cog de control de eventos narrativos."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -----------------------------------------------------------------------
    # Grupo de comandos /evento
    # -----------------------------------------------------------------------

    evento_group = app_commands.Group(
        name="evento",
        description="Control del estado de evento narrativo",
    )

    @evento_group.command(name="on", description="Activa el evento narrativo (Narrador+)")
    @app_commands.describe(descripcion="Descripción narrativa del evento (opcional)")
    @require_role(NARRADOR)
    async def evento_on(self, interaction: Interaction,
                         descripcion: str = "Operación en curso.") -> None:
        """
        Activa el estado Evento-ON en el servidor.
        Bloquea la tienda, desactiva zonas seguras y restringe el inventario.

        Args:
            interaction : Contexto de Discord.
            descripcion : Nota narrativa sobre el evento.
        """
        async with await repo.get_conn() as conn:
            estado = await repo.get_event_state(conn)

            if estado and estado["evento_activo"]:
                await interaction.response.send_message(
                    embed=emb.advertencia(
                        "Evento ya activo",
                        "Ya hay un evento en curso. Usa `/evento off` para finalizarlo primero.",
                    ),
                    ephemeral=True,
                )
                return

            await repo.set_event_state(conn, activo=True,
                                        user_id=interaction.user.id,
                                        descripcion=descripcion)
            await audit(
                conn,
                tipo="cambio_evento",
                descripcion=f"Evento-ON activado. Descripción: {descripcion}",
                actor_id=interaction.user.id,
                detalles={"descripcion": descripcion},
            )

        log_info(f"[EVENTO] ON activado por {interaction.user} ({interaction.user.id})")

        embed = emb.evento_on(descripcion, str(interaction.user))
        await interaction.response.send_message(embed=embed)

        # Anunciar en el canal general si está configurado
        await self._anunciar_evento(embed, interaction.guild)

    @evento_group.command(name="off", description="Finaliza el evento narrativo (Narrador+)")
    @require_role(NARRADOR)
    async def evento_off(self, interaction: Interaction) -> None:
        """
        Desactiva el estado Evento-OFF.
        Desbloquea tienda, activa zonas seguras y habilita inventario general.

        Args:
            interaction: Contexto de Discord.
        """
        async with await repo.get_conn() as conn:
            estado = await repo.get_event_state(conn)

            if estado and not estado["evento_activo"]:
                await interaction.response.send_message(
                    embed=emb.advertencia(
                        "Sin evento activo",
                        "No hay ningún evento en curso actualmente.",
                    ),
                    ephemeral=True,
                )
                return

            await repo.set_event_state(conn, activo=False,
                                        user_id=interaction.user.id)
            await audit(
                conn,
                tipo="cambio_evento",
                descripcion="Evento-OFF declarado.",
                actor_id=interaction.user.id,
            )

        log_info(f"[EVENTO] OFF declarado por {interaction.user} ({interaction.user.id})")

        embed = emb.evento_off(str(interaction.user))
        await interaction.response.send_message(embed=embed)
        await self._anunciar_evento(embed, interaction.guild)

    @evento_group.command(name="estado", description="Muestra el estado actual del evento")
    @require_role(USUARIO)
    async def evento_estado(self, interaction: Interaction) -> None:
        """
        Muestra si hay un evento activo y quién lo activó.

        Args:
            interaction: Contexto de Discord.
        """
        async with await repo.get_conn() as conn:
            estado = await repo.get_event_state(conn)

        if not estado:
            await interaction.response.send_message(
                embed=emb.error("Error", "No se pudo obtener el estado del evento."),
                ephemeral=True,
            )
            return

        if estado["evento_activo"]:
            desc = estado["descripcion"] or "Sin descripción."
            activado_en = estado["activado_en"] or "—"
            activado_por_id = estado["activado_por"]
            activado_por = f"<@{activado_por_id}>" if activado_por_id else "Sistema"

            embed = discord.Embed(
                title="🔴  EVENTO ACTIVO",
                description=f"**{desc}**",
                color=emb.C_ERROR,
            )
            embed.add_field(name="Activado por",   value=activado_por, inline=True)
            embed.add_field(name="Activado el",    value=activado_en,  inline=True)
            embed.add_field(
                name="Restricciones activas",
                value="• Tienda: **bloqueada**\n"
                      "• Zonas seguras: **inactivas**\n"
                      "• Inventario general: **restringido**",
                inline=False,
            )
        else:
            embed = discord.Embed(
                title="🟢  SIN EVENTO ACTIVO",
                description="El servidor está en modo operativo normal.",
                color=emb.C_OK,
            )
            embed.add_field(
                name="Sistemas activos",
                value="• Tienda: **operativa**\n"
                      "• Zonas seguras: **activas**\n"
                      "• Inventario general: **accesible**",
                inline=False,
            )

        embed.set_footer(text=emb.FOOTER_TEXT)
        await interaction.response.send_message(embed=embed)

    # -----------------------------------------------------------------------
    # Helpers internos
    # -----------------------------------------------------------------------

    async def _anunciar_evento(self, embed: discord.Embed,
                                guild: discord.Guild | None) -> None:
        """
        Envía el embed de anuncio de evento al canal de log/admin si está configurado.

        Args:
            embed : Embed a enviar.
            guild : Servidor de Discord.
        """
        if not guild:
            return

        cfg         = getattr(self.bot, "raisa_config", {})
        canal_id    = cfg.get("canales", {}).get("log_admin")
        if not canal_id:
            return

        canal = guild.get_channel(canal_id)
        if canal and isinstance(canal, discord.TextChannel):
            try:
                await canal.send(embed=embed)
            except discord.Forbidden:
                log_info(f"[EVENTOS] Sin permisos para enviar al canal de log {canal_id}")


# ---------------------------------------------------------------------------
# Función de verificación de evento (importable por otros cogs)
# ---------------------------------------------------------------------------

async def evento_activo() -> bool:
    """
    Función de utilidad para que otros cogs consulten el estado del evento.
    No requiere instancia del cog.

    Returns:
        True si hay un evento activo, False si no.
    """
    async with await repo.get_conn() as conn:
        estado = await repo.get_event_state(conn)
    return bool(estado and estado["evento_activo"])


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot) -> None:
    """Registra el cog en el bot."""
    await bot.add_cog(EventosCog(bot))