"""
cogs/economia.py — Sistema económico y tienda de RAISA
=======================================================
Responsabilidad : Economía de personajes, tienda de ítems y pago de salarios.
Dependencias    : db.repository, utils.embeds, utils.permisos,
                  utils.validaciones, cogs.eventos, discord.ext.tasks
Autor           : RAISA Dev

Comandos
--------
  /economia saldo              — Ver saldo propio (Usuario+)
  /economia historial          — Historial de transacciones (Usuario+)
  /tienda ver [pagina]         — Ver catálogo (Usuario+)
  /tienda comprar [item]       — Comprar ítem (Usuario+) — bloqueado en Evento-ON
  /tienda vender [item]        — Vender ítem (Usuario+)  — bloqueado en Evento-ON
  /tienda añadir ...           — Añadir ítem a tienda (Narrador+)
  /tienda quitar [item]        — Quitar ítem de tienda (Narrador+)
  /admin-eco entregar [user] [amt] — Dar dinero (Narrador+)
  /admin-eco retirar [user] [amt]  — Quitar dinero (Narrador+)
  /admin-eco salarios              — Pagar salarios manualmente (Narrador+)

CORRECCIONES APLICADAS:
- Eliminado _patch_repo() (función movida a db/repository.py)
- Mejorado manejo de config/inventario.json con try/except y fallback
"""

import json
from pathlib import Path

import discord
from discord import Interaction, app_commands
from discord.ext import commands, tasks

from cogs.eventos import evento_activo
from db import repository as repo
from utils import embeds as emb
from utils.logger import audit, log_info, log_error
from utils.permisos import NARRADOR, USUARIO, get_user_level, require_role

# ---------------------------------------------------------------------------
# Carga de configuración económica e inventario
# ---------------------------------------------------------------------------

def _cargar_economia_config() -> dict:
    """Carga config/economia.json con fallback a defaults."""
    try:
        with Path("config/economia.json").open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "salarios": {
                "frecuencia_horas": 168,
                "montos_por_rango": {
                    "usuario": 500, "narrador": 800, "gestor": 1000,
                    "admin": 0, "owner": 0, "holder": 0,
                },
            },
            "moneda": {"nombre": "Créditos", "simbolo": "₢", "decimales": 2},
        }


