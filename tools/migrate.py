#!/usr/bin/env python3
"""
tools/migrate.py — Herramienta CLI de migración de datos para RAISA
======================================================================
Responsabilidad : Cargar datos desde /seeds/ hacia la base de datos SQLite.
Dependencias    : sqlite3 (stdlib), json (stdlib), pathlib (stdlib), argparse (stdlib)
Uso             : python tools/migrate.py [comando] [opciones]

Comandos disponibles
--------------------
  init          Aplica el esquema (schema.sql) sobre la BBDD indicada.
  seed items    Importa todos los ítems desde seeds/items/*.json
  seed vehiculos Importa vehículos desde seeds/vehiculos/*.json
  seed tienda   Importa el catálogo de tienda desde seeds/tienda/catalogo.json
  seed all      Ejecuta los tres seeds en orden (items → vehiculos → tienda)
  status        Muestra resumen de registros actuales en la BBDD
  reset         Elimina y recrea todas las tablas (DESTRUCTIVO, pide confirmación)

Ejemplos
--------
  python tools/migrate.py init
  python tools/migrate.py seed all
  python tools/migrate.py seed items --dry-run
  python tools/migrate.py seed tienda --db data/raisa.db
  python tools/migrate.py status
"""

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Rutas por defecto (relativas a la raíz del proyecto)
# ---------------------------------------------------------------------------
PROJECT_ROOT  = Path(__file__).resolve().parent.parent
DEFAULT_DB    = PROJECT_ROOT / "data" / "raisa.db"
SCHEMA_FILE   = PROJECT_ROOT / "db"    / "schema.sql"
SEEDS_DIR     = PROJECT_ROOT / "seeds"
ITEMS_DIR     = SEEDS_DIR   / "items"
VEHICLES_DIR  = SEEDS_DIR   / "vehiculos"
SHOP_FILE     = SEEDS_DIR   / "tienda" / "catalogo.json"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-8s %(message)s"
)
log = logging.getLogger("migrate")


# ---------------------------------------------------------------------------
# Helpers de conexión
# ---------------------------------------------------------------------------

