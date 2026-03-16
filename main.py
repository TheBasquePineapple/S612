"""
RAISA — Record and Information Administration of the SCP Foundation Security
Bot principal de Discord para servidor de rol narrativo SCP.

Responsabilidad : Arranque del bot, carga de cogs, tareas periódicas globales.
Dependencias    : discord.py >= 2.0, python-dotenv, aiosqlite
Autor           : Proyecto RAISA
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

import db.repository as repository
from utils.logger import setup_logging

# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN INICIAL
# ──────────────────────────────────────────────────────────────────────────────

load_dotenv()
setup_logging()
log = logging.getLogger("raisa.main")

# Cogs que se cargarán al iniciar. El orden importa para dependencias.
COGS: list[str] = [
    "cogs.eventos",    # Primero: define el estado global ON/OFF
    "cogs.economia",
    "cogs.inventario",
    "cogs.medico",
    "cogs.radio",
    "cogs.vehiculos",
    "cogs.registro",   # Último: depende de config ya cargada
]


# ──────────────────────────────────────────────────────────────────────────────
# CLASE PRINCIPAL DEL BOT
# ──────────────────────────────────────────────────────────────────────────────

class RAISA(commands.Bot):
    """
    Clase principal del bot RAISA.

    Hereda de commands.Bot y añade:
    - Repositorio SQLite centralizado (self.repo)
    - Sesiones SUDO activas en memoria (self.sudo_sessions)
    - Referencia al ID del Owner y Holder desde .env
    """

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True   # Para leer mensajes de MD en registro
        intents.members = True           # Para asignar roles automáticamente
        intents.guilds = True

        super().__init__(
            command_prefix="!",          # Prefijo legacy (apenas se usa; slash primario)
            intents=intents,
            help_command=None,           # Desactivamos el help nativo
        )

        # Vincular errores de slash commands
        self.tree.on_error = self.on_app_command_error

        # IDs críticos desde .env
        self.owner_id: int = int(os.getenv("OWNER_ID", "0"))
        self.holder_id: int = int(os.getenv("HOLDER_ID", "0"))

        # Sesiones SUDO: {user_id: datetime_expiry}
        # Almacenadas únicamente en RAM; nunca persisten entre reinicios.
        self.sudo_sessions: dict[int, float] = {}

        # Repositorio centralizado (módulo de funciones en db/repository)
        self.repo = repository

    async def truncate_all_tables(self) -> None:
        """Helper para desarrollo: delega en el repositorio el vaciado de tablas."""
        await self.repo.truncate_all_tables()

    # ──────────────────────────────────────────────────────────────────────
    # LIFECYCLE
    # ──────────────────────────────────────────────────────────────────────

    async def setup_hook(self) -> None:
        """
        Ejecutado una sola vez antes del primer on_ready.
        Inicializa la BBDD y carga todos los cogs.
        """
        # Inicializar repositorio: abrir conexión y aplicar esquema
        repository.get_connection()
        # await self.truncate_all_tables()  # Descomentar para resetear BBDD en desarrollo
        log.info("Base de datos inicializada correctamente.")

        # Cargar cogs de forma modular
        for cog in COGS:
            try:
                await self.load_extension(cog)
                log.info("Cog cargado: %s", cog)
            except Exception as exc:  # noqa: BLE001
                log.error("Error cargando cog %s: %s", cog, exc, exc_info=True)

        # ── Sincronización de slash commands ──────────────────────────────────
        # tree.sync() envía a Discord la lista completa actual del árbol local:
        #   · Comandos nuevos → se añaden
        #   · Comandos eliminados → se borran en Discord
        #   · Comandos sin cambios → se mantienen
        # No es necesario llamar a clear_commands(): hacerlo vaciaría el árbol
        # justo antes del sync, borrando todo lo que los cogs registraron.
        synced = await self.tree.sync()
        log.info("Slash commands sincronizados: %d comando(s) registrado(s).", len(synced))

        # Iniciar tarea de limpieza de sesiones SUDO
        self.purge_sudo_sessions.start()

        # Iniciar tarea de limpieza de assets temporales (cada 24 h)
        self.cleanup_temp_assets.start()

    async def on_ready(self) -> None:
        log.info("RAISA operativa — Usuario: %s | ID: %s", self.user, self.user.id)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="los registros de la Fundación"
            )
        )

    async def on_command_error(self, ctx: commands.Context, error: Exception) -> None:
        """Captura errores no controlados en comandos prefix."""
        log.error("Error en comando %s: %s", ctx.command, error, exc_info=True)

    # ──────────────────────────────────────────────────────────────────────
    # ERROR HANDLING — SLASH COMMANDS
    # ──────────────────────────────────────────────────────────────────────

    async def on_app_command_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError) -> None:
        """Captura errores en slash commands y responde al usuario."""
        log.error("Error en slash command '%s': %s", interaction.command.name if interaction.command else "desconocido", error, exc_info=True)
        
        if interaction.response.is_done():
            send = interaction.followup.send
        else:
            send = interaction.response.send_message

        try:
            if isinstance(error, discord.app_commands.CheckFailure):
                # Esto captura fallos en decoradores de permisos si usaran app_commands.check
                await send("❌ No tienes permisos para usar este comando.", ephemeral=True)
            else:
                await send(f"⚠️ Ha ocurrido un error interno: `{error}`", ephemeral=True)
        except Exception:
            log.error("No se pudo enviar mensaje de error al usuario.", exc_info=True)

    # ──────────────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────────────
    # TAREAS PERIÓDICAS
    # ──────────────────────────────────────────────────────────────────────

    @tasks.loop(minutes=10)
    async def purge_sudo_sessions(self) -> None:
        """
        Elimina sesiones SUDO expiradas del dict en memoria.
        Intervalo: 10 minutos (mínimo permitido por restricciones de hardware).
        """
        import time
        now = time.monotonic()
        expired = [uid for uid, expiry in self.sudo_sessions.items() if now > expiry]
        for uid in expired:
            del self.sudo_sessions[uid]
            log.info("Sesión SUDO expirada para user_id=%s", uid)

    @tasks.loop(hours=24)
    async def cleanup_temp_assets(self) -> None:
        """
        Elimina archivos temporales en /data/assets/ con más de 7 días.
        Restricción de disco: evitar acumulación indefinida de assets.
        """
        import time
        assets_dir = Path("data/assets")
        if not assets_dir.exists():
            return

        cutoff = time.time() - (7 * 24 * 3600)  # 7 días en segundos
        removed = 0
        for file in assets_dir.rglob("*"):
            if file.is_file() and file.stat().st_mtime < cutoff:
                try:
                    file.unlink()
                    removed += 1
                except OSError as exc:
                    log.warning("No se pudo eliminar asset temporal %s: %s", file, exc)

        if removed:
            log.info("Limpieza de assets: %d archivos eliminados.", removed)

    @purge_sudo_sessions.before_loop
    @cleanup_temp_assets.before_loop
    async def _wait_ready(self) -> None:
        await self.wait_until_ready()


# ──────────────────────────────────────────────────────────────────────────────
# PUNTO DE ENTRADA
# ──────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    """Función asíncrona principal. Arranca el bot con el token de .env."""
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        log.critical("DISCORD_TOKEN no encontrado en .env. Abortando.")
        sys.exit(1)

    async with RAISA() as bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
