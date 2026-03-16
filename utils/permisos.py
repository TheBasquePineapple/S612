"""
utils/permisos.py — Decoradores de autenticación y autorización de RAISA
=========================================================================
Responsabilidad : Middleware de permisos para comandos slash de Discord.
                  Provee @require_role(nivel) y @require_sudo para aplicar
                  antes de ejecutar cualquier lógica de negocio.
Dependencias    : discord.py >= 2.0, utils.embeds, utils.logger
Autor           : RAISA Dev

Jerarquía de niveles (mayor a menor)
-------------------------------------
  OWNER    = 6  — ID en .env, todos los permisos
  HOLDER   = 5  — ID en .env, equivalente a Owner
  ADMIN    = 4  — IDs en config/roles.json
  GESTOR   = 3  — Rol de Discord en config/roles.json
  NARRADOR = 2  — Rol de Discord en config/roles.json
  USUARIO  = 1  — Rol de Discord en config/roles.json
  VISITANTE= 0  — Sin rol asignado

NOTA SOBRE SESIONES SUDO
-------------------------
Las sesiones SUDO se almacenan en el dict en memoria `_sudo_sessions`.
Son INTENCIONALES en memoria: un reinicio del bot invalida todas las
sesiones activas, lo cual es una característica de seguridad.
No persistir en BBDD bajo ninguna circunstancia.
"""

import asyncio
import os
import time
from collections import OrderedDict
from functools import wraps
from typing import Callable

import discord
from discord import Interaction
from discord.ext import tasks

from utils import embeds as emb
from utils.logger import audit, log_info, log_warning

# ---------------------------------------------------------------------------
# Constantes de nivel
# ---------------------------------------------------------------------------
VISITANTE = 0
USUARIO   = 1
NARRADOR  = 2
GESTOR    = 3
ADMIN     = 4
HOLDER    = 5
OWNER     = 6

NIVEL_NOMBRES = {
    VISITANTE: "Visitante",
    USUARIO:   "Usuario",
    NARRADOR:  "Narrador",
    GESTOR:    "Gestor",
    ADMIN:     "Admin",
    HOLDER:    "Holder",
    OWNER:     "Owner",
}

# ---------------------------------------------------------------------------
# Sesiones SUDO — SOLO EN MEMORIA, nunca persistir
# ---------------------------------------------------------------------------
# Estructura: { user_id: timestamp_expiracion }
_sudo_sessions: OrderedDict[int, float] = OrderedDict()
SUDO_DURATION_SECONDS = 30 * 60   # 30 minutos
SUDO_MAX_SESSIONS     = 20        # límite de sesiones simultáneas (LRU)


def _sudo_is_active(user_id: int) -> bool:
    """
    Comprueba si existe una sesión SUDO activa y no expirada para el usuario.

    Args:
        user_id: discord user_id a comprobar.

    Returns:
        True si la sesión existe y no ha expirado.
    """
    expires_at = _sudo_sessions.get(user_id)
    if expires_at is None:
        return False
    if time.monotonic() > expires_at:
        _sudo_sessions.pop(user_id, None)
        return False
    return True


def sudo_activate(user_id: int) -> None:
    """
    Activa una sesión SUDO para el usuario indicado durante SUDO_DURATION_SECONDS.
    Implementa LRU: si se supera SUDO_MAX_SESSIONS, expulsa la más antigua.

    Args:
        user_id: discord user_id que obtuvo autenticación SUDO.
    """
    if len(_sudo_sessions) >= SUDO_MAX_SESSIONS:
        _sudo_sessions.popitem(last=False)  # expulsar la más antigua (FIFO)
    _sudo_sessions[user_id] = time.monotonic() + SUDO_DURATION_SECONDS
    log_info(f"[SUDO] Sesión activada para user_id={user_id} ({SUDO_DURATION_SECONDS}s)")


def sudo_deactivate(user_id: int) -> None:
    """
    Invalida manualmente la sesión SUDO de un usuario.

    Args:
        user_id: discord user_id cuya sesión se invalida.
    """
    _sudo_sessions.pop(user_id, None)


def get_sudo_remaining(user_id: int) -> int:
    """
    Devuelve los segundos restantes de la sesión SUDO del usuario, o 0 si no activa.

    Args:
        user_id: discord user_id a consultar.

    Returns:
        Segundos restantes (0 si sin sesión o expirada).
    """
    expires_at = _sudo_sessions.get(user_id)
    if expires_at is None:
        return 0
    remaining = int(expires_at - time.monotonic())
    return max(0, remaining)


# ---------------------------------------------------------------------------
# Tarea periódica de limpieza de sesiones expiradas
# Se registra automáticamente al importar el módulo.
# Intervalo: 10 minutos (mínimo según restricción de hardware del target).
# ---------------------------------------------------------------------------
@tasks.loop(minutes=10)
async def _cleanup_sudo_sessions() -> None:
    """Limpia sesiones SUDO expiradas del dict en memoria."""
    now = time.monotonic()
    expired = [uid for uid, exp in _sudo_sessions.items() if now > exp]
    for uid in expired:
        _sudo_sessions.pop(uid, None)
        log_info(f"[SUDO] Sesión expirada automáticamente para user_id={uid}")


