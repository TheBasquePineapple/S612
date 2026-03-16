"""
cogs/sudo.py — Sistema de autenticación SUDO de RAISA
======================================================
Responsabilidad : Flujo de autenticación SUDO por MD.
                  Activación, consulta y revocación de sesiones.
Dependencias    : utils.permisos, utils.embeds, utils.logger
Autor           : RAISA Dev

Comandos
--------
  /sudo auth     — Solicitar autenticación SUDO por MD (Admin+)
  /sudo estado   — Ver tiempo restante de sesión SUDO (Admin+)
  /sudo revocar [usuario] — Revocar sesión SUDO de un usuario (Owner/Holder)

Flujo de autenticación
----------------------
  1. Usuario ejecuta /sudo auth
  2. Bot envía embed de solicitud por MD con timeout de 2 minutos
  3. Usuario responde con la clave SUDO
  4. Si correcta → sesión activada (30 min), embed de confirmación
  5. Si incorrecta → registrar en audit_log, notificar al Owner
  6. 3 fallos seguidos → bloqueo temporal de 10 minutos (anti-brute-force)

Ver utils/permisos.py para la implementación del dict de sesiones.
NOTA: Las sesiones SUDO son SOLO en memoria — se invalidan al reiniciar.
"""

import asyncio
import os
from collections import defaultdict
from time import monotonic

import discord
from discord import Interaction, app_commands
from discord.ext import commands

from utils import embeds as emb
from utils.logger import audit, log_info, log_warning
from utils.permisos import (
    ADMIN, OWNER, HOLDER,
    get_user_level,
    get_sudo_remaining,
    sudo_activate,
    sudo_deactivate,
    require_role,
)

# Anti-brute-force: {user_id: [timestamp_fallo, ...]}
_fail_timestamps: dict[int, list[float]] = defaultdict(list)
MAX_INTENTOS        = 3
BLOQUEO_SEGUNDOS    = 10 * 60   # 10 minutos
VENTANA_INTENTOS    = 5  * 60   # ventana de 5 minutos para contar fallos


def _esta_bloqueado(user_id: int) -> bool:
    """
    Comprueba si el usuario está temporalmente bloqueado por intentos fallidos.

    Args:
        user_id: discord user_id.

    Returns:
        True si está bloqueado.
    """
    ahora   = monotonic()
    fallos  = _fail_timestamps.get(user_id, [])
    # Mantener solo fallos dentro de la ventana
    recientes = [t for t in fallos if ahora - t < VENTANA_INTENTOS]
    _fail_timestamps[user_id] = recientes
    return len(recientes) >= MAX_INTENTOS


def _registrar_fallo(user_id: int) -> int:
    """
    Registra un fallo de autenticación y devuelve el total de intentos recientes.

    Args:
        user_id: discord user_id.

    Returns:
        Número de intentos fallidos en la ventana actual.
    """
    _fail_timestamps[user_id].append(monotonic())
    return len(_fail_timestamps[user_id])


def _limpiar_fallos(user_id: int) -> None:
    """Limpia el historial de fallos tras autenticación exitosa."""
    _fail_timestamps.pop(user_id, None)


