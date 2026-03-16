"""
RAISA — Cog de Sistema de Inventario
cogs/inventario.py

Responsabilidad : Gestión del loadout, inventario general y pouches.
                  Control de peso/volumen, asignación de slots.
Dependencias    : discord.py, db/repository, utils/permisos, utils/embeds, utils/validaciones
Autor           : Proyecto RAISA
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import embed_loadout, embed_ok, embed_error, embed_aviso
from utils.permisos import require_role, RANGO_USUARIO, RANGO_NARRADOR
from utils.validaciones import validar_capacidad_inventario, validar_slot_pouch

log = logging.getLogger("raisa.inventario")

SLOTS_ARMA = ["arma_primaria_id", "arma_secundaria_id", "arma_terciaria_id"]
SLOTS_PROTECCION = ["chaleco_id", "portaplacas_id", "placas_id", "soportes_id", "casco_id"]
SLOTS_UNIFORMIDAD = ["pantalon_id", "camisa_id", "chaqueta_id", "botas_id", "guantes_id", "reloj_id"]
SLOTS_ACCESORIOS = ["mochila_id", "cinturon_id", "radio_id"]

NOMBRE_SLOTS = {
    "arma_primaria_id": "Arma primaria",
    "arma_secundaria_id": "Arma secundaria",
    "arma_terciaria_id": "Arma terciaria",
    "chaleco_id": "Chaleco",
    "portaplacas_id": "Portaplacas",
    "placas_id": "Placas",
    "soportes_id": "Soportes",
    "casco_id": "Casco",
    "pantalon_id": "Pantalón",
    "camisa_id": "Camisa",
    "chaqueta_id": "Chaqueta",
    "botas_id": "Botas",
    "guantes_id": "Guantes",
    "reloj_id": "Reloj",
    "mochila_id": "Mochila/Backpanel",
    "cinturon_id": "Cinturón",
    "radio_id": "Radio",
}
TODOS_SLOTS = list(NOMBRE_SLOTS.keys())


class InventarioCog(commands.Cog, name="Inventario"):
    """Cog para el sistema de inventario y loadout de RAISA."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def repo(self):
        return self.bot.repo

    # ──────────────────────────────────────────────────────────────────────
    # LOADOUT — Visualización
    # ──────────────────────────────────────────────────────────────────────

    inv_group = app_commands.Group(name="inventario", description="Gestión de inventario y loadout")

    @inv_group.command(name="loadout", description="Muestra tu loadout actual.")
    @require_role(RANGO_USUARIO)
    async def ver_loadout(self, interaction: discord.Interaction) -> None:
        """Visualiza el loadout completo del usuario."""
        uid = interaction.user.id
        personaje = await self.repo.get_personaje(uid)
        if not personaje:
            await interaction.response.send_message(embed=embed_error("No tienes personaje registrado."), ephemeral=True)
            return

        loadout = await self.repo.get_loadout(uid)
        if not loadout:
            await interaction.response.send_message(embed=embed_error("Error al cargar el loadout."), ephemeral=True)
            return

        # Resolver nombres de ítems (solo los que no son None)
        item_ids = {v for k, v in loadout.items() if k.endswith("_id") and v is not None}
        items_data = {}
        for iid in item_ids:
            item = await self.repo.get_item(iid)
            if item:
                items_data[iid] = item

        embed = embed_loadout(personaje, loadout, items_data)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @inv_group.command(name="general", description="Muestra tu inventario general.")
    @require_role(RANGO_USUARIO)
    async def ver_inventario_general(self, interaction: discord.Interaction) -> None:
        """Visualiza el inventario general. Solo disponible en Evento-OFF."""
        modo = await self.repo.get_modo_evento()
        if modo == "ON":
            await interaction.response.send_message(
                embed=embed_aviso("Acceso restringido", "El inventario general no es accesible durante un **Evento-ON**."),
                ephemeral=True,
            )
            return

        uid = interaction.user.id
        items = await self.repo.get_inventario_general(uid)

        if not items:
            await interaction.response.send_message(
                embed=embed_ok("Inventario general", "Tu inventario general está vacío."),
                ephemeral=True,
            )
            return

        # Agrupar por categoría
        categorias: dict[str, list] = {}
        for it in items:
            cat = it.get("categoria", "Miscelánea")
            categorias.setdefault(cat, []).append(it)

        embed = discord.Embed(title="🗃️ Inventario General", color=discord.Color.blue())
        for cat, cat_items in categorias.items():
            lineas = "\n".join(
                f"• **{it['nombre']}** ×{it['cantidad']} — `{it['peso_kg']} kg`"
                for it in cat_items
            )
            embed.add_field(name=cat, value=lineas[:1024], inline=False)

        from utils.embeds import FOOTER_TEXT
        embed.set_footer(text=FOOTER_TEXT)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ──────────────────────────────────────────────────────────────────────
    # LOADOUT — Equipar / Desequipar
    # ──────────────────────────────────────────────────────────────────────

    @inv_group.command(name="equipar", description="Equipa un ítem del inventario general en un slot del loadout.")
    @app_commands.describe(
        item_id="ID del ítem a equipar",
        slot="Slot donde equiparlo (ej: arma_primaria_id)",
    )
    @app_commands.choices(slot=[app_commands.Choice(name=v, value=k) for k, v in NOMBRE_SLOTS.items()])
    @require_role(RANGO_USUARIO)
    async def equipar_item(
        self, interaction: discord.Interaction, item_id: int, slot: str
    ) -> None:
        """
        Equipa un ítem del inventario general en el slot indicado del loadout.
        Verifica límites de peso/volumen antes de asignar.
        """
        uid = interaction.user.id

        # Verificar que el ítem está en el inventario general del usuario
        inv = await self.repo.get_inventario_general(uid)
        tiene_item = any(it["item_uuid"] == item_id for it in inv)
        if not tiene_item:
            await interaction.response.send_message(
                embed=embed_error(f"No tienes el ítem `{item_id}` en tu inventario general."),
                ephemeral=True,
            )
            return

        # Validar capacidad (solo aplica a ítems que añaden peso)
        ok, msg = await validar_capacidad_inventario(self.repo, uid, item_id)
        if not ok:
            await interaction.response.send_message(embed=embed_error(msg), ephemeral=True)
            return

        # Verificar si el slot ya está ocupado
        loadout = await self.repo.get_loadout(uid)
        slot_actual = loadout.get(slot)
        if slot_actual:
            # Devolver al inventario general antes de reemplazar
            await self.repo.añadir_item_inventario(uid, slot_actual, 1)

        # Asignar en loadout y retirar del general
        await self.repo.set_slot_loadout(uid, slot, item_id)
        await self.repo.retirar_item_inventario(uid, item_id, 1)

        item = await self.repo.get_item(item_id)
        await interaction.response.send_message(
            embed=embed_ok(
                "Ítem equipado",
                f"**{item['nombre']}** equipado en slot **{NOMBRE_SLOTS.get(slot, slot)}**."
            ),
        )

    @inv_group.command(name="desequipar", description="Retira un ítem del loadout al inventario general.")
    @app_commands.describe(slot="Slot a desequipar")
    @app_commands.choices(slot=[app_commands.Choice(name=v, value=k) for k, v in NOMBRE_SLOTS.items()])
    @require_role(RANGO_USUARIO)
    async def desequipar_item(self, interaction: discord.Interaction, slot: str) -> None:
        """Desequipa el ítem del slot indicado y lo devuelve al inventario general."""
        uid = interaction.user.id
        loadout = await self.repo.get_loadout(uid)
        item_id = loadout.get(slot) if loadout else None

        if not item_id:
            await interaction.response.send_message(
                embed=embed_error(f"El slot **{NOMBRE_SLOTS.get(slot, slot)}** está vacío."),
                ephemeral=True,
            )
            return

        await self.repo.set_slot_loadout(uid, slot, None)
        await self.repo.añadir_item_inventario(uid, item_id, 1)

        item = await self.repo.get_item(item_id)
        await interaction.response.send_message(
            embed=embed_ok("Ítem desequipado", f"**{item['nombre']}** devuelto al inventario general."),
        )

    @inv_group.command(name="parche", description="Establece la URL del parche de uniformidad.")
    @app_commands.describe(url="URL de la imagen del parche (máx. 1)")
    @require_role(RANGO_USUARIO)
    async def set_parche(self, interaction: discord.Interaction, url: str) -> None:
        """Asigna la URL de imagen del parche. Máximo 1 parche."""
        uid = interaction.user.id
        await self.repo.set_slot_loadout(uid, "parche_url", url)
        embed = embed_ok("Parche actualizado", f"Parche de uniformidad establecido.")
        embed.set_thumbnail(url=url)
        await interaction.response.send_message(embed=embed)

    # ──────────────────────────────────────────────────────────────────────
    # POUCHES
    # ──────────────────────────────────────────────────────────────────────

    @inv_group.command(name="pouch_añadir", description="Añade un pouch a un contenedor del loadout.")
    @app_commands.describe(
        contenedor="Contenedor donde añadir el pouch",
        item_id="ID del pouch a añadir",
    )
    @app_commands.choices(contenedor=[
        app_commands.Choice(name="Chaleco", value="chaleco"),
        app_commands.Choice(name="Portaplacas", value="portaplacas"),
        app_commands.Choice(name="Soportes", value="soportes"),
    ])
    @require_role(RANGO_USUARIO)
    async def pouch_añadir(
        self, interaction: discord.Interaction, contenedor: str, item_id: int
    ) -> None:
        """Equipa un pouch del inventario general en el contenedor especificado."""
        uid = interaction.user.id
        item = await self.repo.get_item(item_id)
        if not item or not item.get("tipo_pouch"):
            await interaction.response.send_message(
                embed=embed_error(f"El ítem `{item_id}` no es un pouch válido."),
                ephemeral=True,
            )
            return

        # Verificar que lo tiene en inventario general
        inv = await self.repo.get_inventario_general(uid)
        if not any(it["item_uuid"] == item_id for it in inv):
            await interaction.response.send_message(
                embed=embed_error(f"No tienes el ítem `{item_id}` en tu inventario general."),
                ephemeral=True,
            )
            return

        ok, msg, slot = await validar_slot_pouch(self.repo, uid, contenedor, item["tipo_pouch"])
        if not ok:
            await interaction.response.send_message(embed=embed_error(msg), ephemeral=True)
            return

        await self.repo.añadir_pouch(uid, contenedor, slot, item_id)
        await self.repo.retirar_item_inventario(uid, item_id, 1)
        await interaction.response.send_message(
            embed=embed_ok("Pouch añadido", f"**{item['nombre']}** en slot `{slot}` de **{contenedor}**."),
        )

    @inv_group.command(name="pouch_retirar", description="Retira un pouch de un contenedor.")
    @app_commands.describe(contenedor="Contenedor", slot="Número de slot")
    @app_commands.choices(contenedor=[
        app_commands.Choice(name="Chaleco", value="chaleco"),
        app_commands.Choice(name="Portaplacas", value="portaplacas"),
        app_commands.Choice(name="Soportes", value="soportes"),
    ])
    @require_role(RANGO_USUARIO)
    async def pouch_retirar(
        self, interaction: discord.Interaction, contenedor: str, slot: int
    ) -> None:
        """Retira un pouch de un slot y lo devuelve al inventario general."""
        uid = interaction.user.id
        pouches = await self.repo.get_pouches(uid, contenedor)
        pouch = next((p for p in pouches if p["slot_numero"] == slot), None)
        if not pouch:
            await interaction.response.send_message(
                embed=embed_error(f"No hay pouch en slot `{slot}` del {contenedor}."),
                ephemeral=True,
            )
            return

        await self.repo.retirar_pouch(uid, contenedor, slot)
        await self.repo.añadir_item_inventario(uid, pouch["pouch_item_id"], 1)
        await interaction.response.send_message(
            embed=embed_ok("Pouch retirado", f"**{pouch['nombre']}** devuelto al inventario general."),
        )

    # ──────────────────────────────────────────────────────────────────────
    # NARRADOR — CRUD de ítems en inventario de usuario
    # ──────────────────────────────────────────────────────────────────────

    @inv_group.command(name="entregar", description="[Narrador] Entrega un ítem al inventario general de un usuario.")
    @app_commands.describe(usuario="Usuario objetivo", item_id="ID del ítem", cantidad="Cantidad")
    @require_role(RANGO_NARRADOR)
    async def entregar_item(
        self, interaction: discord.Interaction,
        usuario: discord.Member, item_id: int, cantidad: int = 1
    ) -> None:
        """Narrador entrega ítems al inventario general del usuario."""
        item = await self.repo.get_item(item_id)
        if not item:
            await interaction.response.send_message(embed=embed_error(f"Ítem `{item_id}` no encontrado."), ephemeral=True)
            return

        await self.repo.añadir_item_inventario(usuario.id, item_id, cantidad)
        await interaction.response.send_message(
            embed=embed_ok("Ítem entregado", f"**{item['nombre']}** ×{cantidad} entregado a {usuario.mention}."),
        )

    @inv_group.command(name="retirar", description="[Narrador] Retira un ítem del inventario general de un usuario.")
    @app_commands.describe(usuario="Usuario objetivo", item_id="ID del ítem", cantidad="Cantidad")
    @require_role(RANGO_NARRADOR)
    async def retirar_item_narrador(
        self, interaction: discord.Interaction,
        usuario: discord.Member, item_id: int, cantidad: int = 1
    ) -> None:
        """Narrador retira ítems del inventario general del usuario."""
        ok = await self.repo.retirar_item_inventario(usuario.id, item_id, cantidad)
        if not ok:
            await interaction.response.send_message(
                embed=embed_error(f"El usuario no tiene suficiente cantidad del ítem `{item_id}`."),
                ephemeral=True,
            )
            return

        item = await self.repo.get_item(item_id)
        nombre = item["nombre"] if item else str(item_id)
        await interaction.response.send_message(
            embed=embed_ok("Ítem retirado", f"**{nombre}** ×{cantidad} retirado de {usuario.mention}."),
        )

    @inv_group.command(name="crear_item", description="[Narrador] Crea un nuevo ítem en el catálogo.")
    @require_role(RANGO_NARRADOR)
    async def crear_item_modal(self, interaction: discord.Interaction) -> None:
        """Abre un modal para que el Narrador cree un nuevo ítem."""
        await interaction.response.send_modal(CrearItemModal(self))


