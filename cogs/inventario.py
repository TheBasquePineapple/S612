"""
cogs/inventario.py — Sistema de inventario de RAISA
=====================================================
Responsabilidad : Gestión del loadout, inventario general y sistema de pouches.
Dependencias    : db.repository, utils.embeds, utils.permisos,
                  utils.validaciones, cogs.eventos
Autor           : RAISA Dev

Comandos
--------
  /loadout ver                — Ver loadout propio (Usuario+)
  /loadout equipar [slot] [item] — Equipar ítem en slot (Usuario+)
  /loadout desequipar [slot]  — Vaciar slot del loadout (Usuario+)
  /loadout parche [url]       — Establecer parche de uniformidad (Usuario+)
  /inventario ver             — Ver inventario general (Usuario+)
  /inventario mover [item] [slot] — Mover de general a loadout (Usuario+)
  /pouches añadir [proteccion] [pouch] — Asignar pouch (Usuario+)
  /pouches ver                — Ver pouches asignados (Usuario+)
  /pouches quitar [id]        — Retirar pouch (Usuario+)
"""

import json
from pathlib import Path

import discord
from discord import Interaction, app_commands
from discord.ext import commands

from cogs.eventos import evento_activo
from db import repository as repo
from utils import embeds as emb
from utils.logger import log_info
from utils.permisos import USUARIO, get_user_level, require_role
from utils.validaciones import (
    validar_peso_volumen,
    validar_slots_pouches,
    validar_url_imagen,
)

# ---------------------------------------------------------------------------
# Slots válidos del loadout
# ---------------------------------------------------------------------------
SLOTS_VALIDOS = {
    "primaria", "secundaria", "terciaria",
    "chaleco", "portaplacas", "placas", "soporte", "casco",
    "pantalon", "camisa", "chaqueta", "botas", "guantes", "reloj",
    "mochila", "cinturon", "radio",
    # 'parche' se maneja por separado (URL, no item_id)
}

SLOTS_PROTECCION = {"chaleco", "portaplacas", "soporte"}


def _cargar_inv_config() -> dict:
    """Carga config/inventario.json con defaults."""
    try:
        with Path("config/inventario.json").open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"limites_globales": {"peso_max_kg": 40.0, "volumen_max_u": 80}}


