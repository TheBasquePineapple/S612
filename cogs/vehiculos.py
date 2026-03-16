"""
RAISA — Cog de Sistema de Vehículos
cogs/vehiculos.py

Responsabilidad : CRUD de vehículos, embarque/desembarque, gestión de munición,
                  combustible, estado de componentes y daños.
Dependencias    : discord.py, db/repository, utils/permisos, utils/embeds, utils/validaciones
Autor           : Proyecto RAISA

TIPOS IMPLEMENTADOS: coche, furgoneta, blindado_ligero, blindado_pesado, mbt,
                     helo_transporte, helo_ataque, avion_transporte, avion_combate
# TODO: Naval — estructura en BBDD preparada, sin funcionalidad activa.
"""

import json
import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import embed_vehiculo, embed_ok, embed_error, embed_aviso
from utils.permisos import require_role, RANGO_USUARIO, RANGO_NARRADOR
from utils.validaciones import (
    validar_municion, validar_capacidad_vehiculo, validar_transferencia_municion
)

log = logging.getLogger("raisa.vehiculos")

TIPOS_VEHICULO = [
    "coche", "furgoneta", "blindado_ligero", "blindado_pesado", "mbt",
    "helo_transporte", "helo_ataque", "avion_transporte", "avion_combate",
    # "naval",  # TODO: sin funcionalidad activa
]