class SudoCog(commands.Cog, name="SUDO"):
    """Cog de autenticación SUDO."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    sudo_group = app_commands.Group(
        name="sudo",
        description="Autenticación SUDO para comandos críticos",
    )

    # -----------------------------------------------------------------------
    # /sudo auth
    # -----------------------------------------------------------------------

    @sudo_group.command(name="auth",
                        description="Iniciar autenticación SUDO por MD (Admin+)")
    @require_role(ADMIN)
    async def sudo_auth(self, interaction: Interaction) -> None:
        """
        Inicia el flujo de autenticación SUDO.
        Si ya hay sesión activa, informa del tiempo restante.
        Si no, envía la solicitud de clave por MD.

        Args:
            interaction: Contexto de Discord.
        """
        user_id = interaction.user.id

        # Sesión ya activa
        restante = get_sudo_remaining(user_id)
        if restante > 0:
            await interaction.response.send_message(
                embed=emb.sudo_activa(restante), ephemeral=True
            )
            return

        # Verificar bloqueo anti-brute-force
        if _esta_bloqueado(user_id):
            await interaction.response.send_message(
                embed=emb.error(
                    "Acceso bloqueado",
                    f"Demasiados intentos fallidos. Espera {BLOQUEO_SEGUNDOS // 60} minutos.",
                ),
                ephemeral=True,
            )
            return

        # Notificar que se envió MD
        await interaction.response.send_message(
            embed=emb.info(
                "Autenticación SUDO",
                "Se ha enviado la solicitud de clave por Mensaje Directo.\n"
                "Tienes **2 minutos** para responder.",
            ),
            ephemeral=True,
        )

        # Enviar solicitud por MD y esperar respuesta
        try:
            await interaction.user.send(embed=emb.sudo_solicitud())
        except discord.Forbidden:
            await interaction.followup.send(
                embed=emb.error("MD bloqueados",
                                "No puedo enviarte un MD. Habilita los mensajes directos."),
                ephemeral=True,
            )
            return

        # Esperar respuesta del usuario (2 minutos)
        def check(m: discord.Message) -> bool:
            return (
                isinstance(m.channel, discord.DMChannel)
                and m.author.id == user_id
            )

        try:
            msg = await self.bot.wait_for("message", check=check, timeout=120)
        except asyncio.TimeoutError:
            try:
                await interaction.user.send(
                    embed=emb.error("Tiempo agotado",
                                    "No respondiste a tiempo. El proceso ha sido cancelado.")
                )
            except discord.Forbidden:
                pass
            return

        clave_ingresada = msg.content.strip()
        clave_correcta  = getattr(self.bot, "sudo_key", "") or os.getenv("SUDO_KEY", "")

        if not clave_correcta:
            await interaction.user.send(
                embed=emb.error("SUDO no configurado",
                                "La clave SUDO no está configurada en el sistema.")
            )
            return

        if clave_ingresada == clave_correcta:
            # Autenticación exitosa
            sudo_activate(user_id)
            _limpiar_fallos(user_id)

            # Eliminar el mensaje con la clave por seguridad (best effort)
            try:
                await msg.delete()
            except (discord.Forbidden, discord.NotFound):
                pass

            await interaction.user.send(embed=emb.sudo_ok())
            log_info(f"[SUDO] Sesión activada correctamente para user_id={user_id}")

            async with await _get_conn_for_audit() as conn:
                await audit(
                    conn,
                    tipo        = "sudo_auth",
                    descripcion = f"Sesión SUDO activada para user_id={user_id}",
                    actor_id    = user_id,
                )

        else:
            # Fallo de autenticación
            n_fallos = _registrar_fallo(user_id)
            restantes = MAX_INTENTOS - n_fallos

            await interaction.user.send(embed=emb.sudo_fail())
            log_warning(f"[SUDO] Fallo de autenticación para user_id={user_id} "
                        f"(intento {n_fallos}/{MAX_INTENTOS})")

            async with await _get_conn_for_audit() as conn:
                await audit(
                    conn,
                    tipo        = "sudo_fail",
                    descripcion = f"Intento fallido de SUDO — user_id={user_id} "
                                  f"({n_fallos}/{MAX_INTENTOS})",
                    actor_id    = user_id,
                    detalles    = {"intentos": n_fallos},
                )

            # Notificar al Owner
            await self._notificar_owner_fallo(user_id, n_fallos)

            if restantes <= 0:
                await interaction.user.send(
                    embed=emb.error(
                        "Cuenta bloqueada",
                        f"Has superado el número máximo de intentos.\n"
                        f"Acceso bloqueado durante {BLOQUEO_SEGUNDOS // 60} minutos.",
                    )
                )

    # -----------------------------------------------------------------------
    # /sudo estado
    # -----------------------------------------------------------------------

    @sudo_group.command(name="estado", description="Ver el estado de tu sesión SUDO")
    @require_role(ADMIN)
    async def sudo_estado(self, interaction: Interaction) -> None:
        """
        Muestra el tiempo restante de la sesión SUDO activa del usuario.

        Args:
            interaction: Contexto de Discord.
        """
        restante = get_sudo_remaining(interaction.user.id)
        if restante > 0:
            await interaction.response.send_message(
                embed=emb.sudo_activa(restante), ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=emb.info("Sin sesión SUDO",
                               "No tienes una sesión SUDO activa.\n"
                               "Usa `/sudo auth` para autenticarte."),
                ephemeral=True,
            )

    # -----------------------------------------------------------------------
    # /sudo revocar
    # -----------------------------------------------------------------------

    @sudo_group.command(name="revocar",
                        description="Revocar sesión SUDO de un usuario (Owner/Holder)")
    @app_commands.describe(usuario="Usuario cuya sesión SUDO revocar")
    @require_role(HOLDER)
    async def sudo_revocar(self, interaction: Interaction,
                            usuario: discord.Member) -> None:
        """
        Revoca manualmente la sesión SUDO de un usuario.
        Solo Owner/Holder pueden ejecutar esto.

        Args:
            interaction: Contexto de Discord.
            usuario    : Miembro cuya sesión se revoca.
        """
        restante = get_sudo_remaining(usuario.id)
        if restante == 0:
            await interaction.response.send_message(
                embed=emb.advertencia("Sin sesión",
                                       f"{usuario.mention} no tiene una sesión SUDO activa."),
                ephemeral=True,
            )
            return

        sudo_deactivate(usuario.id)
        log_info(f"[SUDO] Sesión revocada por {interaction.user.id} para user_id={usuario.id}")

        async with await _get_conn_for_audit() as conn:
            await audit(
                conn,
                tipo        = "sudo_expire",
                descripcion = f"Sesión SUDO de {usuario} revocada manualmente",
                actor_id    = interaction.user.id,
                target_id   = usuario.id,
            )

        await interaction.response.send_message(
            embed=emb.ok("Sesión revocada",
                          f"La sesión SUDO de {usuario.mention} ha sido revocada.")
        )

        try:
            await usuario.send(
                embed=emb.advertencia(
                    "Sesión SUDO revocada",
                    "Tu sesión SUDO ha sido revocada por un administrador.",
                )
            )
        except discord.Forbidden:
            pass

    # -----------------------------------------------------------------------
    # Helper: notificar al Owner de un fallo SUDO
    # -----------------------------------------------------------------------

    async def _notificar_owner_fallo(self, user_id: int, n_fallos: int) -> None:
        """
        Notifica al Owner por MD de un intento fallido de autenticación SUDO.

        Args:
            user_id  : discord user_id del actor.
            n_fallos : Número acumulado de fallos.
        """
        owner_id = getattr(self.bot, "owner_id", None)
        if not owner_id:
            return

        owner = self.bot.get_user(owner_id)
        if not owner:
            return

        try:
            await owner.send(
                embed=discord.Embed(
                    title="⚠️  Intento fallido de SUDO",
                    description=f"El usuario <@{user_id}> ha fallado la autenticación SUDO.\n"
                                f"Intentos acumulados: **{n_fallos}/{MAX_INTENTOS}**",
                    color=emb.C_WARN,
                ).set_footer(text=emb.FOOTER_TEXT)
            )
        except discord.Forbidden:
            log_warning(f"[SUDO] No se pudo notificar al Owner (user_id={owner_id})")


# ---------------------------------------------------------------------------
# Helper de conexión para auditoría (import tardío para evitar circular)
# ---------------------------------------------------------------------------

async def _get_conn_for_audit():
    """Devuelve un context manager de conexión para operaciones de auditoría."""
    from db.repository import get_conn
    import aiosqlite

    class _ConnCtx:
        def __init__(self): self.conn = None
        async def __aenter__(self):
            self.conn = await get_conn()
            return self.conn
        async def __aexit__(self, *_):
            if self.conn:
                await self.conn.close()

    return _ConnCtx()


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot) -> None:
    """Registra el cog en el bot."""
    await bot.add_cog(SudoCog(bot))