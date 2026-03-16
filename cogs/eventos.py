"""
RAISA — Cog de Control de Eventos
cogs/eventos.py

Responsabilidad : Gestión del estado Evento-ON / Evento-OFF del servidor.
                  Bloqueo/desbloqueo automático de tienda y zonas.
Dependencias    : discord.py, db/repository, utils/permisos, utils/embeds
Autor           : Proyecto RAISA
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import embed_estado_evento, embed_ok, embed_error
from utils.permisos import require_role, RANGO_NARRADOR, RANGO_USUARIO

log = logging.getLogger("raisa.eventos")


class EventosCog(commands.Cog, name="Eventos"):
    """Cog para el control del estado del evento narrativo."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def repo(self):
        return self.bot.repo

    # ──────────────────────────────────────────────────────────────────────
    # COMANDOS SLASH
    # ──────────────────────────────────────────────────────────────────────

    evento_group = app_commands.Group(name="evento", description="Control del estado del evento")

    @evento_group.command(name="estado", description="Muestra el estado actual del evento.")
    @require_role(RANGO_USUARIO)
    async def evento_estado(self, interaction: discord.Interaction) -> None:
        """Muestra si hay un evento activo o no."""
        modo = await self.repo.get_modo_evento()
        row  = await self.repo._fetch_one("SELECT * FROM estado_evento WHERE id = 1")
        activado_por = row["activado_por"] if row else None
        embed = embed_estado_evento(modo, activado_por)
        await interaction.response.send_message(embed=embed, ephemeral=False)

    @evento_group.command(name="activar", description="[Narrador] Activa el Evento-ON.")
    @require_role(RANGO_NARRADOR)
    async def evento_activar(self, interaction: discord.Interaction) -> None:
        """
        Activa el modo Evento-ON:
        - Bloquea la tienda.
        - Desactiva zonas seguras.
        - Restringe inventario a personal + vehículos.
        """
        modo_actual = await self.repo.get_modo_evento()
        if modo_actual == "ON":
            await interaction.response.send_message(
                embed=embed_error("El evento ya está **activo**."), ephemeral=True
            )
            return

        await self.repo.set_modo_evento("ON", interaction.user.id)
        await self.repo.log_accion("EVENTO_CAMBIO", interaction.user.id, detalle={"modo": "ON"})
        log.info("Evento-ON activado por user_id=%s", interaction.user.id)

        embed = embed_estado_evento("ON", interaction.user.id)
        await interaction.response.send_message(embed=embed)

    @evento_group.command(name="desactivar", description="[Narrador] Desactiva el Evento (OFF).")
    @require_role(RANGO_NARRADOR)
    async def evento_desactivar(self, interaction: discord.Interaction) -> None:
        """
        Desactiva el evento:
        - Desbloquea tienda.
        - Reactiva zonas seguras.
        - Devuelve acceso al inventario general.
        - En Evento-OFF las muertes requieren autorización de Gestor+.
        """
        modo_actual = await self.repo.get_modo_evento()
        if modo_actual == "OFF":
            await interaction.response.send_message(
                embed=embed_error("El evento ya está **inactivo**."), ephemeral=True
            )
            return

        await self.repo.set_modo_evento("OFF", interaction.user.id)
        await self.repo.log_accion("EVENTO_CAMBIO", interaction.user.id, detalle={"modo": "OFF"})
        log.info("Evento-OFF activado por user_id=%s", interaction.user.id)

        embed = embed_estado_evento("OFF", interaction.user.id)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EventosCog(bot))
