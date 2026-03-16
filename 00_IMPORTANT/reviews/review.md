# RAISA — Revisión exhaustiva de diseño
## Fallos encontrados, correcciones aplicadas y decisiones de arquitectura

---

## 1. FALLOS CRÍTICOS (rompen funcionalidad si no se corrigen)

### 1.1 — Slot de radio no definido en el loadout
**Fallo:** La especificación dice "el usuario debe tener una radio en el slot
de radio del loadout", pero el loadout nunca define ese slot por nombre.
Sin un slot llamado `radio` explícitamente, no hay forma de distinguirlo del
resto del equipo.

**Corrección aplicada:** El schema.sql incluye `radio` como slot válido en la
tabla `loadout`. El trigger `trg_loadout_radio_sync` sincroniza automáticamente
`radio_state.tiene_radio` al equipar/desequipar ese slot. El sistema de radio
puede hacer `WHERE slot='radio' AND item_id IS NOT NULL` para validar.

---

### 1.2 — Validación de munición sin campo en armas
**Fallo:** La regla de compatibilidad exige comparar `id_compatibilidad` del
cargador contra el del arma, pero los ítems de tipo arma no tenían ese campo
asignado en el diseño original. Solo los cargadores lo declaraban.

**Corrección aplicada:** La tabla `items` incluye `id_compatibilidad` tanto
para armas como para cargadores. Al registrar un arma, se le asigna su
`id_compatibilidad` correspondiente. La función de validación en
`utils/validaciones.py` debe comprobar:
```python
assert cargador.calibre == arma.calibre
assert cargador.id_compatibilidad == arma.id_compatibilidad
```
Ambas condiciones deben cumplirse. Si una falla, acción cancelada con embed
de error explícito indicando cuál condición falló.

---

### 1.3 — estado_general médico "calculado automáticamente" sin fórmula
**Fallo:** La especificación dice que `estado_general` se calcula a partir
de los otros campos, pero no define la fórmula. Sin ella, cada desarrollador
implementaría una lógica distinta, rompiendo la consistencia entre módulos.

**Corrección:** El campo NO se persiste en BBDD (evita inconsistencias) y
se calcula en tiempo de lectura. Fórmula propuesta para `calcular_estado_general()`:

| Condición                                    | Estado general       |
|----------------------------------------------|----------------------|
| sangre == 0 O consciencia == 'Clínico'        | `Muerte clínica`     |
| sangre < 20 O consciencia == 'Inconsciente'   | `Crítico`            |
| sangre < 40 O consciencia == 'Semiconsciente' | `Grave`              |
| fracturas con tipo == 'expuesta'              | `Herido grave`       |
| heridas con gravedad == 'grave'               | `Herido`             |
| heridas no vacías O fracturas no vacías       | `Lesionado`          |
| todo lo demás                                 | `Operativo`          |

La función siempre devuelve el peor estado aplicable (evaluación en cascada).

---

### 1.4 — Muerte en Evento-ON sin umbral definido
**Fallo:** "Muerte libre según sistema médico" pero nunca se define qué
condición del sistema médico la dispara. Sin umbral, el Narrador no sabe
cuándo puede ejecutarla sin comando manual.

**Corrección:** Definir explícitamente que la muerte en Evento-ON puede
ejecutarse por Narrador cuando `estado_general == 'Muerte clínica'` (sangre=0
o consciencia=Clínico), sin requerir confirmación adicional. El log de
auditoría registra el evento igualmente.

---

### 1.5 — ON CONFLICT en tabla items requiere índice UNIQUE
**Fallo:** La estrategia de upsert en migrate.py (y en el repositorio) asume
que el nombre del ítem es único, pero el schema original no declaraba esa
restricción. Dos ítems con el mismo nombre pero diferente categoría serían
casos válidos (ej: un chaleco y una mochila ambos llamados "Básico").

**Corrección aplicada:** El upsert en migrate.py usa la combinación
`(nombre, categoria)` como clave de deduplicación, no solo el nombre.
En producción, el repositorio debe hacer lo mismo al crear ítems desde
comandos de Discord. Se recomienda añadir este índice al schema:
```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_items_nombre_cat ON items(nombre, categoria);
```

---

### 1.6 — Webhook: límite de 10 webhooks por canal de Discord
**Fallo:** La especificación dice "reutilizar webhook si ya existe", pero no
maneja el caso en que el canal ya tenga 10 webhooks (límite de la API de
Discord) y ninguno sea del bot.

