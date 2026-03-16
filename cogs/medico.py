"""
cogs/medico.py — Sistema médico de RAISA
=========================================
Responsabilidad : Gestión del estado médico de los personajes.
                  Usuarios ven y usan ítems. Narradores modifican estado.
Dependencias    : db.repository, utils.embeds, utils.permisos,
                  utils.validaciones, cogs.eventos
Autor           : RAISA Dev

Comandos
--------
  /medico estado [usuario]     — Ver estado médico (Usuario ve el propio, Narrador ve cualquiera)
  /medico herir                — Aplicar herida (Narrador+)
  /medico curar-herida         — Tratar/retirar herida (Narrador+)
  /medico fractura             — Aplicar fractura (Narrador+)
  /medico consciencia          — Cambiar consciencia (Narrador+)
  /medico sangre               — Modificar sangre (Narrador+)
  /medico muerte               — Ejecutar muerte (permisos según Evento)
"""

import json

import discord
from discord import Interaction, app_commands
from discord.ext import commands

from cogs.eventos import evento_activo
from db import repository as repo
from utils import embeds as emb
from utils.logger import audit, log_info
from utils.permisos import GESTOR, NARRADOR, USUARIO, get_user_level, require_role
from utils.validaciones import calcular_estado_general

# Opciones de consciencia
OPCIONES_CONSCIENCIA = [
    app_commands.Choice(name="Consciente",      value="Consciente"),
    app_commands.Choice(name="Semiconsciente",   value="Semiconsciente"),
    app_commands.Choice(name="Inconsciente",     value="Inconsciente"),
    app_commands.Choice(name="Clínico",          value="Clínico"),
]

OPCIONES_GRAVEDAD = [
    app_commands.Choice(name="Leve",    value="leve"),
    app_commands.Choice(name="Moderada", value="moderada"),
    app_commands.Choice(name="Grave",   value="grave"),
]

OPCIONES_FRACTURA = [
    app_commands.Choice(name="Simple",   value="simple"),
    app_commands.Choice(name="Expuesta", value="expuesta"),
]


