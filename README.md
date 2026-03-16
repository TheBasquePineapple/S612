# RAISA — Record and Information Administration of the SCP Foundation Security

Bot de Discord para servidor de rol narrativo SCP. Sistema de gestión operativa completo.

## 📋 Requisitos

- Python 3.11+
- discord.py >= 2.3.0
- aiosqlite >= 0.19.0
- Pillow >= 10.0.0
- python-dotenv >= 1.0.0

## 🚀 Instalación

```bash
pip install -r requirements.txt
cp .env.example .env
# Editar .env con los valores reales
python main.py
```

## 📁 Estructura

```
raisa/
├── main.py                  ← Arranque del bot
├── .env.example             ← Plantilla de variables de entorno
├── requirements.txt
├── config/
│   ├── roles.json           ← IDs de roles de Discord
│   ├── banlist.json         ← Palabras prohibidas en nombres
│   ├── radio.json           ← Configuración de frecuencias
│   └── inventario.json      ← Límites y salarios
├── data/
│   ├── raisa.db             ← Base de datos SQLite (se crea al arrancar)
│   └── characters/          ← Avatares de personajes
├── assets/radio/            ← Avatar por defecto de webhooks de radio
├── cogs/
│   ├── registro.py          ← Sistema de registro por MD
│   ├── inventario.py        ← Loadout, inventario general, pouches
│   ├── medico.py            ← Estado médico, heridas, fracturas
│   ├── radio.py             ← Sistema de radio con webhooks dinámicos
│   ├── vehiculos.py         ← Tierra, aire; munición, combustible
│   ├── economia.py          ← Tienda, compra/venta, salarios
│   └── eventos.py           ← Control Evento-ON / Evento-OFF
├── db/
│   ├── repository.py        ← Capa de acceso a datos (única)
│   └── schema.sql           ← Esquema inicial de tablas
└── utils/
    ├── embeds.py            ← Builders de embeds reutilizables
    ├── permisos.py          ← Decoradores @require_role, @require_sudo
    ├── validaciones.py      ← Validaciones centralizadas (munición, peso…)
    └── logger.py            ← Log centralizado con rotación
```

## 🔐 Configuración inicial

1. **`.env`** — Token del bot, IDs de Owner/Holder, clave SUDO, canal de verificación.
2. **`config/roles.json`** — IDs de roles de Discord para cada nivel de la jerarquía.
3. **`config/radio.json`** — IDs de canales y roles para cada frecuencia de radio.

## 👥 Jerarquía de permisos

| Rango | Descripción |
|---|---|
| Owner | Todos los permisos. ID en `.env` |
| Holder | Equivalente a Owner (cuenta de desarrollo). ID en `.env` |
| Admin | Gestión técnica. IDs individuales en `config/roles.json` |
| Gestor | Gestión de info y usuarios |
| Narrador | Gestión de eventos y flujo narrativo |
| Usuario | Interacción estándar con sus sistemas |
| Visitante | Solo registro |

## 🛡️ Modo SUDO

Comandos críticos requieren autenticación SUDO:
- El bot solicita la clave SUDO por MD.
- Sesión de 30 minutos tras autenticación correcta.
- Intentos fallidos registrados y notificados al Owner.

## 📻 Sistema de Radio

Requiere radio equipada en el slot de radio del loadout.
- `/radio encendida` — Activa comunicaciones
- `/radio apagada` — Corta todas las comunicaciones
- `/radio canal [frecuencia]` — Cambia de frecuencia
- `/radio toda_malla [mensaje]` — Mensaje a todos los canales

## ⚙️ Consideraciones de hardware

Optimizado para Intel Atom D2550 / 4 GB RAM:
- Todo el sistema es event-driven (asyncio, sin bucles bloqueantes)
- Caché LRU de 50 entradas para datos frecuentes
- Tareas periódicas con intervalo mínimo de 10 minutos
- Imágenes comprimidas al 85% con resolución máx 800×800px
- SQLite con WAL mode para lecturas concurrentes sin bloqueo
