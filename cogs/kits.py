"""
cogs/kits.py — Sistema de KITs Médicos de RAISA
================================================
Responsabilidad : Gestión de KITs médicos (compra, extracción, inserción de ítems).
Dependencias    : db.repository, db.kits_repository, utils.embeds, utils.permisos
Autor           : RAISA Dev

Comandos
--------
  /kit ver [usuario]                  — Ver KITs de un usuario (Usuario+ propio, Narrador+ otros)
  /kit contenido [instancia_id]       — Ver contenido de un KIT (Usuario+)
  /kit extraer [instancia_id] [item] [cantidad] — Extraer ítem de KIT (Narrador+)
  /kit insertar [instancia_id] [item] [cantidad] — Insertar ítem en KIT (Narrador+)
  /kit eliminar [instancia_id]        — Eliminar KIT (Narrador+)

Flujo de compra (manejado por economia.py)
-------------------------------------------
1. Usuario compra KIT desde /tienda
2. Se llama a kits_repository.create_kit_instance()
3. KIT aparece en inventario general con contenido default

Gestión de contenido
--------------------
Solo los Narradores pueden extraer/insertar ítems en KITs mediante comandos.
Los usuarios pueden ver el contenido pero no modificarlo directamente.
"""

import discord
from discord import Interaction, app_commands
from discord.ext import commands

from db import kits_repository as kits_repo
from db import repository as repo
from utils import embeds as emb
from utils.logger import log_info
from utils.permisos import NARRADOR, USUARIO, require_role


