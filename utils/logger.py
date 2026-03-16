"""
RAISA — Logger centralizado
utils/logger.py

Responsabilidad : Configuración del sistema de logs. Registro a fichero +
                  consola. Nivel configurable desde .env.
Dependencias    : logging, os
Autor           : Proyecto RAISA
"""

import logging
import logging.handlers
import os
from pathlib import Path


def setup_logging() -> None:
    """
    Configura el sistema de logging global de RAISA.
    - Nivel desde LOG_LEVEL en .env (default INFO).
    - Rotación de archivo: 5 MB, 3 backups.
    - Formato: timestamp | nivel | módulo | mensaje.
    """
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    log_dir = Path("data/logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Handler de archivo con rotación
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "raisa.log",
        maxBytes=5 * 1024 * 1024,   # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(level)

    # Handler de consola
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    console_handler.setLevel(level)

    # Aplicar a logger raíz de RAISA
    root = logging.getLogger("raisa")
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # Silenciar discord.py en producción (muy verboso)
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
