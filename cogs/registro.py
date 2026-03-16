"""
RAISA — Cog de Sistema de Registro
cogs/registro.py

Responsabilidad : Flujo completo de registro de personaje por MD.
                  Persiste progreso en SQLite. Formulario en 4 bloques.
                  Verificación mediante botones en canal prefijado.
Dependencias    : discord.py, db/repository, Pillow, utils/permisos, utils/embeds
Autor           : Proyecto RAISA
"""

import asyncio
import json
import logging
import os
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.embeds import embed_ficha_personaje, embed_ok, embed_error, embed_aviso
from utils.permisos import require_role, RANGO_VISITANTE, RANGO_NARRADOR, _cargar_config_roles

log = logging.getLogger("raisa.registro")

TIMEOUT_INACTIVIDAD = 20 * 60  # 20 minutos en segundos

# ──────────────────────────────────────────────────────────────────────────────
# CLASES DE ESPECIALIDAD
# ──────────────────────────────────────────────────────────────────────────────

CLASES_REGULARES = [
    "JTAC", "Mecánico", "Operador de Drones", "K9 Handler",
    "Ametrallador", "Tirador Designado", "Especialista AT/AA",
    "Experto en Contención", "Conductor", "Intérprete", "Operador",
]
CLASES_COMPLEJAS = [
    "Piloto {Σ-9}", "Tirador de Precisión", "EOD", "Experto en EW",
    "Auxiliar de Seguridad", "Sanitario", "Especialista NBQ", "Zapador",
]

# Preguntas del examen psicotécnico — respuesta libre, sin evaluación automática
PREGUNTAS_PSI = [
    "Un compañero comete un error grave en misión que compromete la operación entera. "
    "¿Qué pasos tomas al respecto?",

    "Encuentras documentos clasificados de la Fundación fuera de su lugar seguro. "
    "Describe tu reacción inmediata y las acciones posteriores que tomarías.",

    "Durante una operación recibes una orden que consideras moralmente cuestionable "
    "pero no ilegítima. ¿Cómo lo manejas?",

    "Llevas varios turnos sin descanso y empiezas a notar que tu rendimiento disminuye. "
    "¿Cuál es tu protocolo personal para gestionarlo?",

    "Describe brevemente por qué crees que tu personaje encajaría en las filas de la "
    "Fundación SCP y qué aporta al equipo.",
]


# ──────────────────────────────────────────────────────────────────────────────
# VIEWS DE DISCORD
# ──────────────────────────────────────────────────────────────────────────────