def _cargar_inventario_config() -> dict:
    """
    Carga config/inventario.json con fallback a defaults.
    
    CORREGIDO: Manejo robusto de FileNotFoundError para evitar crashes
    en tienda_comprar cuando el archivo no existe.
    """
    try:
        with Path("config/inventario.json").open(encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        log_error("[ECONOMÍA] config/inventario.json no encontrado, usando valores por defecto")
        return {"limites_globales": {"peso_max_kg": 40.0, "volumen_max_u": 80}}
    except Exception as e:
        log_error(f"[ECONOMÍA] Error cargando config/inventario.json: {e}")
        return {"limites_globales": {"peso_max_kg": 40.0, "volumen_max_u": 80}}


class EconomiaCog(commands.Cog, name="Economía"):
    """Cog del sistema económico y tienda."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot     = bot
        self.cfg     = _cargar_economia_config()
        self.inv_cfg = _cargar_inventario_config()  # Cargado una vez al init
        self.simbolo = self.cfg["moneda"]["simbolo"]
        self._salario_task_started = False

    async def cog_load(self) -> None:
        """Inicia la tarea de salarios al cargar el cog."""
        freq_h = self.cfg["salarios"].get("frecuencia_horas", 168)
        freq_m = freq_h * 60
        # Configurar intervalo dinámico (mínimo 10 minutos según hardware target)
        self._salario_loop.change_interval(minutes=max(10, freq_m))
        self._salario_loop.start()
        log_info(f"[ECONOMÍA] Tarea de salarios iniciada (cada {freq_h}h)")

    async def cog_unload(self) -> None:
        """Para la tarea de salarios al descargar el cog."""
        self._salario_loop.cancel()

    # -----------------------------------------------------------------------
    # Tarea periódica de salarios
    # -----------------------------------------------------------------------

    @tasks.loop(hours=168)  # valor real configurado en cog_load
    async def _salario_loop(self) -> None:
        """Paga salarios automáticamente a todos los usuarios activos."""
        await self._pagar_salarios_automatico()

    @_salario_loop.before_loop
    async def _before_salario(self) -> None:
        """Espera a que el bot esté listo antes de iniciar la tarea."""
        await self.bot.wait_until_ready()

    async def _pagar_salarios_automatico(self) -> None:
        """
        Recorre todos los personajes activos y les paga el salario según su rango.
        Registra cada pago en transactions.
        """
        montos = self.cfg["salarios"]["montos_por_rango"]
        async with await repo.get_conn() as conn:
            # CORREGIDO: Ahora usa la función directamente de repository.py
            personajes = await repo.get_characters_activos(conn)
            
            for char in personajes:
                rango = await self._determinar_rango(char["user_id"])
                monto = montos.get(rango, 0)
                
                if monto <= 0:
                    continue
                
                try:
                    await repo.update_balance(
                        conn,
                        user_id     = char["user_id"],
                        delta       = monto,
                        tipo        = "salario",
                        descripcion = f"Salario automático — rango {rango}",
                        ejecutado_por = None,  # Sistema
                    )
                    log_info(f"[ECONOMÍA] Salario pagado: {char['nombre_completo']} ({rango}) → {monto} {self.simbolo}")
                except Exception as exc:
                    log_error(f"[ECONOMÍA] Error pagando salario a {char['user_id']}: {exc}")

    async def _determinar_rango(self, user_id: int) -> str:
        """
        Determina el rango de un usuario basándose en sus roles de Discord.
        Devuelve el rango en minúsculas para coincidir con la config de salarios.

        Args:
            user_id: discord user_id.

        Returns:
            Nombre del rango en minúsculas.
        """
        from utils.permisos import (
            get_user_level, OWNER, HOLDER, ADMIN, GESTOR, NARRADOR, USUARIO, VISITANTE
        )
        cfg   = getattr(self.bot, "raisa_config", {})
        roles = cfg.get("roles", {})

        for guild in self.bot.guilds:
            member = guild.get_member(user_id)
            if not member:
                continue
            member_role_ids = {r.id for r in member.roles}
            if roles.get("gestor")   in member_role_ids: return "gestor"
            if roles.get("narrador") in member_role_ids: return "narrador"
            if roles.get("usuario")  in member_role_ids: return "usuario"
        return "visitante"

    # -----------------------------------------------------------------------
    # Grupos de comandos
    # -----------------------------------------------------------------------

    economia_group = app_commands.Group(
        name="economia", description="Gestión económica de personaje"
    )
    tienda_group = app_commands.Group(
        name="tienda", description="Tienda de ítems"
    )
    admin_eco_group = app_commands.Group(
        name="admin-eco", description="Administración económica (Narrador+)"
    )

    # -----------------------------------------------------------------------
    # /economia saldo
    # -----------------------------------------------------------------------

    @economia_group.command(name="saldo", description="Muestra tu saldo actual")
    @require_role(USUARIO)
    async def eco_saldo(self, interaction: Interaction) -> None:
        """
        Muestra el saldo del personaje del usuario.

        Args:
            interaction: Contexto de Discord.
        """
        async with await repo.get_conn() as conn:
            char = await repo.get_character(conn, interaction.user.id)
            if not char or char["estado"] != "activo":
                await interaction.response.send_message(
                    embed=emb.error("Sin personaje", "No tienes un personaje activo registrado."),
                    ephemeral=True,
                )
                return

            saldo = await repo.get_balance(conn, interaction.user.id)

        await interaction.response.send_message(
            embed=emb.info(
                f"💰 Saldo — {char['nombre_completo']}",
                f"**{saldo:,.2f} {self.simbolo}**"
            ),
            ephemeral=True,
        )

    # -----------------------------------------------------------------------
    # /economia historial
    # -----------------------------------------------------------------------

    @economia_group.command(name="historial", description="Ver historial de transacciones")
    @app_commands.describe(pagina="Página del historial (default: 1)")
    @require_role(USUARIO)
    async def eco_historial(self, interaction: Interaction, pagina: int = 1) -> None:
        """
        Muestra el historial de transacciones del usuario paginado.

        Args:
            interaction: Contexto de Discord.
            pagina     : Número de página.
        """
        pagina  = max(1, pagina)
        por_pag = 10
        offset  = (pagina - 1) * por_pag

        async with await repo.get_conn() as conn:
            char = await repo.get_character(conn, interaction.user.id)
            if not char or char["estado"] != "activo":
                await interaction.response.send_message(
                    embed=emb.error("Sin personaje", "No tienes un personaje activo."),
                    ephemeral=True,
                )
                return
            txs = await repo.get_transactions(conn, interaction.user.id,
                                               limit=por_pag, offset=offset)

        if not txs:
            await interaction.response.send_message(
                embed=emb.info("Historial vacío", "No tienes transacciones registradas."),
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"📊  Historial económico — {char['nombre_completo']}",
            color=emb.C_GOLD,
        )
        for tx in txs:
            signo = "+" if tx["cantidad"] >= 0 else ""
            embed.add_field(
                name=f"{tx['tipo'].upper()} — {tx['creado_en'][:10]}",
                value=f"{signo}{tx['cantidad']:,.2f} {self.simbolo}\n_{tx['descripcion'] or '—'}_",
                inline=False,
            )
        embed.set_footer(text=f"{emb.FOOTER_TEXT} | Página {pagina}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -----------------------------------------------------------------------
    # /tienda ver
    # -----------------------------------------------------------------------

    @tienda_group.command(name="ver", description="Ver el catálogo de la tienda")
    @app_commands.describe(pagina="Página del catálogo (default: 1)")
    @require_role(USUARIO)
    async def tienda_ver(self, interaction: Interaction, pagina: int = 1) -> None:
        """
        Muestra el catálogo de ítems disponibles en la tienda.
        No está bloqueado en Evento-ON (solo comprar/vender lo está).

        Args:
            interaction: Contexto de Discord.
            pagina     : Número de página.
        """
        pagina = max(1, pagina)
        async with await repo.get_conn() as conn:
            items, total_paginas = await repo.get_shop_listings(conn, pagina=pagina)

        if not items:
            await interaction.response.send_message(
                embed=emb.info("Tienda vacía", "No hay ítems disponibles en este momento."),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=emb.tienda_listado(
                [dict(it) for it in items],
                pagina=pagina,
                total_paginas=total_paginas,
                simbolo=self.simbolo,
            ),
            ephemeral=True,
        )

    # -----------------------------------------------------------------------
    # /tienda comprar
    # -----------------------------------------------------------------------

    @tienda_group.command(name="comprar", description="Comprar un ítem de la tienda")
    @app_commands.describe(nombre="Nombre exacto del ítem a comprar")
    @require_role(USUARIO)
    async def tienda_comprar(self, interaction: Interaction, nombre: str) -> None:
        """
        Compra un ítem de la tienda y lo añade al inventario general.
        Bloqueado durante Evento-ON.

        Args:
            interaction: Contexto de Discord.
            nombre     : Nombre del ítem.
        """
        # Verificar Evento-ON
        if await evento_activo():
            await interaction.response.send_message(
                embed=emb.evento_bloqueado("La tienda"), ephemeral=True
            )
            return

        async with await repo.get_conn() as conn:
            # Verificar personaje activo
            char = await repo.get_character(conn, interaction.user.id)
            if not char or char["estado"] != "activo":
                await interaction.response.send_message(
                    embed=emb.error("Sin personaje", "No tienes un personaje activo."),
                    ephemeral=True,
                )
                return

            # Buscar ítem en tienda
            listing = await repo.get_shop_item_by_name(conn, nombre)
            if not listing:
                await interaction.response.send_message(
                    embed=emb.error("Ítem no encontrado",
                                    f"No se encontró **{nombre}** en la tienda o está agotado."),
                    ephemeral=True,
                )
                return

            # Verificar saldo
            saldo_actual = await repo.get_balance(conn, interaction.user.id)
            precio = float(listing["precio"])

            if saldo_actual < precio:
                await interaction.response.send_message(
                    embed=emb.error(
                        "Saldo insuficiente",
                        f"Precio: **{precio:,.2f} {self.simbolo}**\n"
                        f"Tu saldo: **{saldo_actual:,.2f} {self.simbolo}**",
                    ),
                    ephemeral=True,
                )
                return

            # CORREGIDO: Verificar límites de inventario con fallback robusto
            from utils.validaciones import validar_peso_volumen
            
            # Usar config cargada en __init__ en lugar de cargar cada vez
            peso_max = self.inv_cfg["limites_globales"]["peso_max_kg"]
            vol_max  = self.inv_cfg["limites_globales"]["volumen_max_u"]

            peso_actual, vol_actual = await repo.get_inventory_totals(conn, interaction.user.id)
            resultado = validar_peso_volumen(
                peso_actual, vol_actual,
                float(listing["peso_kg"]), int(listing["volumen_u"]),
                peso_max=peso_max, volumen_max=vol_max,
            )
            if not resultado:
                await interaction.response.send_message(
                    embed=emb.error("Sin espacio en inventario", resultado.motivo),
                    ephemeral=True,
                )
                return

            # Ejecutar compra
            try:
                nuevo_saldo = await repo.update_balance(
                    conn,
                    user_id     = interaction.user.id,
                    delta       = -precio,
                    tipo        = "compra",
                    descripcion = f"Compra: {listing['nombre']}",
                    item_id     = listing["item_id"],
                )
                await repo.add_to_general_inventory(conn, interaction.user.id, listing["item_id"])
                await repo.reduce_shop_stock(conn, listing["listing_id"])
            except ValueError as exc:
                await interaction.response.send_message(
                    embed=emb.error("Error en compra", str(exc)), ephemeral=True
                )
                return

        await interaction.response.send_message(
            embed=emb.ok(
                "Compra realizada",
                f"**{listing['nombre']}** añadido a tu inventario general.\n"
                f"Saldo restante: **{nuevo_saldo:,.2f} {self.simbolo}**",
            ),
            ephemeral=True,
        )

    # -----------------------------------------------------------------------
    # /tienda vender
    # -----------------------------------------------------------------------

    @tienda_group.command(name="vender", description="Vender un ítem de tu inventario")
    @app_commands.describe(nombre="Nombre del ítem a vender")
    @require_role(USUARIO)
    async def tienda_vender(self, interaction: Interaction, nombre: str) -> None:
        """
        Vende un ítem del inventario general del usuario.
        Bloqueado en Evento-ON. El precio de venta es 50% del precio base.

        Args:
            interaction: Contexto de Discord.
            nombre     : Nombre del ítem a vender.
        """
        if await evento_activo():
            await interaction.response.send_message(
                embed=emb.evento_bloqueado("La tienda"), ephemeral=True
            )
            return

        async with await repo.get_conn() as conn:
            char = await repo.get_character(conn, interaction.user.id)
            if not char or char["estado"] != "activo":
                await interaction.response.send_message(
                    embed=emb.error("Sin personaje", "No tienes un personaje activo."),
                    ephemeral=True,
                )
                return

            # Buscar ítem en inventario del usuario
            item = await repo.get_item_by_name(conn, nombre)
            if not item:
                await interaction.response.send_message(
                    embed=emb.error("Ítem no encontrado", f"No se encontró **{nombre}** en el catálogo."),
                    ephemeral=True,
                )
                return

            # Verificar que el usuario tenga el ítem en su inventario general
            quitado = await repo.remove_from_general_inventory(conn, interaction.user.id, item["id"])
            if not quitado:
                await interaction.response.send_message(
                    embed=emb.error("Sin ítem", f"No tienes **{nombre}** en tu inventario general."),
                    ephemeral=True,
                )
                return

            precio_venta = float(item["precio_base"]) * 0.5
            nuevo_saldo = await repo.update_balance(
                conn,
                user_id     = interaction.user.id,
                delta       = precio_venta,
                tipo        = "venta",
                descripcion = f"Venta: {item['nombre']}",
                item_id     = item["id"],
            )

        await interaction.response.send_message(
            embed=emb.ok(
                "Venta realizada",
                f"Vendiste **{item['nombre']}** por **{precio_venta:,.2f} {self.simbolo}**.\n"
                f"Saldo actual: **{nuevo_saldo:,.2f} {self.simbolo}**",
            ),
            ephemeral=True,
        )

    # -----------------------------------------------------------------------
    # /admin-eco entregar
    # -----------------------------------------------------------------------

    @admin_eco_group.command(name="entregar", description="Entregar dinero a un usuario (Narrador+)")
    @app_commands.describe(usuario="Usuario destino", cantidad="Cantidad a entregar",
                            motivo="Motivo (opcional)")
    @require_role(NARRADOR)
    async def admin_entregar(self, interaction: Interaction,
                              usuario: discord.Member,
                              cantidad: float,
                              motivo: str = "Entrega por Narrador") -> None:
        """
        Entrega dinero a un usuario. Registrado en audit_log.

        Args:
            interaction: Contexto de Discord.
            usuario    : Miembro destino.
            cantidad   : Cuántos créditos entregar.
            motivo     : Descripción del motivo.
        """
        if cantidad <= 0:
            await interaction.response.send_message(
                embed=emb.error("Cantidad inválida", "La cantidad debe ser positiva."),
                ephemeral=True,
            )
            return

        async with await repo.get_conn() as conn:
            nuevo_saldo = await repo.update_balance(
                conn,
                user_id       = usuario.id,
                delta         = cantidad,
                tipo          = "entrega",
                descripcion   = motivo,
                ejecutado_por = interaction.user.id,
            )
            await audit(
                conn,
                tipo        = "economia_admin",
                descripcion = f"Entregados {cantidad:,.2f}{self.simbolo} a {usuario} — {motivo}",
                actor_id    = interaction.user.id,
                target_id   = usuario.id,
                detalles    = {"cantidad": cantidad, "motivo": motivo},
            )

        await interaction.response.send_message(
            embed=emb.ok(
                "Entrega realizada",
                f"**{cantidad:,.2f} {self.simbolo}** entregados a {usuario.mention}.\n"
                f"Nuevo saldo: **{nuevo_saldo:,.2f} {self.simbolo}**",
            )
        )

    # -----------------------------------------------------------------------
    # /admin-eco retirar
    # -----------------------------------------------------------------------

    @admin_eco_group.command(name="retirar", description="Retirar dinero a un usuario (Narrador+)")
    @app_commands.describe(usuario="Usuario afectado", cantidad="Cantidad a retirar",
                            motivo="Motivo (opcional)")
    @require_role(NARRADOR)
    async def admin_retirar(self, interaction: Interaction,
                             usuario: discord.Member,
                             cantidad: float,
                             motivo: str = "Retiro por Narrador") -> None:
        """
        Retira dinero de un usuario. Registrado en audit_log.

        Args:
            interaction: Contexto de Discord.
            usuario    : Miembro destino.
            cantidad   : Cuántos créditos retirar.
            motivo     : Descripción del motivo.
        """
        if cantidad <= 0:
            await interaction.response.send_message(
                embed=emb.error("Cantidad inválida", "La cantidad debe ser positiva."),
                ephemeral=True,
            )
            return

        async with await repo.get_conn() as conn:
            try:
                nuevo_saldo = await repo.update_balance(
                    conn,
                    user_id       = usuario.id,
                    delta         = -cantidad,
                    tipo          = "retiro",
                    descripcion   = motivo,
                    ejecutado_por = interaction.user.id,
                )
            except ValueError as exc:
                await interaction.response.send_message(
                    embed=emb.error("Sin saldo suficiente", str(exc)), ephemeral=True
                )
                return

            await audit(
                conn,
                tipo        = "economia_admin",
                descripcion = f"Retirados {cantidad:,.2f}{self.simbolo} de {usuario} — {motivo}",
                actor_id    = interaction.user.id,
                target_id   = usuario.id,
                detalles    = {"cantidad": cantidad, "motivo": motivo},
            )

        await interaction.response.send_message(
            embed=emb.ok(
                "Retiro realizado",
                f"**{cantidad:,.2f} {self.simbolo}** retirados de {usuario.mention}.\n"
                f"Nuevo saldo: **{nuevo_saldo:,.2f} {self.simbolo}**",
            )
        )

    # -----------------------------------------------------------------------
    # /admin-eco salarios
    # -----------------------------------------------------------------------

    @admin_eco_group.command(name="salarios", description="Pagar salarios manualmente (Narrador+)")
    @require_role(NARRADOR)
    async def admin_salarios(self, interaction: Interaction) -> None:
        """
        Fuerza el pago de salarios a todos los personajes activos de inmediato.

        Args:
            interaction: Contexto de Discord.
        """
        await interaction.response.defer(ephemeral=True)
        await self._pagar_salarios_automatico()
        await interaction.followup.send(
            embed=emb.ok("Salarios pagados", "Se han procesado los salarios manualmente.")
        )


# ---------------------------------------------------------------------------
# Setup - CORREGIDO: Ya no usa _patch_repo()
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot) -> None:
    """Registra el cog en el bot."""
    await bot.add_cog(EconomiaCog(bot))