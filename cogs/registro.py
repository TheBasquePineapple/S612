"""
cogs/registro.py — Sistema de registro de personajes de RAISA
=============================================================
Responsabilidad : Formulario completo de registro por Mensaje Directo.
                  Persistencia de progreso, verificación de fichas,
                  compresión de avatar con Pillow.
Dependencias    : db.repository, utils.embeds, utils.permisos,
                  utils.validaciones, Pillow (PIL)
Autor           : RAISA Dev

Flujo
-----
  1. Usuario ejecuta /registro en el servidor
  2. Bot inicia MD secuencial con 12 pasos (4 bloques)
  3. Progreso guardado en BBDD tras cada respuesta
  4. Inactividad 20 min → formulario suspendido (no borrado)
  5. Al completar → ficha enviada al canal de verificación
  6. Narrador acepta/deniega con botones persistentes
  7. Aceptar → asignar rol Usuario automáticamente
  8. Denegar → solicitar feedback → enviar al usuario por MD

Ver REVIEW.md §2.1 — manejo de "No apto"
Ver REVIEW.md §2.2 — restauración de Views tras reinicio
Ver REVIEW.md §3.2 — compresión de avatares con Pillow
"""

import asyncio
import io
import json
from pathlib import Path

import discord
from discord import Interaction, app_commands
from discord.ext import commands

from db import repository as repo
from utils import embeds as emb
from utils.logger import audit, log_info, log_warning
from utils.permisos import NARRADOR, VISITANTE, USUARIO, get_user_level, require_role
from utils.validaciones import (
    validar_edad,
    validar_nombre_banlist,
    validar_url_imagen,
)

# ---------------------------------------------------------------------------
# Constantes del formulario
# ---------------------------------------------------------------------------
TIMEOUT_SEGUNDOS    = 20 * 60   # 20 minutos de inactividad → suspender
AVATAR_MAX_SIZE     = (800, 800)
AVATAR_QUALITY      = 85
AVATAR_DIR          = Path("data/characters")

# Clases disponibles
CLASES_REGULARES = [
    "JTAC", "Mecánico", "Operador de Drones", "K9 Handler",
    "Ametrallador", "Tirador Designado", "Especialista AT/AA",
    "Experto en Contención", "Conductor", "Intérprete",
    "Operador (Especialidad Base)",
]
CLASES_COMPLEJAS = [
    "Piloto {Σ-9}", "Tirador de Precisión", "EOD",
    "Experto en EW", "Auxiliar de Seguridad", "Sanitario",
    "Especialista NBQ", "Zapador",
]

# Preguntas del psicotécnico con respuestas evaluadas
PREGUNTAS_PSICO = [
    {
        "pregunta": "En una situación de emboscada, tu primer instinto es:\n"
                    "A) Buscar cobertura y evaluar la situación\n"
                    "B) Abrir fuego en la dirección del enemigo\n"
                    "C) Pedir instrucciones por radio",
        "correcta": "A",
        "apto_pero": "B",
    },
    {
        "pregunta": "Un compañero cae herido durante una operación. ¿Qué haces?\n"
                    "A) Lo dejas y continúas el objetivo\n"
                    "B) Lo arrastras a cobertura y aplicas primeros auxilios\n"
                    "C) Llamas por radio sin moverte",
        "correcta": "B",
        "apto_pero": "C",
    },
]


# ---------------------------------------------------------------------------
# Select para clase
# ---------------------------------------------------------------------------

class ClaseSelect(discord.ui.Select):
    """Desplegable de selección de clase dividido en regulares y complejas."""

    def __init__(self) -> None:
        opciones = [
            discord.SelectOption(
                label=c, value=c,
                description="Clase regular",
                emoji="🔹"
            )
            for c in CLASES_REGULARES
        ] + [
            discord.SelectOption(
                label=c, value=c,
                description="Clase compleja — requiere confirmación",
                emoji="🔶"
            )
            for c in CLASES_COMPLEJAS
        ]
        super().__init__(
            placeholder="Selecciona tu clase...",
            min_values=1,
            max_values=1,
            options=opciones[:25],  # límite de Discord
        )
        self.clase_seleccionada: str | None = None

    async def callback(self, interaction: Interaction) -> None:
        self.clase_seleccionada = self.values[0]
        if self.clase_seleccionada in CLASES_COMPLEJAS:
            # Mostrar confirmación
            view = ConfirmacionClaseView(self.clase_seleccionada, self.view)
            await interaction.response.send_message(
                embed=emb.advertencia(
                    "Clase compleja",
                    f"La clase **{self.clase_seleccionada}** requiere conocimientos o "
                    "formación avanzados.\n¿Confirmas que cumples estos requisitos?",
                ),
                view=view,
                ephemeral=True,
            )
        else:
            self.view.clase_confirmada = self.clase_seleccionada
            self.view.stop()
            await interaction.response.defer()


