"""
cogs/vehiculos.py — Sistema de vehículos de RAISA
===================================================
Responsabilidad : Gestión de vehículos terrestres, aéreos y navales.
                  Aplicación centralizada de la regla de compatibilidad
                  de munición (regla absoluta, sin excepciones).
Dependencias    : db.repository, utils.embeds, utils.permisos,
                  utils.validaciones
Autor           : RAISA Dev

Comandos
--------
  /vehiculo lista [tipo]       — Ver vehículos disponibles (Usuario+)
  /vehiculo ficha [id]         — Ver ficha técnica de un vehículo (Usuario+)
  /vehiculo inventario [id]    — Ver inventario de un vehículo (Usuario+)
  /vehiculo embarcar [id]      — Unirse a la tripulación (Narrador+)
  /vehiculo desembarcar [id] [usuario] — Sacar de tripulación (Narrador+)
  /vehiculo combustible [id] [distancia] — Registrar km y descontar combustible (Narrador+)
  /vehiculo recargar [id] [arma] [cargador_nombre] — Recargar arma de vehículo (Narrador+)
  /vehiculo estado [id] [componente] [estado] — Actualizar estado de componente (Narrador+)
"""

import json

import discord
from discord import Interaction, app_commands
from discord.ext import commands

from db import repository as repo
from utils import embeds as emb
from utils.logger import audit, log_info
from utils.permisos import NARRADOR, USUARIO, get_user_level, require_role
from utils.validaciones import validar_compatibilidad_municion

# Tipos de vehículo válidos
TIPOS_VALIDOS = [
    "coche", "furgoneta", "blindado_ligero", "blindado_pesado", "mbt",
    "helicoptero_transporte", "helicoptero_ataque",
    "avion_transporte", "avion_combate",
    "naval",
]


