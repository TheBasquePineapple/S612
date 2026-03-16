-- =============================================================================
-- RAISA — Esquema de base de datos SQLite v1.0
-- Autor: RAISA Dev
-- Dependencias: SQLite 3.x con soporte WAL
-- Descripción: Esquema principal unificado. Ejecutar al inicializar el bot o
--              al aplicar migraciones con tools/migrate.py
-- =============================================================================

PRAGMA journal_mode = WAL;       -- Lecturas concurrentes sin bloqueo
PRAGMA foreign_keys = ON;        -- Integridad referencial obligatoria
PRAGMA synchronous = NORMAL;     -- Balance rendimiento/seguridad en WAL
PRAGMA cache_size = -8000;       -- ~8 MB de caché de páginas
PRAGMA temp_store = MEMORY;      -- Tablas temporales en RAM


-- ===========================================================================
-- ESTADO GLOBAL DEL SERVIDOR
-- Singleton: siempre existe exactamente 1 fila (id = 1)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS event_state (
    id                INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton forzado
    evento_activo     INTEGER NOT NULL DEFAULT 0,          -- 0=OFF 1=ON
    activado_por      INTEGER,                             -- discord user_id
    activado_en       TEXT,                                -- ISO-8601 timestamp
    descripcion       TEXT                                 -- nota narrativa opcional
);
-- Insertar fila singleton si no existe (seguro ejecutar múltiples veces)
INSERT OR IGNORE INTO event_state (id, evento_activo) VALUES (1, 0);