class ConfirmacionClaseView(discord.ui.View):
    """View de confirmación para clases complejas."""

    def __init__(self, clase: str, parent_view) -> None:
        super().__init__(timeout=120)
        self.clase        = clase
        self.parent_view  = parent_view

    @discord.ui.button(label="✅ Confirmar", style=discord.ButtonStyle.success)
    async def confirmar(self, interaction: Interaction,
                         button: discord.ui.Button) -> None:
        self.parent_view.clase_confirmada = self.clase
        self.parent_view.stop()
        await interaction.response.edit_message(
            embed=emb.ok("Clase confirmada", f"Clase **{self.clase}** seleccionada."),
            view=None,
        )

    @discord.ui.button(label="↩ Volver", style=discord.ButtonStyle.secondary)
    async def volver(self, interaction: Interaction,
                      button: discord.ui.Button) -> None:
        self.parent_view.clase_confirmada = None
        await interaction.response.edit_message(
            embed=emb.info("Selección cancelada", "Por favor vuelve a seleccionar tu clase."),
            view=None,
        )


class ClaseView(discord.ui.View):
    """View con el desplegable de selección de clase."""

    def __init__(self) -> None:
        super().__init__(timeout=TIMEOUT_SEGUNDOS)
        self.clase_confirmada: str | None = None
        self.select = ClaseSelect()
        self.add_item(self.select)


# ---------------------------------------------------------------------------
# View de género (botones)
# ---------------------------------------------------------------------------

class GeneroView(discord.ui.View):
    """View con botones de selección de género."""

    def __init__(self) -> None:
        super().__init__(timeout=TIMEOUT_SEGUNDOS)
        self.genero: str | None = None

    @discord.ui.button(label="Hombre", style=discord.ButtonStyle.primary, emoji="🧑")
    async def hombre(self, interaction: Interaction,
                      button: discord.ui.Button) -> None:
        self.genero = "Hombre"
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Mujer", style=discord.ButtonStyle.primary, emoji="👩")
    async def mujer(self, interaction: Interaction,
                     button: discord.ui.Button) -> None:
        self.genero = "Mujer"
        self.stop()
        await interaction.response.defer()


# ---------------------------------------------------------------------------
# View de servicio previo (opcional con botón Saltar)
# ---------------------------------------------------------------------------

class ServicioPrevioView(discord.ui.View):
    """View con botón de saltar para el servicio previo."""

    def __init__(self) -> None:
        super().__init__(timeout=TIMEOUT_SEGUNDOS)
        self.saltado = False

    @discord.ui.button(label="[Saltar]", style=discord.ButtonStyle.secondary, emoji="⏭️")
    async def saltar(self, interaction: Interaction,
                      button: discord.ui.Button) -> None:
        self.saltado = True
        self.stop()
        await interaction.response.defer()


# ---------------------------------------------------------------------------
# View de verificación de fichas (persistente)
# ---------------------------------------------------------------------------