def start_sudo_cleanup() -> None:
    """Inicia la tarea periódica de limpieza. Llamar desde main.py on_ready."""
    if not _cleanup_sudo_sessions.is_running():
        _cleanup_sudo_sessions.start()


# ---------------------------------------------------------------------------
# Resolución de nivel de un usuario de Discord
# ---------------------------------------------------------------------------

def _get_config(bot) -> dict:
    """
    Obtiene la configuración de roles del bot (cacheada en bot.raisa_config).

    Args:
        bot: Instancia del bot con atributo raisa_config cargado en main.py.

    Returns:
        Dict con la estructura de config/roles.json.
    """
    return getattr(bot, "raisa_config", {})


def get_user_level(interaction: Interaction) -> int:
    """
    Determina el nivel de autorización de un usuario a partir de sus roles
    de Discord y su ID comparado con Owner/Holder en .env.

    Args:
        interaction: Objeto Interaction de discord.py con .user y .guild.

    Returns:
        Nivel entero (VISITANTE=0 … OWNER=6).
    """
    user = interaction.user
    bot  = interaction.client

    # Owner y Holder por ID (desde .env, cargados en bot)
    owner_id  = getattr(bot, "owner_id",  None)
    holder_id = getattr(bot, "holder_id", None)

    if owner_id  and user.id == owner_id:  return OWNER
    if holder_id and user.id == holder_id: return HOLDER

    # Resto de niveles por rol de Discord
    cfg   = _get_config(bot)
    roles = cfg.get("roles", {})

    if not interaction.guild:
        return VISITANTE   # DM sin guild → visitante por defecto

    member_role_ids = {r.id for r in user.roles}

    if roles.get("admin")    in member_role_ids: return ADMIN
    if roles.get("gestor")   in member_role_ids: return GESTOR
    if roles.get("narrador") in member_role_ids: return NARRADOR
    if roles.get("usuario")  in member_role_ids: return USUARIO

    return VISITANTE


# ---------------------------------------------------------------------------
# Decoradores
# ---------------------------------------------------------------------------

def require_role(nivel_minimo: int) -> Callable:
    """
    Decorador para comandos slash que verifica que el usuario tenga
    al menos el nivel de autorización indicado antes de ejecutar.

    Uso:
        @app_commands.command(name="mi-comando")
        @require_role(NARRADOR)
        async def mi_comando(self, interaction: Interaction): ...

    Args:
        nivel_minimo: Nivel mínimo requerido (constante de este módulo).

    Returns:
        Decorador que envuelve la función del comando.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(self_or_interaction, *args, **kwargs):
            # Determinar el objeto Interaction correctamente
            # (puede ser primer arg si es método de cog, o el propio self_or_interaction)
            if isinstance(self_or_interaction, Interaction):
                interaction = self_or_interaction
            else:
                # Método de cog: primer arg positional es interaction
                interaction = args[0] if args else None
                if not isinstance(interaction, Interaction):
                    # Buscar en kwargs
                    interaction = kwargs.get("interaction")

            if interaction is None:
                return  # No se puede determinar contexto

            user_level = get_user_level(interaction)

            if user_level < nivel_minimo:
                nombre_requerido = NIVEL_NOMBRES.get(nivel_minimo, str(nivel_minimo))
                await interaction.response.send_message(
                    embed=emb.acceso_denegado(nombre_requerido),
                    ephemeral=True,
                )
                return

            # Nivel suficiente → ejecutar comando
            if isinstance(self_or_interaction, Interaction):
                return await func(self_or_interaction, *args, **kwargs)
            else:
                return await func(self_or_interaction, *args, **kwargs)

        return wrapper
    return decorator


def require_sudo(func: Callable) -> Callable:
    """
    Decorador para comandos críticos que requieren autenticación SUDO activa.

    Flujo si no hay sesión activa:
      1. Notifica al usuario que debe autenticarse por MD.
      2. El bot envía un MD pidiendo la clave SUDO.
      3. Si la clave es correcta → activa sesión y reintenta el comando.
      4. Si falla → registra en audit_log y notifica al Owner.

    Para activar la sesión SUDO de forma interactiva, usar el comando
    /sudo auth — este decorador solo verifica que ya exista una sesión activa.

    Uso:
        @app_commands.command(name="reset-bbdd")
        @require_sudo
        async def reset_bbdd(self, interaction: Interaction): ...
    """
    @wraps(func)
    async def wrapper(self_or_interaction, *args, **kwargs):
        if isinstance(self_or_interaction, Interaction):
            interaction = self_or_interaction
        else:
            interaction = args[0] if args else kwargs.get("interaction")

        if interaction is None:
            return

        user_id = interaction.user.id

        if not _sudo_is_active(user_id):
            remaining_hint = (
                f"Tienes una sesión activa por {get_sudo_remaining(user_id)}s más."
                if get_sudo_remaining(user_id) > 0
                else "No tienes una sesión SUDO activa."
            )
            await interaction.response.send_message(
                embed=emb.error(
                    "Autenticación requerida",
                    f"{remaining_hint}\n\nUsa `/sudo auth` para autenticarte.\n"
                    "La sesión dura 30 minutos.",
                ),
                ephemeral=True,
            )
            return

        if isinstance(self_or_interaction, Interaction):
            return await func(self_or_interaction, *args, **kwargs)
        else:
            return await func(self_or_interaction, *args, **kwargs)

    return wrapper