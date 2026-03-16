"""
utils/logger.py — Log centralizado de acciones críticas de RAISA
=================================================================
Responsabilidad : Registrar en BBDD y en archivo de log toda acción que
                  requiere auditoría: SUDO, muertes, modificaciones médicas,
                  cambios de evento, acciones económicas de staff.
Dependencias    : sqlite3, logging (stdlib), db.repository
Autor           : RAISA Dev

Tipos de log válidos
--------------------
  sudo_auth       — Autenticación SUDO exitosa
  sudo_fail       — Intento fallido de SUDO
  sudo_expire     — Sesión SUDO expirada
  muerte          — Muerte de personaje ejecutada
  mod_medica      — Modificación del estado médico por Narrador+
  cambio_evento   — Cambio de estado Evento-ON / Evento-OFF
  mod_config      — Modificación de configuración del sistema
  economia_admin  — Acción económica ejecutada por Narrador+ (entregar/retirar dinero)
  ban             — Baneo/desbaneo de usuario
  verificacion    — Aceptación o denegación de ficha de personaje
  asignacion_unidad — Asignación de unidad radio por Narrador
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

# Logger estándar de Python (para archivo/consola)
_log = logging.getLogger("raisa")

# Ruta del archivo de log en disco
_LOG_FILE = Path(os.getenv("LOG_PATH", "data/raisa.log"))


def _setup_file_handler() -> None:
    """Configura el handler de archivo si no está ya configurado."""
    if any(isinstance(h, logging.FileHandler) for h in _log.handlers):
        return
    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    _log.addHandler(handler)
    _log.setLevel(logging.DEBUG if os.getenv("DEBUG", "0") == "1" else logging.INFO)


_setup_file_handler()


async def audit(
    conn,
    tipo: str,
    descripcion: str,
    actor_id: int | None = None,
    target_id: int | None = None,
    detalles: dict | None = None,
) -> None:
    """
    Registra una acción crítica en la tabla audit_log y en el archivo de log.

    Esta función es el único punto de entrada para auditoría.
    NUNCA lanzar excepciones desde aquí — el log no debe romper el flujo.

    Args:
        conn        : Conexión SQLite (desde db.repository).
        tipo        : Tipo de acción (ver docstring del módulo).
        descripcion : Texto descriptivo legible para humanos.
        actor_id    : discord user_id de quien ejecutó la acción (opcional).
        target_id   : discord user_id del afectado (opcional).
        detalles    : Dict con contexto adicional serializable como JSON.
    """
    detalles_json = json.dumps(detalles, ensure_ascii=False) if detalles else None
    ts = datetime.now(timezone.utc).isoformat()

    try:
        await conn.execute(
            """
            INSERT INTO audit_log (tipo, actor_id, target_id, descripcion, detalles_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (tipo, actor_id, target_id, descripcion, detalles_json),
        )
        await conn.commit()
    except Exception as exc:  # noqa: BLE001
        # El log de auditoría nunca debe interrumpir el flujo de negocio
        _log.error(f"[AUDIT-WRITE-FAIL] {exc} — tipo={tipo} descripcion={descripcion}")

    # Siempre escribir en archivo de log independientemente de la BBDD
    _log.info(
        f"[{tipo.upper()}] actor={actor_id} target={target_id} | {descripcion}"
        + (f" | {detalles_json}" if detalles_json else "")
    )


def log_info(msg: str) -> None:
    """Escribe un mensaje informativo en el log de archivo/consola."""
    _log.info(msg)


def log_warning(msg: str) -> None:
    """Escribe un aviso en el log de archivo/consola."""
    _log.warning(msg)


def log_error(msg: str) -> None:
    """Escribe un error en el log de archivo/consola."""
    _log.error(msg)


def log_debug(msg: str) -> None:
    """Escribe un mensaje de depuración (solo en modo DEBUG=1)."""
    _log.debug(msg)