**Corrección:** El módulo `radio.py` debe implementar la siguiente lógica:
1. Buscar en `webhook_cache` (BBDD) si ya hay un webhook registrado para el canal.
2. Si no, llamar a `channel.webhooks()` y buscar uno creado por el bot.
3. Si no existe ninguno del bot y hay 10 webhooks, eliminar el más antiguo
   del canal antes de crear uno nuevo (solo si pertenece al bot).
4. Si no hay webhooks del bot y los 10 existentes son de terceros, emitir
   embed de error al Narrador: "Canal lleno de webhooks externos".

---

## 2. FALLOS DE DISEÑO (inconsistencias que generarían bugs en runtime)

### 2.1 — Formulario "No apto": destino del progreso indefinido
**Fallo:** Si el psicotécnico resulta "No apto", el registro se cancela, pero
no se especifica qué pasa con la fila en `registration_forms`. Si se borra,
el usuario podría reintentar con un nuevo formulario limpio (posiblemente
intencionado). Si no se borra, el formulario suspendido queda en BBDD
indefinidamente.

**Corrección:** Al resultado "No apto":
- Insertar registro en `characters` con estado `'denegado'` y `motivo_denegacion = 'No apto en psicotécnico'`.
- Eliminar la fila de `registration_forms`.
- El usuario no puede reiniciar el registro mientras exista un personaje con
  estado `'denegado'` (necesita autorización de Gestor+ para limpiar).

---

### 2.2 — Verificación de fichas: mensaje_id puede quedar huérfano
**Fallo:** Si el bot se reinicia entre que se envía la ficha al canal de
verificación y que el Narrador pulsa el botón, el `View` (con los botones)
se pierde y los botones dejan de funcionar.

**Corrección:** Al iniciar el bot, reconstruir los Views activos consultando
`verification_queue WHERE resuelto = 0`. El `message_id` almacenado permite
volver a registrar el listener con `bot.add_view(VerificationView(...), message_id=...)`.

---

### 2.3 — Caché de webhooks en BBDD vs. memoria
**Fallo:** El spec original dice "caché solo en memoria para datos de baja
variación", pero los webhooks de radio necesitan persistir entre reinicios
del bot para no crear duplicados cada vez.

**Corrección aplicada:** La tabla `webhook_cache` en BBDD almacena
`(channel_id, webhook_id, webhook_url)`. Al arrancar, se carga en un dict
en memoria. Las escrituras van a BBDD + memoria. Al invalidar (webhook
eliminado externamente), se borra de ambos.

---

### 2.4 — Pouches: "doble capacidad" sin referencia base
**Fallo:** El tipo de pouch `Doble` se describe como "2 slots, doble
capacidad", pero doble ¿de qué? Sin una capacidad base de referencia, el
cálculo es ambiguo.

**Corrección:** La capacidad de un pouch es absoluta (`capacidad_pouch` en
la tabla `items`), no relativa. El tipo `doble` simplemente ocupa 2 slots
Y tiene `capacidad_pouch` más alta que un `dual` normal. Los seeds ya
reflejan esto con valores concretos.

---

### 2.5 — Salarios automáticos: frecuencia no definida
**Fallo:** "Pago de salarios automático por rango" — no se define cada cuánto
(diario, semanal, mensual) ni cómo se calculan los montos.

**Corrección:** El módulo `economia.py` debe leer la frecuencia y los montos
desde `/config/economia.json`. Estructura sugerida:
```json
{
  "salarios": {
    "frecuencia_horas": 168,
    "montos_por_rango": {
      "Usuario":  500,
      "Narrador": 800,
      "Gestor":   1000,
      "Admin":    0
    }
  }
}
```
La tarea periódica (`discord.ext.tasks`) no debe tener intervalo inferior a
10 minutos (restricción de hardware). Con frecuencia semanal, esto no es
un problema.

---

### 2.6 — Inventario de vehículo: sin validación de tipo de vehículo al acceder
**Fallo:** El sistema dice que el inventario de vehículo solo es accesible
"dentro del vehículo asignado", pero no hay un mecanismo explícito para
verificar que el usuario está dentro del vehículo.

**Corrección:** La tabla `vehicles` tiene `tripulacion_json` (array de
user_ids). El comando de inventario de vehículo verifica:
```python
user.id in vehicle.tripulacion_json  # O es conductor
```
Si no, embed de error: "No estás asignado a este vehículo."
El Narrador puede añadir/quitar usuarios de la tripulación con un comando dedicado.

---

### 2.7 — Sesiones SUDO: no se limpian al reiniciar el bot
**Fallo:** Las sesiones SUDO se guardan en un dict en memoria. Si el bot se
reinicia, el dict desaparece, lo cual está bien. Pero si el bot cae durante
una sesión activa, el usuario debe re-autenticarse en el siguiente inicio,
lo cual también está bien. Sin embargo, falta documentar que esto es
intencional (no es un fallo, sino una decisión de diseño).

