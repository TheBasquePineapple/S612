raisa/
├── main.py                  — Entrada, carga de cogs, on_ready, restauración de Views
├── requirements.txt
├── .env.example
│
├── cogs/
│   ├── registro.py          — Formulario MD completo (12 pasos, 4 bloques, Pillow)
│   ├── sudo.py              — Auth SUDO por MD + anti-brute-force (3 intentos)
│   ├── inventario.py        — Loadout, inventario general, pouches MOLLE
│   ├── medico.py            — Estado médico, heridas, fracturas, muerte
│   ├── radio.py             — Webhooks dinámicos, frecuencias, estática, unidades
│   ├── vehiculos.py         — Vehículos + regla absoluta de munición
│   ├── economia.py          — Tienda, saldo, salarios automáticos (tasks)
│   └── eventos.py           — Evento-ON/OFF, bloqueos automáticos
│
├── db/
│   ├── schema.sql           — Schema completo con triggers e índices
│   └── repository.py        — Única capa de acceso a BBDD (~450 líneas)
│
├── utils/
│   ├── permisos.py          — @require_role, @require_sudo, sesiones SUDO LRU
│   ├── embeds.py            — Todos los constructores de Embed centralizados
│   ├── validaciones.py      — Munición, peso/volumen, estado médico, banlist, edad
│   └── logger.py            — audit() → BBDD + archivo de log
│
├── config/                  — roles.json, banlist.json, radio.json, inventario.json, economia.json
├── seeds/                   — 77 ítems, 15 vehículos, 49 listados de tienda
└── tools/migrate.py         — CLI: init / seed / status / reset / --dry-run


seeds/                       ← Editas aquí, nunca SQL a mano
├── items/
│   ├── armas.json           (17 armas con calibre y tipo)
│   ├── municion.json        (16 cargadores/granadas con id_compatibilidad)
│   ├── protecciones_y_equipo.json  (29 ítems: chalecos, placas, pouches, uniforme)
│   └── medico_y_misc.json   (16 ítems: torniquetes, radios, NVG...)
├── vehiculos/
│   ├── terrestres.json      (7 vehículos con munición y componentes reales)
│   └── aereos_y_naval.json  (8 vehículos, naval marcado como reservado)
└── tienda/
    └── catalogo.json        (49 listados con precio y stock)

tools/migrate.py             ← CLI único para todo