-- ============================================================================
-- SCHEMA EXTENSION: Sistema de KITs Médicos
-- ============================================================================
-- Autor: RAISA Dev
-- Propósito: Extensión del esquema SQLite para gestión de KITs médicos
-- Versión: 1.0
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Tabla: kits_catalogo
-- Descripción: Catálogo de KITs disponibles (cargado desde JSON)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kits_catalogo (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo              TEXT NOT NULL UNIQUE,           -- KIT-001, KIT-002, etc.
    nombre              TEXT NOT NULL,
    descripcion         TEXT,
    categoria           TEXT NOT NULL DEFAULT 'kit',
    subcategoria        TEXT NOT NULL,
    peso_kg             REAL NOT NULL,                  -- Peso total (contenedor + contenido)
    volumen_u           INTEGER NOT NULL,               -- Volumen del contenedor
    slots_pouch         INTEGER NOT NULL DEFAULT 1,     -- Slots que ocupa como pouch (1 o 2)
    espacio_libre_pct   INTEGER NOT NULL DEFAULT 10,    -- % espacio disponible (5-20)
    precio_base         REAL NOT NULL DEFAULT 0.0,
    es_kit              INTEGER NOT NULL DEFAULT 1,     -- Flag: 1 = es kit
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ----------------------------------------------------------------------------
-- Tabla: kits_contenido_default
-- Descripción: Contenido predefinido de cada KIT (relación N:M)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kits_contenido_default (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    kit_id              INTEGER NOT NULL,               -- FK a kits_catalogo
    item_codigo         TEXT NOT NULL,                  -- Código del ítem médico (MED-XXX)
    cantidad            INTEGER NOT NULL DEFAULT 1,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (kit_id) REFERENCES kits_catalogo(id) ON DELETE CASCADE
);

-- ----------------------------------------------------------------------------
-- Tabla: kits_instancias
-- Descripción: Instancias de KITs asignados a usuarios (inventario activo)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kits_instancias (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL,               -- Discord user ID
    kit_catalogo_id     INTEGER NOT NULL,               -- FK a kits_catalogo
    ubicacion           TEXT NOT NULL DEFAULT 'general', -- 'general', 'loadout_slot', 'pouch_ID', 'vehiculo_ID'
    slot_destino        TEXT,                           -- Si ubicacion='loadout_slot': nombre del slot
    peso_contenido_actual REAL NOT NULL DEFAULT 0.0,   -- Peso actual del contenido (dinámico)
    volumen_usado       INTEGER NOT NULL DEFAULT 0,     -- Volumen usado del KIT
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (kit_catalogo_id) REFERENCES kits_catalogo(id) ON DELETE CASCADE
);

-- ----------------------------------------------------------------------------
-- Tabla: kits_contenido_actual
-- Descripción: Contenido actual de cada instancia de KIT (puede variar del default)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kits_contenido_actual (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    kit_instancia_id    INTEGER NOT NULL,               -- FK a kits_instancias
    item_codigo         TEXT NOT NULL,                  -- Código del ítem médico
    cantidad            INTEGER NOT NULL DEFAULT 1,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (kit_instancia_id) REFERENCES kits_instancias(id) ON DELETE CASCADE
);

-- ----------------------------------------------------------------------------
-- Índices para optimización de consultas
-- ----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_kits_catalogo_codigo 
    ON kits_catalogo(codigo);

CREATE INDEX IF NOT EXISTS idx_kits_contenido_default_kit 
    ON kits_contenido_default(kit_id);

CREATE INDEX IF NOT EXISTS idx_kits_instancias_user 
    ON kits_instancias(user_id);

CREATE INDEX IF NOT EXISTS idx_kits_instancias_ubicacion 
    ON kits_instancias(ubicacion);

CREATE INDEX IF NOT EXISTS idx_kits_contenido_actual_instancia 
    ON kits_contenido_actual(kit_instancia_id);

-- ----------------------------------------------------------------------------
-- Triggers para actualización automática de timestamps
-- ----------------------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS update_kits_catalogo_timestamp
    AFTER UPDATE ON kits_catalogo
    FOR EACH ROW
BEGIN
    UPDATE kits_catalogo SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS update_kits_instancias_timestamp
    AFTER UPDATE ON kits_instancias
    FOR EACH ROW
BEGIN
    UPDATE kits_instancias SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS update_kits_contenido_actual_timestamp
    AFTER UPDATE ON kits_contenido_actual
    FOR EACH ROW
BEGIN
    UPDATE kits_contenido_actual SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- ============================================================================
-- NOTAS DE IMPLEMENTACIÓN
-- ============================================================================
-- 
-- FLUJO DE COMPRA DE KIT:
-- 1. Usuario compra KIT desde tienda
-- 2. Se crea registro en kits_instancias con ubicacion='general'
-- 3. Se copian los ítems desde kits_contenido_default a kits_contenido_actual
-- 4. Se calcula peso_contenido_actual basado en los ítems
--
-- FLUJO DE EXTRACCIÓN DE ÍTEM:
-- 1. Narrador ejecuta comando /kit extraer [instancia_id] [item_codigo] [cantidad]
-- 2. Se reduce cantidad en kits_contenido_actual (o se elimina el registro si queda en 0)
-- 3. Se actualiza peso_contenido_actual y volumen_usado
-- 4. Ítem se añade al inventario general del usuario
--
-- FLUJO DE INSERCIÓN DE ÍTEM:
-- 1. Narrador ejecuta comando /kit insertar [instancia_id] [item_codigo] [cantidad]
-- 2. Se verifica espacio disponible (volumen_usado + nuevo_item <= volumen_u * (1 + espacio_libre_pct/100))
-- 3. Se añade/incrementa registro en kits_contenido_actual
-- 4. Se actualiza peso_contenido_actual y volumen_usado
-- 5. Ítem se retira del inventario general del usuario
--
-- COMPATIBILIDAD CON POUCHES:
-- - Los KITs pueden asignarse como pouches a protecciones
-- - Ocupan el número de slots definido en kits_catalogo.slots_pouch
-- - Cuando se asignan como pouch, ubicacion cambia a 'pouch_ID' donde ID es el ID del registro de pouch
--
-- ============================================================================