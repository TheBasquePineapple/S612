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
  
CORRECCIONES APLICADAS:
-  Agregado check de inicialización de BD en setup_hook
- Advertencia clara si la BD no existe o no está inicializada
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
DB_PATH       = Path(os.getenv("DB_PATH", "data/raisa.db"))

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
        Carga configuración, verifica BD y registra todos los cogs.
        """
        # CORREGIDO: Verificar que la base de datos existe y está inicializada
        await self._check_database()
        
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

    async def _check_database(self) -> None:
        """
        CORREGIDO: Verifica que la base de datos existe y tiene tablas críticas.
        Si no existe o está vacía, emite advertencia clara.
        
        Esto previene el error fatal reportado en el bug report donde el bot
        intenta consultar tablas que no existen.
        """
        if not DB_PATH.exists():
            log_error("=" * 80)
            log_error("⚠️  BASE DE DATOS NO ENCONTRADA")
            log_error(f"⚠️  No se encontró el archivo: {DB_PATH}")
            log_error("⚠️  ")
            log_error("⚠️  Debes inicializar la base de datos antes de arrancar el bot:")
            log_error("⚠️  1. python tools/migrate.py init")
            log_error("⚠️  2. python tools/migrate.py seed --all")
            log_error("⚠️  ")
            log_error("⚠️  El bot continuará pero FALLARÁ al ejecutar cualquier comando.")
            log_error("=" * 80)
            return
        
        # Verificar que tiene tablas críticas
        try:
            import aiosqlite
            conn = await aiosqlite.connect(DB_PATH)
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                "('characters', 'items', 'economy', 'vehicles')"
            )
            tables = await cursor.fetchall()
            await conn.close()
            
            if len(tables) < 4:
                log_warning("=" * 80)
                log_warning("⚠️  BASE DE DATOS INCOMPLETA")
                log_warning(f"⚠️  Solo se encontraron {len(tables)}/4 tablas críticas")
                log_warning("⚠️  ")
                log_warning("⚠️  Probablemente necesitas ejecutar:")
                log_warning("⚠️  python tools/migrate.py init")
                log_warning("⚠️  python tools/migrate.py seed --all")
                log_warning("⚠️  ")
                log_warning("⚠️  El bot puede fallar al ejecutar comandos.")
                log_warning("=" * 80)
            else:
                log_info(f"✅  Base de datos verificada: {DB_PATH}")
                log_info(f"✅  Tablas críticas encontradas: {len(tables)}/4")
                
        except Exception as exc:
            log_error(f"❌  Error verificando base de datos: {exc}")

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
        
        # Registrar el error completo en logs
        log_error(f"Error en comando slash: {error}")
        log_error(f"  Comando: {interaction.command.name if interaction.command else 'desconocido'}")
        log_error(f"  Usuario: {interaction.user} (ID: {interaction.user.id})")

        # Respuesta genérica al usuario (sin detalles técnicos)
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    embed=emb.error(
                        "Error interno",
                        "Ha ocurrido un error al ejecutar el comando. "
                        "El incidente ha sido registrado."
                    ),
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    embed=emb.error(
                        "Error interno",
                        "Ha ocurrido un error al procesar tu solicitud."
                    ),
                    ephemeral=True,
                )
        except Exception:
            # Si incluso el envío de error falla, solo registrar
            log_error("No se pudo enviar mensaje de error al usuario")


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

async def main() -> None:
    """Función principal asíncrona que arranca el bot."""
    bot = RAISA()
    
    log_info("=" * 80)
    log_info("RAISA — Record and Information Administration")
    log_info("Sistema de gestión operativa de la Fundación SCP")
    log_info("=" * 80)
    log_info("")
    log_info("Iniciando bot...")
    log_info("")
    
    try:
        async with bot:
            await bot.start(DISCORD_TOKEN)
    except KeyboardInterrupt:
        log_info("\n🛑  Interrupción de teclado detectada")
        log_info("🔌  Cerrando conexiones...")
    except Exception as exc:
        log_error(f"❌  Error fatal: {exc}")
        if DEBUG:
            raise
    finally:
        log_info("👋  RAISA desconectada")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass  # Ya manejado en main()