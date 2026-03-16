-- ─────────────────────────────────────────────────────────────────────────────
-- RAISA — Esquema de base de datos SQLite
-- Activar WAL mode en la conexión: PRAGMA journal_mode=WAL;
-- ─────────────────────────────────────────────────────────────────────────────

-- ─── PERSONAJES ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS personajes (
    user_id         INTEGER PRIMARY KEY,
    nombre          TEXT    NOT NULL,
    apellidos       TEXT    NOT NULL,
    edad            INTEGER NOT NULL,
    genero          TEXT    NOT NULL CHECK(genero IN ('Hombre','Mujer')),
    nacionalidad    TEXT    NOT NULL,
    servicio_previo TEXT,
    destinos        TEXT,
    clase           TEXT    NOT NULL,
    psicotecnico    TEXT,
    estudios        TEXT    NOT NULL,
    ocupaciones     TEXT    NOT NULL,
    trasfondo       TEXT    NOT NULL,
    avatar_path     TEXT,
    verificado      INTEGER NOT NULL DEFAULT 0,
    creado_en       TEXT    NOT NULL DEFAULT (datetime('now')),
    dinero          REAL    NOT NULL DEFAULT 0.0,
    unidad_radio    TEXT
);

-- ─── FORMULARIOS EN PROGRESO ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS formularios_registro (
    user_id         INTEGER PRIMARY KEY,
    paso_actual     INTEGER NOT NULL DEFAULT 1,
    datos_json      TEXT    NOT NULL DEFAULT '{}',
    ultima_actividad TEXT   NOT NULL DEFAULT (datetime('now')),
    suspendido      INTEGER NOT NULL DEFAULT 0
);

-- ─── INVENTARIO — LOADOUT ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS loadout (
    user_id             INTEGER PRIMARY KEY REFERENCES personajes(user_id),
    arma_primaria_id    INTEGER,
    arma_secundaria_id  INTEGER,
    arma_terciaria_id   INTEGER,
    chaleco_id          INTEGER,
    portaplacas_id      INTEGER,
    placas_id           INTEGER,
    soportes_id         INTEGER,
    casco_id            INTEGER,
    pantalon_id         INTEGER,
    camisa_id           INTEGER,
    chaqueta_id         INTEGER,
    botas_id            INTEGER,
    guantes_id          INTEGER,
    reloj_id            INTEGER,
    parche_url          TEXT,
    mochila_id          INTEGER,
    cinturon_id         INTEGER,
    radio_id            INTEGER
);

-- ─── POUCHES EQUIPADOS ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pouches_equipados (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES personajes(user_id),
    contenedor      TEXT    NOT NULL CHECK(contenedor IN ('chaleco','portaplacas','soportes')),
    slot_numero     INTEGER NOT NULL,
    pouch_item_id   INTEGER NOT NULL,
    UNIQUE(user_id, contenedor, slot_numero)
);

-- ─── INVENTARIO GENERAL ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS inventario_general (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES personajes(user_id),
    item_uuid       INTEGER NOT NULL,
    cantidad        INTEGER NOT NULL DEFAULT 1,
    UNIQUE(user_id, item_uuid)
);

-- ─── CATÁLOGO DE ÍTEMS ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS items (
    item_uuid           INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre              TEXT    NOT NULL,
    descripcion         TEXT,
    categoria           TEXT    NOT NULL,
    peso_kg             REAL    NOT NULL DEFAULT 0.0,
    volumen             REAL    NOT NULL DEFAULT 0.0,
    precio_base         REAL    NOT NULL DEFAULT 0.0,
    disponible          INTEGER NOT NULL DEFAULT 1,
    calibre             TEXT,
    capacidad_cargador  INTEGER,
    id_compatibilidad   TEXT,
    slots_pouches       INTEGER DEFAULT 0,
    tipo_pouch          TEXT,
    estado              TEXT    DEFAULT 'Operativo'
);

-- ─── TIENDA ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tienda (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    item_uuid       INTEGER NOT NULL REFERENCES items(item_uuid),
    precio_actual   REAL    NOT NULL,
    stock           INTEGER NOT NULL DEFAULT -1,
    activo          INTEGER NOT NULL DEFAULT 1
);

