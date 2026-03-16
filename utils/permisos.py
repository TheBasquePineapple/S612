"""
RAISA — Decoradores de permisos y autenticación SUDO
utils/permisos.py

Responsabilidad : Middleware reutilizable @require_role y @require_sudo.
                  Se aplica a cada comando antes de ejecutar cualquier lógica.
Dependencias    : discord.py, json, os
Autor           : Proyecto RAISA

JERARQUÍA (mayor → menor):
    OWNER > HOLDER > ADMIN > GESTOR > NARRADOR > USUARIO > VISITANTE
"""

import functools
import json
import logging
import os
import time
from pathlib import Path

import discord
from discord import app_commands

from utils.embeds import embed_acceso_denegado

log = logging.getLogger("raisa.permisos")

# ──────────────────────────────────────────────────────────────────────────────
# CONSTANTES DE RANGO
# ──────────────────────────────────────────────────────────────────────────────

RANGO_VISITANTE  = 0
RANGO_USUARIO    = 1
RANGO_NARRADOR   = 2
RANGO_GESTOR     = 3
RANGO_ADMIN      = 4
RANGO_HOLDER     = 5
RANGO_OWNER      = 6

NOMBRES_RANGO = {
    RANGO_VISITANTE : "Visitante",
    RANGO_USUARIO   : "Usuario",
    RANGO_NARRADOR  : "Narrador",
    RANGO_GESTOR    : "Gestor",
    RANGO_ADMIN     : "Administrador",
    RANGO_HOLDER    : "Holder",
    RANGO_OWNER     : "Owner",
}

_roles_config: dict | None = None


def _cargar_config_roles() -> dict:
    """Carga /config/roles.json con caché de módulo (se invalida al reiniciar)."""
    global _roles_config
    if _roles_config is None:
        path = Path("config/roles.json")
        _roles_config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return _roles_config


def invalidar_cache_roles() -> None:
    """Fuerza recarga de roles.json en la próxima llamada. Llamar tras modificar config."""
    global _roles_config
    _roles_config = None


def get_rango(member: discord.Member) -> int:
    """
    Calcula el rango numérico de un miembro del servidor.

    :param member: discord.Member del que obtener el rango.
    :returns: Entero de RANGO_VISITANTE a RANGO_OWNER.
    """
    cfg = _cargar_config_roles()
    uid = member.id
    role_ids = {r.id for r in member.roles}

    owner_id  = int(os.getenv("OWNER_ID", "0"))
    holder_id = int(os.getenv("HOLDER_ID", "0"))

    if uid == owner_id:
        return RANGO_OWNER
    if uid == holder_id:
        return RANGO_HOLDER
    if uid in [int(x) for x in cfg.get("admin_ids", [])]:
        return RANGO_ADMIN
    if int(cfg.get("gestor_role_id", 0)) in role_ids:
        return RANGO_GESTOR
    if int(cfg.get("narrador_role_id", 0)) in role_ids:
        return RANGO_NARRADOR
    if int(cfg.get("usuario_role_id", 0)) in role_ids:
        return RANGO_USUARIO
    return RANGO_VISITANTE


# ──────────────────────────────────────────────────────────────────────────────
# DECORADOR @require_role
# ──────────────────────────────────────────────────────────────────────────────