class VehiculosCog(commands.Cog, name="Vehículos"):
    """Cog para el sistema de vehículos de RAISA."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def repo(self):
        return self.bot.repo

    veh_group = app_commands.Group(name="vehiculo", description="Sistema de vehículos")

    # ──────────────────────────────────────────────────────────────────────
    # USUARIO — Embarque / Desembarque
    # ──────────────────────────────────────────────────────────────────────

    @veh_group.command(name="subir", description="Sube a un vehículo disponible.")
    @app_commands.describe(vehiculo_id="ID del vehículo", asiento="Número de asiento")
    @require_role(RANGO_USUARIO)
    async def subir(
        self, interaction: discord.Interaction, vehiculo_id: int, asiento: int
    ) -> None:
        """Sube al usuario a un asiento del vehículo si está disponible."""
        vehiculo = await self.repo.get_vehiculo(vehiculo_id)
        if not vehiculo:
            await interaction.response.send_message(embed=embed_error(f"Vehículo `{vehiculo_id}` no encontrado."), ephemeral=True)
            return

        if vehiculo.get("destruido"):
            await interaction.response.send_message(embed=embed_error("Este vehículo está **destruido**."), ephemeral=True)
            return

        if asiento < 1 or asiento > vehiculo["asientos"]:
            await interaction.response.send_message(
                embed=embed_error(f"Asiento `{asiento}` inválido. Este vehículo tiene `{vehiculo['asientos']}` asientos."),
                ephemeral=True,
            )
            return

        ocupantes = await self.repo.get_ocupantes(vehiculo_id)
        if any(o["asiento"] == asiento for o in ocupantes):
            await interaction.response.send_message(embed=embed_error(f"El asiento `{asiento}` ya está ocupado."), ephemeral=True)
            return

        if any(o["user_id"] == interaction.user.id for o in ocupantes):
            await interaction.response.send_message(embed=embed_error("Ya estás en este vehículo."), ephemeral=True)
            return

        await self.repo.subir_vehiculo(vehiculo_id, interaction.user.id, asiento)
        await interaction.response.send_message(
            embed=embed_ok("A bordo", f"Te has subido a **{vehiculo['nombre']}** (asiento `{asiento}`)."),
        )

    @veh_group.command(name="bajar", description="Baja del vehículo actual.")
    @require_role(RANGO_USUARIO)
    async def bajar(self, interaction: discord.Interaction) -> None:
        """Desembarca al usuario del vehículo en que se encuentre."""
        await self.repo.bajar_vehiculo(interaction.user.id)
        await interaction.response.send_message(
            embed=embed_ok("Desembarcado", "Has bajado del vehículo."),
        )

    @veh_group.command(name="estado", description="Muestra el estado de un vehículo.")
    @app_commands.describe(vehiculo_id="ID del vehículo")
    @require_role(RANGO_USUARIO)
    async def ver_estado(self, interaction: discord.Interaction, vehiculo_id: int) -> None:
        """Muestra el estado completo del vehículo."""
        vehiculo = await self.repo.get_vehiculo(vehiculo_id)
        if not vehiculo:
            await interaction.response.send_message(embed=embed_error("Vehículo no encontrado."), ephemeral=True)
            return

        ocupantes = await self.repo.get_ocupantes(vehiculo_id)
        embed = embed_vehiculo(vehiculo)

        # Añadir lista de ocupantes
        if ocupantes:
            ocup_txt = "\n".join(f"• Asiento `{o['asiento']}`: <@{o['user_id']}>" for o in ocupantes)
            embed.add_field(name="👥 Ocupantes", value=ocup_txt, inline=False)

        await interaction.response.send_message(embed=embed)

    # ──────────────────────────────────────────────────────────────────────
    # USUARIO — Inventario de vehículo
    # ──────────────────────────────────────────────────────────────────────

    @veh_group.command(name="inventario", description="Muestra el inventario del vehículo.")
    @app_commands.describe(vehiculo_id="ID del vehículo")
    @require_role(RANGO_USUARIO)
    async def ver_inventario_vehiculo(self, interaction: discord.Interaction, vehiculo_id: int) -> None:
        """Visualiza el inventario de un vehículo."""
        ocupantes = await self.repo.get_ocupantes(vehiculo_id)
        esta_dentro = any(o["user_id"] == interaction.user.id for o in ocupantes)

        modo = await self.repo.get_modo_evento()
        if not esta_dentro and modo == "ON":
            await interaction.response.send_message(
                embed=embed_aviso("Sin acceso", "Debes estar dentro del vehículo para acceder a su inventario durante un Evento-ON."),
                ephemeral=True,
            )
            return

        items = await self.repo._fetch_all(
            """SELECT iv.cantidad, i.nombre, i.categoria, i.peso_kg
               FROM inventario_vehiculo iv
               JOIN items i ON iv.item_uuid = i.item_uuid
               WHERE iv.vehiculo_id = ?""",
            (vehiculo_id,),
        )

        vehiculo = await self.repo.get_vehiculo(vehiculo_id)
        embed = discord.Embed(title=f"🚗 Inventario — {vehiculo['nombre']}", color=discord.Color.blue())

        if items:
            lineas = "\n".join(f"• **{it['nombre']}** ×{it['cantidad']} — `{it['peso_kg']} kg`" for it in items)
            embed.add_field(name="Contenido", value=lineas[:1024], inline=False)
        else:
            embed.description = "El inventario del vehículo está vacío."

        peso_actual = vehiculo.get("inventario_peso_actual", 0)
        peso_max    = vehiculo.get("inventario_peso_max", 0)
        embed.set_footer(text=f"Capacidad: {peso_actual:.1f}/{peso_max:.1f} kg")
        await interaction.response.send_message(embed=embed)

    # ──────────────────────────────────────────────────────────────────────
    # NARRADOR — CRUD de vehículos
    # ──────────────────────────────────────────────────────────────────────

    @veh_group.command(name="crear", description="[Narrador] Crea un nuevo vehículo.")
    @require_role(RANGO_NARRADOR)
    async def crear_vehiculo(self, interaction: discord.Interaction) -> None:
        """Abre modal de creación de vehículo."""
        await interaction.response.send_modal(CrearVehiculoModal(self))

    @veh_group.command(name="destruir", description="[Narrador] Destruye un vehículo.")
    @app_commands.describe(vehiculo_id="ID del vehículo")
    @require_role(RANGO_NARRADOR)
    async def destruir_vehiculo(self, interaction: discord.Interaction, vehiculo_id: int) -> None:
        """Marca el vehículo como destruido y expulsa a todos los ocupantes."""
        vehiculo = await self.repo.get_vehiculo(vehiculo_id)
        if not vehiculo:
            await interaction.response.send_message(embed=embed_error("Vehículo no encontrado."), ephemeral=True)
            return

        ocupantes = await self.repo.get_ocupantes(vehiculo_id)
        for ocup in ocupantes:
            await self.repo.bajar_vehiculo(ocup["user_id"])

        await self.repo.actualizar_vehiculo(vehiculo_id, {"destruido": 1, "estado_general": "Destruido"})
        await self.repo.log_accion("VEHICULO_DESTRUIDO", interaction.user.id, vehiculo_id, detalle={"nombre": vehiculo["nombre"]})

        await interaction.response.send_message(
            embed=embed_ok("Vehículo destruido", f"**{vehiculo['nombre']}** ha sido destruido. `{len(ocupantes)}` ocupantes expulsados."),
        )

    @veh_group.command(name="daño_componente", description="[Narrador] Aplica daño a un componente del vehículo.")
    @app_commands.describe(vehiculo_id="ID del vehículo", componente="Nombre del componente", estado="Estado nuevo")
    @require_role(RANGO_NARRADOR)
    async def daño_componente(
        self, interaction: discord.Interaction, vehiculo_id: int, componente: str, estado: str
    ) -> None:
        """Cambia el estado de un componente específico del vehículo."""
        vehiculo = await self.repo.get_vehiculo(vehiculo_id)
        if not vehiculo:
            await interaction.response.send_message(embed=embed_error("Vehículo no encontrado."), ephemeral=True)
            return

        componentes = vehiculo.get("componentes_json", {})
        if isinstance(componentes, str):
            componentes = json.loads(componentes)

        componentes[componente] = estado
        await self.repo.actualizar_vehiculo(vehiculo_id, {"componentes_json": componentes})
        await self.repo.log_accion("COMPONENTE_DAÑADO", interaction.user.id, vehiculo_id, detalle={"componente": componente, "estado": estado})

        await interaction.response.send_message(
            embed=embed_ok("Componente actualizado", f"**{componente}** del vehículo `{vehiculo_id}`: **{estado}**"),
        )

    @veh_group.command(name="combustible", description="[Narrador] Ajusta el combustible de un vehículo.")
    @app_commands.describe(vehiculo_id="ID del vehículo", cantidad="Litros a añadir (negativo = gastar)")
    @require_role(RANGO_NARRADOR)
    async def ajustar_combustible(
        self, interaction: discord.Interaction, vehiculo_id: int, cantidad: float
    ) -> None:
        """Suma o resta combustible al depósito del vehículo."""
        vehiculo = await self.repo.get_vehiculo(vehiculo_id)
        if not vehiculo:
            await interaction.response.send_message(embed=embed_error("Vehículo no encontrado."), ephemeral=True)
            return

        nuevo = max(0.0, min(vehiculo["combustible_max"], vehiculo["combustible_actual"] + cantidad))
        await self.repo.actualizar_vehiculo(vehiculo_id, {"combustible_actual": nuevo})

        await interaction.response.send_message(
            embed=embed_ok(
                "Combustible actualizado",
                f"**{vehiculo['nombre']}**: `{nuevo:.1f}/{vehiculo['combustible_max']:.1f} L`"
            ),
        )

    # ──────────────────────────────────────────────────────────────────────
    # NARRADOR — Munición
    # ──────────────────────────────────────────────────────────────────────

    @veh_group.command(name="recargar", description="[Narrador] Recarga un arma del vehículo.")
    @app_commands.describe(vehiculo_id="ID del vehículo", arma_id="ID del arma a recargar", cargador_id="ID del cargador/munición")
    @require_role(RANGO_NARRADOR)
    async def recargar_vehiculo(
        self, interaction: discord.Interaction,
        vehiculo_id: int, arma_id: int, cargador_id: int
    ) -> None:
        """
        Recarga un arma del vehículo.
        REGLA ABSOLUTA: verifica calibre e id_compatibilidad antes de recargar.
        Solo permitido en vehículos terrestres y helos de transporte.
        """
        vehiculo = await self.repo.get_vehiculo(vehiculo_id)
        if not vehiculo:
            await interaction.response.send_message(embed=embed_error("Vehículo no encontrado."), ephemeral=True)
            return

        # Verificar permiso de transferencia de munición
        ok_transfer, msg_transfer = validar_transferencia_municion(vehiculo["tipo"])
        if not ok_transfer:
            await interaction.response.send_message(embed=embed_error(msg_transfer), ephemeral=True)
            return

        # REGLA ABSOLUTA: verificar calibre e id_compatibilidad
        ok_mun, msg_mun = await validar_municion(self.repo, arma_id, cargador_id)
        if not ok_mun:
            await interaction.response.send_message(embed=embed_error(msg_mun), ephemeral=True)
            return

        # Actualizar munición en el JSON del vehículo
        municion = vehiculo.get("municion_json", {})
        if isinstance(municion, str):
            municion = json.loads(municion)

        arma_key = str(arma_id)
        if arma_key not in municion:
            municion[arma_key] = {"actual": 0, "maximo": 100}

        municion[arma_key]["actual"] = municion[arma_key]["maximo"]
        await self.repo.actualizar_vehiculo(vehiculo_id, {"municion_json": municion})

        await interaction.response.send_message(
            embed=embed_ok("Arma recargada", f"Arma `{arma_id}` del vehículo `{vehiculo_id}` recargada correctamente."),
        )

    @veh_group.command(name="disparar", description="[Narrador] Dispara un arma del vehículo.")
    @app_commands.describe(vehiculo_id="ID del vehículo", arma_id="ID del arma", disparos="Número de disparos")
    @require_role(RANGO_NARRADOR)
    async def disparar(
        self, interaction: discord.Interaction, vehiculo_id: int, arma_id: int, disparos: int = 1
    ) -> None:
        """Descuenta munición al disparar. Puede provocar encasquillamiento si la munición es defectuosa."""
        vehiculo = await self.repo.get_vehiculo(vehiculo_id)
        if not vehiculo:
            await interaction.response.send_message(embed=embed_error("Vehículo no encontrado."), ephemeral=True)
            return

        municion = vehiculo.get("municion_json", {})
        arma_key = str(arma_id)
        if arma_key not in municion or municion[arma_key]["actual"] < disparos:
            await interaction.response.send_message(
                embed=embed_error(f"Munición insuficiente en arma `{arma_id}`. Disponible: `{municion.get(arma_key, {}).get('actual', 0)}`"),
                ephemeral=True,
            )
            return

        municion[arma_key]["actual"] -= disparos
        await self.repo.actualizar_vehiculo(vehiculo_id, {"municion_json": municion})

        restante = municion[arma_key]["actual"]
        await interaction.response.send_message(
            embed=embed_ok("Disparo registrado", f"`{disparos}` disparo(s) realizados. Munición restante: `{restante}`"),
        )


class CrearVehiculoModal(discord.ui.Modal, title="Crear vehículo"):
    nombre  = discord.ui.TextInput(label="Nombre",            placeholder="Ej: Humvee Blindado 01")
    tipo    = discord.ui.TextInput(label="Tipo",              placeholder="coche / blindado_ligero / helo_ataque …")
    asientos = discord.ui.TextInput(label="Asientos",         placeholder="Ej: 4")
    comb_max = discord.ui.TextInput(label="Combustible máx (L)", placeholder="Ej: 120")
    inv_peso = discord.ui.TextInput(label="Inventario máx (kg)",  placeholder="Ej: 500", required=False)

    def __init__(self, cog) -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if self.tipo.value not in TIPOS_VEHICULO:
            await interaction.response.send_message(
                embed=embed_error(f"Tipo `{self.tipo.value}` inválido. Tipos válidos: {', '.join(TIPOS_VEHICULO)}"),
                ephemeral=True,
            )
            return

        try:
            asientos = int(self.asientos.value)
            comb     = float(self.comb_max.value)
            inv      = float(self.inv_peso.value or "0")
        except ValueError:
            await interaction.response.send_message(embed=embed_error("Valores numéricos inválidos."), ephemeral=True)
            return

        vid = await self.cog.repo.crear_vehiculo({
            "nombre": self.nombre.value,
            "tipo": self.tipo.value,
            "asientos": asientos,
            "combustible_max": comb,
            "inventario_peso_max": inv,
        })
        await interaction.response.send_message(
            embed=embed_ok("Vehículo creado", f"**{self.nombre.value}** creado con ID `{vid}`."),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VehiculosCog(bot))
