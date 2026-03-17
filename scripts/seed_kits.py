#!/usr/bin/env python3
"""
scripts/seed_kits.py — Script de seed para KITs médicos
========================================================
Responsabilidad : Carga el catálogo de KITs desde JSON a la BBDD SQLite.
Dependencias    : aiosqlite, db.kits_repository
Autor           : RAISA Dev

Uso
---
python scripts/seed_kits.py [--json PATH] [--db PATH]

Argumentos
----------
--json PATH : Ruta al archivo JSON con los KITs (default: protecciones_y_equipo.json)
--db PATH   : Ruta a la base de datos SQLite (default: data/raisa.db)

Ejemplo
-------
python scripts/seed_kits.py --json /path/to/protecciones_y_equipo.json --db /path/to/raisa.db
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Añadir directorio raíz al path para imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import aiosqlite

from db import kits_repository as kits_repo


async def main(json_path: str, db_path: str) -> None:
    """
    Ejecuta el seed de KITs en la BBDD.
    
    Args:
        json_path: Ruta al archivo JSON con los KITs.
        db_path  : Ruta a la base de datos SQLite.
    """
    print(f"🔧 Iniciando seed de KITs médicos...")
    print(f"   JSON: {json_path}")
    print(f"   BBDD: {db_path}")
    print()
    
    # Verificar que el archivo JSON existe
    if not Path(json_path).exists():
        print(f"❌ ERROR: Archivo JSON no encontrado: {json_path}")
        sys.exit(1)
    
    # Verificar que la BBDD existe
    if not Path(db_path).exists():
        print(f"⚠️  ADVERTENCIA: La base de datos no existe. Se creará: {db_path}")
    
    # Conectar a la BBDD
    async with aiosqlite.connect(db_path) as conn:
        # Habilitar foreign keys
        await conn.execute("PRAGMA foreign_keys = ON")
        
        # Cargar schema si es necesario
        # (Asumiendo que el schema ya está aplicado)
        # Si no, descomentar las siguientes líneas:
        # schema_path = Path(__file__).parent.parent / "db" / "schema_kits.sql"
        # if schema_path.exists():
        #     with schema_path.open() as f:
        #         await conn.executescript(f.read())
        #     print("✅ Schema de KITs aplicado")
        
        # Ejecutar seed
        try:
            count = await kits_repo.seed_kits_catalogo(conn, json_path)
            print(f"✅ Seed completado: {count} KIT(s) insertados en el catálogo")
            
            # Mostrar resumen
            cursor = await conn.execute("SELECT COUNT(*) FROM kits_catalogo")
            total = (await cursor.fetchone())[0]
            print(f"📦 Total de KITs en catálogo: {total}")
            
        except FileNotFoundError as e:
            print(f"❌ ERROR: {e}")
            sys.exit(1)
        except Exception as e:
            print(f"❌ ERROR durante el seed: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    
    print()
    print("🎉 Proceso completado exitosamente")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Carga el catálogo de KITs médicos desde JSON a la BBDD SQLite"
    )
    parser.add_argument(
        "--json",
        type=str,
        default="protecciones_y_equipo.json",
        help="Ruta al archivo JSON con los KITs (default: protecciones_y_equipo.json)",
    )
    parser.add_argument(
        "--db",
        type=str,
        default="data/raisa.db",
        help="Ruta a la base de datos SQLite (default: data/raisa.db)",
    )
    
    args = parser.parse_args()
    
    asyncio.run(main(args.json, args.db))