class InventarioCog(commands.Cog, name="Inventario"):
    """Cog del sistema de inventario."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot    = bot
        self.cfg    = _cargar_inv_config()
        self.peso_max = float(self.cfg["limites_globales"]["peso_max_kg"])
        self.vol_max  = int(self.cfg["limites_globales"]["volumen_max_u"])

    # -----------------------------------------------------------------------
    # Grupos de comandos
    # -----------------------------------------------------------------------

    loadout_group   = app_commands.Group(name="loadout",    description="Gestión del loadout equipado")
    inventario_group= app_commands.Group(name="inventario", description="Inventario general")
    pouches_group   = app_commands.Group(name="pouches",    description="Sistema de pouches MOLLE")

    # -----------------------------------------------------------------------
    # /loadout ver
    # -----------------------------------------------------------------------

    @loadout_group.command(name="ver", description="Ver tu loadout completo")
    @require_role(USUARIO)
    async def loadout_ver(self, interaction: Interaction) -> None:
        """
        Muestra el loadout completo del personaje del usuario.

        Args:
            interaction: Contexto de Discord.
        """
        async with await repo.get_conn() as conn:
            char = await repo.get_character(conn, interaction.user.id)
            if not char or char["estado"] != "activo":
                await interaction.response.send_message(
                    embed=emb.error("Sin personaje", "No tienes un personaje activo."),
                    ephemeral=True,
                )
                return
            slots_rows = await repo.get_loadout(conn, interaction.user.id)

        # Construir dict de slots para el embed
        slots_dict: dict[str, str] = {}
        parche_url: str | None = None

        for row in slots_rows:
            nombre_item = row["item_nombre"] or "— Vacío"
            slots_dict[row["slot"]] = nombre_item
            if row["slot"] == "parche" and row.get("parche_url"):
                parche_url = row["parche_url"]

        slots_dict["parche_url"] = parche_url or ""

        await interaction.response.send_message(
            embed=emb.loadout(char["nombre_completo"], slots_dict),
            ephemeral=True,
        )

    # -----------------------------------------------------------------------
    # /loadout equipar
    # -----------------------------------------------------------------------

    @loadout_group.command(name="equipar",
                           description="Equipar un ítem del inventario general en un slot")
    @app_commands.describe(slot="Slot del loadout", nombre_item="Nombre del ítem a equipar")
    @require_role(USUARIO)
    async def loadout_equipar(self, interaction: Interaction,
                               slot: str, nombre_item: str) -> None:
        """
        Equipa un ítem del inventario general en el slot indicado del loadout.
        Mueve el ítem: sale de inventario general y va al loadout.

        Args:
            interaction : Contexto de Discord.
            slot        : Nombre del slot destino.
            nombre_item : Nombre del ítem a equipar.
        """
        slot = slot.lower().strip()
        if slot == "parche":
            await interaction.response.send_message(
                embed=emb.error("Slot parche",
                                "Usa `/loadout parche [url]` para establecer el parche."),
                ephemeral=True,
            )
            return

        if slot not in SLOTS_VALIDOS:
            await interaction.response.send_message(
                embed=emb.error("Slot inválido",
                                f"Slots válidos: {', '.join(sorted(SLOTS_VALIDOS))}"),
                ephemeral=True,
            )
            return

        async with await repo.get_conn() as conn:
            char = await repo.get_character(conn, interaction.user.id)
            if not char or char["estado"] != "activo":
                await interaction.response.send_message(
                    embed=emb.error("Sin personaje", "No tienes personaje activo."),
                    ephemeral=True,
                )
                return

            # Buscar ítem en el catálogo
            item = await repo.get_item_by_name(conn, nombre_item)
            if not item:
                await interaction.response.send_message(
                    embed=emb.error("Ítem no encontrado",
                                    f"No se encontró **{nombre_item}** en el catálogo."),
                    ephemeral=True,
                )
                return

            # Verificar que el usuario tiene el ítem en inventario general
            quitado = await repo.remove_from_general_inventory(
                conn, interaction.user.id, item["id"]
            )
            if not quitado:
                await interaction.response.send_message(
                    embed=emb.error("Sin ítem",
                                    f"No tienes **{nombre_item}** en tu inventario general."),
                    ephemeral=True,
                )
                return

            # Si el slot ya tiene un ítem, devolverlo al inventario general
            slots_actuales = await repo.get_loadout(conn, interaction.user.id)
            for row in slots_actuales:
                if row["slot"] == slot and row["item_id"]:
                    await repo.add_to_general_inventory(
                        conn, interaction.user.id, row["item_id"]
                    )
                    break

            # Equipar el nuevo ítem
            await repo.upsert_loadout_slot(conn, interaction.user.id, slot, item["id"])

        await interaction.response.send_message(
            embed=emb.ok("Ítem equipado",
                          f"**{nombre_item}** equipado en slot `{slot}`."),
            ephemeral=True,
        )

    # -----------------------------------------------------------------------
    # /loadout desequipar
    # -----------------------------------------------------------------------

    @loadout_group.command(name="desequipar", description="Retirar un ítem del loadout")
    @app_commands.describe(slot="Slot a vaciar")
    @require_role(USUARIO)
    async def loadout_desequipar(self, interaction: Interaction, slot: str) -> None:
        """
        Retira el ítem de un slot del loadout y lo devuelve al inventario general.
        Solo disponible en Evento-OFF para ítems no de combate.

        Args:
            interaction: Contexto de Discord.
            slot       : Slot a vaciar.
        """
        slot = slot.lower().strip()

        async with await repo.get_conn() as conn:
            char = await repo.get_character(conn, interaction.user.id)
            if not char or char["estado"] != "activo":
                await interaction.response.send_message(
                    embed=emb.error("Sin personaje", "No tienes personaje activo."), ephemeral=True
                )
                return

            slots_actuales = await repo.get_loadout(conn, interaction.user.id)
            slot_row = next((r for r in slots_actuales if r["slot"] == slot), None)

            if not slot_row or not slot_row["item_id"]:
                await interaction.response.send_message(
                    embed=emb.advertencia("Slot vacío",
                                          f"El slot `{slot}` ya está vacío."),
                    ephemeral=True,
                )
                return

            # Devolver al inventario general y vaciar slot
            await repo.add_to_general_inventory(conn, interaction.user.id, slot_row["item_id"])
            await repo.upsert_loadout_slot(conn, interaction.user.id, slot, None)

        await interaction.response.send_message(
            embed=emb.ok("Slot vaciado",
                          f"**{slot_row['item_nombre']}** devuelto al inventario general."),
            ephemeral=True,
        )

    # -----------------------------------------------------------------------
    # /loadout parche
    # -----------------------------------------------------------------------

    @loadout_group.command(name="parche",
                           description="Establecer parche de uniformidad (URL de imagen)")
    @app_commands.describe(url="URL de la imagen del parche (PNG/JPG/WEBP)")
    @require_role(USUARIO)
    async def loadout_parche(self, interaction: Interaction, url: str) -> None:
        """
        Establece el parche de uniformidad como URL de imagen.
        Solo puede haber 1 parche activo. Se renderiza en el embed de loadout.
        Ver REVIEW.md §3.5 — validación de URL.

        Args:
            interaction: Contexto de Discord.
            url        : URL de la imagen del parche.
        """
        resultado = validar_url_imagen(url)
        if not resultado:
            await interaction.response.send_message(
                embed=emb.error("URL inválida", resultado.motivo), ephemeral=True
            )
            return

        async with await repo.get_conn() as conn:
            char = await repo.get_character(conn, interaction.user.id)
            if not char or char["estado"] != "activo":
                await interaction.response.send_message(
                    embed=emb.error("Sin personaje", "No tienes personaje activo."), ephemeral=True
                )
                return
            await repo.upsert_loadout_slot(
                conn, interaction.user.id, "parche",
                item_id=None, parche_url=url
            )

        embed = emb.ok("Parche establecido", "Tu parche de uniformidad ha sido actualizado.")
        embed.set_thumbnail(url=url)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -----------------------------------------------------------------------
    # /inventario ver
    # -----------------------------------------------------------------------

    @inventario_group.command(name="ver", description="Ver tu inventario general")
    @require_role(USUARIO)
    async def inventario_ver(self, interaction: Interaction) -> None:
        """
        Muestra el inventario general del personaje.
        Solo disponible en Evento-OFF.

        Args:
            interaction: Contexto de Discord.
        """
        if await evento_activo():
            await interaction.response.send_message(
                embed=emb.evento_bloqueado("El inventario general"), ephemeral=True
            )
            return

        async with await repo.get_conn() as conn:
            char = await repo.get_character(conn, interaction.user.id)
            if not char or char["estado"] != "activo":
                await interaction.response.send_message(
                    embed=emb.error("Sin personaje", "No tienes personaje activo."), ephemeral=True
                )
                return

            items      = await repo.get_inventory_general(conn, interaction.user.id)
            peso, vol  = await repo.get_inventory_totals(conn, interaction.user.id)

        await interaction.response.send_message(
            embed=emb.inventario_general(
                char["nombre_completo"],
                [dict(it) for it in items],
                peso, int(vol),
                self.peso_max, self.vol_max,
            ),
            ephemeral=True,
        )

    # -----------------------------------------------------------------------
    # /pouches añadir
    # -----------------------------------------------------------------------

    @pouches_group.command(name="añadir",
                           description="Asignar un pouch a una protección del loadout")
    @app_commands.describe(
        slot_proteccion="chaleco, portaplacas o soporte",
        nombre_pouch="Nombre del pouch a asignar",
    )
    @require_role(USUARIO)
    async def pouches_añadir(self, interaction: Interaction,
                              slot_proteccion: str, nombre_pouch: str) -> None:
        """
        Asigna un pouch del inventario general a una protección del loadout.
        Verifica disponibilidad de slots antes de asignar.

        Args:
            interaction     : Contexto de Discord.
            slot_proteccion : 'chaleco', 'portaplacas' o 'soporte'.
            nombre_pouch    : Nombre del pouch a asignar.
        """
        slot_proteccion = slot_proteccion.lower().strip()
        if slot_proteccion not in SLOTS_PROTECCION:
            await interaction.response.send_message(
                embed=emb.error("Slot inválido",
                                "El slot de protección debe ser: chaleco, portaplacas o soporte"),
                ephemeral=True,
            )
            return

        async with await repo.get_conn() as conn:
            char = await repo.get_character(conn, interaction.user.id)
            if not char or char["estado"] != "activo":
                await interaction.response.send_message(
                    embed=emb.error("Sin personaje", "No tienes personaje activo."), ephemeral=True
                )
                return

            # Verificar que hay protección equipada en ese slot
            slots_rows = await repo.get_loadout(conn, interaction.user.id)
            prot_row   = next((r for r in slots_rows if r["slot"] == slot_proteccion), None)

            if not prot_row or not prot_row["item_id"]:
                await interaction.response.send_message(
                    embed=emb.error("Sin protección",
                                    f"No tienes ninguna protección equipada en `{slot_proteccion}`."),
                    ephemeral=True,
                )
                return

            slots_totales = prot_row["slots_pouches"] or 0

            # Calcular slots ya usados
            pouches_actuales = await repo.get_pouches(conn, interaction.user.id)
            slots_usados = sum(
                p["slots_ocupa"] for p in pouches_actuales
                if p["slot_proteccion"] == slot_proteccion
            )

            # Buscar el pouch en el catálogo
            pouch_item = await repo.get_item_by_name(conn, nombre_pouch)
            if not pouch_item or pouch_item["categoria"] != "pouch":
                await interaction.response.send_message(
                    embed=emb.error("Pouch no encontrado",
                                    f"No se encontró **{nombre_pouch}** como pouch válido."),
                    ephemeral=True,
                )
                return

            slots_ocupa = pouch_item["slots_ocupa"] or 1

            # Validar slots disponibles
            resultado = validar_slots_pouches(slots_totales, slots_usados, slots_ocupa)
            if not resultado:
                await interaction.response.send_message(
                    embed=emb.error("Sin slots", resultado.motivo), ephemeral=True
                )
                return

            # Retirar del inventario general y asignar pouch
            quitado = await repo.remove_from_general_inventory(
                conn, interaction.user.id, pouch_item["id"]
            )
            if not quitado:
                await interaction.response.send_message(
                    embed=emb.error("Sin pouch",
                                    f"No tienes **{nombre_pouch}** en tu inventario general."),
                    ephemeral=True,
                )
                return

            await repo.add_pouch(conn, interaction.user.id, slot_proteccion, pouch_item["id"])

        await interaction.response.send_message(
            embed=emb.ok("Pouch asignado",
                          f"**{nombre_pouch}** asignado a `{slot_proteccion}` "
                          f"({slots_ocupa} slot(s) usados)"),
            ephemeral=True,
        )

    # -----------------------------------------------------------------------
    # /pouches ver
    # -----------------------------------------------------------------------

    @pouches_group.command(name="ver", description="Ver los pouches asignados a tus protecciones")
    @require_role(USUARIO)
    async def pouches_ver(self, interaction: Interaction) -> None:
        """
        Muestra todos los pouches asignados a las protecciones del personaje.

        Args:
            interaction: Contexto de Discord.
        """
        async with await repo.get_conn() as conn:
            char = await repo.get_character(conn, interaction.user.id)
            if not char or char["estado"] != "activo":
                await interaction.response.send_message(
                    embed=emb.error("Sin personaje", "No tienes personaje activo."), ephemeral=True
                )
                return
            pouches = await repo.get_pouches(conn, interaction.user.id)

        if not pouches:
            await interaction.response.send_message(
                embed=emb.info("Sin pouches", "No tienes pouches asignados."), ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"🎽  Pouches — {char['nombre_completo']}",
            color=emb.C_INFO,
        )
        agrupados: dict[str, list] = {}
        for p in pouches:
            agrupados.setdefault(p["slot_proteccion"], []).append(p)

        for slot, lista in agrupados.items():
            lineas = [
                f"[{p['id']}] **{p['pouch_nombre']}** ({p['tipo_pouch']}, {p['slots_ocupa']} slot(s))"
                for p in lista
            ]
            embed.add_field(name=f"🛡️ {slot.capitalize()}", value="\n".join(lineas), inline=False)

        embed.set_footer(text=emb.FOOTER_TEXT)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -----------------------------------------------------------------------
    # /pouches quitar
    # -----------------------------------------------------------------------

    @pouches_group.command(name="quitar", description="Retirar un pouch de tus protecciones")
    @app_commands.describe(pouch_id="ID del pouch (ver /pouches ver)")
    @require_role(USUARIO)
    async def pouches_quitar(self, interaction: Interaction, pouch_id: int) -> None:
        """
        Retira un pouch de las protecciones y lo devuelve al inventario general.

        Args:
            interaction: Contexto de Discord.
            pouch_id   : ID del registro de pouch (de /pouches ver).
        """
        async with await repo.get_conn() as conn:
            char = await repo.get_character(conn, interaction.user.id)
            if not char or char["estado"] != "activo":
                await interaction.response.send_message(
                    embed=emb.error("Sin personaje", "No tienes personaje activo."), ephemeral=True
                )
                return

            # Verificar que el pouch pertenece al usuario
            pouches = await repo.get_pouches(conn, interaction.user.id)
            pouch   = next((p for p in pouches if p["id"] == pouch_id), None)
            if not pouch:
                await interaction.response.send_message(
                    embed=emb.error("Pouch no encontrado",
                                    f"No se encontró pouch con ID `{pouch_id}` en tu equipo."),
                    ephemeral=True,
                )
                return

            # Retirar y devolver al inventario
            pouch_item = await repo.get_item_by_name(conn, pouch["pouch_nombre"])
            await repo.remove_pouch(conn, pouch_id, interaction.user.id)
            if pouch_item:
                await repo.add_to_general_inventory(conn, interaction.user.id, pouch_item["id"])

        await interaction.response.send_message(
            embed=emb.ok("Pouch retirado",
                          f"**{pouch['pouch_nombre']}** devuelto al inventario general."),
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot) -> None:
    """Registra el cog en el bot."""
    await bot.add_cog(InventarioCog(bot))