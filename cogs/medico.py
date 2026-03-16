"""
RAISA — Cog de Sistema Médico
cogs/medico.py

Responsabilidad : Gestión del estado médico de personajes.
                  Narradores aplican/modifican heridas; Usuarios visualizan.
Dependencias    : discord.py, db/repository, utils/permisos, utils/embeds
Autor           : Proyecto RAISA
"""

import json
import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import embed_estado_medico, embed_ok, embed_error, embed_aviso
from utils.permisos import (
    require_role, RANGO_USUARIO, RANGO_NARRADOR, RANGO_GESTOR, get_rango
)

log = logging.getLogger("raisa.medico")

CONSCIENCIAS = ["Consciente", "Semiconsciente", "Inconsciente", "Clínico"]

# Estados generales calculados según sangre
def _calcular_estado_general(sangre: int, heridas: list, fracturas: list) -> str:
    """
    Calcula el estado general automáticamente.
    Lógica: combina sangre, número de heridas graves y fracturas expuestas.
    """
    heridas_criticas = sum(1 for h in heridas if h.get("gravedad") in ("Crítica", "Grave"))
    fracturas_expuestas = sum(1 for f in fracturas if f.get("tipo") == "expuesta")

    if sangre <= 0 or (heridas_criticas >= 2 and fracturas_expuestas >= 1):
        return "Crítico"
    if sangre <= 20 or heridas_criticas >= 2:
        return "Grave"
    if sangre <= 50 or heridas_criticas >= 1 or fracturas_expuestas >= 1:
        return "Herido"
    if sangre <= 75 or len(heridas) >= 2:
        return "Levemente herido"
    return "Óptimo"


class MedicoModal(discord.ui.Modal, title="Aplicar Herida"):
    """Modal para que el Narrador aplique una herida a un personaje."""

    tipo         = discord.ui.TextInput(label="Tipo de herida",          placeholder="Ej: Herida de bala, Quemadura")
    localizacion = discord.ui.TextInput(label="Localización",            placeholder="Ej: Hombro izquierdo, Torso")
    gravedad     = discord.ui.TextInput(label="Gravedad",                placeholder="Leve / Moderada / Grave / Crítica")
    tratamiento  = discord.ui.TextInput(label="Estado de tratamiento",   placeholder="Sin tratar / En tratamiento / Estabilizado", required=False)

    def __init__(self, cog, objetivo_id: int) -> None:
        super().__init__()
        self.cog = cog
        self.objetivo_id = objetivo_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        herida = {
            "tipo":               self.tipo.value,
            "localizacion":       self.localizacion.value,
            "gravedad":           self.gravedad.value,
            "estado_tratamiento": self.tratamiento.value or "Sin tratar",
        }

        medico = await self.cog.repo.get_estado_medico(self.objetivo_id)
        if not medico:
            await interaction.response.send_message(
                embed=embed_error("Personaje no encontrado."), ephemeral=True
            )
            return

        heridas = medico["heridas"]
        heridas.append(herida)
        estado_general = _calcular_estado_general(medico["sangre"], heridas, medico["fracturas"])

        await self.cog.repo.actualizar_estado_medico(
            self.objetivo_id,
            {"heridas": heridas, "estado_general": estado_general}
        )
        await self.cog.repo.log_accion(
            "HERIDA_APLICADA", interaction.user.id, self.objetivo_id,
            detalle=herida
        )

        personaje = await self.cog.repo.get_personaje(self.objetivo_id)
        medico_actualizado = await self.cog.repo.get_estado_medico(self.objetivo_id)
        await interaction.response.send_message(
            embed=embed_estado_medico(personaje, medico_actualizado), ephemeral=False
        )


