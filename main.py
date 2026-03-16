"""
main.py — Punto de entrada del bot RAISA
=========================================
Responsabilidad : Inicializar el bot de Discord, cargar configuración,
                  registrar cogs y gestionar el ciclo de vida principal.
Dependencias    : discord.py >= 2.0, python-dotenv, aiosqlite
Autor           : RAISA Dev

Uso
---
  python main.py

Variables de entorno requeridas (.env)
--------------------------------------
  DISCORD_TOKEN  — Token del bot
  OWNER_ID       — Discord user_id del Owner
  HOLDER_ID      — Discord user_id del Holder
  SUDO_KEY       — Clave SUDO para comandos críticos
  DB_PATH        — Ruta a la BBDD SQLite (default: data/raisa.db)
"""

import asyncio
import json
import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

from utils.logger import log_info, log_error, log_warning
from utils.permisos import start_sudo_cleanup

# ---------------------------------------------------------------------------
# Carga de variables de entorno
# ---------------------------------------------------------------------------
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID      = int(os.getenv("OWNER_ID",  "0"))
HOLDER_ID     = int(os.getenv("HOLDER_ID", "0"))
SUDO_KEY      = os.getenv("SUDO_KEY", "")
DEBUG         = os.getenv("DEBUG", "0") == "1"

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN no está configurado en .env")
if not SUDO_KEY:
    log_warning("SUDO_KEY no configurada — los comandos SUDO estarán deshabilitados.")


# ---------------------------------------------------------------------------
# Cogs que se cargan al iniciar
# ---------------------------------------------------------------------------
COGS = [
    "cogs.registro",
    "cogs.inventario",
    "cogs.medico",
    "cogs.radio",
    "cogs.vehiculos",
    "cogs.economia",
    "cogs.eventos",
    "cogs.sudo",       # Cog de autenticación SUDO (independiente)
]


# ---------------------------------------------------------------------------
# Clase principal del bot
# ---------------------------------------------------------------------------