class CrearItemModal(discord.ui.Modal, title="Crear nuevo ítem"):
    nombre      = discord.ui.TextInput(label="Nombre",      placeholder="Ej: Chaleco táctico IOTV")
    categoria   = discord.ui.TextInput(label="Categoría",   placeholder="Protecciones / Armamento / Munición …")
    peso        = discord.ui.TextInput(label="Peso (kg)",   placeholder="Ej: 1.5")
    volumen     = discord.ui.TextInput(label="Volumen",     placeholder="Ej: 5")
    precio_base = discord.ui.TextInput(label="Precio base", placeholder="Ej: 500")

    def __init__(self, cog) -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            peso_val   = float(self.peso.value.replace(",", "."))
            vol_val    = float(self.volumen.value.replace(",", "."))
            precio_val = float(self.precio_base.value.replace(",", "."))
        except ValueError:
            await interaction.response.send_message(
                embed=embed_error("Peso, volumen y precio deben ser números válidos."), ephemeral=True
            )
            return

        item_id = await self.cog.repo.crear_item({
            "nombre": self.nombre.value,
            "categoria": self.categoria.value,
            "peso_kg": peso_val,
            "volumen": vol_val,
            "precio_base": precio_val,
        })
        await interaction.response.send_message(
            embed=embed_ok("Ítem creado", f"**{self.nombre.value}** creado con ID `{item_id}`."),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InventarioCog(bot))