**Clarificación:** Documentar explícitamente en `utils/permisos.py` que las
sesiones SUDO son intencionales en memoria y no persisten entre reinicios.
Esto es una característica de seguridad: un reinicio forzado del bot
invalida automáticamente todas las sesiones activas.

---

## 3. OMISIONES (funcionalidad implícita no especificada)

### 3.1 — No hay comando para registrar distancia recorrida por vehículo
El campo `consumo_por_km` existe, pero el movimiento es narrativo (no GPS).
Añadir comando `/vehiculo distancia [km]` para que el Narrador registre
kilómetros recorridos y el bot descuente combustible automáticamente.

### 3.2 — Compresión de avatares no especificada en el spec principal
El archivo APOYO_ menciona Pillow con calidad 85% y máximo 800×800px, pero
el spec principal no lo recoge. Debe implementarse al guardar avatares en
el formulario de registro.

### 3.3 — LRU Cache: tamaño no especificado en spec principal
APOYO_ especifica 50 entradas máximo. El spec principal no menciona el límite.
Usar 50 entradas como estándar del proyecto en todos los cachés en memoria.

### 3.4 — Sin comando de consulta de historial económico para Usuario
El sistema registra transacciones en `transactions` pero el spec no define
un comando para que el Usuario consulte su historial. Añadir `/economia historial
[página]` que muestre las últimas N transacciones del usuario en un embed
paginado.

### 3.5 — Parche de uniformidad: URL sin validación de formato
El slot `parche` acepta URL de imagen, pero sin validar que sea una URL
válida de imagen (extensión .png/.jpg/.webp) ni que sea accesible.
Añadir validación de formato URL y `HEAD` request asíncrono para verificar
que la imagen existe antes de aceptarla.

---

## 4. MEJORAS DE ARQUITECTURA APLICADAS EN EL SCHEMA

### 4.1 — Triggers automáticos
Se añadieron triggers en lugar de confiar en que el código de negocio
actualice timestamps y sincronice estados:
- `trg_characters_updated` — actualiza `actualizado_en` automáticamente.
- `trg_vehicles_updated` — ídem para vehículos.
- `trg_loadout_radio_sync` — sincroniza `radio_state.tiene_radio` al
  modificar el slot de radio del loadout.

### 4.2 — Tabla de transacciones inmutable
`transactions` no debe tener UPDATE ni DELETE. Toda corrección se hace con
un registro de ajuste positivo/negativo. Esto permite auditoría completa
del historial económico.

### 4.3 — Separación event_state como singleton forzado
La condición `CHECK (id = 1)` en `event_state` garantiza que solo puede
existir exactamente una fila, eliminando cualquier riesgo de inconsistencia
si un bug intentase insertar múltiples estados de evento.

### 4.4 — Historial médico sin campo calculado en BBDD
`estado_general` NO está en la tabla `medical_state`. Se calcula en tiempo
de lectura en `utils/validaciones.py`. Esto evita el riesgo de que el campo
calculado quede desincronizado con los campos fuente.

---

## 5. RESUMEN DE ARCHIVOS NUEVOS/MODIFICADOS

| Archivo                              | Estado      | Descripción                              |
|--------------------------------------|-------------|------------------------------------------|
| `db/schema.sql`                      | ✅ Revisado  | Schema completo con triggers, índices y correcciones |
| `seeds/items/armas.json`             | ✅ Nuevo     | 17 armas con calibres y tipos            |
| `seeds/items/municion.json`          | ✅ Nuevo     | 16 cargadores/granadas con id_compat     |
| `seeds/items/protecciones_y_equipo.json` | ✅ Nuevo | Protecciones, uniformidad, pouches, accesorios |
| `seeds/items/medico_y_misc.json`     | ✅ Nuevo     | Ítems médicos, radios y misc             |
| `seeds/vehiculos/terrestres.json`    | ✅ Nuevo     | 7 vehículos terrestres con datos realistas |
| `seeds/vehiculos/aereos_y_naval.json`| ✅ Nuevo     | 8 vehículos aéreos + naval reservado     |
| `seeds/tienda/catalogo.json`         | ✅ Nuevo     | 49 listados de tienda iniciales          |
| `seeds/README.md`                    | ✅ Nuevo     | Documentación del sistema de seeds       |
| `tools/migrate.py`                   | ✅ Nuevo     | CLI: init, seed, status, reset, dry-run  |