def get_connection(db_path: Path) -> sqlite3.Connection:
    """
    Abre conexión SQLite con las pragmas de producción activadas.

    Args:
        db_path: Ruta al archivo .db

    Returns:
        Conexión SQLite configurada.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def load_json(path: Path) -> list | dict:
    """
    Carga y devuelve el contenido de un archivo JSON.

    Args:
        path: Ruta al archivo .json

    Returns:
        Objeto Python (list o dict).

    Raises:
        SystemExit: Si el archivo no existe o tiene JSON inválido.
    """
    if not path.exists():
        log.error(f"Archivo no encontrado: {path}")
        sys.exit(1)
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        log.error(f"JSON inválido en {path}: {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Comando: init
# ---------------------------------------------------------------------------

def cmd_init(conn: sqlite3.Connection, dry_run: bool = False) -> None:
    """
    Aplica el esquema SQL sobre la base de datos.

    Args:
        conn   : Conexión SQLite activa.
        dry_run: Si True, muestra las sentencias sin ejecutarlas.
    """
    if not SCHEMA_FILE.exists():
        log.error(f"Esquema no encontrado: {SCHEMA_FILE}")
        sys.exit(1)

    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    if dry_run:
        log.info("[DRY-RUN] Esquema que se aplicaría:")
        print(sql[:2000], "...\n(truncado)")
        return

    with conn:
        conn.executescript(sql)
    log.info(f"✅  Esquema aplicado desde {SCHEMA_FILE}")


# ---------------------------------------------------------------------------
# Comando: seed items
# ---------------------------------------------------------------------------

# Campos de la tabla 'items' que se pueden poblar desde los JSON.
# Los campos no incluidos aquí se dejan en su valor DEFAULT de la BBDD.
ITEM_FIELDS = [
    "nombre", "descripcion", "categoria", "subcategoria",
    "peso_kg", "volumen_u",
    "calibre", "tipo_arma", "id_compatibilidad", "capacidad_cargador",
    "slots_pouches", "nivel_proteccion", "tipo_pouch", "slots_ocupa", "capacidad_pouch",
    "precio_base",
    "codigo",          # ← NUEVO: código de catálogo único (ENT-001, CAL-001, etc.)
]


def _upsert_item(conn: sqlite3.Connection, row: dict, dry_run: bool):
    """
    Inserta o actualiza un ítem en la tabla 'items'.
    Clave de upsert: nombre + categoria.
    Si el JSON incluye 'codigo', se persiste; si no, se deja NULL.

    Args:
        conn   : Conexión SQLite.
        row    : Diccionario con datos del ítem.
        dry_run: Si True, no escribe.

    Returns:
        ID del registro, o None en dry_run.
    """
    import json as _json
    data   = {k: v for k, v in row.items() if not k.startswith("_")}
    fields = {k: data.get(k) for k in ITEM_FIELDS if k in data}

    for required in ("nombre", "categoria"):
        if not fields.get(required):
            import logging; logging.getLogger("migrate").warning(
                f"  ⚠  Registro omitido — falta campo '{required}': {data}"
            )
            return None

    columns      = list(fields.keys())
    placeholders = ", ".join("?" for _ in columns)
    values       = [fields[k] for k in columns]

    sql_check  = "SELECT id FROM items WHERE nombre = ? AND categoria = ?"
    sql_insert = f"INSERT INTO items ({', '.join(columns)}) VALUES ({placeholders})"
    sql_update = (
        f"UPDATE items SET {', '.join(f'{c}=?' for c in columns)} "
        "WHERE nombre=? AND categoria=?"
    )

    if dry_run:
        import logging; logging.getLogger("migrate").info(
            f"  [DRY] UPSERT item: {fields['nombre']} ({fields.get('codigo','—')})"
        )
        return None

    cur      = conn.execute(sql_check, (fields["nombre"], fields["categoria"]))
    existing = cur.fetchone()
    if existing:
        conn.execute(sql_update, values + [fields["nombre"], fields["categoria"]])
        return existing["id"]
    else:
        cur = conn.execute(sql_insert, values)
        return cur.lastrowid


def cmd_seed_items(conn: sqlite3.Connection, dry_run: bool = False) -> dict[str, int]:
    """
    Importa todos los archivos JSON de seeds/items/ hacia la tabla 'items'.

    Args:
        conn   : Conexión SQLite.
        dry_run: Si True, simula sin escribir.

    Returns:
        Diccionario {archivo: n_insertados}.
    """
    json_files = sorted(ITEMS_DIR.glob("*.json"))
    if not json_files:
        log.warning(f"No se encontraron archivos en {ITEMS_DIR}")
        return {}

    summary: dict[str, int] = {}

    for jf in json_files:
        data = load_json(jf)
        if not isinstance(data, list):
            log.warning(f"  ⚠  {jf.name} no es un array JSON — omitido")
            continue

        count = 0
        log.info(f"📄  Procesando {jf.name} ({len(data)} entradas)...")
        with conn:
            for row in data:
                if not isinstance(row, dict):
                    continue
                if row.get("nombre"):      # omitir filas que sean solo comentarios
                    result = _upsert_item(conn, row, dry_run)
                    if result is not None or dry_run:
                        count += 1

        log.info(f"    ✅  {count} ítems {'simulados' if dry_run else 'importados'}")
        summary[jf.name] = count

    return summary


# ---------------------------------------------------------------------------
# Comando: seed vehiculos
# ---------------------------------------------------------------------------

VEHICLE_FIELDS = [
    "nombre", "tipo", "subtipo",
    "matricula",       # ← NUEVO: matrícula o código de registro
    "asientos",        # total de plazas (escalar, mantener para compatibilidad)
    "estado_general",
    "combustible_actual", "combustible_max", "consumo_por_km",
    "inv_peso_max_kg", "inv_volumen_max_u",
    "artillado", "permite_transferencia_mun", "activo",
    "creado_por",
    # ELIMINADO: "ubicacion" — no existe en schema, se quitó de los seeds
]

VEHICLE_JSON_FIELDS = [
    "componentes",
    "municion_json",
    "hardpoints_json",
    "tripulacion_json",
    "asientos_json",         # ← NUEVO: {piloto, copiloto, artillero, comandante, pasajeros}
    "contramedidas_json",    # ← NUEVO: {chaffs:{actual,max,min}, bengalas:{actual,max,min}}
]


def _upsert_vehicle(conn: sqlite3.Connection, row: dict, dry_run: bool) -> int | None:
    """
    Inserta o actualiza un vehículo en la tabla 'vehicles'.
    Clave de upsert: nombre + tipo (combinación única).

    Args:
        conn   : Conexión SQLite.
        row    : Diccionario con datos del vehículo.
        dry_run: Si True, no escribe.

    Returns:
        ID del registro, o None en dry_run.
    """
    data = {k: v for k, v in row.items() if not k.startswith("_")}

    # Campos escalares
    scalar = {k: data.get(k) for k in VEHICLE_FIELDS}
    # Campos JSON: serializar dict/list a string
    json_cols: dict[str, str] = {}
    for jf in VEHICLE_JSON_FIELDS:
        val = data.get(jf)
        json_cols[jf] = json.dumps(val, ensure_ascii=False) if val is not None else "{}"

    all_fields = {**scalar, **json_cols}
    # Eliminar None para dejar caer en defaults cuando corresponda
    all_fields = {k: v for k, v in all_fields.items() if v is not None}

    for required in ("nombre", "tipo"):
        if not all_fields.get(required):
            log.warning(f"  ⚠  Vehículo omitido — falta '{required}': {data}")
            return None

    if dry_run:
        log.info(f"  [DRY] UPSERT vehículo: {all_fields['nombre']} ({all_fields['tipo']})")
        return None

    columns = list(all_fields.keys())
    placeholders = ", ".join("?" for _ in columns)
    values = [all_fields[k] for k in columns]

    sql_check  = "SELECT id FROM vehicles WHERE nombre = ? AND tipo = ?"
    sql_insert = f"INSERT INTO vehicles ({', '.join(columns)}) VALUES ({placeholders})"
    sql_update = f"UPDATE vehicles SET {', '.join(f'{c}=?' for c in columns)} WHERE nombre=? AND tipo=?"

    cur = conn.execute(sql_check, (all_fields["nombre"], all_fields["tipo"]))
    existing = cur.fetchone()
    if existing:
        conn.execute(sql_update, values + [all_fields["nombre"], all_fields["tipo"]])
        return existing["id"]
    else:
        cur = conn.execute(sql_insert, values)
        return cur.lastrowid


def cmd_seed_vehiculos(conn: sqlite3.Connection, dry_run: bool = False) -> dict[str, int]:
    """
    Importa todos los archivos JSON de seeds/vehiculos/ hacia la tabla 'vehicles'.

    Args:
        conn   : Conexión SQLite.
        dry_run: Si True, simula sin escribir.

    Returns:
        Diccionario {archivo: n_importados}.
    """
    json_files = sorted(VEHICLES_DIR.glob("*.json"))
    if not json_files:
        log.warning(f"No se encontraron archivos en {VEHICLES_DIR}")
        return {}

    summary: dict[str, int] = {}

    for jf in json_files:
        data = load_json(jf)
        if not isinstance(data, list):
            log.warning(f"  ⚠  {jf.name} no es un array JSON — omitido")
            continue

        count = 0
        log.info(f"🚗  Procesando {jf.name} ({len(data)} entradas)...")
        with conn:
            for row in data:
                if not isinstance(row, dict) or not row.get("nombre"):
                    continue
                result = _upsert_vehicle(conn, row, dry_run)
                if result is not None or dry_run:
                    count += 1

        log.info(f"    ✅  {count} vehículos {'simulados' if dry_run else 'importados'}")
        summary[jf.name] = count

    return summary


# ---------------------------------------------------------------------------
# Comando: seed tienda
# ---------------------------------------------------------------------------

def cmd_seed_tienda(conn, dry_run: bool = False) -> int:
    """
    Importa el catálogo de tienda desde seeds/tienda/catalogo.json.
    Resolución de item_id: busca primero por 'codigo_item', luego por 'nombre_item'.
    Si ninguno resuelve, emite advertencia y omite la entrada.
 
    Args:
        conn   : Conexión SQLite.
        dry_run: Si True, simula sin escribir.
 
    Returns:
        Número de listados importados.
    """
    import json as _json
    import logging
    log = logging.getLogger("migrate")
 
    try:
        catalog  = load_json(SHOP_FILE)
        listados = catalog.get("listados", [])
    except FileNotFoundError:
        log.error(f"No se encontró {SHOP_FILE}")
        return 0
 
    count     = 0
    not_found: list[str] = []
 
    log.info(f"🏪  Procesando catálogo de tienda ({len(listados)} entradas)...")
 
    with conn:
        for entry in listados:
            if not isinstance(entry, dict):
                continue
            # Saltar entradas de sección (solo tienen _seccion)
            if not entry.get("nombre_item") and not entry.get("codigo_item"):
                continue
 
            nombre  = entry.get("nombre_item", "")
            codigo  = entry.get("codigo_item", "")
            row     = None
 
            # Resolución prioritaria: codigo_item
            if codigo:
                cur = conn.execute(
                    "SELECT id, precio_base FROM items WHERE codigo = ?", (codigo,)
                )
                row = cur.fetchone()
 
            # Fallback: nombre_item (case-insensitive)
            if row is None and nombre:
                cur = conn.execute(
                    "SELECT id, precio_base FROM items WHERE LOWER(nombre) = LOWER(?)",
                    (nombre,)
                )
                row = cur.fetchone()
 
            if row is None:
                ref = codigo or nombre
                not_found.append(ref)
                log.warning(f"  ⚠  Ítem no resuelto: '{ref}' — omitido")
                continue
 
            item_id = row["id"]
            precio  = entry.get("precio") or row["precio_base"]
            stock   = entry.get("stock", -1)
            activo  = 1 if entry.get("activo", True) else 0
 
            if dry_run:
                log.info(
                    f"  [DRY] UPSERT shop: {codigo or nombre} | "
                    f"precio={precio} | stock={stock}"
                )
                count += 1
                continue
 
            cur = conn.execute(
                "SELECT id FROM shop_listings WHERE item_id = ?", (item_id,)
            )
            existing = cur.fetchone()
            if existing:
                conn.execute(
                    "UPDATE shop_listings SET precio=?, stock=?, activo=? WHERE item_id=?",
                    (precio, stock, activo, item_id),
                )
            else:
                conn.execute(
                    "INSERT INTO shop_listings (item_id, precio, stock, activo) "
                    "VALUES (?,?,?,?)",
                    (item_id, precio, stock, activo),
                )
            count += 1
 
    log.info(f"    ✅  {count} listados {'simulados' if dry_run else 'importados'}")
    if not_found:
        log.warning(
            f"    ⚠  {len(not_found)} ítems no resueltos "
            f"(ejecuta 'seed items' primero): "
            + ", ".join(not_found[:5])
            + ("..." if len(not_found) > 5 else "")
        )
    return count


# ---------------------------------------------------------------------------
# Comando: status
# ---------------------------------------------------------------------------

def cmd_status(conn: sqlite3.Connection) -> None:
    """
    Muestra un resumen del número de registros en las tablas principales.

    Args:
        conn: Conexión SQLite.
    """
    tables = [
        ("items",            "Ítems en catálogo"),
        ("shop_listings",    "Listados en tienda"),
        ("vehicles",         "Vehículos registrados"),
        ("characters",       "Personajes"),
        ("economy",          "Cuentas económicas"),
        ("audit_log",        "Entradas de log"),
    ]
    print("\n" + "─" * 45)
    print(f"  {'Tabla':<28} {'Registros':>10}")
    print("─" * 45)
    for table, label in tables:
        try:
            cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
            n = cur.fetchone()[0]
        except sqlite3.OperationalError:
            n = "N/A (tabla no existe)"
        print(f"  {label:<28} {str(n):>10}")
    print("─" * 45 + "\n")


# ---------------------------------------------------------------------------
# Comando: reset
# ---------------------------------------------------------------------------

def cmd_reset(conn: sqlite3.Connection) -> None:
    """
    Elimina todas las tablas y vuelve a aplicar el esquema.
    Destructivo: solicita confirmación explícita antes de ejecutar.

    Args:
        conn: Conexión SQLite.
    """
    confirm = input(
        "⚠️  ATENCIÓN: Esto borrará TODOS los datos. Escribe 'CONFIRMAR' para continuar: "
    ).strip()
    if confirm != "CONFIRMAR":
        log.info("Operación cancelada.")
        return

    log.warning("Eliminando todas las tablas...")
    # Obtener lista de tablas y vistas para eliminar
    cur = conn.execute(
        "SELECT name, type FROM sqlite_master WHERE type IN ('table','view','trigger') "
        "AND name NOT LIKE 'sqlite_%'"
    )
    objects = cur.fetchall()
    with conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        for obj in objects:
            conn.execute(f"DROP {obj['type'].upper()} IF EXISTS [{obj['name']}]")
        conn.execute("PRAGMA foreign_keys = ON")

    log.info("Tablas eliminadas. Aplicando esquema limpio...")
    cmd_init(conn)
    log.info("✅  Reset completado.")


# ---------------------------------------------------------------------------
# Entry point y parsing de argumentos
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Construye y devuelve el parser de argumentos CLI."""
    parser = argparse.ArgumentParser(
        prog="migrate",
        description="Herramienta de migración de datos RAISA",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        metavar="RUTA",
        help=f"Ruta a la base de datos SQLite (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simula las operaciones sin escribir en la BBDD",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMANDO")
    subparsers.required = True

    # init
    subparsers.add_parser("init", help="Aplica el esquema SQL sobre la BBDD")

    # patch
    subparsers.add_parser("patch", help="Aplica el schema_patch_v2.sql sobre la BBDD")

    # seed
    seed_parser = subparsers.add_parser("seed", help="Importa datos desde seeds/")
    seed_parser.add_argument(
        "target",
        choices=["items", "vehiculos", "tienda", "all"],
        help="Qué seed importar",
    )

    # status
    subparsers.add_parser("status", help="Muestra resumen de registros en la BBDD")

    # reset
    subparsers.add_parser("reset", help="Elimina y recrea todas las tablas (DESTRUCTIVO)")

    return parser


def main() -> None:
    """Punto de entrada principal del CLI."""
    parser  = build_parser()
    args    = parser.parse_args()
    db_path = args.db

    log.info(f"Base de datos: {db_path}")

    conn = get_connection(db_path)

    try:
        if args.command == "init":
            cmd_init(conn, dry_run=args.dry_run)

        elif args.command == "patch":
            patch_file = PROJECT_ROOT / "db" / "schema_patch_v2.sql"
            if not patch_file.exists():
                log.error(f"No se encontró {patch_file}")
                sys.exit(1)
            sql = patch_file.read_text(encoding="utf-8")
            # Ejecutar cada sentencia por separado (ALTER TABLE no admite multi-statement)
            for stmt in sql.split(";"):
                stmt = stmt.strip()
                if stmt and not stmt.startswith("--"):
                    try:
                        conn.execute(stmt)
                    except sqlite3.OperationalError as e:
                        # "duplicate column" es esperado si el patch ya se aplicó
                        if "duplicate column" in str(e).lower():
                            log.info(f"  ℹ  Ya aplicado: {stmt[:60]}...")
                        else:
                            raise
            conn.commit()
            log.info("✅  Patch v2 aplicado.")

        elif args.command == "seed":
            target = args.target

            # Para 'tienda' y 'all' verificamos que los ítems existan primero
            if target in ("tienda", "all"):
                cur = conn.execute("SELECT COUNT(*) FROM items")
                item_count = cur.fetchone()[0]
                if item_count == 0 and target == "tienda":
                    log.error(
                        "La tabla 'items' está vacía. "
                        "Ejecuta 'seed items' antes de 'seed tienda'."
                    )
                    sys.exit(1)

            if target in ("items", "all"):
                cmd_seed_items(conn, dry_run=args.dry_run)
            if target in ("vehiculos", "all"):
                cmd_seed_vehiculos(conn, dry_run=args.dry_run)
            if target in ("tienda", "all"):
                cmd_seed_tienda(conn, dry_run=args.dry_run)

        elif args.command == "status":
            cmd_status(conn)

        elif args.command == "reset":
            cmd_reset(conn)

    finally:
        conn.close()


if __name__ == "__main__":
    main()