"""
RAISA — Cog de Sistema de Radio
cogs/radio.py

Responsabilidad : Gestión de canales de radio mediante webhooks dinámicos.
                  Filtro de estática, cambio de canal, intercom.
Dependencias    : discord.py, db/repository, utils/permisos, utils/embeds
Autor           : Proyecto RAISA
"""

import json
import logging
import random
import string
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import embed_radio_sin_equipo, embed_radio_canal, embed_ok, embed_error, embed_aviso
from utils.permisos import require_role, RANGO_USUARIO, RANGO_NARRADOR

log = logging.getLogger("raisa.radio")


def _aplicar_statica(texto: str, intensidad: float = 0.15) -> str:
    """
    Corrompe parcialmente el texto para simular estática de radio.
    Reemplaza caracteres aleatorios con ruido.

    :param texto: Mensaje original.
    :param intensidad: Fracción de caracteres a corromper (0.0–1.0).
    :returns: Texto con estática aplicada.
    """
    RUIDO = ["*", "#", "%", "~", "■", "▪", "░", "▒", "?", "-"]
    chars = list(texto)
    for i in range(len(chars)):
        if chars[i] != " " and random.random() < intensidad:
            chars[i] = random.choice(RUIDO)
    return "".join(chars)


def _cargar_config_radio() -> dict:
    """Carga config/radio.json. Se llama en cada operación (bajo volumen de cambio)."""
    path = Path("config/radio.json")
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