class KitsCog(commands.Cog, name="KITs"):
    """Cog del sistema de KITs médicos."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -----------------------------------------------------------------------
    # Grupo de comandos
    # -----------------------------------------------------------------------

    kit_group = app_commands.Group(name="kit", description="Gestión de KITs médicos")

    # -----------------------------------------------------------------------
    # /kit ver
    # -----------------------------------------------------------------------

    @kit_group.command(name="ver", description="Ver los KITs de un usuario")
    @app_commands.describe(
        usuario="Usuario objetivo (dejar vacío para ver los tuyos)",
    )
    @require_role(USUARIO)
    async def kit_ver(
        self,
        interaction: Interaction,
        usuario: discord.Member | None = None,
    ) -> None:
        """
        Muestra todos los KITs que posee un usuario.
        
        Args:
            interaction: Contexto de Discord.
            usuario    : Usuario objetivo (opcional, por defecto el que ejecuta).
        """
        # Determinar usuario objetivo
        target_user = usuario or interaction.user
        
        # Si el usuario objetivo no es el que ejecuta, verificar permisos de Narrador
        if target_user.id != interaction.user.id:
            nivel = await repo.get_user_level(interaction.user.id)
            if nivel < NARRADOR:
                await interaction.response.send_message(
                    embed=emb.error(
                        "Permiso denegado",
                        "Solo puedes ver tus propios KITs. Necesitas nivel Narrador para ver los de otros.",
                    ),
                    ephemeral=True,
                )
                return

        async with await repo.get_conn() as conn:
            kits = await kits_repo.get_user_kits(conn, target_user.id)

        if not kits:
            await interaction.response.send_message(
                embed=emb.info(
                    "Sin KITs",
                    f"**{target_user.display_name}** no tiene ningún KIT.",
                ),
                ephemeral=True,
            )
            return

        # Construir embed
        embed = discord.Embed(
            title=f"🎒 KITs Médicos — {target_user.display_name}",
            color=emb.C_INFO,
            description=f"Total: **{len(kits)}** KIT(s)",
        )

        # Agrupar por ubicación
        agrupados: dict[str, list] = {}
        for kit in kits:
            ubicacion = kit["ubicacion"]
            agrupados.setdefault(ubicacion, []).append(kit)

        for ubicacion, lista in agrupados.items():
            lineas = []
            for k in lista:
                slot_info = f" → `{k['slot_destino']}`" if k["slot_destino"] else ""
                lineas.append(
                    f"[{k['id']}] **{k['kit_nombre']}** ({k['codigo']})\n"
                    f"└ Peso contenido: {k['peso_contenido']:.2f}kg | "
                    f"Volumen: {k['volumen_total']}u (+{k['espacio_libre_pct']}%){slot_info}"
                )
            
            ubicacion_display = {
                "general": "📦 Inventario General",
                "loadout_slot": "🎽 Equipado (Loadout)",
                "pouch": "🎒 Dentro de Pouch",
                "vehiculo": "🚗 Vehículo",
            }.get(ubicacion.split("_")[0], ubicacion.capitalize())
            
            embed.add_field(
                name=ubicacion_display,
                value="\n\n".join(lineas),
                inline=False,
            )

        embed.set_footer(text=emb.FOOTER_TEXT)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -----------------------------------------------------------------------
    # /kit contenido
    # -----------------------------------------------------------------------

    @kit_group.command(name="contenido", description="Ver el contenido de un KIT")
    @app_commands.describe(instancia_id="ID de la instancia del KIT (ver /kit ver)")
    @require_role(USUARIO)
    async def kit_contenido(self, interaction: Interaction, instancia_id: int) -> None:
        """
        Muestra el contenido actual de un KIT específico.
        
        Args:
            interaction : Contexto de Discord.
            instancia_id: ID de la instancia del KIT.
        """
        async with await repo.get_conn() as conn:
            kit = await kits_repo.get_kit_instance(conn, instancia_id)
            
            if not kit:
                await interaction.response.send_message(
                    embed=emb.error(
                        "KIT no encontrado",
                        f"No existe ningún KIT con ID `{instancia_id}`.",
                    ),
                    ephemeral=True,
                )
                return
            
            # Verificar pertenencia si no es Narrador+
            nivel = await repo.get_user_level(interaction.user.id)
            if nivel < NARRADOR and kit["user_id"] != interaction.user.id:
                await interaction.response.send_message(
                    embed=emb.error(
                        "Permiso denegado",
                        "Este KIT no te pertenece.",
                    ),
                    ephemeral=True,
                )
                return
            
            contenido = await kits_repo.get_kit_contents(conn, instancia_id)
            
            # Obtener nombres de ítems desde el catálogo
            items_detalle = []
            for item in contenido:
                item_data = await repo.get_item_by_code(conn, item["item_codigo"])
                nombre = item_data["nombre"] if item_data else item["item_codigo"]
                items_detalle.append(f"• **{nombre}** x{item['cantidad']}")

        # Construir embed
        volumen_max = kit["volumen_total"] * (1 + kit["espacio_libre_pct"] / 100.0)
        espacio_usado_pct = (kit["volumen_usado"] / volumen_max * 100) if volumen_max > 0 else 0

        embed = discord.Embed(
            title=f"📦 {kit['kit_nombre']} (ID: {instancia_id})",
            color=emb.C_INFO,
            description=(
                f"**Código:** {kit['kit_codigo']}\n"
                f"**Ubicación:** {kit['ubicacion']}\n"
                f"**Peso contenido:** {kit['peso_contenido_actual']:.2f}kg\n"
                f"**Espacio usado:** {kit['volumen_usado']}u / {volumen_max:.0f}u "
                f"({espacio_usado_pct:.1f}%)"
            ),
        )

        if items_detalle:
            # Dividir en chunks si es muy largo
            chunk_size = 20
            for i in range(0, len(items_detalle), chunk_size):
                chunk = items_detalle[i:i + chunk_size]
                embed.add_field(
                    name=f"Contenido ({i+1}-{min(i+chunk_size, len(items_detalle))})",
                    value="\n".join(chunk),
                    inline=False,
                )
        else:
            embed.add_field(
                name="Contenido",
                value="*KIT vacío*",
                inline=False,
            )

        embed.set_footer(text=emb.FOOTER_TEXT)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -----------------------------------------------------------------------
    # /kit extraer
    # -----------------------------------------------------------------------

    @kit_group.command(
        name="extraer",
        description="[NARRADOR] Extraer un ítem de un KIT al inventario del usuario",
    )
    @app_commands.describe(
        instancia_id="ID de la instancia del KIT",
        item_codigo="Código del ítem a extraer (ej: MED-CHH-001)",
        cantidad="Cantidad a extraer (default: 1)",
    )
    @require_role(NARRADOR)
    async def kit_extraer(
        self,
        interaction: Interaction,
        instancia_id: int,
        item_codigo: str,
        cantidad: int = 1,
    ) -> None:
        """
        Extrae un ítem de un KIT y lo añade al inventario general del usuario.
        Solo accesible para Narradores.
        
        Args:
            interaction : Contexto de Discord.
            instancia_id: ID de la instancia del KIT.
            item_codigo : Código del ítem a extraer.
            cantidad    : Cantidad a extraer.
        """
        if cantidad <= 0:
            await interaction.response.send_message(
                embed=emb.error("Cantidad inválida", "La cantidad debe ser mayor a 0."),
                ephemeral=True,
            )
            return

        async with await repo.get_conn() as conn:
            # Verificar que existe el KIT
            kit = await kits_repo.get_kit_instance(conn, instancia_id)
            if not kit:
                await interaction.response.send_message(
                    embed=emb.error(
                        "KIT no encontrado",
                        f"No existe ningún KIT con ID `{instancia_id}`.",
                    ),
                    ephemeral=True,
                )
                return
            
            # Verificar que el ítem existe en el catálogo
            item_data = await repo.get_item_by_code(conn, item_codigo)
            if not item_data:
                await interaction.response.send_message(
                    embed=emb.error(
                        "Ítem no encontrado",
                        f"No existe ningún ítem con código `{item_codigo}` en el catálogo.",
                    ),
                    ephemeral=True,
                )
                return
            
            # Extraer del KIT
            exito = await kits_repo.extract_item_from_kit(
                conn, instancia_id, item_codigo, cantidad
            )
            
            if not exito:
                await interaction.response.send_message(
                    embed=emb.error(
                        "Sin cantidad suficiente",
                        f"El KIT no contiene suficiente **{item_data['nombre']}** (necesitas {cantidad}).",
                    ),
                    ephemeral=True,
                )
                return
            
            # Añadir al inventario general del usuario
            for _ in range(cantidad):
                await repo.add_to_general_inventory(conn, kit["user_id"], item_data["id"])

        # Log de acción
        log_info(
            f"[KIT EXTRAER] Narrador {interaction.user.id} extrajo {cantidad}x {item_codigo} "
            f"del KIT {instancia_id} (usuario {kit['user_id']})"
        )

        await interaction.response.send_message(
            embed=emb.ok(
                "Ítem extraído",
                f"**{cantidad}x {item_data['nombre']}** extraído del KIT `{kit['kit_nombre']}` "
                f"y añadido al inventario general de <@{kit['user_id']}>.",
            ),
            ephemeral=True,
        )

    # -----------------------------------------------------------------------
    # /kit insertar
    # -----------------------------------------------------------------------

    @kit_group.command(
        name="insertar",
        description="[NARRADOR] Insertar un ítem del inventario del usuario en un KIT",
    )
    @app_commands.describe(
        instancia_id="ID de la instancia del KIT",
        item_codigo="Código del ítem a insertar (ej: MED-CHH-001)",
        cantidad="Cantidad a insertar (default: 1)",
    )
    @require_role(NARRADOR)
    async def kit_insertar(
        self,
        interaction: Interaction,
        instancia_id: int,
        item_codigo: str,
        cantidad: int = 1,
    ) -> None:
        """
        Inserta un ítem del inventario general del usuario en un KIT.
        Solo accesible para Narradores.
        
        Args:
            interaction : Contexto de Discord.
            instancia_id: ID de la instancia del KIT.
            item_codigo : Código del ítem a insertar.
            cantidad    : Cantidad a insertar.
        """
        if cantidad <= 0:
            await interaction.response.send_message(
                embed=emb.error("Cantidad inválida", "La cantidad debe ser mayor a 0."),
                ephemeral=True,
            )
            return

        async with await repo.get_conn() as conn:
            # Verificar que existe el KIT
            kit = await kits_repo.get_kit_instance(conn, instancia_id)
            if not kit:
                await interaction.response.send_message(
                    embed=emb.error(
                        "KIT no encontrado",
                        f"No existe ningún KIT con ID `{instancia_id}`.",
                    ),
                    ephemeral=True,
                )
                return
            
            # Verificar que el ítem existe en el catálogo
            item_data = await repo.get_item_by_code(conn, item_codigo)
            if not item_data:
                await interaction.response.send_message(
                    embed=emb.error(
                        "Ítem no encontrado",
                        f"No existe ningún ítem con código `{item_codigo}` en el catálogo.",
                    ),
                    ephemeral=True,
                )
                return
            
            # Verificar que el usuario tiene suficientes ítems en inventario general
            # (Asumiendo que existe función en repo para verificar cantidad)
            # NOTA: Ajustar según la implementación real de tu repositorio
            inventario = await repo.get_general_inventory(conn, kit["user_id"])
            cantidad_disponible = sum(
                1 for i in inventario if i["item_id"] == item_data["id"]
            )
            
            if cantidad_disponible < cantidad:
                await interaction.response.send_message(
                    embed=emb.error(
                        "Sin ítems suficientes",
                        f"El usuario solo tiene {cantidad_disponible}x **{item_data['nombre']}** "
                        f"en su inventario general (necesitas {cantidad}).",
                    ),
                    ephemeral=True,
                )
                return
            
            # Insertar en el KIT
            exito, motivo = await kits_repo.insert_item_into_kit(
                conn,
                instancia_id,
                item_codigo,
                item_data["peso_kg"],
                item_data["volumen_u"],
                cantidad,
            )
            
            if not exito:
                await interaction.response.send_message(
                    embed=emb.error("Error al insertar", motivo),
                    ephemeral=True,
                )
                return
            
            # Retirar del inventario general
            for _ in range(cantidad):
                await repo.remove_from_general_inventory(conn, kit["user_id"], item_data["id"])

        # Log de acción
        log_info(
            f"[KIT INSERTAR] Narrador {interaction.user.id} insertó {cantidad}x {item_codigo} "
            f"en el KIT {instancia_id} (usuario {kit['user_id']})"
        )

        await interaction.response.send_message(
            embed=emb.ok(
                "Ítem insertado",
                f"**{cantidad}x {item_data['nombre']}** insertado en el KIT `{kit['kit_nombre']}`.",
            ),
            ephemeral=True,
        )

    # -----------------------------------------------------------------------
    # /kit eliminar
    # -----------------------------------------------------------------------

    @kit_group.command(
        name="eliminar",
        description="[NARRADOR] Eliminar un KIT completamente (contenido se pierde)",
    )
    @app_commands.describe(instancia_id="ID de la instancia del KIT a eliminar")
    @require_role(NARRADOR)
    async def kit_eliminar(self, interaction: Interaction, instancia_id: int) -> None:
        """
        Elimina permanentemente una instancia de KIT.
        ADVERTENCIA: El contenido del KIT se pierde.
        
        Args:
            interaction : Contexto de Discord.
            instancia_id: ID de la instancia a eliminar.
        """
        async with await repo.get_conn() as conn:
            # Verificar que existe el KIT
            kit = await kits_repo.get_kit_instance(conn, instancia_id)
            if not kit:
                await interaction.response.send_message(
                    embed=emb.error(
                        "KIT no encontrado",
                        f"No existe ningún KIT con ID `{instancia_id}`.",
                    ),
                    ephemeral=True,
                )
                return
            
            # Eliminar
            eliminado = await kits_repo.delete_kit_instance(conn, instancia_id, kit["user_id"])
            
            if not eliminado:
                await interaction.response.send_message(
                    embed=emb.error("Error", "No se pudo eliminar el KIT."),
                    ephemeral=True,
                )
                return

        # Log de acción
        log_info(
            f"[KIT ELIMINAR] Narrador {interaction.user.id} eliminó el KIT {instancia_id} "
            f"(usuario {kit['user_id']})"
        )

        await interaction.response.send_message(
            embed=emb.ok(
                "KIT eliminado",
                f"El KIT `{kit['kit_nombre']}` (ID: {instancia_id}) ha sido eliminado permanentemente.",
            ),
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot) -> None:
    """Registra el cog en el bot."""
    await bot.add_cog(KitsCog(bot))