def require_role(nivel_minimo: int):
    """
    Decorador para slash commands (app_commands.command y subcomandos de Group).
    Verifica que el ejecutor tenga rango >= nivel_minimo.
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Buscar la interacción entre los argumentos (puede ser el 1º o 2º)
            interaction = None
            for arg in args:
                if isinstance(arg, discord.Interaction):
                    interaction = arg
                    break
            
            if not interaction:
                for kwarg in kwargs.values():
                    if isinstance(kwarg, discord.Interaction):
                        interaction = kwarg
                        break
            
            if not interaction:
                log.error("No se encontró discord.Interaction en los argumentos de %s", func.__name__)
                # Si no hay interacción, no podemos responder; dejamos que falle
                return await func(*args, **kwargs)

            member = interaction.user
            rango = get_rango(member)

            if rango < nivel_minimo:
                nombre_requerido = NOMBRES_RANGO.get(nivel_minimo, str(nivel_minimo))
                embed = embed_acceso_denegado(nombre_requerido)
                if not interaction.response.is_done():
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                else:
                    await interaction.followup.send(embed=embed, ephemeral=True)
                
                log.warning(
                    "Acceso denegado: user=%s rango=%s requería=%s comando=%s",
                    member.id, NOMBRES_RANGO.get(rango, "visitante"), nombre_requerido, func.__name__
                )
                return

            return await func(*args, **kwargs)

        return wrapper
    return decorator


# ──────────────────────────────────────────────────────────────────────────────
# DECORADOR @require_sudo
# ──────────────────────────────────────────────────────────────────────────────

def require_sudo(func):
    """
    Decorador para slash commands que requieren autenticación SUDO activa.
    Si no hay sesión activa, inicia el flujo de solicitud de clave por MD.

    La sesión SUDO vive en bot.sudo_sessions: {user_id: timestamp_expiry}.
    Duración: 30 minutos (1800 segundos).

    Uso:
        @app_commands.command()
        @require_sudo
        async def borrar_todo(self, interaction, ...):
            ...
    """
    @functools.wraps(func)
    async def wrapper(self_cog, interaction: discord.Interaction, *args, **kwargs):
        bot = interaction.client
        uid = interaction.user.id
        now = time.monotonic()

        # ¿Hay sesión SUDO activa y vigente?
        expiry = bot.sudo_sessions.get(uid)
        if expiry and now < expiry:
            # Sesión válida → ejecutar comando
            return await func(self_cog, interaction, *args, **kwargs)

        # Sin sesión válida → iniciar flujo de autenticación
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🔐 Autenticación SUDO requerida",
                description=(
                    "Este comando requiere autenticación elevada.\n"
                    "Se ha enviado una solicitud de clave a tu **Mensaje Directo**.\n"
                    "La sesión durará **30 minutos** una vez autenticada."
                ),
                color=discord.Color.orange(),
            ),
            ephemeral=True,
        )

        try:
            dm = await interaction.user.create_dm()
            await dm.send(
                embed=discord.Embed(
                    title="🔐 RAISA — Autenticación SUDO",
                    description=(
                        "Introduce la **clave SUDO** para continuar.\n"
                        "Tienes **60 segundos** para responder."
                    ),
                    color=discord.Color.orange(),
                )
            )
        except discord.Forbidden:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Error",
                    description="No se pudo enviarte un MD. Abre tus MDs del servidor.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        def check(m: discord.Message) -> bool:
            return m.channel.type == discord.ChannelType.private and m.author.id == uid

        try:
            msg = await bot.wait_for("message", check=check, timeout=60.0)
        except TimeoutError:
            await dm.send(
                embed=discord.Embed(
                    title="⏰ Tiempo agotado",
                    description="La autenticación SUDO ha expirado por inactividad.",
                    color=discord.Color.red(),
                )
            )
            return

        sudo_key = os.getenv("SUDO_KEY", "")
        if msg.content.strip() == sudo_key:
            # Clave correcta → activar sesión 30 min
            bot.sudo_sessions[uid] = now + 1800
            await bot.repo.log_accion("SUDO_OK", uid)
            log.info("Sesión SUDO activada para user_id=%s", uid)
            await dm.send(
                embed=discord.Embed(
                    title="✅ SUDO activado",
                    description="Sesión SUDO activa por **30 minutos**. Reintenta el comando.",
                    color=discord.Color.green(),
                )
            )
        else:
            # Clave incorrecta → registrar y notificar al Owner
            await bot.repo.log_accion("SUDO_FAIL", uid, detalle={"intento": "fallido"})
            log.warning("Intento SUDO fallido para user_id=%s", uid)

            # Notificar al Owner por MD
            owner_id = int(os.getenv("OWNER_ID", "0"))
            try:
                owner = await bot.fetch_user(owner_id)
                await owner.send(
                    embed=discord.Embed(
                        title="⚠️ Intento SUDO fallido",
                        description=(
                            f"El usuario <@{uid}> (ID: `{uid}`) ha introducido "
                            f"una clave SUDO incorrecta."
                        ),
                        color=discord.Color.red(),
                    )
                )
            except Exception:
                log.error("No se pudo notificar al Owner del intento SUDO fallido.")

            await dm.send(
                embed=discord.Embed(
                    title="❌ Clave incorrecta",
                    description="La clave SUDO introducida es incorrecta. Evento registrado.",
                    color=discord.Color.red(),
                )
            )

    return wrapper