class MedicoCog(commands.Cog, name="Médico"):
    """Cog para el sistema médico de RAISA."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def repo(self):
        return self.bot.repo

    medico_group = app_commands.Group(name="medico", description="Sistema médico de personajes")

    # ──────────────────────────────────────────────────────────────────────
    # USUARIO — Ver estado
    # ──────────────────────────────────────────────────────────────────────

    @medico_group.command(name="estado", description="Muestra tu estado médico actual.")
    @require_role(RANGO_USUARIO)
    async def ver_estado(self, interaction: discord.Interaction) -> None:
        """El usuario visualiza su propio estado médico."""
        uid = interaction.user.id
        personaje = await self.repo.get_personaje(uid)
        if not personaje:
            await interaction.response.send_message(
                embed=embed_error("No tienes un personaje registrado."), ephemeral=True
            )
            return

        medico = await self.repo.get_estado_medico(uid)
        if not medico:
            await interaction.response.send_message(
                embed=embed_error("Estado médico no encontrado."), ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=embed_estado_medico(personaje, medico), ephemeral=True
        )

    # ──────────────────────────────────────────────────────────────────────
    # NARRADOR — Aplicar herida
    # ──────────────────────────────────────────────────────────────────────

    @medico_group.command(name="herida_aplicar", description="[Narrador] Aplica una herida a un personaje.")
    @app_commands.describe(usuario="Usuario objetivo")
    @require_role(RANGO_NARRADOR)
    async def herida_aplicar(
        self, interaction: discord.Interaction, usuario: discord.Member
    ) -> None:
        """Abre un modal para que el Narrador defina la herida."""
        personaje = await self.repo.get_personaje(usuario.id)
        if not personaje:
            await interaction.response.send_message(
                embed=embed_error(f"{usuario.mention} no tiene personaje registrado."), ephemeral=True
            )
            return
        await interaction.response.send_modal(MedicoModal(self, usuario.id))

    @medico_group.command(name="herida_retirar", description="[Narrador] Retira una herida de un personaje.")
    @app_commands.describe(usuario="Usuario objetivo", indice="Índice de la herida (1 = primera)")
    @require_role(RANGO_NARRADOR)
    async def herida_retirar(
        self, interaction: discord.Interaction, usuario: discord.Member, indice: int
    ) -> None:
        """Retira la herida en posición `indice` (base 1)."""
        medico = await self.repo.get_estado_medico(usuario.id)
        if not medico:
            await interaction.response.send_message(embed=embed_error("Personaje no encontrado."), ephemeral=True)
            return

        heridas = medico["heridas"]
        if not heridas or not (1 <= indice <= len(heridas)):
            await interaction.response.send_message(
                embed=embed_error(f"Índice `{indice}` inválido. El personaje tiene `{len(heridas)}` heridas."),
                ephemeral=True
            )
            return

        herida_retirada = heridas.pop(indice - 1)
        estado_general = _calcular_estado_general(medico["sangre"], heridas, medico["fracturas"])
        await self.repo.actualizar_estado_medico(usuario.id, {"heridas": heridas, "estado_general": estado_general})
        await self.repo.log_accion("HERIDA_RETIRADA", interaction.user.id, usuario.id, detalle=herida_retirada)

        await interaction.response.send_message(
            embed=embed_ok("Herida retirada", f"Se eliminó la herida: **{herida_retirada['tipo']}** en {herida_retirada['localizacion']}."),
        )

    @medico_group.command(name="sangre", description="[Narrador] Modifica la cantidad de sangre de un personaje.")
    @app_commands.describe(usuario="Usuario objetivo", valor="Valor de sangre (0-100)")
    @require_role(RANGO_NARRADOR)
    async def cambiar_sangre(
        self, interaction: discord.Interaction, usuario: discord.Member, valor: int
    ) -> None:
        """Cambia directamente el nivel de sangre. Recalcula estado general."""
        if not (0 <= valor <= 100):
            await interaction.response.send_message(
                embed=embed_error("El valor debe estar entre `0` y `100`."), ephemeral=True
            )
            return

        medico = await self.repo.get_estado_medico(usuario.id)
        if not medico:
            await interaction.response.send_message(embed=embed_error("Personaje no encontrado."), ephemeral=True)
            return

        estado_general = _calcular_estado_general(valor, medico["heridas"], medico["fracturas"])
        await self.repo.actualizar_estado_medico(usuario.id, {"sangre": valor, "estado_general": estado_general})
        await self.repo.log_accion("SANGRE_CAMBIADA", interaction.user.id, usuario.id, detalle={"sangre": valor})

        await interaction.response.send_message(
            embed=embed_ok("Sangre actualizada", f"Sangre de {usuario.mention}: `{valor}%`\nEstado general: **{estado_general}**"),
        )

    @medico_group.command(name="consciencia", description="[Narrador] Cambia el nivel de consciencia de un personaje.")
    @app_commands.describe(usuario="Usuario objetivo", nivel="Nivel de consciencia")
    @app_commands.choices(nivel=[app_commands.Choice(name=c, value=c) for c in CONSCIENCIAS])
    @require_role(RANGO_NARRADOR)
    async def cambiar_consciencia(
        self, interaction: discord.Interaction, usuario: discord.Member, nivel: str
    ) -> None:
        """Cambia el nivel de consciencia del personaje."""
        medico = await self.repo.get_estado_medico(usuario.id)
        if not medico:
            await interaction.response.send_message(embed=embed_error("Personaje no encontrado."), ephemeral=True)
            return

        await self.repo.actualizar_estado_medico(usuario.id, {"consciencia": nivel})
        await self.repo.log_accion("CONSCIENCIA_CAMBIADA", interaction.user.id, usuario.id, detalle={"consciencia": nivel})

        await interaction.response.send_message(
            embed=embed_ok("Consciencia actualizada", f"Consciencia de {usuario.mention}: **{nivel}**"),
        )

    @medico_group.command(name="fractura", description="[Narrador] Añade una fractura a un personaje.")
    @app_commands.describe(usuario="Usuario objetivo", miembro="Miembro fracturado", tipo="Tipo de fractura")
    @app_commands.choices(tipo=[
        app_commands.Choice(name="Simple", value="simple"),
        app_commands.Choice(name="Expuesta", value="expuesta"),
    ])
    @require_role(RANGO_NARRADOR)
    async def añadir_fractura(
        self, interaction: discord.Interaction, usuario: discord.Member, miembro: str, tipo: str
    ) -> None:
        """Añade una fractura al registro del personaje."""
        medico = await self.repo.get_estado_medico(usuario.id)
        if not medico:
            await interaction.response.send_message(embed=embed_error("Personaje no encontrado."), ephemeral=True)
            return

        fracturas = medico["fracturas"]
        fracturas.append({"miembro": miembro, "tipo": tipo})
        estado_general = _calcular_estado_general(medico["sangre"], medico["heridas"], fracturas)
        await self.repo.actualizar_estado_medico(usuario.id, {"fracturas": fracturas, "estado_general": estado_general})
        await self.repo.log_accion("FRACTURA_AÑADIDA", interaction.user.id, usuario.id, detalle={"miembro": miembro, "tipo": tipo})

        await interaction.response.send_message(
            embed=embed_ok("Fractura registrada", f"Fractura **{tipo}** en `{miembro}` de {usuario.mention}."),
        )

    @medico_group.command(name="muerte", description="[Gestor+] Ejecuta la muerte de un personaje.")
    @app_commands.describe(usuario="Usuario objetivo", motivo="Motivo de la muerte")
    @require_role(RANGO_GESTOR)
    async def ejecutar_muerte(
        self, interaction: discord.Interaction, usuario: discord.Member, motivo: str
    ) -> None:
        """
        Ejecuta la muerte de un personaje.
        En Evento-OFF requiere Gestor+.
        En Evento-ON el Narrador puede ejecutarla libremente (verificado por permisos).
        """
        modo = await self.repo.get_modo_evento()

        # En Evento-ON un Narrador puede matar; en OFF solo Gestor+.
        rango = get_rango(interaction.user)
        if modo == "OFF" and rango < RANGO_GESTOR:
            await interaction.response.send_message(
                embed=embed_error(
                    "En **Evento-OFF** la muerte de un personaje requiere autorización de **Gestor+**."
                ),
                ephemeral=True,
            )
            return

        personaje = await self.repo.get_personaje(usuario.id)
        if not personaje:
            await interaction.response.send_message(embed=embed_error("Personaje no encontrado."), ephemeral=True)
            return

        # Poner sangre a 0 y marcar inconsciente/clínico
        await self.repo.actualizar_estado_medico(
            usuario.id,
            {"sangre": 0, "consciencia": "Clínico", "estado_general": "Crítico"}
        )
        await self.repo.log_accion("MUERTE", interaction.user.id, usuario.id, detalle={"motivo": motivo, "modo_evento": modo})
        log.warning("MUERTE ejecutada: objetivo=%s por=%s motivo=%s", usuario.id, interaction.user.id, motivo)

        await interaction.response.send_message(
            embed=discord.Embed(
                title="☠️ Muerte ejecutada",
                description=(
                    f"**{personaje['nombre']} {personaje['apellidos']}** ha muerto.\n"
                    f"**Motivo:** {motivo}\n"
                    f"Registrado por: {interaction.user.mention}"
                ),
                color=discord.Color.dark_red(),
            )
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MedicoCog(bot))