-- ─── ESTADO MÉDICO ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS estado_medico (
    user_id         INTEGER PRIMARY KEY REFERENCES personajes(user_id),
    heridas         TEXT    NOT NULL DEFAULT '[]',
    fracturas       TEXT    NOT NULL DEFAULT '[]',
    consciencia     TEXT    NOT NULL DEFAULT 'Consciente'
                    CHECK(consciencia IN ('Consciente','Semiconsciente','Inconsciente','Clínico')),
    sangre          INTEGER NOT NULL DEFAULT 100 CHECK(sangre BETWEEN 0 AND 100),
    estado_general  TEXT    NOT NULL DEFAULT 'Óptimo',
    ultima_actualizacion TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ─── VEHÍCULOS ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS vehiculos (
    vehiculo_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre                  TEXT    NOT NULL,
    tipo                    TEXT    NOT NULL,
    matricula               TEXT    UNIQUE,
    asientos                INTEGER NOT NULL DEFAULT 1,
    estado_general          TEXT    NOT NULL DEFAULT 'Operativo',
    componentes_json        TEXT    NOT NULL DEFAULT '{}',
    combustible_actual      REAL    NOT NULL DEFAULT 100.0,
    combustible_max         REAL    NOT NULL DEFAULT 100.0,
    consumo_por_km          REAL    NOT NULL DEFAULT 1.0,
    inventario_peso_actual  REAL    NOT NULL DEFAULT 0.0,
    inventario_peso_max     REAL    NOT NULL DEFAULT 0.0,
    inventario_volumen_actual REAL  NOT NULL DEFAULT 0.0,
    inventario_volumen_max    REAL  NOT NULL DEFAULT 0.0,
    municion_json           TEXT    NOT NULL DEFAULT '{}',
    hardpoints_json         TEXT    NOT NULL DEFAULT '[]',
    artillado               INTEGER NOT NULL DEFAULT 0,
    destruido               INTEGER NOT NULL DEFAULT 0,
    propietario_id          INTEGER
    -- TODO: Naval — estructura preparada, sin funcionalidad activa
);

-- ─── OCUPANTES DE VEHÍCULO ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS vehiculo_ocupantes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    vehiculo_id     INTEGER NOT NULL REFERENCES vehiculos(vehiculo_id),
    user_id         INTEGER NOT NULL REFERENCES personajes(user_id),
    asiento         INTEGER NOT NULL,
    UNIQUE(vehiculo_id, asiento),
    UNIQUE(user_id)
);

-- ─── INVENTARIO DE VEHÍCULO ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS inventario_vehiculo (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    vehiculo_id     INTEGER NOT NULL REFERENCES vehiculos(vehiculo_id),
    item_uuid       INTEGER NOT NULL,
    cantidad        INTEGER NOT NULL DEFAULT 1,
    UNIQUE(vehiculo_id, item_uuid)
);

-- ─── ESTADO DE EVENTO ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS estado_evento (
    id              INTEGER PRIMARY KEY DEFAULT 1 CHECK(id = 1),
    modo            TEXT    NOT NULL DEFAULT 'OFF' CHECK(modo IN ('ON','OFF')),
    activado_por    INTEGER,
    activado_en     TEXT    DEFAULT (datetime('now'))
);
INSERT OR IGNORE INTO estado_evento (id, modo) VALUES (1, 'OFF');

-- ─── REGISTRO DE LOGS CRÍTICOS ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS log_critico (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL DEFAULT (datetime('now')),
    accion          TEXT    NOT NULL,
    ejecutor_id     INTEGER NOT NULL,
    objetivo_id     INTEGER,
    detalle         TEXT
);

-- ─── ESTÁTICA DE RADIO POR CANAL ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS radio_statica (
    canal_key   TEXT PRIMARY KEY,
    activa      INTEGER NOT NULL DEFAULT 0
);

-- ─── ÍNDICES DE RENDIMIENTO ──────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_inventario_general_user ON inventario_general(user_id);
CREATE INDEX IF NOT EXISTS idx_pouches_user            ON pouches_equipados(user_id);
CREATE INDEX IF NOT EXISTS idx_items_categoria         ON items(categoria);
CREATE INDEX IF NOT EXISTS idx_tienda_activo           ON tienda(activo);
CREATE INDEX IF NOT EXISTS idx_vehiculo_tipo           ON vehiculos(tipo);
CREATE INDEX IF NOT EXISTS idx_log_timestamp           ON log_critico(timestamp);
CREATE INDEX IF NOT EXISTS idx_formularios_user        ON formularios_registro(user_id);
