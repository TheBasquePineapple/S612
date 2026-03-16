# Seeds — Sistema de carga de datos para RAISA

Este directorio contiene los datos maestros del juego en formato JSON.
Son la fuente de verdad para poblar la base de datos sin tener que
insertar filas manualmente en SQLite.

---

## Estructura

```
seeds/
├── items/
│   ├── armas.json                — Armas primarias, secundarias y terciarias
│   ├── municion.json             — Cargadores, granadas y bengalas
│   ├── protecciones_y_equipo.json — Chalecos, placas, cascos, uniformidad, pouches, accesorios
│   └── medico_y_misc.json        — Ítems médicos, radios y misceláneos
├── vehiculos/
│   ├── terrestres.json           — Coches, furgonetas, blindados y MBT
│   └── aereos_y_naval.json       — Helicópteros, aviones y naval (reservado)
└── tienda/
    └── catalogo.json             — Ítems disponibles en la tienda con precio y stock
```

---

## Cómo usar

### 1. Inicializar la base de datos (solo la primera vez)

```bash
python tools/migrate.py init
```

### 2. Cargar todos los datos

```bash
python tools/migrate.py seed all
```

### 3. Cargar solo una categoría

```bash
python tools/migrate.py seed items
python tools/migrate.py seed vehiculos
python tools/migrate.py seed tienda   # requiere que items ya estén cargados
```

### 4. Simular sin escribir (dry-run)

```bash
python tools/migrate.py seed all --dry-run
```

### 5. Ver estado actual de la BBDD

```bash
python tools/migrate.py status
```

### 6. Especificar una BBDD diferente

```bash
python tools/migrate.py --db /ruta/otra.db seed all
```

---

## Cómo añadir nuevos ítems

1. Abre el archivo JSON correspondiente a la categoría del ítem.
2. Añade un nuevo objeto al array siguiendo la estructura existente.
3. Ejecuta `python tools/migrate.py seed items` — el script hace **upsert**
   (inserta si no existe, actualiza si ya existe por nombre+categoría).
   Nunca crea duplicados.

### Estructura mínima de un ítem

```json
{
  "nombre":       "Nombre único del ítem",
  "descripcion":  "Descripción narrativa",
  "categoria":    "arma|proteccion|uniforme|accesorio|medico|municion|radio|pouch|misc",
  "subcategoria": "primaria|secundaria|chaleco|portaplacas|etc.",
  "peso_kg":      1.5,
  "volumen_u":    3,
  "precio_base":  250.00
}
```

### Campos opcionales por tipo

| Campo               | Aplica a                       | Descripción                              |
|---------------------|--------------------------------|------------------------------------------|
| `calibre`           | arma, munición/cargador        | `"5.56x45"`, `"9x19"`, etc.              |
| `tipo_arma`         | arma                           | `rifle_asalto`, `pistola`, etc.          |
| `id_compatibilidad` | munición/cargador              | Clave de compatibilidad con arma         |
| `capacidad_cargador`| munición/cargador              | Número de rondas                         |
| `slots_pouches`     | chaleco, portaplacas, soporte  | Slots MOLLE disponibles                  |
| `nivel_proteccion`  | chaleco, placas, casco         | Nivel numérico NIJ (2, 3, 4)             |
| `tipo_pouch`        | pouch                          | `simple`, `dual`, `doble`                |
| `slots_ocupa`       | pouch                          | Cuántos slots del chaleco consume (1 o 2)|
| `capacidad_pouch`   | pouch                          | Cuántos ítems almacena el pouch          |

---

## Cómo añadir nuevos vehículos

1. Abre `seeds/vehiculos/terrestres.json` o `aereos_y_naval.json`.
2. Añade un objeto al array con la estructura de ejemplo.
3. Ejecuta `python tools/migrate.py seed vehiculos`.

### Campos clave de vehículo

| Campo                     | Tipo    | Descripción                                          |
|---------------------------|---------|------------------------------------------------------|
| `nombre`                  | string  | Nombre único del vehículo                            |
| `tipo`                    | string  | Ver tipos válidos abajo                              |
| `asientos`                | int     | Número de plazas                                     |
| `combustible_max`         | float   | Litros de combustible máximo                         |
| `consumo_por_km`          | float   | Litros por kilómetro                                 |
| `inv_peso_max_kg`         | float   | Capacidad de carga en kg                             |
| `componentes`             | object  | Estado inicial de cada componente                    |
| `municion_json`           | object  | Munición inicial por nombre de arma                  |
| `hardpoints_json`         | array   | Slots de hardpoint (solo aéreos de combate)          |
| `permite_transferencia_mun` | 0/1  | `1` = terrestre o helicóptero transporte             |
| `artillado`               | 0/1    | `1` si tiene armas integradas                        |

**Tipos válidos:** `coche`, `furgoneta`, `blindado_ligero`, `blindado_pesado`,
`mbt`, `helicoptero_transporte`, `helicoptero_ataque`, `avion_transporte`,
`avion_combate`, `naval` (reservado)

---

## Claves de compatibilidad de munición (`id_compatibilidad`)

> ⚠️ **CRÍTICO**: Este campo es **independiente** del ID de ítem.
> Dos cargadores distintos pueden tener el mismo `id_compatibilidad`
> si son intercambiables con el mismo arma.
>
> La validación de recarga comprueba **ambas** condiciones:
> 1. `calibre` del cargador == `calibre` del arma
> 2. `id_compatibilidad` del cargador == `id_compatibilidad` del arma
>
> Si cualquiera falla → acción cancelada.

### Claves actuales

| id_compatibilidad     | Arma(s) compatibles              |
|-----------------------|----------------------------------|
| `STANAG_556`          | M4A1, HK416 A5                   |
| `AK74_545`            | AK-74M                           |
| `SCARH_762`           | SCAR-H                           |
| `MCX_SPEAR_68`        | SIG MCX Spear                    |
| `9MM_ESTANDAR`        | Glock 17, SIG P226, Beretta M9A3 |
| `DE_50AE`             | Desert Eagle .50AE               |
| `M249_CINTA_556`      | M249 SAW                         |
| `AXMC_338LM`          | AI AXMC                          |
| `M82_50BMG`           | Barrett M82A1                    |
| `12GA_ESTANDAR`       | Mossberg 590A1                   |
| `SENALIZADORA_265`    | Pistola señalizadora 26.5mm      |

---

## Notas editoriales en JSON

Los campos cuya clave empieza por `_` son ignorados por el migrador:

```json
{ "_comentario": "Este texto es ignorado", "_seccion": "=== ARMAS ===" }
```

Úsalos para organizar el JSON sin afectar la importación.