class RadioCog(commands.Cog, name="Radio"):
    """Cog para el sistema de radio de RAISA."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Caché de webhooks por canal {channel_id: webhook}
        # Se invalida al reiniciar el cog.
        self._webhook_cache: dict[int, discord.Webhook] = {}

    @property
    def repo(self):
        return self.bot.repo

    # ──────────────────────────────────────────────────────────────────────
    # HELPER: Obtener/crear webhook del canal
    # ──────────────────────────────────────────────────────────────────────

    async def _get_webhook(self, canal: discord.TextChannel) -> discord.Webhook | None:
        """
        Devuelve un webhook existente del canal o crea uno nuevo.
        NUNCA crea un webhook nuevo si ya existe uno reutilizable.

        :param canal: Canal de Discord donde opera la frecuencia.
        :returns: Instancia de discord.Webhook o None si no se pudo crear.
        """
        # Buscar en caché en memoria
        cached = self._webhook_cache.get(canal.id)
        if cached:
            return cached

        # Buscar en webhooks existentes del canal
        try:
            webhooks = await canal.webhooks()
            raisa_hook = next((w for w in webhooks if w.user and w.user.id == self.bot.user.id), None)
            if raisa_hook:
                self._webhook_cache[canal.id] = raisa_hook
                return raisa_hook

            # Crear solo si no hay ninguno del bot
            hook = await canal.create_webhook(name="RAISA Radio")
            self._webhook_cache[canal.id] = hook
            log.info("Webhook de radio creado en canal #%s", canal.name)
            return hook
        except discord.Forbidden:
            log.error("Sin permisos para gestionar webhooks en #%s", canal.name)
            return None

    # ──────────────────────────────────────────────────────────────────────
    # HELPER: Verificar radio equipada
    # ──────────────────────────────────────────────────────────────────────

    async def _tiene_radio(self, user_id: int) -> bool:
        """Verifica que el usuario tenga radio equipada en su loadout."""
        loadout = await self.repo.get_loadout(user_id)
        return bool(loadout and loadout.get("radio_id"))

    # ──────────────────────────────────────────────────────────────────────
    # COMANDOS SLASH
    # ──────────────────────────────────────────────────────────────────────

    radio_group = app_commands.Group(name="radio", description="Sistema de comunicaciones por radio")

    @radio_group.command(name="encendida", description="Activa la radio (rol Intercom + último canal).")
    @require_role(RANGO_USUARIO)
    async def radio_encendida(self, interaction: discord.Interaction) -> None:
        """
        Activa la radio del usuario:
        1. Asigna el rol de Intercom.
        2. Restaura el último canal de radio activo.
        """
        if not await self._tiene_radio(interaction.user.id):
            await interaction.response.send_message(embed=embed_radio_sin_equipo(), ephemeral=True)
            return

        cfg = _cargar_config_radio()
        from utils.permisos import _cargar_config_roles
        roles_cfg = _cargar_config_roles()

        intercom_role_id = int(cfg.get("intercom", {}).get("role_id", 0))
        guild = interaction.guild
        if intercom_role_id:
            role = guild.get_role(intercom_role_id)
            if role:
                await interaction.user.add_roles(role, reason="Radio encendida")

        await interaction.response.send_message(
            embed=embed_ok("Radio encendida", "Comunicaciones activadas. Canal Intercom asignado."),
            ephemeral=True,
        )

    @radio_group.command(name="apagada", description="Desactiva todas las comunicaciones de radio.")
    @require_role(RANGO_USUARIO)
    async def radio_apagada(self, interaction: discord.Interaction) -> None:
        """Retira todos los roles de radio e Intercom del usuario."""
        cfg = _cargar_config_radio()
        guild = interaction.guild
        roles_a_retirar: list[discord.Role] = []

        # Intercom
        intercom_id = int(cfg.get("intercom", {}).get("role_id", 0))
        if intercom_id:
            r = guild.get_role(intercom_id)
            if r and r in interaction.user.roles:
                roles_a_retirar.append(r)

        # Todos los canales de frecuencia
        for freq_data in cfg.get("frecuencias", {}).values():
            role_id = int(freq_data.get("role_id", 0))
            if role_id:
                r = guild.get_role(role_id)
                if r and r in interaction.user.roles:
                    roles_a_retirar.append(r)

        if roles_a_retirar:
            await interaction.user.remove_roles(*roles_a_retirar, reason="Radio apagada")

        await interaction.response.send_message(
            embed=embed_ok("Radio apagada", "Todas las comunicaciones han sido cortadas."),
            ephemeral=True,
        )

    @radio_group.command(name="canal", description="Cambia el canal de radio activo.")
    @app_commands.describe(frecuencia="Nombre de la frecuencia a sintonizar")
    @require_role(RANGO_USUARIO)
    async def radio_canal(self, interaction: discord.Interaction, frecuencia: str) -> None:
        """
        Cambia el canal activo de radio.
        Retira el rol de la frecuencia anterior y asigna el de la nueva.
        """
        if not await self._tiene_radio(interaction.user.id):
            await interaction.response.send_message(embed=embed_radio_sin_equipo(), ephemeral=True)
            return

        cfg = _cargar_config_radio()
        frecuencias = cfg.get("frecuencias", {})

        # Buscar frecuencia por clave o nombre
        freq_key = None
        freq_data = None
        for k, v in frecuencias.items():
            if k == frecuencia or v.get("nombre", "").lower() == frecuencia.lower():
                freq_key = k
                freq_data = v
                break

        if not freq_data:
            nombres = [v.get("nombre", k) for k, v in frecuencias.items()]
            await interaction.response.send_message(
                embed=embed_error(
                    f"Frecuencia `{frecuencia}` no encontrada.\nFrecuencias disponibles: {', '.join(nombres)}"
                ),
                ephemeral=True,
            )
            return

        guild = interaction.guild
        # Retirar todos los roles de frecuencia que tenga activos
        for k, v in frecuencias.items():
            rid = int(v.get("role_id", 0))
            if rid and k != freq_key:
                r = guild.get_role(rid)
                if r and r in interaction.user.roles:
                    await interaction.user.remove_roles(r, reason="Cambio de canal de radio")

        # Asignar nuevo rol de frecuencia
        nuevo_rol_id = int(freq_data.get("role_id", 0))
        if nuevo_rol_id:
            nuevo_rol = guild.get_role(nuevo_rol_id)
            if nuevo_rol and nuevo_rol not in interaction.user.roles:
                await interaction.user.add_roles(nuevo_rol, reason="Canal de radio seleccionado")

        personaje = await self.repo.get_personaje(interaction.user.id)
        unidad = personaje.get("unidad_radio") if personaje else None

        await interaction.response.send_message(
            embed=embed_radio_canal(freq_data.get("nombre", frecuencia), unidad),
            ephemeral=True,
        )

    @radio_group.command(name="toda_malla", description="Envía un mensaje por Intercom a todos los canales.")
    @app_commands.describe(mensaje="Mensaje a enviar por toda-malla")
    @require_role(RANGO_USUARIO)
    async def radio_toda_malla(self, interaction: discord.Interaction, mensaje: str) -> None:
        """
        Envía un mensaje a todos los canales de radio mediante webhook.
        El webhook toma el nombre del personaje y su avatar.
        Si hay estática en un canal, el texto se corrompe antes de enviarse.
        """
        if not await self._tiene_radio(interaction.user.id):
            await interaction.response.send_message(embed=embed_radio_sin_equipo(), ephemeral=True)
            return

        cfg = _cargar_config_radio()
        personaje = await self.repo.get_personaje(interaction.user.id)
        nombre_webhook = (
            interaction.user.display_name if not personaje
            else f"{personaje['nombre']} {personaje['apellidos']}"
        )

        # Avatar del personaje
        avatar_path = Path(f"data/characters/{interaction.user.id}/avatar.png")
        avatar_bytes = None
        if avatar_path.exists():
            avatar_bytes = avatar_path.read_bytes()

        # Determinar avatar URL para el webhook (se usa URL si no hay bytes locales)
        avatar_url = str(interaction.user.display_avatar.url)

        await interaction.response.defer(ephemeral=True)

        enviados = 0
        for freq_key, freq_data in cfg.get("frecuencias", {}).items():
            ch_id = int(freq_data.get("channel_id", 0))
            if not ch_id:
                continue
            canal = interaction.guild.get_channel(ch_id)
            if not canal:
                continue

            hook = await self._get_webhook(canal)
            if not hook:
                continue

            # Aplicar estática si está activa en este canal
            statica = await self.repo.get_statica_canal(freq_key)
            texto_enviar = _aplicar_statica(mensaje) if statica else mensaje

            try:
                await hook.send(
                    content=texto_enviar,
                    username=nombre_webhook,
                    avatar_url=avatar_url,
                )
                enviados += 1
            except Exception as exc:
                log.error("Error enviando mensaje de radio en canal %s: %s", ch_id, exc)

        # Intercom también
        intercom_ch_id = int(cfg.get("intercom", {}).get("channel_id", 0))
        if intercom_ch_id:
            canal_intercom = interaction.guild.get_channel(intercom_ch_id)
            if canal_intercom:
                hook = await self._get_webhook(canal_intercom)
                if hook:
                    try:
                        await hook.send(content=f"📡 **[TODA-MALLA]** {mensaje}", username=nombre_webhook, avatar_url=avatar_url)
                    except Exception as exc:
                        log.error("Error en toda-malla intercom: %s", exc)

        await interaction.followup.send(
            embed=embed_ok("Toda-malla enviada", f"Mensaje transmitido a `{enviados}` frecuencias."),
            ephemeral=True,
        )

    # ──────────────────────────────────────────────────────────────────────
    # NARRADOR — Control de estática y unidades
    # ──────────────────────────────────────────────────────────────────────

    @radio_group.command(name="statica", description="[Narrador] Activa o desactiva la estática en un canal.")
    @app_commands.describe(frecuencia="Clave de frecuencia", activa="True = activa / False = desactivada")
    @require_role(RANGO_NARRADOR)
    async def radio_statica(
        self, interaction: discord.Interaction, frecuencia: str, activa: bool
    ) -> None:
        """Activa o desactiva el filtro de estática en una frecuencia."""
        await self.repo.set_statica_canal(frecuencia, activa)
        estado = "activada ⚡" if activa else "desactivada ✅"
        await interaction.response.send_message(
            embed=embed_ok("Estática de radio", f"Estática **{estado}** en frecuencia `{frecuencia}`."),
            ephemeral=False,
        )

    @radio_group.command(name="unidad", description="[Narrador] Asigna una unidad de radio a un usuario.")
    @app_commands.describe(usuario="Usuario objetivo", unidad="Ej: Bravo 5-3")
    @require_role(RANGO_NARRADOR)
    async def asignar_unidad(
        self, interaction: discord.Interaction, usuario: discord.Member, unidad: str
    ) -> None:
        """
        Asigna una unidad de radio al personaje.
        Formato: [Grupo] [NúmeroUnidad]-[Designación]. Ej: Bravo 5-3
        """
        personaje = await self.repo.get_personaje(usuario.id)
        if not personaje:
            await interaction.response.send_message(embed=embed_error("Personaje no encontrado."), ephemeral=True)
            return

        await self.repo._execute(
            "UPDATE personajes SET unidad_radio = ? WHERE user_id = ?",
            (unidad, usuario.id)
        )
        self.repo._cache.invalidate(f"personaje:{usuario.id}")

        await interaction.response.send_message(
            embed=embed_ok("Unidad asignada", f"Unidad de {usuario.mention}: **{unidad}**"),
        )

    @radio_group.command(name="bloquear", description="[Narrador] Bloquea/desbloquea el acceso de radio a un usuario.")
    @app_commands.describe(usuario="Usuario objetivo", bloquear="True = bloquear / False = desbloquear")
    @require_role(RANGO_NARRADOR)
    async def bloquear_radio(
        self, interaction: discord.Interaction, usuario: discord.Member, bloquear: bool
    ) -> None:
        """Retira o restaura los roles de radio de un usuario."""
        cfg = _cargar_config_radio()
        guild = interaction.guild
        roles_radio: list[discord.Role] = []

        intercom_id = int(cfg.get("intercom", {}).get("role_id", 0))
        if intercom_id:
            r = guild.get_role(intercom_id)
            if r:
                roles_radio.append(r)

        for freq_data in cfg.get("frecuencias", {}).values():
            rid = int(freq_data.get("role_id", 0))
            if rid:
                r = guild.get_role(rid)
                if r:
                    roles_radio.append(r)

        if bloquear:
            roles_activos = [r for r in roles_radio if r in usuario.roles]
            if roles_activos:
                await usuario.remove_roles(*roles_activos, reason="Radio bloqueada por Narrador")
            accion = "bloqueada 🔇"
        else:
            accion = "desbloqueada 📻"

        await interaction.response.send_message(
            embed=embed_ok("Radio de usuario", f"Radio de {usuario.mention}: **{accion}**"),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RadioCog(bot))