class SeleccionGeneroView(discord.ui.View):
    """Botones de selección de género en el registro."""

    def __init__(self) -> None:
        super().__init__(timeout=TIMEOUT_INACTIVIDAD)
        self.valor: str | None = None

    @discord.ui.button(label="Hombre", style=discord.ButtonStyle.primary)
    async def hombre(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.valor = "Hombre"
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Mujer", style=discord.ButtonStyle.primary)
    async def mujer(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.valor = "Mujer"
        self.stop()
        await interaction.response.defer()


class SaltarView(discord.ui.View):
    """Botón para saltar una pregunta opcional."""

    def __init__(self) -> None:
        super().__init__(timeout=TIMEOUT_INACTIVIDAD)
        self.saltado: bool = False

    @discord.ui.button(label="⏭️ Saltar", style=discord.ButtonStyle.secondary)
    async def saltar(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.saltado = True
        self.stop()
        await interaction.response.defer()


class SeleccionClaseView(discord.ui.View):
    """Desplegable de selección de clase (Regulares + Complejas)."""

    def __init__(self) -> None:
        super().__init__(timeout=TIMEOUT_INACTIVIDAD)
        self.clase: str | None = None
        self.es_compleja: bool = False

        opciones_regulares = [discord.SelectOption(label=c, description="Clase Regular") for c in CLASES_REGULARES]
        opciones_complejas = [discord.SelectOption(label=c, description="⚠️ Clase Compleja", emoji="⚠️") for c in CLASES_COMPLEJAS]

        self.select = discord.ui.Select(
            placeholder="Selecciona tu clase…",
            options=opciones_regulares + opciones_complejas,
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        self.clase = self.select.values[0]
        self.es_compleja = self.clase in CLASES_COMPLEJAS
        self.stop()
        await interaction.response.defer()


class ConfirmarClaseComplejaView(discord.ui.View):
    """Confirmación para clases complejas."""

    def __init__(self) -> None:
        super().__init__(timeout=TIMEOUT_INACTIVIDAD)
        self.confirmado: bool = False

    @discord.ui.button(label="✅ Confirmar", style=discord.ButtonStyle.success)
    async def confirmar(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.confirmado = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="↩️ Volver", style=discord.ButtonStyle.danger)
    async def volver(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.confirmado = False
        self.stop()
        await interaction.response.defer()


class VerificacionFichaView(discord.ui.View):
    """Botones de aceptar/denegar ficha en el canal de verificación."""

    def __init__(self, cog, user_id: int) -> None:
        super().__init__(timeout=None)   # Sin timeout: persiste hasta interacción
        self.cog = cog
        self.user_id = user_id

    @discord.ui.button(label="✅ Aceptar", style=discord.ButtonStyle.success, custom_id="ficha_aceptar")
    async def aceptar(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Acepta la ficha, asigna roles de Usuario al personaje."""
        from utils.permisos import get_rango, RANGO_NARRADOR
        if get_rango(interaction.user) < RANGO_NARRADOR:
            await interaction.response.send_message("Sin permisos.", ephemeral=True)
            return

        # No usar actualizar_verificacion directamente si el personaje no existe
        # await self.cog.repo.actualizar_verificacion(self.user_id, 1)

        # 1. Recuperar datos del formulario
        form = await self.cog.repo.get_formulario(self.user_id)
        if not form:
            await interaction.response.send_message(
                embed=embed_error("No se encontró el formulario de registro. El usuario podría haberlo reiniciado."),
                ephemeral=True
            )
            return

        datos = json.loads(form["datos_json"])

        # 2. Preparar datos para la tabla 'personajes'
        # Eliminamos lo que no va en la tabla o necesita mapeo
        personaje_data = datos.copy()
        personaje_data.pop("psi_respuestas", None)
        personaje_data.pop("_salto_servicio", None)
        
        # El usuario pidió que el test psicotécnico pase a ser 'Apto' si la ficha se acepta
        personaje_data["psicotecnico"] = "Apto" 
        personaje_data["verificado"] = 1

        # 3. Crear el personaje en la BBDD
        try:
            await self.cog.repo.crear_personaje(self.user_id, personaje_data)
        except Exception as exc:
            log.error("Error al crear personaje para %s: %s", self.user_id, exc)
            await interaction.response.send_message(
                embed=embed_error(f"Error crítico al crear el personaje: {exc}"),
                ephemeral=True
            )
            return

        # 4. Asignar roles
        cfg = _cargar_config_roles()
        usuario_role_id = int(cfg.get("usuario_role_id", 0))

        miembro = interaction.guild.get_member(self.user_id)
        if miembro and usuario_role_id:
            rol = interaction.guild.get_role(usuario_role_id)
            if rol:
                await miembro.add_roles(rol, reason="Ficha de personaje aceptada")

        # 5. Limpieza y notificación
        await self.cog.repo.borrar_formulario(self.user_id)

        try:
            user = await interaction.client.fetch_user(self.user_id)
            await user.send(
                embed=embed_ok(
                    "Ficha aceptada",
                    "¡Tu ficha de personaje ha sido **aceptada** por el personal de la Fundación!\n"
                    "Ya tienes acceso a los sistemas operativos."
                )
            )
        except discord.Forbidden:
            pass

        await interaction.response.edit_message(
            embed=embed_ok("Ficha aceptada", f"Ficha de <@{self.user_id}> aceptada por {interaction.user.mention}."),
            view=None,
        )

    @discord.ui.button(label="❌ Denegar", style=discord.ButtonStyle.danger, custom_id="ficha_denegar")
    async def denegar(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Abre modal para escribir feedback de denegación."""
        from utils.permisos import get_rango, RANGO_NARRADOR
        if get_rango(interaction.user) < RANGO_NARRADOR:
            await interaction.response.send_message("Sin permisos.", ephemeral=True)
            return

        await interaction.response.send_modal(DenegarFichaModal(self.cog, self.user_id, interaction.message))


class DenegarFichaModal(discord.ui.Modal, title="Motivo de denegación"):
    """Modal para escribir el feedback de denegación al usuario."""

    motivo = discord.ui.TextInput(
        label="Motivo",
        style=discord.TextStyle.long,
        placeholder="Explica el motivo de la denegación para que el usuario pueda corregirlo.",
        max_length=1000,
    )

    def __init__(self, cog, user_id: int, original_message: discord.Message) -> None:
        super().__init__()
        self.cog = cog
        self.user_id = user_id
        self.original_message = original_message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.repo.actualizar_verificacion(self.user_id, 2)

        # Borrar formulario de la BBDD para que pueda re-registrarse
        await self.cog.repo.borrar_formulario(self.user_id)

        # Enviar feedback al usuario
        try:
            user = await interaction.client.fetch_user(self.user_id)
            await user.send(
                embed=discord.Embed(
                    title="❌ Ficha denegada",
                    description=(
                        f"Tu ficha de personaje ha sido **denegada** por el personal de verificación.\n\n"
                        f"**Motivo:**\n{self.motivo.value}\n\n"
                        f"Puedes volver a registrarte corrigiendo los puntos indicados."
                    ),
                    color=discord.Color.red(),
                )
            )
        except discord.Forbidden:
            pass

        await interaction.response.edit_message(
            embed=embed_error(f"Ficha de <@{self.user_id}> denegada por {interaction.user.mention}."),
            view=None,
        )


# ──────────────────────────────────────────────────────────────────────────────
# COG PRINCIPAL
# ──────────────────────────────────────────────────────────────────────────────

class RegistroCog(commands.Cog, name="Registro"):
    """Cog para el sistema de registro de personajes de RAISA."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._suspender_formularios_inactivos.start()

    def cog_unload(self) -> None:
        self._suspender_formularios_inactivos.cancel()

    @property
    def repo(self):
        return self.bot.repo

    # ──────────────────────────────────────────────────────────────────────
    # TAREA PERIÓDICA: Suspender formularios inactivos
    # ──────────────────────────────────────────────────────────────────────

    @tasks.loop(minutes=10)
    async def _suspender_formularios_inactivos(self) -> None:
        """Marca como suspendidos los formularios sin actividad > 20 min."""
        n = await self.repo.suspender_formularios_inactivos(minutos=20)
        if n:
            log.info("%d formularios de registro suspendidos por inactividad.", n)

    @_suspender_formularios_inactivos.before_loop
    async def _wait(self) -> None:
        await self.bot.wait_until_ready()

    # ──────────────────────────────────────────────────────────────────────
    # HELPERS
    # ──────────────────────────────────────────────────────────────────────

    def _cargar_banlist(self) -> list[str]:
        """Carga la lista de palabras prohibidas para nombres."""
        path = Path("config/banlist.json")
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8")).get("palabras", [])

    def _nombre_en_banlist(self, nombre: str, banlist: list[str]) -> bool:
        """Verifica si alguna palabra de la banlist aparece en el nombre."""
        nombre_lower = nombre.lower()
        return any(palabra in nombre_lower for palabra in banlist)

    async def _guardar_progreso(self, user_id: int, paso: int, datos: dict) -> None:
        """Persiste el progreso del formulario en SQLite."""
        await self.repo.upsert_formulario(user_id, paso, datos)

    async def _esperar_mensaje(
        self, dm: discord.DMChannel, user_id: int, timeout: float = TIMEOUT_INACTIVIDAD
    ) -> discord.Message | None:
        """
        Espera un mensaje del usuario en su MD.
        Retorna None si hay timeout (inactividad).
        """
        def check(m: discord.Message) -> bool:
            return m.channel.id == dm.id and m.author.id == user_id

        try:
            msg = await self.bot.wait_for("message", check=check, timeout=timeout)
            return msg
        except asyncio.TimeoutError:
            return None

    async def _guardar_avatar(self, user_id: int, attachment: discord.Attachment) -> str | None:
        """
        Descarga y procesa la imagen de avatar del personaje.
        Comprime a 85% de calidad y redimensiona a máx 800x800px usando Pillow.
        Restricción de disco: nunca guardar sin comprimir.

        :param user_id: ID del usuario.
        :param attachment: Adjunto de Discord con la imagen.
        :returns: Ruta relativa del archivo guardado o None si falla.
        """
        try:
            from PIL import Image
            import io

            directorio = Path(f"data/characters/{user_id}")
            directorio.mkdir(parents=True, exist_ok=True)
            ruta = directorio / "avatar.png"

            # Descargar bytes de la imagen
            img_bytes = await attachment.read()
            img = Image.open(io.BytesIO(img_bytes))

            # Redimensionar si supera 800x800
            img.thumbnail((800, 800), Image.LANCZOS)

            # Convertir a RGB si tiene canal alpha (para guardar como PNG/JPEG)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            img.save(str(ruta), format="PNG", optimize=True, quality=85)
            log.info("Avatar guardado para user_id=%s en %s", user_id, ruta)
            return str(ruta)

        except Exception as exc:
            log.error("Error guardando avatar de user_id=%s: %s", user_id, exc)
            return None



    @app_commands.command(name="registrar", description="Inicia o retoma el formulario de registro de personaje.")
    async def registrar(self, interaction: discord.Interaction) -> None:
        """
        Punto de entrada del sistema de registro.
        - Si hay formulario en progreso, pregunta si continuar o empezar de cero.
        - Si no hay, inicia un nuevo formulario.
        Todo el flujo ocurre por MD.
        """
        uid = interaction.user.id

        # Verificar que no tenga ya un personaje verificado
        personaje = await self.repo.get_personaje(uid)
        if personaje and personaje["verificado"] == 1:
            await interaction.response.send_message(
                embed=embed_aviso("Ya registrado", "Ya tienes un personaje activo en el sistema."),
                ephemeral=True,
            )
            return

        # Intentar abrir MD
        try:
            dm = await interaction.user.create_dm()
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=embed_error("No se pueden abrir tus Mensajes Directos. Habilítalos para el servidor."),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=embed_ok("Registro iniciado", "Revisa tus **Mensajes Directos** para continuar."),
            ephemeral=True,
        )

        # ¿Hay formulario en progreso?
        formulario = await self.repo.get_formulario(uid)
        datos_previos = {}
        paso_inicial = 1

        if formulario:
            datos_previos = json.loads(formulario["datos_json"])
            paso_guardado = formulario["paso_actual"]

            view_retomar = discord.ui.View(timeout=60)
            btn_continuar = discord.ui.Button(label="▶️ Continuar desde donde lo dejé", style=discord.ButtonStyle.success)
            btn_reiniciar = discord.ui.Button(label="🔄 Empezar de cero", style=discord.ButtonStyle.secondary)
            respuesta_retomar = {"elegido": None}

            async def on_continuar(it):
                respuesta_retomar["elegido"] = "continuar"
                view_retomar.stop()
                await it.response.defer()

            async def on_reiniciar(it):
                respuesta_retomar["elegido"] = "reiniciar"
                view_retomar.stop()
                await it.response.defer()

            btn_continuar.callback = on_continuar
            btn_reiniciar.callback = on_reiniciar
            view_retomar.add_item(btn_continuar)
            view_retomar.add_item(btn_reiniciar)

            await dm.send(
                embed=embed_aviso(
                    "Formulario en progreso",
                    f"Tienes un formulario guardado en el paso `{paso_guardado}` de 12.\n"
                    f"¿Deseas continuar donde lo dejaste o empezar de nuevo?"
                ),
                view=view_retomar,
            )
            await view_retomar.wait()

            if respuesta_retomar["elegido"] == "continuar":
                paso_inicial = paso_guardado
            else:
                datos_previos = {}
                paso_inicial = 1
                await self.repo.borrar_formulario(uid)

        # Lanzar el flujo del formulario en una tarea independiente
        asyncio.ensure_future(
            self._flujo_formulario(dm, uid, datos_previos, paso_inicial)
        )

    # ──────────────────────────────────────────────────────────────────────
    # FLUJO PRINCIPAL DEL FORMULARIO
    # ──────────────────────────────────────────────────────────────────────

    async def _flujo_formulario(
        self, dm: discord.DMChannel, uid: int, datos: dict, paso_inicial: int
    ) -> None:
        """
        Conduce el formulario de registro pregunta a pregunta.
        Guarda el progreso en SQLite tras cada respuesta.
        Suspende si hay inactividad de 20 minutos.
        """
        banlist = self._cargar_banlist()

        # ── BLOQUE 1: Datos de personaje ──────────────────────────────────

        # Paso 1 — Nombre y Apellidos
        if paso_inicial <= 1:
            while True:
                await dm.send(embed=discord.Embed(
                    title="📋 Registro — Paso 1/12",
                    description="Escribe el **nombre completo** de tu personaje (nombre y apellidos).",
                    color=discord.Color.blue(),
                ))
                msg = await self._esperar_mensaje(dm, uid)
                if not msg:
                    await self._timeout_formulario(dm, uid, 1, datos)
                    return

                nombre_completo = msg.content.strip()
                if self._nombre_en_banlist(nombre_completo, banlist):
                    await dm.send(embed=embed_error("Ese nombre no está permitido. Por favor, elige otro."))
                    continue

                partes = nombre_completo.split(maxsplit=1)
                datos["nombre"] = partes[0]
                datos["apellidos"] = partes[1] if len(partes) > 1 else ""
                await self._guardar_progreso(uid, 2, datos)
                break

        # Paso 2 — Edad
        if paso_inicial <= 2:
            while True:
                await dm.send(embed=discord.Embed(
                    title="📋 Registro — Paso 2/12",
                    description="¿Cuál es la **edad** de tu personaje? (Mínimo 18 años.)",
                    color=discord.Color.blue(),
                ))
                msg = await self._esperar_mensaje(dm, uid)
                if not msg:
                    await self._timeout_formulario(dm, uid, 2, datos)
                    return

                try:
                    edad = int(msg.content.strip())
                    if edad < 18 or edad > 60:
                        raise ValueError
                    datos["edad"] = edad
                    await self._guardar_progreso(uid, 3, datos)
                    break
                except ValueError:
                    await dm.send(embed=embed_error("La edad debe ser un número válido y tener al menos **18 años**."))

        # Paso 3 — Género
        if paso_inicial <= 3:
            view_genero = SeleccionGeneroView()
            await dm.send(
                embed=discord.Embed(title="📋 Registro — Paso 3/12", description="Selecciona el **género** de tu personaje.", color=discord.Color.blue()),
                view=view_genero,
            )
            await view_genero.wait()
            if not view_genero.valor:
                await self._timeout_formulario(dm, uid, 3, datos)
                return
            datos["genero"] = view_genero.valor
            await self._guardar_progreso(uid, 4, datos)

        # Paso 4 — Nacionalidad
        if paso_inicial <= 4:
            await dm.send(embed=discord.Embed(
                title="📋 Registro — Paso 4/12",
                description="¿Cuál es la **nacionalidad** del personaje?",
                color=discord.Color.blue(),
            ))
            msg = await self._esperar_mensaje(dm, uid)
            if not msg:
                await self._timeout_formulario(dm, uid, 4, datos)
                return
            datos["nacionalidad"] = msg.content.strip()
            await self._guardar_progreso(uid, 5, datos)

        # ── BLOQUE 2: Datos de servicio ───────────────────────────────────

        # Paso 5 — Servicio previo (opcional)
        if paso_inicial <= 5:
            view_saltar = SaltarView()
            await dm.send(
                embed=discord.Embed(
                    title="📋 Registro — Paso 5/12",
                    description="¿Tiene tu personaje **servicio previo** (Fundación SCP, fuerzas armadas, etc.)?\n"
                                "Escríbelo o pulsa **Saltar** si no aplica.",
                    color=discord.Color.blue(),
                ),
                view=view_saltar,
            )

            # Esperar tanto mensaje como botón saltar (race)
            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(self._esperar_mensaje(dm, uid, timeout=TIMEOUT_INACTIVIDAD)),
                    asyncio.create_task(self._esperar_view(view_saltar)),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()

            resultado = list(done)[0].result()
            if resultado is None:
                await self._timeout_formulario(dm, uid, 5, datos)
                return

            if isinstance(resultado, discord.Message):
                datos["servicio_previo"] = resultado.content.strip()
                datos["_salto_servicio"] = False
            else:
                datos["servicio_previo"] = None
                datos["_salto_servicio"] = True

            await self._guardar_progreso(uid, 6, datos)

        # Paso 6 — Destinos y operaciones (solo si no saltó paso 5)
        if paso_inicial <= 6 and not datos.get("_salto_servicio"):
            await dm.send(embed=discord.Embed(
                title="📋 Registro — Paso 6/12",
                description="Describe los **destinos y operaciones previas** en los que ha estado tu personaje.",
                color=discord.Color.blue(),
            ))
            msg = await self._esperar_mensaje(dm, uid)
            if not msg:
                await self._timeout_formulario(dm, uid, 6, datos)
                return
            datos["destinos"] = msg.content.strip()
            await self._guardar_progreso(uid, 7, datos)
        elif paso_inicial <= 6:
            datos["destinos"] = None
            await self._guardar_progreso(uid, 7, datos)

        # Paso 7 — Clase
        if paso_inicial <= 7:
            clase_seleccionada = None
            while clase_seleccionada is None:
                view_clase = SeleccionClaseView()
                await dm.send(
                    embed=discord.Embed(title="📋 Registro — Paso 7/12", description="Selecciona la **clase** de tu personaje.", color=discord.Color.blue()),
                    view=view_clase,
                )
                await view_clase.wait()
                if not view_clase.clase:
                    await self._timeout_formulario(dm, uid, 7, datos)
                    return

                if view_clase.es_compleja:
                    # Mostrar aviso de confirmación
                    view_confirm = ConfirmarClaseComplejaView()
                    await dm.send(
                        embed=embed_aviso(
                            "Clase compleja",
                            f"La clase **{view_clase.clase}** requiere conocimientos o formación avanzados.\n"
                            f"¿Confirmas que tu personaje cumple estos requisitos?"
                        ),
                        view=view_confirm,
                    )
                    await view_confirm.wait()
                    if view_confirm.confirmado:
                        clase_seleccionada = view_clase.clase
                    # Si no confirma, vuelve al selector
                else:
                    clase_seleccionada = view_clase.clase

            datos["clase"] = clase_seleccionada
            await self._guardar_progreso(uid, 8, datos)

        # Paso 8 — Examen psicotécnico (5 preguntas de respuesta libre)
        if paso_inicial <= 8:
            respuestas_psi: list[str] = []

            await dm.send(embed=discord.Embed(
                title="📋 Registro — Examen Psicotécnico (Paso 8/12)",
                description=(
                    "A continuación se te harán **5 preguntas** de carácter psicológico.\n"
                    "Responde con sinceridad y con tus propias palabras.\n"
                    "No hay respuestas correctas ni incorrectas: el personal de verificación "
                    "leerá tus respuestas directamente."
                ),
                color=discord.Color.orange(),
            ))

            for idx, pregunta in enumerate(PREGUNTAS_PSI, start=1):
                await dm.send(embed=discord.Embed(
                    title=f"🧠 Pregunta {idx}/5",
                    description=pregunta,
                    color=discord.Color.orange(),
                ))
                msg = await self._esperar_mensaje(dm, uid)
                if not msg:
                    await self._timeout_formulario(dm, uid, 8, datos)
                    return
                respuestas_psi.append(msg.content.strip())

            # Guardar todas las respuestas en datos (solo para el embed, no a la BBDD final)
            datos["psi_respuestas"] = respuestas_psi
            await dm.send(embed=embed_ok("Examen completado", "Has completado el examen psicotécnico. Tus respuestas han sido registradas."))
            await self._guardar_progreso(uid, 9, datos)

        # ── BLOQUE 3: Datos civiles ───────────────────────────────────────

        # Paso 9 — Estudios
        if paso_inicial <= 9:
            await dm.send(embed=discord.Embed(
                title="📋 Registro — Paso 9/12",
                description="Describe los **estudios** del personaje.",
                color=discord.Color.blue(),
            ))
            msg = await self._esperar_mensaje(dm, uid)
            if not msg:
                await self._timeout_formulario(dm, uid, 9, datos)
                return
            datos["estudios"] = msg.content.strip()
            await self._guardar_progreso(uid, 10, datos)

        # Paso 10 — Ocupaciones previas
        if paso_inicial <= 10:
            await dm.send(embed=discord.Embed(
                title="📋 Registro — Paso 10/12",
                description="¿Cuáles han sido las **ocupaciones previas** del personaje?",
                color=discord.Color.blue(),
            ))
            msg = await self._esperar_mensaje(dm, uid)
            if not msg:
                await self._timeout_formulario(dm, uid, 10, datos)
                return
            datos["ocupaciones"] = msg.content.strip()
            await self._guardar_progreso(uid, 11, datos)

        # ── BLOQUE 4: Off-rol ─────────────────────────────────────────────

        # Paso 11 — Trasfondo
        if paso_inicial <= 11:
            await dm.send(embed=discord.Embed(
                title="📋 Registro — Paso 11/12",
                description="Escribe el **trasfondo e historia** de tu personaje.",
                color=discord.Color.blue(),
            ))
            msg = await self._esperar_mensaje(dm, uid)
            if not msg:
                await self._timeout_formulario(dm, uid, 11, datos)
                return
            datos["trasfondo"] = msg.content.strip()
            await self._guardar_progreso(uid, 12, datos)

        # Paso 12 — Apariencia / Foto (IMAGEN OBLIGATORIA)
        if paso_inicial <= 12:
            avatar_guardado = False
            while not avatar_guardado:
                await dm.send(embed=discord.Embed(
                    title="📋 Registro — Paso 12/12 — Apariencia",
                    description="Envía una **imagen** que represente la apariencia de tu personaje.\n"
                                "Esta imagen se usará como foto de perfil en el sistema de radio.",
                    color=discord.Color.blue(),
                ))
                msg = await self._esperar_mensaje(dm, uid)
                if not msg:
                    await self._timeout_formulario(dm, uid, 12, datos)
                    return

                if not msg.attachments:
                    await dm.send(embed=embed_error("Debes adjuntar una **imagen** a tu mensaje."))
                    continue

                adjunto = msg.attachments[0]
                if not adjunto.content_type or not adjunto.content_type.startswith("image/"):
                    await dm.send(embed=embed_error("El archivo adjunto debe ser una **imagen** (JPG, PNG, etc.)."))
                    continue

                ruta = await self._guardar_avatar(uid, adjunto)
                if not ruta:
                    await dm.send(embed=embed_error("Error al procesar la imagen. Intenta con otro archivo."))
                    continue

                datos["avatar_path"] = ruta
                datos["user_id"] = uid
                avatar_guardado = True

        # ── ENVIAR FICHA AL CANAL DE VERIFICACIÓN ────────────────────────

        await self._enviar_ficha_verificacion(uid, datos)
        # ELIMINADO: self.repo.borrar_formulario(uid) -> Se borra al ACEPTAR o DENEGAR

        await dm.send(
            embed=embed_ok(
                "Formulario completado",
                "Tu ficha ha sido enviada al personal de verificación.\n"
                "Recibirás una notificación cuando sea revisada."
            )
        )

    async def _esperar_view(self, view: discord.ui.View) -> bool:
        """Espera a que una View sea interactuada. Devuelve True al completarse."""
        await view.wait()
        return True

    async def _timeout_formulario(self, dm: discord.DMChannel, uid: int, paso: int, datos: dict) -> None:
        """Gestiona el timeout del formulario: guarda progreso y notifica."""
        await self.repo.upsert_formulario(uid, paso, datos, suspendido=True)
        await dm.send(
            embed=embed_aviso(
                "Formulario suspendido",
                "No has respondido en 20 minutos. El formulario ha sido **guardado**.\n"
                "Usa `/registrar` para retomarlo donde lo dejaste."
            )
        )
        log.info("Formulario suspendido por inactividad: user_id=%s paso=%s", uid, paso)

    async def _enviar_ficha_verificacion(self, uid: int, datos: dict) -> None:
        """Envía la ficha completada al canal de verificación definido en .env."""
        canal_id = int(os.getenv("CANAL_VERIFICACION_ID", "0"))
        if not canal_id:
            log.error("CANAL_VERIFICACION_ID no configurado en .env")
            return

        canal = self.bot.get_channel(canal_id)
        if not canal:
            log.error("Canal de verificación no encontrado: %s", canal_id)
            return

        # Construir URL de avatar si existe
        avatar_url = None
        avatar_path = Path(datos.get("avatar_path", ""))
        if avatar_path.exists():
            # Discord no puede acceder a rutas locales; en prod se usaría un CDN o attachment
            # Aquí enviamos la imagen como adjunto junto al embed
            pass

        embed = embed_ficha_personaje(datos)
        view = VerificacionFichaView(self, uid)

        try:
            if avatar_path.exists():
                file = discord.File(str(avatar_path), filename="avatar.png")
                embed.set_image(url="attachment://avatar.png")
                await canal.send(embed=embed, view=view, file=file)
            else:
                await canal.send(embed=embed, view=view)
        except Exception as exc:
            log.error("Error enviando ficha al canal de verificación: %s", exc)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RegistroCog(bot))
