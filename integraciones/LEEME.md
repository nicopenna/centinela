# Centinela · Integración con Adminia y Unitify

Conecta Centinela con los dos únicos softwares de administración de consorcios que hoy publican una API: **Adminia Manager** (REST, solo lectura) y **Unitify** (GraphQL). El administrador sigue usando su sistema; Centinela lee de ahí edificios, unidades, gastos, liquidaciones, cobros y reclamos.

Ya funciona en **modo simulado** (datos de prueba que imitan cada API). Para pasar a datos reales solo falta poner las credenciales en `integraciones.ini`.

## Qué tenés que conseguir

| Para | Qué | Dónde | Costo |
|---|---|---|---|
| **Adminia** | Una **API key** (empieza con `am_live_`) | Cuenta del administrador en adminiamanager.com → *Configuración → Integraciones → API & Webhooks → Crear clave*. Darle solo permisos de lectura. Se muestra una sola vez: copiala ahí mismo. | Plan gratis: 1 clave y 1.000 pedidos por mes. Planes pagos sin límite mensual (60 por minuto). |
| **Unitify** | Un **usuario de servicio** (email y contraseña) con acceso a la organización | Pedirlo a Unitify (programa de partners en unitify.com/partners). La API requiere el **plan Pro**. | USD 3 por departamento por mes. |
| **Unitify** | La **URL de producción** de la API GraphQL | Preguntarle a Unitify. La que viene configurada es la del playground de prueba (`https://app.unitify.com/admin/api`). | |
| **Unitify** (opcional) | ID de la organización, para crear reclamos desde alarmas | Lo ve el usuario de servicio en el playground. | |
| **Unitify** (opcional) | Que habiliten la URL de webhooks de pago | Soporte de Unitify (la tienen que poner en lista blanca). | |

**Lo más rápido:** Alan, como administrador matriculado, puede abrir la cuenta gratis de Adminia, cargar un edificio de prueba y crear la API key. Con eso se prueba con datos reales en el día, sin pagar nada.

## Qué hace cada conector

**Adminia → Centinela** (lee, no escribe nada en Adminia)

| Adminia | Centinela |
|---|---|
| `GET /buildings` | Edificios (dirección, CUIT, período activo) |
| `GET /buildings/:id/units` | Unidades con titular, teléfono y porcentual. Si Adminia manda coeficientes (0,0585) se pasan a porcentaje (5,85). |
| `GET /buildings/:id/debt` | Deuda actual por unidad (`deudaExterna`) |
| `GET /buildings/:id/expenses` | Gastos del mes, clasificados en los rubros de Centinela, ordinarios o extraordinarios |
| `GET /buildings/:id/collections` | Pagos confirmados, asociados a su unidad |
| `GET /buildings/:id/liquidations` | Liquidaciones emitidas (el fondo de reserva ya viene como gasto, por eso no se suma dos veces) |

Una sincronización completa usa unos 6 pedidos por edificio. Con el plan gratis (1.000/mes) alcanza para unos 5 edificios sincronizados todos los días. El programa cuenta los pedidos del mes y avisa al pasar el 80 %.

**Unitify ↔ Centinela**

| Unitify | Centinela |
|---|---|
| Propiedades | Edificios |
| Contactos (unidad + nombre + teléfono) | Unidades. Unitify no maneja porcentuales: se cargan una vez en Centinela y la sincronización no los pisa. |
| Tickets | Reclamos, con estado traducido (nuevo / en curso / resuelto) y categoría (ascensor, agua, calefacción…) |
| Webhooks de pago | Se reciben, se verifica la firma y se guardan en `salida/unitify_pagos.jsonl` |
| **Escritura (opcional):** alarma de Centinela → crea un ticket en Unitify | Para que el administrador vea la falla en su propio sistema. Apagado por defecto. |

## Cómo se usa

Necesita Python 3.8 o más nuevo. No hay que instalar nada más.

```bash
cd integraciones
python3 sincronizar.py --simulado      # prueba con datos de ejemplo
python3 probar_conectores.py           # 18 pruebas con servidores falsos (sin internet)
python3 sincronizar.py --probar        # prueba las credenciales reales
python3 sincronizar.py                 # sincroniza de verdad
```

Después, en la app Centinela: **Operador → Ajustes → Software de administración → Elegir archivo** y elegís `salida/centinela_import.json`. Muestra qué trae y con un botón lo importa. Si un registro ya existe lo actualiza; nunca borra nada. Lo que se trabajó en Centinela (un reclamo con orden de trabajo, una liquidación emitida acá) no se pisa.

Los datos simulados entran marcados como ejemplo: se borran con *Ajustes → Borrar datos de ejemplo*.

### Que corra solo todas las mañanas

En la Raspberry o en el VPS (`crontab -e`):

```
15 6 * * * cd /home/pi/centinela/integraciones && python3 sincronizar.py >> salida/log.txt 2>&1
```

Si en `integraciones.ini` ponés el token del bot de Telegram (el mismo de la demo), avisa cuando entra un reclamo nuevo o una unidad pasa a deber.

### Otros comandos

```bash
python3 sincronizar.py --solo adminia
python3 sincronizar.py --explorar adminia      # guarda las respuestas crudas en salida/adminia_crudo.json
python3 sincronizar.py --webhooks              # escucha pagos de Unitify en el puerto 8090
python3 sincronizar.py --alarma "Bomba 2 sin presión" --propiedad <id Unitify> --unidad "Sala de máquinas"
```

## Lo que no está confirmado (y cómo se ajusta)

Ninguna de las dos empresas publica el detalle completo de su API:

- **Adminia** publica las rutas, pero no los nombres de campo de casi ninguna respuesta. El conector prueba varios nombres posibles (en inglés y castellano). Con la primera clave real, correr `--explorar adminia` y mandarme `salida/adminia_crudo.json`: en 10 minutos queda ajustado.
- **Unitify** publica el login, los webhooks de pago y la convención de nombres, pero no el esquema de edificios, contactos ni tickets. Las consultas están en `conectores/unitify_consultas.graphql`, separadas del programa: si un nombre cambia se corrige ahí, después de probarlo en el playground.

## Archivos

| Archivo | Qué es |
|---|---|
| `sincronizar.py` | Programa principal |
| `integraciones.ini` | Credenciales y opciones (no subirlo a Drive con claves puestas) |
| `conectores/adminia.py` | Conector Adminia y su mapeo |
| `conectores/unitify.py` | Conector Unitify, creación de reclamos y verificación de webhooks |
| `conectores/unitify_consultas.graphql` | Consultas GraphQL editables |
| `conectores/comun.py` | Formato de Centinela, rubros y categorías |
| `conectores/http.py` | Pedidos con reintentos y límite de ritmo |
| `probar_conectores.py` | Pruebas de punta a punta sin internet |
| `simulado/` | Respuestas de prueba de cada API (`generar_simulado.py` las rehace) |
| `salida/centinela_import_ejemplo.json` | Un archivo de importación ya generado, para probar el botón de la app |

## Además (recomendado en el relevamiento del 10/11)

Los software de consorcios son la fuente de datos administrativos, pero el circuito de cobro y aviso se arma con infraestructura que sí tiene API pública: **WhatsApp Cloud API** (avisos), **Mercado Pago** (cobro de expensas, cupones Rapipago/Pago Fácil) y **ARCA vía Afip SDK** (factura del abono). Se pueden sumar como conectores con esta misma estructura.