class VerificationView(discord.ui.View):
    """
    View con botones de Aceptar/Denegar para fichas de personaje.
    Persistente: se restaura tras reinicio del bot cargando el message_id.
    Ver REVIEW.md §2.2.
    """

    def __init__(self, char_id: int, user_id: int, nombre: str) -> None:
        super().__init__(timeout=None)  # Sin timeout — persistente
        self.char_id  = char_id
        self.user_id  = user_id
        self.nombre   = nombre

    @discord.ui.button(
        label="✅ Aceptar",
        style=discord.ButtonStyle.success,
        custom_id="verify_accept",
    )
    async def aceptar(self, interaction: Interaction,
                       button: discord.ui.Button) -> None:
        """Acepta la ficha y asigna el rol Usuario."""
        # Verificar que quien pulsa es Narrador+
        if get_user_level(interaction) < NARRADOR:
            await interaction.response.send_message(
                embed=emb.acceso_denegado("Narrador"), ephemeral=True
            )
            return

        async with await repo.get_conn() as conn:
            await repo.update_character_estado(
                conn, self.user_id, "activo",
                verificado_por=interaction.user.id,
            )
            await repo.resolve_verification(conn, self.char_id)
            await audit(
                conn,
                tipo="verificacion",
                descripcion=f"Ficha de {self.nombre} ACEPTADA",
                actor_id=interaction.user.id,
                target_id=self.user_id,
            )

        # Asignar rol Usuario en el servidor
        guild = interaction.guild
        if guild:
            cfg      = getattr(interaction.client, "raisa_config", {})
            rol_id   = cfg.get("roles", {}).get("usuario")
            if rol_id:
                member = guild.get_member(self.user_id)
                rol    = guild.get_role(rol_id)
                if member and rol:
                    try:
                        await member.add_roles(rol, reason="Ficha verificada por RAISA")
                    except discord.Forbidden:
                        log_warning(f"[REGISTRO] Sin permisos para asignar rol a {self.user_id}")

        # Notificar al usuario
        try:
            usuario = interaction.client.get_user(self.user_id)
            if usuario:
                await usuario.send(
                    embed=emb.ok(
                        "Ficha aceptada",
                        f"Tu ficha para **{self.nombre}** ha sido **aceptada**.\n"
                        "Ya tienes acceso al servidor como Usuario.",
                    )
                )
        except discord.Forbidden:
            pass

        # Deshabilitar botones del mensaje
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=emb.ok("Ficha aceptada", f"**{self.nombre}** verificado por {interaction.user.mention}"),
            view=self,
        )

    @discord.ui.button(
        label="❌ Denegar",
        style=discord.ButtonStyle.danger,
        custom_id="verify_deny",
    )
    async def denegar(self, interaction: Interaction,
                       button: discord.ui.Button) -> None:
        """Abre un modal para pedir el motivo de denegación."""
        if get_user_level(interaction) < NARRADOR:
            await interaction.response.send_message(
                embed=emb.acceso_denegado("Narrador"), ephemeral=True
            )
            return

        modal = DenegacionModal(self.char_id, self.user_id, self.nombre, self)
        await interaction.response.send_modal(modal)


class DenegacionModal(discord.ui.Modal, title="Motivo de denegación"):
    """Modal para capturar el motivo de denegación de una ficha."""

    motivo = discord.ui.TextInput(
        label="Motivo",
        placeholder="Explica el motivo de la denegación...",
        style=discord.TextStyle.paragraph,
        max_length=500,
    )

    def __init__(self, char_id: int, user_id: int, nombre: str,
                  parent_view: VerificationView) -> None:
        super().__init__()
        self.char_id     = char_id
        self.user_id     = user_id
        self.nombre      = nombre
        self.parent_view = parent_view

    async def on_submit(self, interaction: Interaction) -> None:
        motivo_txt = self.motivo.value

        async with await repo.get_conn() as conn:
            await repo.update_character_estado(
                conn, self.user_id, "denegado",
                verificado_por=interaction.user.id,
                motivo=motivo_txt,
            )
            await repo.delete_form(conn, self.user_id)
            await repo.resolve_verification(conn, self.char_id)
            await audit(
                conn,
                tipo="verificacion",
                descripcion=f"Ficha de {self.nombre} DENEGADA — {motivo_txt}",
                actor_id=interaction.user.id,
                target_id=self.user_id,
            )

        # Notificar al usuario
        try:
            usuario = interaction.client.get_user(self.user_id)
            if usuario:
                await usuario.send(
                    embed=emb.error(
                        "Ficha denegada",
                        f"Tu ficha para **{self.nombre}** ha sido **denegada**.\n\n"
                        f"**Motivo:** {motivo_txt}\n\n"
                        "Puedes contactar con un Narrador para más información.",
                    )
                )
        except discord.Forbidden:
            pass

        for item in self.parent_view.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=emb.error("Ficha denegada",
                             f"**{self.nombre}** denegado por {interaction.user.mention}\n"
                             f"Motivo: {motivo_txt}"),
            view=self.parent_view,
        )


# ---------------------------------------------------------------------------
# Cog principal
# ---------------------------------------------------------------------------