-- ===========================================================================
-- CATÁLOGO MAESTRO DE ÍTEMS
-- Todos los ítems del universo del juego. La tienda y los inventarios
-- referencian siempre esta tabla. Nunca duplicar datos de ítem.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS items (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre              TEXT    NOT NULL,
    descripcion         TEXT,
    categoria           TEXT    NOT NULL,   -- arma|proteccion|uniforme|accesorio|medico|municion|radio|pouch|misc
    subcategoria        TEXT,               -- primaria|secundaria|terciaria|chaleco|portaplacas|etc.
    peso_kg             REAL    NOT NULL DEFAULT 0.0,
    volumen_u           REAL    NOT NULL DEFAULT 0.0,
    -- Propiedades de armas (NULL si no aplica)
    calibre             TEXT,               -- '5.56x45', '9x19', etc.
    tipo_arma           TEXT,               -- rifle|pistola|escopeta|lanzagranadas|etc.
    -- Propiedades de munición/cargadores
    id_compatibilidad   TEXT,               -- clave de compatibilidad INDEPENDIENTE del id de ítem
                                            -- CRÍTICO: dos cargadores distintos pueden tener el mismo
                                            -- id_compatibilidad si son intercambiables
    capacidad_cargador  INTEGER,            -- rondas (NULL si no es cargador)
    -- Propiedades de protección
    slots_pouches       INTEGER DEFAULT 0,  -- slots disponibles para pouches (chaleco/portaplacas/soporte)
    nivel_proteccion    INTEGER,            -- numérico; NULL si no es protección
    -- Propiedades de pouch
    tipo_pouch          TEXT,               -- simple|dual|doble (NULL si no es pouch)
    slots_ocupa         INTEGER DEFAULT 1,  -- cuántos slots del chaleco ocupa este pouch
    capacidad_pouch     INTEGER,            -- unidades que almacena el pouch
    -- Propiedades de vehículo-arma (hardpoints)
    es_hardpoint_item   INTEGER DEFAULT 0,  -- 1 si es carga para hardpoint
    -- Control
    disponible_tienda   INTEGER DEFAULT 1,  -- 1 si puede aparecer en tienda
    precio_base         REAL    DEFAULT 0.0,
    imagen_url          TEXT,               -- URL de imagen para embed (opcional)
    creado_en           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_items_categoria   ON items(categoria);
CREATE INDEX IF NOT EXISTS idx_items_calibre     ON items(calibre);
CREATE INDEX IF NOT EXISTS idx_items_compat      ON items(id_compatibilidad);


-- ===========================================================================
-- TIENDA — CATÁLOGO ACTIVO
-- Un ítem puede estar en tienda aunque no esté en el catálogo maestro
-- con disponible_tienda=1, por decisión de Narrador.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS shop_listings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    precio      REAL    NOT NULL,               -- precio actual (puede diferir del precio_base)
    stock       INTEGER DEFAULT -1,             -- -1 = ilimitado; 0 = agotado; N = unidades
    activo      INTEGER NOT NULL DEFAULT 1,     -- 0 = oculto aunque exista
    creado_por  INTEGER,                        -- discord user_id del Narrador que lo añadió
    creado_en   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_shop_item   ON shop_listings(item_id);
CREATE INDEX IF NOT EXISTS idx_shop_activo ON shop_listings(activo);


-- ===========================================================================
-- REGISTRO DE PERSONAJES
-- Un usuario de Discord puede tener como máximo 1 personaje activo.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS characters (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL UNIQUE,    -- discord user_id
    -- Bloque 1: datos de personaje
    nombre_completo     TEXT    NOT NULL,
    edad                INTEGER NOT NULL,
    genero              TEXT    NOT NULL,            -- Hombre|Mujer
    nacionalidad        TEXT    NOT NULL,
    -- Bloque 2: datos de servicio
    servicio_previo     TEXT,                       -- NULL si se saltó
    destinos_ops        TEXT,                       -- NULL si se saltó servicio_previo
    clase               TEXT    NOT NULL,
    clase_compleja      INTEGER NOT NULL DEFAULT 0, -- 1 si es clase compleja confirmada
    resultado_psico     TEXT    NOT NULL,           -- Apto|Apto, pero pendejo
    -- Bloque 3: datos civiles
    estudios            TEXT    NOT NULL,
    ocupaciones_previas TEXT    NOT NULL,
    -- Bloque 4: off-rol
    trasfondo           TEXT    NOT NULL,
    avatar_path         TEXT,                       -- ruta relativa desde /data/
    -- Estado
    estado              TEXT    NOT NULL DEFAULT 'pendiente',  -- pendiente|activo|denegado|baja
    verificado_por      INTEGER,                    -- discord user_id del Narrador
    verificado_en       TEXT,
    motivo_denegacion   TEXT,
    -- Unidad radio (asignada por Narrador)
    unidad_radio        TEXT,                       -- ej: 'Bravo 5-3'
    creado_en           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    actualizado_en      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_chars_user    ON characters(user_id);
CREATE INDEX IF NOT EXISTS idx_chars_estado  ON characters(estado);


-- ===========================================================================
-- PROGRESO DE FORMULARIOS (en curso)
-- Permite suspender y reanudar el registro sin pérdida de datos.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS registration_forms (
    user_id         INTEGER PRIMARY KEY,            -- discord user_id
    paso_actual     INTEGER NOT NULL DEFAULT 1,     -- 1-12 según el formulario
    datos_json      TEXT    NOT NULL DEFAULT '{}',  -- respuestas acumuladas como JSON
    ultima_actividad TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    suspendido      INTEGER NOT NULL DEFAULT 0      -- 1 = suspendido por inactividad
);


-- ===========================================================================
-- COLA DE VERIFICACIÓN
-- Fichas completadas pendientes de revisión por Narrador+.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS verification_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id    INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    message_id      INTEGER,        -- ID del mensaje embed en canal de verificación
    channel_id      INTEGER,        -- canal donde se publicó
    enviado_en      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    resuelto        INTEGER NOT NULL DEFAULT 0
);


-- ===========================================================================
-- ECONOMÍA — SALDOS DE USUARIO
-- ===========================================================================
CREATE TABLE IF NOT EXISTS economy (
    user_id     INTEGER PRIMARY KEY,    -- discord user_id
    saldo       REAL    NOT NULL DEFAULT 0.0,
    actualizado_en TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);


-- ===========================================================================
-- HISTORIAL DE TRANSACCIONES ECONÓMICAS
-- Inmutable: nunca actualizar filas existentes, solo insertar.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    tipo        TEXT    NOT NULL,   -- compra|venta|salario|entrega|retiro|ajuste
    item_id     INTEGER REFERENCES items(id) ON DELETE SET NULL,
    cantidad    REAL    NOT NULL,   -- positivo=ingreso, negativo=egreso
    descripcion TEXT,
    ejecutado_por INTEGER,          -- discord user_id del Narrador/sistema
    creado_en   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_tx_user ON transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_tx_tipo ON transactions(tipo);


-- ===========================================================================
-- INVENTARIO GENERAL (almacén personal — solo disponible en Evento-OFF)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS inventory_general (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE RESTRICT,
    cantidad    INTEGER NOT NULL DEFAULT 1,
    -- Datos de instancia (estado particular de este ejemplar)
    estado      TEXT    DEFAULT 'óptimo',   -- óptimo|dañado|destruido
    notas       TEXT,
    obtenido_en TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    FOREIGN KEY (user_id) REFERENCES characters(user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_inv_general_user ON inventory_general(user_id);
CREATE INDEX IF NOT EXISTS idx_inv_general_item ON inventory_general(item_id);


-- ===========================================================================
-- LOADOUT — EQUIPO EQUIPADO (siempre accesible)
-- Un personaje tiene exactamente un loadout activo.
-- Slots definidos: primaria|secundaria|terciaria|chaleco|portaplacas|placas|
--                  soporte|casco|pantalon|camisa|chaqueta|botas|guantes|
--                  reloj|parche|mochila|cinturon|radio
-- ===========================================================================
CREATE TABLE IF NOT EXISTS loadout (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    slot            TEXT    NOT NULL,               -- nombre del slot
    item_id         INTEGER REFERENCES items(id) ON DELETE SET NULL,
    -- Para el slot 'parche': URL de imagen en lugar de item_id
    parche_url      TEXT,
    estado          TEXT    DEFAULT 'óptimo',
    UNIQUE (user_id, slot),                         -- un ítem por slot por personaje
    FOREIGN KEY (user_id) REFERENCES characters(user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_loadout_user ON loadout(user_id);


-- ===========================================================================
-- POUCHES — Asignados a protecciones del loadout
-- Cada fila es un pouch asignado a un slot de protección específico.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS pouches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    -- slot de protección al que pertenece este pouch
    -- debe coincidir con un slot de loadout: chaleco|portaplacas|soporte
    slot_proteccion TEXT    NOT NULL,
    pouch_item_id   INTEGER NOT NULL REFERENCES items(id) ON DELETE RESTRICT,
    -- contenido del pouch (ítems almacenados dentro, como JSON array de item_id+cantidad)
    contenido_json  TEXT    NOT NULL DEFAULT '[]',
    FOREIGN KEY (user_id) REFERENCES characters(user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pouches_user ON pouches(user_id);


-- ===========================================================================
-- ESTADO MÉDICO — uno por personaje activo
-- ===========================================================================
CREATE TABLE IF NOT EXISTS medical_state (
    user_id         INTEGER PRIMARY KEY,
    -- heridas: JSON array de {tipo, localizacion, gravedad, estado_tratamiento}
    heridas         TEXT    NOT NULL DEFAULT '[]',
    -- fracturas: JSON array de {miembro, tipo: simple|expuesta}
    fracturas       TEXT    NOT NULL DEFAULT '[]',
    -- consciencia: Consciente|Semiconsciente|Inconsciente|Clínico
    consciencia     TEXT    NOT NULL DEFAULT 'Consciente',
    -- sangre: 0-100
    sangre          INTEGER NOT NULL DEFAULT 100 CHECK (sangre BETWEEN 0 AND 100),
    -- estado_general: calculado al leer, NO almacenar aquí para evitar inconsistencias
    -- FÓRMULA: ver utils/validaciones.py → calcular_estado_general()
    -- Historial de modificaciones
    ultima_mod_por  INTEGER,    -- discord user_id del Narrador
    ultima_mod_en   TEXT,
    FOREIGN KEY (user_id) REFERENCES characters(user_id) ON DELETE CASCADE
);


-- ===========================================================================
-- ESTADO DE RADIO — uno por personaje activo
-- ===========================================================================
CREATE TABLE IF NOT EXISTS radio_state (
    user_id         INTEGER PRIMARY KEY,
    encendida       INTEGER NOT NULL DEFAULT 0,     -- 0=apagada 1=encendida
    canal_activo    TEXT,                           -- frecuencia o 'intercom'
    tiene_radio     INTEGER NOT NULL DEFAULT 0,     -- calculado al equipar/desequipar
    estatica_activa INTEGER NOT NULL DEFAULT 0,     -- set por Narrador; corrompe mensajes
    FOREIGN KEY (user_id) REFERENCES characters(user_id) ON DELETE CASCADE
);


-- ===========================================================================
-- VEHÍCULOS
-- ===========================================================================
CREATE TABLE IF NOT EXISTS vehicles (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre              TEXT    NOT NULL,
    tipo                TEXT    NOT NULL,   -- coche|furgoneta|blindado_ligero|blindado_pesado|mbt|
                                            -- helicoptero_transporte|helicoptero_ataque|
                                            -- avion_transporte|avion_combate|naval
    subtipo             TEXT,               -- especificación interna (APC, IFV, etc.)
    asientos            INTEGER NOT NULL,
    estado_general      TEXT    NOT NULL DEFAULT 'óptimo',  -- óptimo|dañado|critico|destruido
    -- componentes: JSON {motor, transmision, ruedas, blindaje, electrónica, armas, ...}
    componentes         TEXT    NOT NULL DEFAULT '{}',
    combustible_actual  REAL    NOT NULL DEFAULT 0.0,
    combustible_max     REAL    NOT NULL,
    consumo_por_km      REAL    NOT NULL DEFAULT 0.0,   -- litros/km
    -- inventario del vehículo
    inv_peso_max_kg     REAL    NOT NULL DEFAULT 0.0,
    inv_volumen_max_u   REAL    NOT NULL DEFAULT 0.0,
    -- munición: JSON {nombre_arma: {cargado: N, max: N, calibre: "...", id_compat: "..."}}
    municion_json       TEXT    NOT NULL DEFAULT '{}',
    -- hardpoints (solo aéreos de combate): JSON [{slot: N, carga_item_id: N|null}]
    hardpoints_json     TEXT    NOT NULL DEFAULT '[]',
    -- artillado: solo blindado_ligero+ y algunos aéreos
    artillado           INTEGER NOT NULL DEFAULT 0,
    -- transferencia de munición permitida
    permite_transferencia_mun INTEGER NOT NULL DEFAULT 0,
    -- asignación
    asignado_a_user_id  INTEGER,    -- conductor/piloto actual (puede ser NULL)
    tripulacion_json    TEXT    NOT NULL DEFAULT '[]',   -- [user_id, ...]
    -- control
    activo              INTEGER NOT NULL DEFAULT 1,
    creado_por          INTEGER,    -- Narrador que lo registró
    creado_en           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    actualizado_en      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    -- TODO: implementar en futura versión → tipo 'naval' (estructura preparada arriba)
);

CREATE INDEX IF NOT EXISTS idx_vehicles_tipo   ON vehicles(tipo);
CREATE INDEX IF NOT EXISTS idx_vehicles_activo ON vehicles(activo);


-- ===========================================================================
-- INVENTARIO DE VEHÍCULO
-- ===========================================================================
CREATE TABLE IF NOT EXISTS inventory_vehicle (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id  INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE RESTRICT,
    cantidad    INTEGER NOT NULL DEFAULT 1,
    estado      TEXT    DEFAULT 'óptimo',
    notas       TEXT,
    añadido_en  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_inv_veh_vehicle ON inventory_vehicle(vehicle_id);
CREATE INDEX IF NOT EXISTS idx_inv_veh_item    ON inventory_vehicle(item_id);


-- ===========================================================================
-- LOG DE AUDITORÍA — INMUTABLE
-- Registra todas las acciones críticas del sistema.
-- Nunca modificar filas existentes; solo INSERT.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo        TEXT    NOT NULL,   -- sudo_auth|sudo_fail|sudo_expire|muerte|mod_medica|
                                    -- cambio_evento|mod_config|ban|economia_admin|etc.
    actor_id    INTEGER,            -- discord user_id de quien ejecutó la acción
    target_id   INTEGER,            -- user_id del afectado (si aplica)
    descripcion TEXT    NOT NULL,
    detalles_json TEXT,             -- contexto adicional estructurado
    creado_en   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_log_tipo     ON audit_log(tipo);
CREATE INDEX IF NOT EXISTS idx_log_actor    ON audit_log(actor_id);
CREATE INDEX IF NOT EXISTS idx_log_fecha    ON audit_log(creado_en);


-- ===========================================================================
-- WEBHOOKS EN CACHÉ — evita recrear webhooks por canal
-- ===========================================================================
CREATE TABLE IF NOT EXISTS webhook_cache (
    channel_id  INTEGER PRIMARY KEY,
    webhook_id  INTEGER NOT NULL,
    webhook_url TEXT    NOT NULL,
    creado_en   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);


-- ===========================================================================
-- TRIGGER: actualizar timestamp de characters al modificar
-- ===========================================================================
CREATE TRIGGER IF NOT EXISTS trg_characters_updated
    AFTER UPDATE ON characters
    FOR EACH ROW
BEGIN
    UPDATE characters
    SET actualizado_en = strftime('%Y-%m-%dT%H:%M:%fZ','now')
    WHERE id = NEW.id;
END;

-- ===========================================================================
-- TRIGGER: actualizar timestamp de vehicles al modificar
-- ===========================================================================
CREATE TRIGGER IF NOT EXISTS trg_vehicles_updated
    AFTER UPDATE ON vehicles
    FOR EACH ROW
BEGIN
    UPDATE vehicles
    SET actualizado_en = strftime('%Y-%m-%dT%H:%M:%fZ','now')
    WHERE id = NEW.id;
END;

-- ===========================================================================
-- TRIGGER: sincronizar radio_state.tiene_radio cuando se modifica el loadout
-- Cuando se equipa/desequipa el slot 'radio', actualiza el flag automáticamente.
-- ===========================================================================
CREATE TRIGGER IF NOT EXISTS trg_loadout_radio_sync
    AFTER UPDATE ON loadout
    FOR EACH ROW
    WHEN NEW.slot = 'radio'
BEGIN
    INSERT INTO radio_state (user_id, tiene_radio)
    VALUES (NEW.user_id, CASE WHEN NEW.item_id IS NOT NULL THEN 1 ELSE 0 END)
    ON CONFLICT(user_id) DO UPDATE
        SET tiene_radio = CASE WHEN NEW.item_id IS NOT NULL THEN 1 ELSE 0 END;
END;

CREATE TRIGGER IF NOT EXISTS trg_loadout_radio_sync_insert
    AFTER INSERT ON loadout
    FOR EACH ROW
    WHEN NEW.slot = 'radio'
BEGIN
    INSERT INTO radio_state (user_id, tiene_radio)
    VALUES (NEW.user_id, CASE WHEN NEW.item_id IS NOT NULL THEN 1 ELSE 0 END)
    ON CONFLICT(user_id) DO UPDATE
        SET tiene_radio = CASE WHEN NEW.item_id IS NOT NULL THEN 1 ELSE 0 END;
END;

-- =============================================================================
-- RAISA — Schema Patch v2
-- Descripción : Añade codigo a items; matricula, asientos_json y
--               contramedidas_json a vehicles.
--               Seguro ejecutar múltiples veces (usa IF NOT EXISTS / OR IGNORE).
-- Aplicar con: python tools/migrate.py patch
-- =============================================================================

-- ---------------------------------------------------------------------------
-- items: código único de catálogo para búsqueda rápida
-- ---------------------------------------------------------------------------
ALTER TABLE items ADD COLUMN codigo TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_items_codigo ON items(codigo);

-- ---------------------------------------------------------------------------
-- vehicles: matrícula, desglose de asientos por rol y contramedidas
-- ---------------------------------------------------------------------------
ALTER TABLE vehicles ADD COLUMN matricula TEXT;
ALTER TABLE vehicles ADD COLUMN asientos_json  TEXT NOT NULL DEFAULT '{}';
ALTER TABLE vehicles ADD COLUMN contramedidas_json TEXT NOT NULL DEFAULT '{}';