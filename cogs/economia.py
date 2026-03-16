"""
RAISA — Cog de Sistema Económico y Tienda
cogs/economia.py

Responsabilidad : Tienda, compra/venta, salarios, gestión de dinero.
                  Bloqueo automático en Evento-ON.
Dependencias    : discord.py, db/repository, utils/permisos, utils/embeds
Autor           : Proyecto RAISA
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import embed_ok, embed_error, embed_aviso, embed_tienda_bloqueada
from utils.permisos import require_role, RANGO_USUARIO, RANGO_NARRADOR

log = logging.getLogger("raisa.economia")

ITEMS_POR_PAGINA = 10


class EconomiaCog(commands.Cog, name="Economía"):
    """Cog para el sistema económico y la tienda de RAISA."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def repo(self):
        return self.bot.repo

    # ──────────────────────────────────────────────────────────────────────
    # HELPER: Verificar que la tienda esté abierta
    # ──────────────────────────────────────────────────────────────────────

    async def _tienda_abierta(self) -> bool:
        modo = await self.repo.get_modo_evento()
        return modo == "OFF"

    # ──────────────────────────────────────────────────────────────────────
    # USUARIO — Tienda
    # ──────────────────────────────────────────────────────────────────────

    eco_group = app_commands.Group(name="economia", description="Sistema económico y tienda")

    @eco_group.command(name="tienda", description="Muestra los ítems disponibles en la tienda.")
    @require_role(RANGO_USUARIO)
    async def ver_tienda(self, interaction: discord.Interaction) -> None:
        """Lista los ítems en venta. Bloqueada en Evento-ON."""
        if not await self._tienda_abierta():
            await interaction.response.send_message(embed=embed_tienda_bloqueada(), ephemeral=True)
            return

        items = await self.repo.get_tienda()
        if not items:
            await interaction.response.send_message(
                embed=embed_ok("Tienda", "La tienda está vacía por el momento."), ephemeral=True
            )
            return

        # Agrupar por categoría para presentación clara
        categorias: dict[str, list] = {}
        for it in items:
            cat = it.get("categoria", "Miscelánea")
            categorias.setdefault(cat, []).append(it)

        embed = discord.Embed(title="🏪 Tienda — Fundación SCP", color=discord.Color.gold())
        for cat, cat_items in list(categorias.items())[:25]:  # límite de campos de Discord
            lineas = "\n".join(
                f"• `ID:{it['id']}` **{it['nombre']}** — ${it['precio_actual']:.2f}"
                f" | `{it['peso_kg']} kg`"
                for it in cat_items
            )
            embed.add_field(name=cat, value=lineas[:1024], inline=False)

        from utils.embeds import FOOTER_TEXT
        embed.set_footer(text=FOOTER_TEXT)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @eco_group.command(name="saldo", description="Muestra tu saldo actual.")
    @require_role(RANGO_USUARIO)
    async def ver_saldo(self, interaction: discord.Interaction) -> None:
        """Muestra el dinero disponible del personaje."""
        saldo = await self.repo.get_dinero(interaction.user.id)
        await interaction.response.send_message(
            embed=embed_ok("Saldo", f"Tu saldo actual: **${saldo:.2f}**"), ephemeral=True
        )

    @eco_group.command(name="comprar", description="Compra un ítem de la tienda.")
    @app_commands.describe(tienda_id="ID del ítem en la tienda", cantidad="Cantidad a comprar")
    @require_role(RANGO_USUARIO)
    async def comprar(
        self, interaction: discord.Interaction, tienda_id: int, cantidad: int = 1
    ) -> None:
        """
        Compra un ítem de la tienda.
        El ítem pasa directamente al inventario general del usuario.
        Bloqueado en Evento-ON.
        """
        if not await self._tienda_abierta():
            await interaction.response.send_message(embed=embed_tienda_bloqueada(), ephemeral=True)
            return

        uid = interaction.user.id
        personaje = await self.repo.get_personaje(uid)
        if not personaje:
            await interaction.response.send_message(embed=embed_error("No tienes personaje registrado."), ephemeral=True)
            return

        # Obtener ítem de la tienda
        row = await self.repo._fetch_one(
            "SELECT t.*, i.nombre, i.categoria FROM tienda t JOIN items i ON t.item_uuid = i.item_uuid WHERE t.id = ? AND t.activo = 1",
            (tienda_id,),
        )
        if not row:
            await interaction.response.send_message(
                embed=embed_error(f"Ítem `{tienda_id}` no disponible en la tienda."), ephemeral=True
            )
            return

        # Verificar stock
        if row["stock"] != -1 and row["stock"] < cantidad:
            await interaction.response.send_message(
                embed=embed_error(f"Stock insuficiente. Disponible: `{row['stock']}`"), ephemeral=True
            )
            return

        coste_total = row["precio_actual"] * cantidad

        # Descontar dinero (lanza ValueError si no hay saldo suficiente)
        try:
            nuevo_saldo = await self.repo.modificar_dinero(uid, -coste_total)
        except ValueError as exc:
            await interaction.response.send_message(embed=embed_error(str(exc)), ephemeral=True)
            return

        # Añadir al inventario general
        await self.repo.añadir_item_inventario(uid, row["item_uuid"], cantidad)

        # Reducir stock si no es ilimitado
        if row["stock"] != -1:
            await self.repo._execute(
                "UPDATE tienda SET stock = stock - ? WHERE id = ?", (cantidad, tienda_id)
            )

        await interaction.response.send_message(
            embed=embed_ok(
                "Compra realizada",
                f"**{row['nombre']}** ×{cantidad} añadido a tu inventario.\n"
                f"Coste: **${coste_total:.2f}** | Saldo restante: **${nuevo_saldo:.2f}**"
            ),
        )

    @eco_group.command(name="vender", description="Vende un ítem de tu inventario general.")
    @app_commands.describe(item_id="ID del ítem a vender", cantidad="Cantidad")
    @require_role(RANGO_USUARIO)
    async def vender(
        self, interaction: discord.Interaction, item_id: int, cantidad: int = 1
    ) -> None:
        """Vende un ítem del inventario general. Bloqueado en Evento-ON."""
        if not await self._tienda_abierta():
            await interaction.response.send_message(embed=embed_tienda_bloqueada(), ephemeral=True)
            return

        uid = interaction.user.id
        inv = await self.repo.get_inventario_general(uid)
        entrada = next((it for it in inv if it["item_uuid"] == item_id), None)
        if not entrada or entrada["cantidad"] < cantidad:
            await interaction.response.send_message(
                embed=embed_error(f"No tienes suficiente cantidad del ítem `{item_id}`."), ephemeral=True
            )
            return

        item = await self.repo.get_item(item_id)
        # Precio de venta = 50% del precio base
        precio_venta = item.get("precio_base", 0) * 0.5 * cantidad

        await self.repo.retirar_item_inventario(uid, item_id, cantidad)
        nuevo_saldo = await self.repo.modificar_dinero(uid, precio_venta)

        await interaction.response.send_message(
            embed=embed_ok(
                "Venta realizada",
                f"**{item['nombre']}** ×{cantidad} vendido.\n"
                f"Ingreso: **${precio_venta:.2f}** | Saldo: **${nuevo_saldo:.2f}**"
            ),
        )

    # ──────────────────────────────────────────────────────────────────────
    # NARRADOR — Gestión económica
    # ──────────────────────────────────────────────────────────────────────

    @eco_group.command(name="entregar_dinero", description="[Narrador] Entrega dinero a un usuario.")
    @app_commands.describe(usuario="Usuario objetivo", cantidad="Cantidad a entregar")
    @require_role(RANGO_NARRADOR)
    async def entregar_dinero(
        self, interaction: discord.Interaction, usuario: discord.Member, cantidad: float
    ) -> None:
        """Entrega dinero al saldo del usuario."""
        if cantidad <= 0:
            await interaction.response.send_message(embed=embed_error("La cantidad debe ser positiva."), ephemeral=True)
            return

        nuevo_saldo = await self.repo.modificar_dinero(usuario.id, cantidad)
        await interaction.response.send_message(
            embed=embed_ok("Dinero entregado", f"**${cantidad:.2f}** entregados a {usuario.mention}.\nSaldo nuevo: **${nuevo_saldo:.2f}**"),
        )

    @eco_group.command(name="retirar_dinero", description="[Narrador] Retira dinero de un usuario.")
    @app_commands.describe(usuario="Usuario objetivo", cantidad="Cantidad a retirar")
    @require_role(RANGO_NARRADOR)
    async def retirar_dinero(
        self, interaction: discord.Interaction, usuario: discord.Member, cantidad: float
    ) -> None:
        """Retira dinero del saldo del usuario."""
        try:
            nuevo_saldo = await self.repo.modificar_dinero(usuario.id, -cantidad)
        except ValueError as exc:
            await interaction.response.send_message(embed=embed_error(str(exc)), ephemeral=True)
            return

        await interaction.response.send_message(
            embed=embed_ok("Dinero retirado", f"**${cantidad:.2f}** retirados de {usuario.mention}.\nSaldo restante: **${nuevo_saldo:.2f}**"),
        )

    @eco_group.command(name="salario", description="[Narrador] Paga el salario a un usuario por sus roles.")
    @app_commands.describe(usuario="Usuario objetivo")
    @require_role(RANGO_NARRADOR)
    async def pagar_salario(self, interaction: discord.Interaction, usuario: discord.Member) -> None:
        """
        Paga el salario correspondiente a los roles de Discord del usuario.
        Se toma el salario más alto configurado en config/inventario.json.
        """
        import json
        from pathlib import Path

        cfg = json.loads(Path("config/inventario.json").read_text(encoding="utf-8"))
        salarios_config = cfg.get("salarios", {})
        
        # Obtener todos los montos de salario aplicables a los roles del usuario
        montos_aplicables = []
        for role in usuario.roles:
            rid_str = str(role.id)
            if rid_str in salarios_config:
                montos_aplicables.append(salarios_config[rid_str])
        
        monto = max(montos_aplicables) if montos_aplicables else 0

        if monto <= 0:
            await interaction.response.send_message(
                embed=embed_aviso("Sin salario", f"{usuario.mention} no tiene ningún rol con salario configurado."),
                ephemeral=True,
            )
            return

        try:
            nuevo_saldo = await self.repo.modificar_dinero(usuario.id, monto)
        except ValueError as exc:
            await interaction.response.send_message(embed=embed_error(str(exc)), ephemeral=True)
            return

        await interaction.response.send_message(
            embed=embed_ok(
                "Salario pagado",
                f"Se han pagado **${monto:.2f}** a {usuario.mention}.\n"
                f"Saldo nuevo: **${nuevo_saldo:.2f}**"
            ),
        )

    @eco_group.command(name="tienda_añadir", description="[Narrador] Añade un ítem a la tienda.")
    @app_commands.describe(item_id="ID del ítem en catálogo", precio="Precio de venta", stock="Stock (-1 = ilimitado)")
    @require_role(RANGO_NARRADOR)
    async def tienda_añadir(
        self, interaction: discord.Interaction, item_id: int, precio: float, stock: int = -1
    ) -> None:
        """Pone a la venta un ítem del catálogo."""
        item = await self.repo.get_item(item_id)
        if not item:
            await interaction.response.send_message(embed=embed_error(f"Ítem `{item_id}` no en catálogo."), ephemeral=True)
            return

        await self.repo.añadir_a_tienda(item_id, precio, stock)
        await interaction.response.send_message(
            embed=embed_ok("Tienda actualizada", f"**{item['nombre']}** añadido a la tienda por **${precio:.2f}**. Stock: `{stock if stock != -1 else 'Ilimitado'}`"),
        )

    @eco_group.command(name="tienda_retirar", description="[Narrador] Retira un ítem de la tienda.")
    @app_commands.describe(tienda_id="ID de la entrada en tienda")
    @require_role(RANGO_NARRADOR)
    async def tienda_retirar(self, interaction: discord.Interaction, tienda_id: int) -> None:
        """Desactiva un ítem de la tienda."""
        await self.repo.retirar_de_tienda(tienda_id)
        await interaction.response.send_message(
            embed=embed_ok("Tienda actualizada", f"Ítem `{tienda_id}` retirado de la tienda."),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EconomiaCog(bot))