class RegistroCog(commands.Cog, name="Registro"):
    """Cog del sistema de registro de personajes."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Tareas de timeout activas: {user_id: asyncio.Task}
        self._timeout_tasks: dict[int, asyncio.Task] = {}

    # -----------------------------------------------------------------------
    # /registro
    # -----------------------------------------------------------------------

    @app_commands.command(
        name="registro",
        description="Iniciar o retomar el registro de tu personaje"
    )
    async def registro(self, interaction: Interaction) -> None:
        """
        Punto de entrada del formulario de registro.
        Comprueba si hay progreso guardado y pregunta si continuar o empezar de cero.

        Args:
            interaction: Contexto de Discord.
        """
        user_id = interaction.user.id

        # Verificar que no tiene ya un personaje activo
        async with await repo.get_conn() as conn:
            char = await repo.get_character(conn, user_id)
            if char and char["estado"] in ("activo", "pendiente"):
                await interaction.response.send_message(
                    embed=emb.advertencia(
                        "Ya tienes un personaje",
                        "Ya tienes un personaje activo o pendiente de verificación.\n"
                        "Contacta con un Narrador si necesitas ayuda.",
                    ),
                    ephemeral=True,
                )
                return

            # Ver si hay un formulario denegado previo (ver REVIEW.md §2.1)
            if char and char["estado"] == "denegado":
                await interaction.response.send_message(
                    embed=emb.error(
                        "Registro denegado",
                        "Tu ficha fue denegada anteriormente. "
                        "Contacta con un Gestor+ para reiniciar el proceso.",
                    ),
                    ephemeral=True,
                )
                return

            form = await repo.get_form(conn, user_id)

        # Confirmar si se puede enviar MD
        try:
            await interaction.response.send_message(
                embed=emb.ok("Registro iniciado",
                              "Te he enviado un Mensaje Directo para continuar el proceso."),
                ephemeral=True,
            )
        except Exception:
            await interaction.response.send_message(
                embed=emb.error("Error", "No se pudo iniciar el proceso de registro."),
                ephemeral=True,
            )
            return

        # Si hay formulario en progreso, preguntar
        if form and not form["suspendido"]:
            continuado = await self._preguntar_continuar(interaction.user)
            if continuado is None:
                return
            if not continuado:
                async with await repo.get_conn() as conn:
                    await repo.delete_form(conn, user_id)
                form = None

        # Iniciar formulario
        asyncio.create_task(
            self._ejecutar_formulario(interaction.user, form)
        )

    # -----------------------------------------------------------------------
    # Preguntar si continuar
    # -----------------------------------------------------------------------

    async def _preguntar_continuar(self, user: discord.User) -> bool | None:
        """
        Pregunta al usuario si desea continuar el formulario o empezar de cero.

        Args:
            user: Usuario de Discord.

        Returns:
            True para continuar, False para empezar de cero, None si timeout.
        """
        class ContinuarView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=120)
                self.resultado: bool | None = None

            @discord.ui.button(label="Continuar", style=discord.ButtonStyle.primary, emoji="▶️")
            async def continuar(self, interaction, button):
                self.resultado = True
                self.stop()
                await interaction.response.defer()

            @discord.ui.button(label="Empezar de cero", style=discord.ButtonStyle.danger, emoji="🔄")
            async def de_cero(self, interaction, button):
                self.resultado = False
                self.stop()
                await interaction.response.defer()

        view = ContinuarView()
        try:
            await user.send(
                embed=emb.info(
                    "Formulario en progreso",
                    "Tienes un formulario de registro pendiente.\n"
                    "¿Deseas continuar desde donde lo dejaste o empezar de cero?",
                ),
                view=view,
            )
        except discord.Forbidden:
            return None

        await view.wait()
        return view.resultado

    # -----------------------------------------------------------------------
    # Ejecutar formulario completo
    # -----------------------------------------------------------------------

    async def _ejecutar_formulario(self, user: discord.User,
                                    form_existente=None) -> None:
        """
        Ejecuta el flujo completo del formulario de registro por MD.
        Cada respuesta se persiste antes de continuar.

        Args:
            user          : Usuario de Discord.
            form_existente: Fila de registration_forms si hay progreso guardado.
        """
        user_id = user.id

        # Restaurar datos guardados o iniciar vacío
        if form_existente:
            datos   = json.loads(form_existente["datos_json"] or "{}")
            paso_ini= form_existente["paso_actual"]
        else:
            datos    = {}
            paso_ini = 1

        # Iniciar/reanudar tarea de timeout
        self._reiniciar_timeout(user_id)

        try:
            datos = await self._bloque1(user, user_id, datos, paso_ini)
            if datos is None: return

            datos = await self._bloque2(user, user_id, datos, paso_ini)
            if datos is None: return

            datos = await self._bloque3(user, user_id, datos, paso_ini)
            if datos is None: return

            datos = await self._bloque4(user, user_id, datos, paso_ini)
            if datos is None: return

            # Formulario completado
            await self._completar_formulario(user, datos)

        except asyncio.TimeoutError:
            async with await repo.get_conn() as conn:
                await repo.suspend_form(conn, user_id)
            try:
                await user.send(embed=emb.formulario_suspendido())
            except discord.Forbidden:
                pass
        except discord.Forbidden:
            log_warning(f"[REGISTRO] MD bloqueados para user_id={user_id}")
        finally:
            self._cancelar_timeout(user_id)

    # -----------------------------------------------------------------------
    # Helper: esperar respuesta de texto por MD
    # -----------------------------------------------------------------------

    async def _esperar_texto(self, user: discord.User, user_id: int,
                               prompt_embed: discord.Embed,
                               view: discord.ui.View | None = None) -> str | None:
        """
        Envía un embed de pregunta y espera la respuesta de texto del usuario por MD.

        Args:
            user        : Usuario de Discord.
            user_id     : discord user_id (para filtrar mensajes).
            prompt_embed: Embed con la pregunta.
            view        : View opcional con botones (Saltar, etc.).

        Returns:
            Texto de la respuesta, o None si timeout.
        """
        await user.send(embed=prompt_embed, view=view)

        def check(m: discord.Message) -> bool:
            return (
                isinstance(m.channel, discord.DMChannel)
                and m.author.id == user_id
            )

        self._reiniciar_timeout(user_id)
        msg = await self.bot.wait_for("message", check=check, timeout=TIMEOUT_SEGUNDOS)
        self._reiniciar_timeout(user_id)
        return msg.content.strip()

    async def _esperar_view(self, user: discord.User, user_id: int,
                             prompt_embed: discord.Embed,
                             view: discord.ui.View) -> discord.ui.View:
        """
        Envía un embed con una View y espera a que el usuario interactúe.

        Returns:
            La view después de que el usuario interactuó.
        """
        await user.send(embed=prompt_embed, view=view)
        self._reiniciar_timeout(user_id)
        try:
            await asyncio.wait_for(view.wait(), timeout=TIMEOUT_SEGUNDOS)
        finally:
            self._reiniciar_timeout(user_id)
        return view

    # -----------------------------------------------------------------------
    # Helper: timeout
    # -----------------------------------------------------------------------

    def _reiniciar_timeout(self, user_id: int) -> None:
        """Reinicia el contador de timeout para un usuario."""
        self._cancelar_timeout(user_id)

    def _cancelar_timeout(self, user_id: int) -> None:
        """Cancela la tarea de timeout activa para un usuario."""
        task = self._timeout_tasks.pop(user_id, None)
        if task and not task.done():
            task.cancel()

    # -----------------------------------------------------------------------
    # BLOQUE 1 — Datos de personaje
    # -----------------------------------------------------------------------

    async def _bloque1(self, user: discord.User, user_id: int,
                        datos: dict, paso_ini: int) -> dict | None:
        """
        Bloque 1: nombre, edad, género, nacionalidad.
        Pasos 1-4.
        """
        # Paso 1: Nombre
        if paso_ini <= 1:
            while True:
                nombre = await self._esperar_texto(
                    user, user_id,
                    emb.formulario_inicio(1, 12, "¿Cuál es el **nombre completo** de tu personaje?"),
                )
                if nombre is None: return None
                resultado = validar_nombre_banlist(nombre)
                if resultado:
                    datos["nombre_completo"] = nombre
                    break
                await user.send(embed=emb.error("Nombre no válido", resultado.motivo))

            async with await repo.get_conn() as conn:
                await repo.upsert_form(conn, user_id, 1, datos)

        # Paso 2: Edad
        if paso_ini <= 2:
            while True:
                edad_txt = await self._esperar_texto(
                    user, user_id,
                    emb.formulario_inicio(2, 12, "¿Cuántos años tiene tu personaje?"),
                )
                if edad_txt is None: return None
                resultado = validar_edad(edad_txt)
                if resultado:
                    datos["edad"] = int(edad_txt.strip())
                    break
                await user.send(embed=emb.error("Edad inválida", resultado.motivo))

            async with await repo.get_conn() as conn:
                await repo.upsert_form(conn, user_id, 2, datos)

        # Paso 3: Género
        if paso_ini <= 3:
            view = GeneroView()
            await self._esperar_view(
                user, user_id,
                emb.formulario_inicio(3, 12, "Selecciona el **género** de tu personaje:"),
                view,
            )
            if view.genero is None: return None
            datos["genero"] = view.genero
            async with await repo.get_conn() as conn:
                await repo.upsert_form(conn, user_id, 3, datos)

        # Paso 4: Nacionalidad
        if paso_ini <= 4:
            nac = await self._esperar_texto(
                user, user_id,
                emb.formulario_inicio(4, 12, "¿Cuál es la **nacionalidad** de tu personaje?"),
            )
            if nac is None: return None
            datos["nacionalidad"] = nac
            async with await repo.get_conn() as conn:
                await repo.upsert_form(conn, user_id, 4, datos)

        return datos

    # -----------------------------------------------------------------------
    # BLOQUE 2 — Datos de servicio
    # -----------------------------------------------------------------------

    async def _bloque2(self, user: discord.User, user_id: int,
                        datos: dict, paso_ini: int) -> dict | None:
        """
        Bloque 2: servicio previo (opcional), destinos/ops, clase, psicotécnico.
        Pasos 5-8.
        """
        # Paso 5: Servicio previo (opcional)
        if paso_ini <= 5:
            view = ServicioPrevioView()
            servicio = await self._esperar_texto(
                user, user_id,
                emb.formulario_inicio(
                    5, 12,
                    "¿Tu personaje tiene **servicio militar previo**?\n"
                    "Descríbelo o pulsa **[Saltar]** si no aplica.",
                ),
                view=view,
            )
            if view.saltado:
                datos["servicio_previo"] = None
                datos["destinos_ops"]    = None
            else:
                if servicio is None: return None
                datos["servicio_previo"] = servicio

            async with await repo.get_conn() as conn:
                await repo.upsert_form(conn, user_id, 5, datos)

        # Paso 6: Destinos y operaciones (solo si no saltó paso 5)
        if paso_ini <= 6 and datos.get("servicio_previo") is not None:
            destinos = await self._esperar_texto(
                user, user_id,
                emb.formulario_inicio(
                    6, 12,
                    "Detalla los **destinos y operaciones** en las que participó tu personaje.",
                ),
            )
            if destinos is None: return None
            datos["destinos_ops"] = destinos
            async with await repo.get_conn() as conn:
                await repo.upsert_form(conn, user_id, 6, datos)

        # Paso 7: Clase
        if paso_ini <= 7:
            clase_seleccionada: str | None = None
            while not clase_seleccionada:
                view = ClaseView()
                await self._esperar_view(
                    user, user_id,
                    emb.formulario_inicio(7, 12, "Selecciona la **clase** de tu personaje:"),
                    view,
                )
                clase_seleccionada = view.clase_confirmada

            datos["clase"]        = clase_seleccionada
            datos["clase_compleja"] = 1 if clase_seleccionada in CLASES_COMPLEJAS else 0
            async with await repo.get_conn() as conn:
                await repo.upsert_form(conn, user_id, 7, datos)

        # Paso 8: Psicotécnico
        if paso_ini <= 8:
            resultado_psico = await self._evaluar_psicotecnico(user, user_id)

            if resultado_psico == "No apto":
                # Cancelar registro (ver REVIEW.md §2.1)
                async with await repo.get_conn() as conn:
                    # Crear personaje con estado denegado para bloquear reintento
                    datos.update({
                        "user_id": user_id, "estado": "denegado",
                        "resultado_psico": "No apto",
                        "estudios": "", "ocupaciones_previas": "",
                        "trasfondo": "",
                    })
                    await repo.create_character(conn, datos)
                    await repo.delete_form(conn, user_id)
                    await audit(
                        conn,
                        tipo="verificacion",
                        descripcion=f"Registro denegado por psicotécnico: user_id={user_id}",
                        target_id=user_id,
                    )
                await user.send(
                    embed=emb.error(
                        "Registro denegado",
                        "No has superado el examen psicotécnico.\n"
                        "No puedes continuar con el proceso de registro.",
                    )
                )
                return None

            datos["resultado_psico"] = resultado_psico
            async with await repo.get_conn() as conn:
                await repo.upsert_form(conn, user_id, 8, datos)

        return datos

    async def _evaluar_psicotecnico(self, user: discord.User, user_id: int) -> str:
        """
        Ejecuta el examen psicotécnico y devuelve el resultado.

        Returns:
            'Apto' | 'Apto, pero pendejo' | 'No apto'
        """
        import random
        pregunta_data = random.choice(PREGUNTAS_PSICO)

        respuesta = await self._esperar_texto(
            user, user_id,
            emb.formulario_inicio(
                8, 12,
                f"**Examen psicotécnico**\n\n{pregunta_data['pregunta']}\n\n"
                "Responde con la letra (A, B o C):",
            ),
        )
        if respuesta is None:
            return "No apto"

        respuesta_upper = respuesta.strip().upper()[:1]

        if respuesta_upper == pregunta_data["correcta"]:
            return "Apto"
        elif respuesta_upper == pregunta_data["apto_pero"]:
            return "Apto, pero pendejo"
        else:
            return "No apto"

    # -----------------------------------------------------------------------
    # BLOQUE 3 — Datos civiles
    # -----------------------------------------------------------------------

    async def _bloque3(self, user: discord.User, user_id: int,
                        datos: dict, paso_ini: int) -> dict | None:
        """
        Bloque 3: estudios, ocupaciones previas.
        Pasos 9-10.
        """
        if paso_ini <= 9:
            estudios = await self._esperar_texto(
                user, user_id,
                emb.formulario_inicio(9, 12,
                                       "¿Qué **estudios** tiene tu personaje?"),
            )
            if estudios is None: return None
            datos["estudios"] = estudios
            async with await repo.get_conn() as conn:
                await repo.upsert_form(conn, user_id, 9, datos)

        if paso_ini <= 10:
            ocupaciones = await self._esperar_texto(
                user, user_id,
                emb.formulario_inicio(10, 12,
                                       "¿Cuáles han sido las **ocupaciones previas** de tu personaje?"),
            )
            if ocupaciones is None: return None
            datos["ocupaciones_previas"] = ocupaciones
            async with await repo.get_conn() as conn:
                await repo.upsert_form(conn, user_id, 10, datos)

        return datos

    # -----------------------------------------------------------------------
    # BLOQUE 4 — Off-rol
    # -----------------------------------------------------------------------

    async def _bloque4(self, user: discord.User, user_id: int,
                        datos: dict, paso_ini: int) -> dict | None:
        """
        Bloque 4: trasfondo e historia, apariencia/avatar.
        Pasos 11-12.
        """
        if paso_ini <= 11:
            trasfondo = await self._esperar_texto(
                user, user_id,
                emb.formulario_inicio(
                    11, 12,
                    "Escribe el **trasfondo e historia** de tu personaje.\n"
                    "_(off-rol: este texto no es IC)_",
                ),
            )
            if trasfondo is None: return None
            datos["trasfondo"] = trasfondo
            async with await repo.get_conn() as conn:
                await repo.upsert_form(conn, user_id, 11, datos)

        # Paso 12: Avatar (imagen adjunta obligatoria)
        if paso_ini <= 12:
            avatar_path = await self._solicitar_avatar(user, user_id)
            if avatar_path is None: return None
            datos["avatar_path"] = str(avatar_path)
            async with await repo.get_conn() as conn:
                await repo.upsert_form(conn, user_id, 12, datos)

        return datos

    async def _solicitar_avatar(self, user: discord.User,
                                 user_id: int) -> Path | None:
        """
        Solicita y descarga la imagen de apariencia del personaje.
        Comprime con Pillow: máx 800×800px, calidad 85%.
        Ver REVIEW.md §3.2.

        Returns:
            Path al archivo guardado, o None si falla/timeout.
        """
        await user.send(
            embed=emb.formulario_inicio(
                12, 12,
                "**Paso final:** Envía una **imagen** de la apariencia de tu personaje.\n"
                "_(adjunta el archivo directamente en este chat)_",
            )
        )

        def check(m: discord.Message) -> bool:
            return (
                isinstance(m.channel, discord.DMChannel)
                and m.author.id == user_id
                and bool(m.attachments)
            )

        self._reiniciar_timeout(user_id)
        msg = await self.bot.wait_for("message", check=check, timeout=TIMEOUT_SEGUNDOS)
        attachment = msg.attachments[0]

        # Verificar que es imagen
        if not any(attachment.filename.lower().endswith(ext)
                   for ext in (".png", ".jpg", ".jpeg", ".webp")):
            await user.send(
                embed=emb.error("Formato inválido",
                                "Por favor envía una imagen PNG, JPG o WEBP.")
            )
            return await self._solicitar_avatar(user, user_id)  # reintentar

        # Descargar y comprimir
        try:
            imagen_bytes = await attachment.read()
            avatar_path  = await self._comprimir_avatar(user_id, imagen_bytes)
            return avatar_path
        except Exception as exc:
            log_warning(f"[REGISTRO] Error procesando avatar de {user_id}: {exc}")
            await user.send(
                embed=emb.error("Error al procesar imagen",
                                "Ocurrió un error. Por favor envía otra imagen.")
            )
            return None

    async def _comprimir_avatar(self, user_id: int, imagen_bytes: bytes) -> Path:
        """
        Comprime el avatar con Pillow: redimensionar a máx 800×800, calidad 85%.
        Operación ejecutada en executor para no bloquear el event loop.

        Args:
            user_id     : discord user_id (para nombre del archivo).
            imagen_bytes: Bytes de la imagen original.

        Returns:
            Path al archivo guardado.
        """
        def _comprimir(uid: int, data: bytes) -> Path:
            try:
                from PIL import Image
            except ImportError:
                # Pillow no instalado → guardar sin comprimir
                path = AVATAR_DIR / str(uid)
                path.mkdir(parents=True, exist_ok=True)
                out  = path / "avatar.png"
                out.write_bytes(data)
                return out

            img = Image.open(io.BytesIO(data)).convert("RGB")
            img.thumbnail(AVATAR_MAX_SIZE, Image.LANCZOS)

            path = AVATAR_DIR / str(uid)
            path.mkdir(parents=True, exist_ok=True)
            out  = path / "avatar.png"
            img.save(out, format="PNG", optimize=True, quality=AVATAR_QUALITY)
            return out

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _comprimir, user_id, imagen_bytes)

    # -----------------------------------------------------------------------
    # Completar formulario → enviar ficha a verificación
    # -----------------------------------------------------------------------

    async def _completar_formulario(self, user: discord.User, datos: dict) -> None:
        """
        Finaliza el formulario: crea el personaje en BBDD, envía la ficha al
        canal de verificación y notifica al usuario.

        Args:
            user  : Usuario de Discord.
            datos : Respuestas acumuladas del formulario.
        """
        user_id = user.id
        datos["user_id"] = user_id
        datos.setdefault("estado", "pendiente")

        async with await repo.get_conn() as conn:
            # Crear personaje en estado 'pendiente'
            char_id = await repo.create_character(conn, datos)

            # Obtener config de canal de verificación
            cfg       = getattr(self.bot, "raisa_config", {})
            canal_id  = cfg.get("canales", {}).get("verificacion")

            if not canal_id:
                log_warning("[REGISTRO] Canal de verificación no configurado en roles.json")
                await repo.delete_form(conn, user_id)
                await user.send(embed=emb.error(
                    "Error de configuración",
                    "El canal de verificación no está configurado. Contacta con un Admin.",
                ))
                return

            # Enviar ficha al canal de verificación
            embed_ficha = emb.ficha_verificacion({**datos, "user_id": user_id})
            view        = VerificationView(char_id=char_id, user_id=user_id,
                                           nombre=datos.get("nombre_completo", "—"))

            msg = None
            for guild in self.bot.guilds:
                canal = guild.get_channel(canal_id)
                if canal and isinstance(canal, discord.TextChannel):
                    try:
                        # Adjuntar avatar si existe
                        avatar_path = datos.get("avatar_path")
                        if avatar_path and Path(avatar_path).exists():
                            archivo = discord.File(avatar_path, filename="avatar.png")
                            msg = await canal.send(
                                embed=embed_ficha, view=view, file=archivo
                            )
                        else:
                            msg = await canal.send(embed=embed_ficha, view=view)
                        break
                    except discord.Forbidden:
                        log_warning(f"[REGISTRO] Sin permisos en canal {canal_id}")

            if msg:
                await repo.add_to_verification_queue(conn, char_id, msg.id, canal_id)

            # Limpiar formulario en progreso
            await repo.delete_form(conn, user_id)

        # Notificar al usuario
        await user.send(
            embed=emb.ok(
                "Formulario completado",
                f"Tu ficha para **{datos.get('nombre_completo', '—')}** ha sido enviada "
                "al equipo de verificación.\n\n"
                "Recibirás un mensaje cuando tu ficha sea revisada.",
            )
        )
        log_info(f"[REGISTRO] Ficha completada para user_id={user_id} "
                 f"nombre={datos.get('nombre_completo')}")


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot) -> None:
    """Registra el cog en el bot."""
    await bot.add_cog(RegistroCog(bot))