class MedicoCog(commands.Cog, name="Médico"):
    """Cog del sistema médico."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    medico_group = app_commands.Group(
        name="medico", description="Sistema médico de personajes"
    )

    # -----------------------------------------------------------------------
    # Helper: obtener estado médico enriquecido con estado_general calculado
    # -----------------------------------------------------------------------

    async def _get_estado_enriquecido(self, conn, user_id: int) -> dict | None:
        """
        Obtiene el estado médico de un personaje con estado_general calculado.

        Args:
            conn    : Conexión aiosqlite.
            user_id : discord user_id.

        Returns:
            Dict con todos los campos médicos + estado_general, o None si no existe.
        """
        row = await repo.get_medical_state(conn, user_id)
        if not row:
            return None

        heridas   = json.loads(row["heridas"]   or "[]")
        fracturas = json.loads(row["fracturas"] or "[]")

        estado = dict(row)
        estado["heridas"]        = heridas
        estado["fracturas"]      = fracturas
        estado["estado_general"] = calcular_estado_general(
            sangre      = row["sangre"],
            consciencia = row["consciencia"],
            heridas     = heridas,
            fracturas   = fracturas,
        )
        return estado

    # -----------------------------------------------------------------------
    # /medico estado
    # -----------------------------------------------------------------------

    @medico_group.command(name="estado", description="Ver estado médico de un personaje")
    @app_commands.describe(usuario="Usuario a consultar (solo Narrador+ puede ver a otros)")
    @require_role(USUARIO)
    async def medico_estado(self, interaction: Interaction,
                             usuario: discord.Member | None = None) -> None:
        """
        Muestra el estado médico completo de un personaje.
        Un Usuario solo puede ver el suyo propio.
        Un Narrador+ puede ver el de cualquier personaje.

        Args:
            interaction: Contexto de Discord.
            usuario    : Miembro a consultar (None = el propio usuario).
        """
        nivel = get_user_level(interaction)

        # Si el usuario intenta ver a otro sin ser Narrador+
        if usuario and usuario.id != interaction.user.id and nivel < NARRADOR:
            await interaction.response.send_message(
                embed=emb.acceso_denegado("Narrador"), ephemeral=True
            )
            return

        target_id = (usuario.id if usuario else interaction.user.id)

        async with await repo.get_conn() as conn:
            char = await repo.get_character(conn, target_id)
            if not char or char["estado"] != "activo":
                await interaction.response.send_message(
                    embed=emb.error("Sin personaje",
                                    "El usuario no tiene un personaje activo."),
                    ephemeral=True,
                )
                return

            estado = await self._get_estado_enriquecido(conn, target_id)

        if not estado:
            # Estado médico no inicializado → operativo por defecto
            estado = {
                "heridas": [], "fracturas": [],
                "consciencia": "Consciente", "sangre": 100,
                "estado_general": "Operativo",
            }

        await interaction.response.send_message(
            embed=emb.estado_medico(char["nombre_completo"], estado),
            ephemeral=True,
        )

    # -----------------------------------------------------------------------
    # /medico herir
    # -----------------------------------------------------------------------

    @medico_group.command(name="herir", description="Aplicar herida a un personaje (Narrador+)")
    @app_commands.describe(
        usuario="Personaje afectado",
        tipo="Tipo de herida",
        localizacion="Localización anatómica",
        gravedad="Gravedad de la herida",
    )
    @app_commands.choices(gravedad=OPCIONES_GRAVEDAD)
    @require_role(NARRADOR)
    async def medico_herir(self, interaction: Interaction,
                            usuario: discord.Member,
                            tipo: str,
                            localizacion: str,
                            gravedad: str = "leve") -> None:
        """
        Aplica una nueva herida al estado médico de un personaje.

        Args:
            interaction  : Contexto de Discord.
            usuario      : Miembro afectado.
            tipo         : Descripción del tipo de herida.
            localizacion : Localización anatómica (torso, pierna izq., etc.).
            gravedad     : 'leve' | 'moderada' | 'grave'.
        """
        async with await repo.get_conn() as conn:
            char = await repo.get_character(conn, usuario.id)
            if not char or char["estado"] != "activo":
                await interaction.response.send_message(
                    embed=emb.error("Sin personaje", "El usuario no tiene personaje activo."),
                    ephemeral=True,
                )
                return

            estado_actual = await repo.get_medical_state(conn, usuario.id)
            heridas = json.loads(estado_actual["heridas"] if estado_actual else "[]") or []

            nueva_herida = {
                "tipo":              tipo,
                "localizacion":      localizacion,
                "gravedad":          gravedad,
                "estado_tratamiento": "sin tratar",
            }
            heridas.append(nueva_herida)

            await repo.upsert_medical_state(
                conn, usuario.id,
                campos={"heridas": heridas},
                modificado_por=interaction.user.id,
            )
            await audit(
                conn,
                tipo        = "mod_medica",
                descripcion = f"Herida aplicada a {char['nombre_completo']}: {tipo} en {localizacion} ({gravedad})",
                actor_id    = interaction.user.id,
                target_id   = usuario.id,
                detalles    = nueva_herida,
            )

        await interaction.response.send_message(
            embed=emb.ok(
                "Herida aplicada",
                f"**{char['nombre_completo']}** — {tipo} en **{localizacion}** ({gravedad})",
            )
        )

    # -----------------------------------------------------------------------
    # /medico curar-herida
    # -----------------------------------------------------------------------

    @medico_group.command(name="curar-herida",
                          description="Tratar o retirar una herida (Narrador+)")
    @app_commands.describe(usuario="Personaje", indice="Índice de la herida (ver /medico estado)",
                            estado_tratamiento="Nuevo estado de tratamiento")
    @require_role(NARRADOR)
    async def medico_curar(self, interaction: Interaction,
                            usuario: discord.Member,
                            indice: int,
                            estado_tratamiento: str = "tratada") -> None:
        """
        Modifica el estado de tratamiento de una herida existente.

        Args:
            interaction         : Contexto de Discord.
            usuario             : Miembro afectado.
            indice              : Índice 1-based de la herida en la lista.
            estado_tratamiento  : Nuevo estado ('tratada', 'estabilizada', 'sin tratar').
        """
        async with await repo.get_conn() as conn:
            char = await repo.get_character(conn, usuario.id)
            if not char:
                await interaction.response.send_message(
                    embed=emb.error("Sin personaje", "Personaje no encontrado."), ephemeral=True
                )
                return

            estado_actual = await repo.get_medical_state(conn, usuario.id)
            heridas = json.loads(estado_actual["heridas"] if estado_actual else "[]") or []

            idx = indice - 1   # convertir a 0-based
            if idx < 0 or idx >= len(heridas):
                await interaction.response.send_message(
                    embed=emb.error("Índice inválido",
                                    f"Solo hay {len(heridas)} herida(s). Índice recibido: {indice}"),
                    ephemeral=True,
                )
                return

            heridas[idx]["estado_tratamiento"] = estado_tratamiento
            await repo.upsert_medical_state(
                conn, usuario.id,
                campos={"heridas": heridas},
                modificado_por=interaction.user.id,
            )
            await audit(
                conn,
                tipo        = "mod_medica",
                descripcion = f"Herida #{indice} de {char['nombre_completo']} → {estado_tratamiento}",
                actor_id    = interaction.user.id,
                target_id   = usuario.id,
            )

        await interaction.response.send_message(
            embed=emb.ok("Herida actualizada",
                          f"Herida #{indice} de **{char['nombre_completo']}** → `{estado_tratamiento}`")
        )

    # -----------------------------------------------------------------------
    # /medico fractura
    # -----------------------------------------------------------------------

    @medico_group.command(name="fractura", description="Aplicar fractura a un personaje (Narrador+)")
    @app_commands.describe(usuario="Personaje", miembro="Miembro afectado", tipo="Tipo de fractura")
    @app_commands.choices(tipo=OPCIONES_FRACTURA)
    @require_role(NARRADOR)
    async def medico_fractura(self, interaction: Interaction,
                               usuario: discord.Member,
                               miembro: str,
                               tipo: str) -> None:
        """
        Aplica una fractura al estado médico de un personaje.

        Args:
            interaction: Contexto de Discord.
            usuario    : Miembro afectado.
            miembro    : Miembro afectado (brazo der., tibia izq., etc.).
            tipo       : 'simple' o 'expuesta'.
        """
        async with await repo.get_conn() as conn:
            char = await repo.get_character(conn, usuario.id)
            if not char:
                await interaction.response.send_message(
                    embed=emb.error("Sin personaje", "Personaje no encontrado."), ephemeral=True
                )
                return

            estado_actual = await repo.get_medical_state(conn, usuario.id)
            fracturas = json.loads(estado_actual["fracturas"] if estado_actual else "[]") or []
            fracturas.append({"miembro": miembro, "tipo": tipo})

            await repo.upsert_medical_state(
                conn, usuario.id,
                campos={"fracturas": fracturas},
                modificado_por=interaction.user.id,
            )
            await audit(
                conn,
                tipo        = "mod_medica",
                descripcion = f"Fractura {tipo} en {miembro} de {char['nombre_completo']}",
                actor_id    = interaction.user.id,
                target_id   = usuario.id,
            )

        await interaction.response.send_message(
            embed=emb.ok("Fractura registrada",
                          f"**{char['nombre_completo']}** — fractura {tipo} en **{miembro}**")
        )

    # -----------------------------------------------------------------------
    # /medico consciencia
    # -----------------------------------------------------------------------

    @medico_group.command(name="consciencia",
                          description="Cambiar nivel de consciencia (Narrador+)")
    @app_commands.describe(usuario="Personaje", nivel="Nuevo nivel de consciencia")
    @app_commands.choices(nivel=OPCIONES_CONSCIENCIA)
    @require_role(NARRADOR)
    async def medico_consciencia(self, interaction: Interaction,
                                  usuario: discord.Member,
                                  nivel: str) -> None:
        """
        Modifica el nivel de consciencia de un personaje.

        Args:
            interaction: Contexto de Discord.
            usuario    : Miembro afectado.
            nivel      : Nuevo nivel de consciencia.
        """
        async with await repo.get_conn() as conn:
            char = await repo.get_character(conn, usuario.id)
            if not char:
                await interaction.response.send_message(
                    embed=emb.error("Sin personaje", "Personaje no encontrado."), ephemeral=True
                )
                return

            await repo.upsert_medical_state(
                conn, usuario.id,
                campos={"consciencia": nivel},
                modificado_por=interaction.user.id,
            )
            await audit(
                conn,
                tipo        = "mod_medica",
                descripcion = f"Consciencia de {char['nombre_completo']} → {nivel}",
                actor_id    = interaction.user.id,
                target_id   = usuario.id,
            )

        await interaction.response.send_message(
            embed=emb.ok("Consciencia actualizada",
                          f"**{char['nombre_completo']}** → `{nivel}`")
        )

    # -----------------------------------------------------------------------
    # /medico sangre
    # -----------------------------------------------------------------------

    @medico_group.command(name="sangre", description="Modificar nivel de sangre (Narrador+)")
    @app_commands.describe(usuario="Personaje", valor="Nivel de sangre (0-100)")
    @require_role(NARRADOR)
    async def medico_sangre(self, interaction: Interaction,
                             usuario: discord.Member,
                             valor: int) -> None:
        """
        Modifica el nivel de sangre de un personaje.

        Args:
            interaction: Contexto de Discord.
            usuario    : Miembro afectado.
            valor      : Nuevo nivel de sangre (0-100).
        """
        if not 0 <= valor <= 100:
            await interaction.response.send_message(
                embed=emb.error("Valor inválido", "El nivel de sangre debe estar entre 0 y 100."),
                ephemeral=True,
            )
            return

        async with await repo.get_conn() as conn:
            char = await repo.get_character(conn, usuario.id)
            if not char:
                await interaction.response.send_message(
                    embed=emb.error("Sin personaje", "Personaje no encontrado."), ephemeral=True
                )
                return

            await repo.upsert_medical_state(
                conn, usuario.id,
                campos={"sangre": valor},
                modificado_por=interaction.user.id,
            )
            await audit(
                conn,
                tipo        = "mod_medica",
                descripcion = f"Sangre de {char['nombre_completo']} → {valor}%",
                actor_id    = interaction.user.id,
                target_id   = usuario.id,
            )

        await interaction.response.send_message(
            embed=emb.ok("Sangre actualizada",
                          f"**{char['nombre_completo']}** → `{valor}%`")
        )

    # -----------------------------------------------------------------------
    # /medico muerte
    # -----------------------------------------------------------------------

    @medico_group.command(name="muerte", description="Ejecutar muerte de personaje")
    @app_commands.describe(usuario="Personaje", motivo="Circunstancias de la muerte")
    @require_role(NARRADOR)
    async def medico_muerte(self, interaction: Interaction,
                             usuario: discord.Member,
                             motivo: str = "Causa no especificada") -> None:
        """
        Ejecuta la muerte de un personaje.

        En Evento-ON:  Narrador puede ejecutarla libremente (cuando sangre=0
                       o consciencia=Clínico, o narrativamente).
        En Evento-OFF: Requiere nivel Gestor+.

        Ver REVIEW.md §1.4 para la justificación del umbral.

        Args:
            interaction: Contexto de Discord.
            usuario    : Miembro cuyo personaje muere.
            motivo     : Descripción narrativa de la muerte.
        """
        nivel     = get_user_level(interaction)
        en_evento = await evento_activo()

        # En Evento-OFF, solo Gestor+ puede ejecutar muerte (RESTRICCIÓN ABSOLUTA)
        if not en_evento and nivel < GESTOR:
            await interaction.response.send_message(
                embed=emb.acceso_denegado("Gestor (fuera de evento)"), ephemeral=True
            )
            return

        async with await repo.get_conn() as conn:
            char = await repo.get_character(conn, usuario.id)
            if not char or char["estado"] != "activo":
                await interaction.response.send_message(
                    embed=emb.error("Sin personaje activo",
                                    "El usuario no tiene un personaje activo."),
                    ephemeral=True,
                )
                return

            # Poner sangre a 0 y consciencia a Clínico
            await repo.upsert_medical_state(
                conn, usuario.id,
                campos={"sangre": 0, "consciencia": "Clínico"},
                modificado_por=interaction.user.id,
            )
            # Marcar personaje como 'baja'
            await repo.update_character_estado(
                conn, usuario.id,
                estado="baja",
                verificado_por=interaction.user.id,
                motivo=f"Muerte narrativa: {motivo}",
            )
            await audit(
                conn,
                tipo        = "muerte",
                descripcion = f"MUERTE de {char['nombre_completo']} — {motivo}",
                actor_id    = interaction.user.id,
                target_id   = usuario.id,
                detalles    = {"motivo": motivo, "evento_activo": en_evento},
            )

        log_info(f"[MUERTE] {char['nombre_completo']} ({usuario.id}) ejecutada por "
                 f"{interaction.user} ({interaction.user.id})")

        embed = discord.Embed(
            title="💀  Muerte registrada",
            description=f"**{char['nombre_completo']}** ha sido marcado como baja.\n\n"
                        f"**Circunstancias:** {motivo}",
            color=0x000000,
        )
        embed.set_footer(text=emb.FOOTER_TEXT)
        await interaction.response.send_message(embed=embed)

        # Notificar al usuario por MD
        try:
            await usuario.send(embed=discord.Embed(
                title="💀  Tu personaje ha caído",
                description=f"**{char['nombre_completo']}** ha sido marcado como baja definitiva.\n"
                            f"**Circunstancias:** {motivo}\n\n"
                            "Contacta con un Narrador o Gestor para más información.",
                color=0x000000,
            ))
        except discord.Forbidden:
            pass  # El usuario tiene los MD cerrados


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot) -> None:
    """Registra el cog en el bot."""
    await bot.add_cog(MedicoCog(bot))