class RAISA(commands.Bot):
    """
    Bot principal RAISA.

    Attributes:
        owner_id    : discord user_id del Owner (desde .env).
        holder_id   : discord user_id del Holder (desde .env).
        sudo_key    : Clave SUDO hasheada (cargada desde .env).
        raisa_config: Config de roles/canales (desde config/roles.json).
    """

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members      = True   # Necesario para asignar roles
        intents.message_content = True  # Necesario para formularios por MD

        super().__init__(
            command_prefix="!",   # Prefijo legacy (no usado activamente, solo slash)
            intents=intents,
            help_command=None,    # Deshabilitar el help por defecto
        )

        self.owner_id  = OWNER_ID
        self.holder_id = HOLDER_ID
        self.sudo_key  = SUDO_KEY

        # Caché de configuración de roles (cargada en setup_hook)
        self.raisa_config: dict = {}

        # Caché de webhooks en memoria {channel_id: discord.Webhook}
        # Sincronizado con BBDD en radio.py
        self.webhook_cache: dict[int, discord.Webhook] = {}

    async def setup_hook(self) -> None:
        """
        Llamado por discord.py antes de conectar.
        Carga configuración y registra todos los cogs.
        """
        # Cargar config de roles
        self._load_config()

        # Cargar todos los cogs
        for cog in COGS:
            try:
                await self.load_extension(cog)
                log_info(f"  ✅  Cog cargado: {cog}")
            except Exception as exc:
                log_error(f"  ❌  Error cargando cog {cog}: {exc}")
                if DEBUG:
                    raise

        # Sincronizar comandos slash con Discord
        # En producción, sincronizar solo al cambiar comandos para no consumir rate-limit
        try:
            synced = await self.tree.sync()
            log_info(f"  🔄  {len(synced)} comandos slash sincronizados")
        except Exception as exc:
            log_error(f"  ❌  Error sincronizando slash commands: {exc}")

    def _load_config(self) -> None:
        """Carga y cachea la configuración de roles desde config/roles.json."""
        config_path = Path("config/roles.json")
        if not config_path.exists():
            log_warning("config/roles.json no encontrado — usando config vacía")
            self.raisa_config = {}
            return
        try:
            with config_path.open(encoding="utf-8") as f:
                self.raisa_config = json.load(f)
            log_info("  📋  Configuración de roles cargada")
        except Exception as exc:
            log_error(f"  ❌  Error cargando config/roles.json: {exc}")
            self.raisa_config = {}

    def reload_config(self) -> None:
        """Recarga la configuración de roles (útil después de editar el archivo)."""
        self._load_config()
        log_info("[CONFIG] Configuración de roles recargada")

    async def on_ready(self) -> None:
        """Evento disparado cuando el bot se conecta correctamente a Discord."""
        log_info(f"RAISA conectada como {self.user} (ID: {self.user.id})")
        log_info(f"Servidores: {len(self.guilds)}")

        # Iniciar limpieza periódica de sesiones SUDO
        start_sudo_cleanup()
        log_info("Limpieza periódica de sesiones SUDO iniciada")

        # Reconstruir Views de verificación pendientes
        # (para que los botones funcionen tras un reinicio)
        await self._restore_verification_views()

        # Establecer presencia
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="las operaciones de la Fundación SCP",
            )
        )

    async def _restore_verification_views(self) -> None:
        """
        Reconstruye los Views de verificación de fichas pendientes.
        Necesario para que los botones sigan funcionando tras reinicio.
        Ver REVIEW.md §2.2.
        """
        try:
            import aiosqlite
            from db import repository as repo

            async with await repo.get_conn() as conn:
                pendientes = await repo.get_pending_verifications(conn)

            for fila in pendientes:
                try:
                    # Importar aquí para evitar circular import en setup_hook
                    from cogs.registro import VerificationView
                    view = VerificationView(
                        char_id  = fila["character_id"],
                        user_id  = fila["user_id"],
                        nombre   = fila["nombre_completo"],
                    )
                    self.add_view(view, message_id=fila["message_id"])
                    log_info(f"  🔄  View de verificación restaurado para char_id={fila['character_id']}")
                except Exception as exc:
                    log_warning(f"  ⚠  No se pudo restaurar view char_id={fila['character_id']}: {exc}")
        except Exception as exc:
            log_error(f"Error restaurando views de verificación: {exc}")

    async def on_command_error(self, ctx, error) -> None:
        """Manejo global de errores de comandos (prefijo legacy)."""
        log_error(f"Error de comando: {error}")

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError,
    ) -> None:
        """
        Manejo global de errores de comandos slash.
        No expone detalles técnicos al usuario.
        """
        from utils import embeds as emb

        log_error(f"Error en slash command: {error}")

        if interaction.response.is_done():
            method = interaction.followup.send
        else:
            method = interaction.response.send_message

        await method(
            embed=emb.error(
                "Error interno",
                "Se produjo un error inesperado. El equipo técnico ha sido notificado.",
            ),
            ephemeral=True,
        )

    async def on_member_remove(self, member: discord.Member) -> None:
        """
        Cuando un usuario abandona el servidor, registrar en log.
        No eliminar datos automáticamente — decisión del Gestor.
        """
        log_info(f"[MEMBER_REMOVE] {member} ({member.id}) abandonó el servidor")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    """Función principal asíncrona."""
    # Crear directorios necesarios si no existen
    for d in ["data", "data/characters", "data/backups", "assets/radio"]:
        Path(d).mkdir(parents=True, exist_ok=True)

    bot = RAISA()

    try:
        async with bot:
            await bot.start(DISCORD_TOKEN)
    except KeyboardInterrupt:
        log_info("Apagando RAISA...")
    except discord.LoginFailure:
        log_error("Token de Discord inválido. Verifica DISCORD_TOKEN en .env")
        raise


if __name__ == "__main__":
    asyncio.run(main())