class VehiculosCog(commands.Cog, name="Vehículos"):
    """Cog del sistema de vehículos."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    vehiculo_group = app_commands.Group(
        name="vehiculo", description="Sistema de vehículos"
    )

    # -----------------------------------------------------------------------
    # /vehiculo lista
    # -----------------------------------------------------------------------

    @vehiculo_group.command(name="lista", description="Ver vehículos disponibles")
    @app_commands.describe(tipo="Tipo de vehículo (opcional)")
    @require_role(USUARIO)
    async def vehiculo_lista(self, interaction: Interaction,
                              tipo: str | None = None) -> None:
        """
        Muestra la lista de vehículos activos, opcionalmente filtrada por tipo.

        Args:
            interaction: Contexto de Discord.
            tipo       : Tipo de vehículo (None = todos).
        """
        if tipo and tipo not in TIPOS_VALIDOS:
            await interaction.response.send_message(
                embed=emb.error("Tipo inválido",
                                f"Tipos válidos: {', '.join(TIPOS_VALIDOS)}"),
                ephemeral=True,
            )
            return

        async with await repo.get_conn() as conn:
            vehiculos = await repo.get_vehicles(conn, tipo=tipo, activo=True)

        if not vehiculos:
            await interaction.response.send_message(
                embed=emb.info("Sin vehículos",
                               "No hay vehículos disponibles" +
                               (f" de tipo `{tipo}`" if tipo else "") + "."),
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🚗  Vehículos disponibles",
            color=emb.C_INFO,
        )
        for v in vehiculos:
            tripulacion = json.loads(v["tripulacion_json"] or "[]")
            embed.add_field(
                name=f"[{v['id']}] {v['nombre']}",
                value=f"Tipo: `{v['tipo']}` | Estado: `{v['estado_general']}`\n"
                      f"Asientos: {v['asientos']} | Combustible: "
                      f"{v['combustible_actual']:.0f}/{v['combustible_max']:.0f}L\n"
                      f"Tripulación: {len(tripulacion)}/{v['asientos']}",
                inline=False,
            )
        embed.set_footer(text=emb.FOOTER_TEXT)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -----------------------------------------------------------------------
    # /vehiculo ficha
    # -----------------------------------------------------------------------

    @vehiculo_group.command(name="ficha", description="Ver ficha técnica de un vehículo")
    @app_commands.describe(id_vehiculo="ID del vehículo (ver /vehiculo lista)")
    @require_role(USUARIO)
    async def vehiculo_ficha(self, interaction: Interaction, id_vehiculo: int) -> None:
        """
        Muestra la ficha técnica completa de un vehículo.

        Args:
            interaction : Contexto de Discord.
            id_vehiculo : ID del vehículo.
        """
        async with await repo.get_conn() as conn:
            v = await repo.get_vehicle(conn, id_vehiculo)

        if not v:
            await interaction.response.send_message(
                embed=emb.error("Vehículo no encontrado",
                                f"No existe vehículo con ID `{id_vehiculo}`."),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=emb.ficha_vehiculo(dict(v)), ephemeral=True
        )

    # -----------------------------------------------------------------------
    # /vehiculo inventario
    # -----------------------------------------------------------------------

    @vehiculo_group.command(name="inventario",
                            description="Ver inventario de un vehículo")
    @app_commands.describe(id_vehiculo="ID del vehículo")
    @require_role(USUARIO)
    async def vehiculo_inventario(self, interaction: Interaction,
                                   id_vehiculo: int) -> None:
        """
        Muestra el inventario de un vehículo.
        Solo accesible a miembros de la tripulación del vehículo.
        Ver REVIEW.md §2.6.

        Args:
            interaction : Contexto de Discord.
            id_vehiculo : ID del vehículo.
        """
        async with await repo.get_conn() as conn:
            v = await repo.get_vehicle(conn, id_vehiculo)
            if not v:
                await interaction.response.send_message(
                    embed=emb.error("No encontrado",
                                    f"Vehículo `{id_vehiculo}` no existe."),
                    ephemeral=True,
                )
                return

            # Verificar que el usuario está en la tripulación
            tripulacion = json.loads(v["tripulacion_json"] or "[]")
            nivel       = get_user_level(interaction)
            if interaction.user.id not in tripulacion and nivel < NARRADOR:
                await interaction.response.send_message(
                    embed=emb.error("Acceso denegado",
                                    "No estás asignado a este vehículo."),
                    ephemeral=True,
                )
                return

            items = await repo.get_vehicle_inventory(conn, id_vehiculo)

        if not items:
            await interaction.response.send_message(
                embed=emb.info(f"Inventario — {v['nombre']}", "*Vacío*"),
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"📦  Inventario — {v['nombre']}",
            color=emb.C_DARK,
        )
        for it in items:
            embed.add_field(
                name=it["nombre"],
                value=f"×{it['cantidad']} — {it['peso_kg']*it['cantidad']:.2f}kg "
                      f"| Estado: {it['estado'] or 'óptimo'}",
                inline=False,
            )
        embed.add_field(
            name="Capacidad de carga",
            value=f"Máx: {v['inv_peso_max_kg']} kg",
            inline=False,
        )
        embed.set_footer(text=emb.FOOTER_TEXT)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -----------------------------------------------------------------------
    # /vehiculo embarcar
    # -----------------------------------------------------------------------

    @vehiculo_group.command(name="embarcar",
                            description="Asignar un usuario a la tripulación (Narrador+)")
    @app_commands.describe(id_vehiculo="ID del vehículo", usuario="Usuario a embarcar")
    @require_role(NARRADOR)
    async def vehiculo_embarcar(self, interaction: Interaction,
                                 id_vehiculo: int, usuario: discord.Member) -> None:
        """
        Añade un usuario a la tripulación de un vehículo.

        Args:
            interaction : Contexto de Discord.
            id_vehiculo : ID del vehículo.
            usuario     : Miembro a embarcar.
        """
        async with await repo.get_conn() as conn:
            v = await repo.get_vehicle(conn, id_vehiculo)
            if not v:
                await interaction.response.send_message(
                    embed=emb.error("No encontrado",
                                    f"Vehículo `{id_vehiculo}` no existe."),
                    ephemeral=True,
                )
                return

            tripulacion = json.loads(v["tripulacion_json"] or "[]")

            if usuario.id in tripulacion:
                await interaction.response.send_message(
                    embed=emb.advertencia("Ya embarcado",
                                          f"{usuario.mention} ya está en la tripulación."),
                    ephemeral=True,
                )
                return

            if len(tripulacion) >= v["asientos"]:
                await interaction.response.send_message(
                    embed=emb.error("Vehículo lleno",
                                    f"Capacidad máxima: {v['asientos']} asientos."),
                    ephemeral=True,
                )
                return

            tripulacion.append(usuario.id)
            await repo.update_vehicle(conn, id_vehiculo,
                                       {"tripulacion_json": tripulacion})

        await interaction.response.send_message(
            embed=emb.ok("Embarcado",
                          f"{usuario.mention} añadido a la tripulación de **{v['nombre']}**.")
        )

    # -----------------------------------------------------------------------
    # /vehiculo desembarcar
    # -----------------------------------------------------------------------

    @vehiculo_group.command(name="desembarcar",
                            description="Retirar usuario de la tripulación (Narrador+)")
    @app_commands.describe(id_vehiculo="ID del vehículo", usuario="Usuario a desembarcar")
    @require_role(NARRADOR)
    async def vehiculo_desembarcar(self, interaction: Interaction,
                                    id_vehiculo: int, usuario: discord.Member) -> None:
        """
        Retira un usuario de la tripulación de un vehículo.

        Args:
            interaction : Contexto de Discord.
            id_vehiculo : ID del vehículo.
            usuario     : Miembro a retirar.
        """
        async with await repo.get_conn() as conn:
            v = await repo.get_vehicle(conn, id_vehiculo)
            if not v:
                await interaction.response.send_message(
                    embed=emb.error("No encontrado",
                                    f"Vehículo `{id_vehiculo}` no existe."),
                    ephemeral=True,
                )
                return

            tripulacion = json.loads(v["tripulacion_json"] or "[]")
            if usuario.id not in tripulacion:
                await interaction.response.send_message(
                    embed=emb.advertencia("No en tripulación",
                                          f"{usuario.mention} no está en la tripulación."),
                    ephemeral=True,
                )
                return

            tripulacion.remove(usuario.id)
            await repo.update_vehicle(conn, id_vehiculo,
                                       {"tripulacion_json": tripulacion})

        await interaction.response.send_message(
            embed=emb.ok("Desembarcado",
                          f"{usuario.mention} retirado de **{v['nombre']}**.")
        )

    # -----------------------------------------------------------------------
    # /vehiculo combustible
    # -----------------------------------------------------------------------

    @vehiculo_group.command(name="combustible",
                            description="Registrar distancia recorrida y descontar combustible (Narrador+)")
    @app_commands.describe(id_vehiculo="ID del vehículo",
                            distancia_km="Kilómetros recorridos")
    @require_role(NARRADOR)
    async def vehiculo_combustible(self, interaction: Interaction,
                                    id_vehiculo: int,
                                    distancia_km: float) -> None:
        """
        Registra los km recorridos por un vehículo y descuenta el combustible.
        Ver REVIEW.md §3.1 — comando de distancia para movimiento narrativo.

        Args:
            interaction  : Contexto de Discord.
            id_vehiculo  : ID del vehículo.
            distancia_km : Kilómetros recorridos (valor narrativo, asignado por Narrador).
        """
        async with await repo.get_conn() as conn:
            v = await repo.get_vehicle(conn, id_vehiculo)
            if not v:
                await interaction.response.send_message(
                    embed=emb.error("No encontrado",
                                    f"Vehículo `{id_vehiculo}` no existe."),
                    ephemeral=True,
                )
                return

            consumo       = float(v["consumo_por_km"]) * distancia_km
            nuevo_comb    = max(0.0, float(v["combustible_actual"]) - consumo)
            sin_gasolina  = nuevo_comb == 0.0

            await repo.update_vehicle(conn, id_vehiculo,
                                       {"combustible_actual": nuevo_comb})

        embed = emb.ok(
            "Combustible actualizado",
            f"**{v['nombre']}** — {distancia_km} km recorridos.\n"
            f"Combustible: **{nuevo_comb:.1f} L** / {v['combustible_max']:.0f} L\n"
            f"Consumo: **{consumo:.1f} L**",
        )
        if sin_gasolina:
            embed.add_field(name="⚠️  Sin combustible",
                            value="El vehículo se ha quedado sin combustible.",
                            inline=False)
        await interaction.response.send_message(embed=embed)

    # -----------------------------------------------------------------------
    # /vehiculo recargar
    # -----------------------------------------------------------------------

    @vehiculo_group.command(name="recargar",
                            description="Recargar arma de un vehículo (Narrador+)")
    @app_commands.describe(
        id_vehiculo    = "ID del vehículo",
        nombre_arma    = "Nombre del arma del vehículo (clave en municion_json)",
        nombre_cargador= "Nombre del cargador/munición a usar",
    )
    @require_role(NARRADOR)
    async def vehiculo_recargar(self, interaction: Interaction,
                                 id_vehiculo: int,
                                 nombre_arma: str,
                                 nombre_cargador: str) -> None:
        """
        Recarga un arma de vehículo verificando compatibilidad de munición.

        REGLA ABSOLUTA: calibre + id_compatibilidad deben coincidir.
        Esta validación no tiene excepciones. Ver REVIEW.md §1.2

        Args:
            interaction     : Contexto de Discord.
            id_vehiculo     : ID del vehículo.
            nombre_arma     : Nombre del arma en municion_json del vehículo.
            nombre_cargador : Nombre del ítem de munición a usar.
        """
        async with await repo.get_conn() as conn:
            v = await repo.get_vehicle(conn, id_vehiculo)
            if not v:
                await interaction.response.send_message(
                    embed=emb.error("Vehículo no encontrado",
                                    f"ID `{id_vehiculo}` no existe."),
                    ephemeral=True,
                )
                return

            municion = json.loads(v["municion_json"] or "{}")
            if nombre_arma not in municion:
                await interaction.response.send_message(
                    embed=emb.error("Arma no encontrada",
                                    f"El vehículo no tiene el arma `{nombre_arma}`.\n"
                                    f"Armas disponibles: {', '.join(municion.keys())}"),
                    ephemeral=True,
                )
                return

            datos_arma    = municion[nombre_arma]

            # Verificar que el vehículo permite transferencia de munición
            if not v["permite_transferencia_mun"]:
                await interaction.response.send_message(
                    embed=emb.error("Transferencia no permitida",
                                    "Este tipo de vehículo no permite transferencia de munición."),
                    ephemeral=True,
                )
                return

            # Buscar el cargador en el catálogo
            cargador_item = await repo.get_item_by_name(conn, nombre_cargador)
            if not cargador_item:
                await interaction.response.send_message(
                    embed=emb.error("Cargador no encontrado",
                                    f"No se encontró `{nombre_cargador}` en el catálogo."),
                    ephemeral=True,
                )
                return

            # ---- VALIDACIÓN ABSOLUTA DE COMPATIBILIDAD ----
            # El arma del vehículo actúa como "arma" con calibre e id_compat
            arma_virtual = {
                "calibre":          datos_arma.get("calibre"),
                "id_compatibilidad": datos_arma.get("id_compat"),
            }
            resultado = validar_compatibilidad_municion(arma_virtual, cargador_item)
            if not resultado:
                await interaction.response.send_message(
                    embed=emb.error("Munición incompatible", resultado.motivo),
                    ephemeral=True,
                )
                return
            # -----------------------------------------------

            # Verificar que el cargador está en el inventario del vehículo
            items_veh = await repo.get_vehicle_inventory(conn, id_vehiculo)
            tiene_cargador = any(
                it["nombre"] == nombre_cargador and it["cantidad"] > 0
                for it in items_veh
            )
            if not tiene_cargador:
                await interaction.response.send_message(
                    embed=emb.error("Sin munición",
                                    f"El vehículo no tiene **{nombre_cargador}** en su inventario."),
                    ephemeral=True,
                )
                return

            # Calcular cuántas rondas añadir
            capacidad_cargador = cargador_item["capacidad_cargador"] or 0
            cargado_actual     = datos_arma.get("cargado", 0)
            max_cargado        = datos_arma.get("max", 0)
            espacio            = max_cargado - cargado_actual

            if espacio <= 0:
                await interaction.response.send_message(
                    embed=emb.advertencia("Arma llena",
                                          f"`{nombre_arma}` ya está al máximo de munición."),
                    ephemeral=True,
                )
                return

            rondas_a_añadir = min(capacidad_cargador, espacio)
            datos_arma["cargado"] = cargado_actual + rondas_a_añadir
            municion[nombre_arma] = datos_arma

            await repo.update_vehicle(conn, id_vehiculo, {"municion_json": municion})

            await audit(
                conn,
                tipo        = "mod_vehiculo",
                descripcion = f"Recarga: {nombre_arma} en vehículo {v['nombre']} — +{rondas_a_añadir} rondas",
                actor_id    = interaction.user.id,
                detalles    = {"vehiculo_id": id_vehiculo, "arma": nombre_arma,
                               "rondas_añadidas": rondas_a_añadir},
            )

        await interaction.response.send_message(
            embed=emb.ok(
                "Recarga completada",
                f"**{nombre_arma}** — {cargado_actual} → {datos_arma['cargado']} rondas "
                f"(+{rondas_a_añadir})\n"
                f"Calibre: `{datos_arma.get('calibre')}` ✅"
            )
        )

    # -----------------------------------------------------------------------
    # /vehiculo estado
    # -----------------------------------------------------------------------

    @vehiculo_group.command(name="estado",
                            description="Actualizar el estado de un componente (Narrador+)")
    @app_commands.describe(
        id_vehiculo = "ID del vehículo",
        componente  = "Nombre del componente",
        nuevo_estado= "Nuevo estado del componente",
    )
    @require_role(NARRADOR)
    async def vehiculo_estado(self, interaction: Interaction,
                               id_vehiculo: int,
                               componente: str,
                               nuevo_estado: str) -> None:
        """
        Actualiza el estado de un componente de un vehículo.

        Args:
            interaction  : Contexto de Discord.
            id_vehiculo  : ID del vehículo.
            componente   : Nombre del componente (motor, ruedas, blindaje, etc.).
            nuevo_estado : Nuevo estado (óptimo, dañado, critico, destruido).
        """
        estados_validos = {"óptimo", "dañado", "critico", "destruido"}
        if nuevo_estado.lower() not in estados_validos:
            await interaction.response.send_message(
                embed=emb.error("Estado inválido",
                                f"Estados válidos: {', '.join(estados_validos)}"),
                ephemeral=True,
            )
            return

        async with await repo.get_conn() as conn:
            v = await repo.get_vehicle(conn, id_vehiculo)
            if not v:
                await interaction.response.send_message(
                    embed=emb.error("No encontrado",
                                    f"Vehículo `{id_vehiculo}` no existe."),
                    ephemeral=True,
                )
                return

            componentes = json.loads(v["componentes"] or "{}")
            if componente not in componentes:
                await interaction.response.send_message(
                    embed=emb.error("Componente no encontrado",
                                    f"Componentes disponibles: {', '.join(componentes.keys())}"),
                    ephemeral=True,
                )
                return

            componentes[componente] = nuevo_estado.lower()

            # Calcular estado general del vehículo
            valores_estado = list(componentes.values())
            if "destruido" in valores_estado:
                estado_general = "destruido"
            elif valores_estado.count("critico") >= 2:
                estado_general = "critico"
            elif "critico" in valores_estado or valores_estado.count("dañado") >= 3:
                estado_general = "dañado"
            else:
                estado_general = "óptimo"

            await repo.update_vehicle(conn, id_vehiculo, {
                "componentes":   componentes,
                "estado_general": estado_general,
            })

        await interaction.response.send_message(
            embed=emb.ok(
                "Estado actualizado",
                f"**{v['nombre']}** — `{componente}` → `{nuevo_estado}`\n"
                f"Estado general: `{estado_general}`"
            )
        )


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot) -> None:
    """Registra el cog en el bot."""
    await bot.add_cog(VehiculosCog(bot))