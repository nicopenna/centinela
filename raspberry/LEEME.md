# Centinela · Demo en Raspberry Pi 3B+

Prototipo de **demostración** (no es el de producción, que usa ESP32 + LoRa). Mide el nivel de un tanque con un HC-SR04, la corriente de la bomba (SCT-013-030 + ADS1115) y su vibración (ADXL345). Detecta problemas y avisa por **Telegram**. Además muestra un **tablero web en vivo** en `http://IP-DE-LA-RASPBERRY:8080`.

Para el lunes solo se conecta el **HC-SR04**. Corriente y vibración quedan **simuladas** (se cambia con `modo = real` en `config.ini` cuando conectes esos sensores). La bomba simulada arranca y para siguiendo el nivel real del tanque, como un automático de flotante.

## Archivos

| Archivo | Qué es |
|---|---|
| `centinela_demo.py` | El programa (Python 3, sin librerías raras) |
| `config.ini` | Todo lo configurable: sitio, umbrales, sensores real/simulado. **Se sube a GitHub: sin secretos** |
| `config.local.ini.ejemplo` | Modelo de `config.local.ini`: token y chat_ids de Telegram y calibración de este equipo. `config.local.ini` **no se sube** y pisa a `config.ini` |
| `sistema.html` | **Sistema completo por rol** (DG, gerencia, supervisor, inspección, empresa, escuela, operador) con estética GCBA. Es la página principal: `http://IP:8080` |
| `tablero.html` | Tablero técnico en vivo del sensor (`/vivo`) |
| `analisis.html` | Tablero de **largo plazo** en `/analisis` (tendencias, pronóstico de vibración, consumo, uso de bomba, alertas) |
| `historial.py` | Guarda un resumen por hora (`data/resumen_horario.csv`) y los eventos (`data/eventos.csv`); genera historial simulado |
| `kiosco.sh` | Muestra Centinela a pantalla completa en un monitor HDMI al prender la Raspberry |
| `instalar.sh` | Instala dependencias, habilita I2C y deja el servicio arrancando solo |

---

## 1. Lista de materiales para el lunes

- Raspberry Pi 3B+ con fuente de 5 V 2,5 A y microSD con **Raspberry Pi OS** (Lite o Desktop)
- HC-SR04
- 1 resistencia de **1 kΩ** y 1 de **2 kΩ** (o dos de 1 kΩ en serie) para el divisor del ECHO
- 4 cables dupont hembra-hembra (más una protoboard chica si querés armar el divisor ahí)
- Un **balde o tacho** con agua para hacer de tanque, y algo para sostener el sensor mirando hacia abajo (una regla o un listón cruzado arriba)
- Celular con **hotspot** (la escuela puede no tener Wi-Fi abierto y Telegram necesita internet)

## 2. Cableado del HC-SR04

> ⚠️ El HC-SR04 se alimenta con 5 V y su pin **ECHO devuelve 5 V**. Los GPIO de la Raspberry aguantan **3,3 V como máximo**. Sin el divisor de tensión se puede quemar el GPIO.

| HC-SR04 | Raspberry Pi 3B+ (pin físico) |
|---|---|
| VCC | **Pin 2** (5 V) |
| TRIG | **Pin 16** (GPIO23), directo |
| ECHO | a través del divisor → **Pin 18** (GPIO24) |
| GND | **Pin 6** (GND) |

Divisor de tensión del ECHO (5 V × 2k / 3k = **3,33 V**):

```
 HC-SR04 ECHO ──[ 1 kΩ ]──┬──────► Pin 18 (GPIO24)
                          │
                       [ 2 kΩ ]
                          │
 HC-SR04 GND ─────────────┴──────► Pin 6 (GND)
```

Ubicación de los pines (vista con la Raspberry con los USB hacia abajo y el conector GPIO arriba a la derecha):

```
            3V3  (1) (2)  5V      ◄── VCC del HC-SR04
   SDA  GPIO2  (3) (4)  5V
   SCL  GPIO3  (5) (6)  GND     ◄── GND del HC-SR04
          GPIO4  (7) (8)  GPIO14
            GND  (9) (10) GPIO15
         GPIO17 (11) (12) GPIO18
         GPIO27 (13) (14) GND
         GPIO22 (15) (16) GPIO23  ◄── TRIG
            3V3 (17) (18) GPIO24  ◄── ECHO (por el divisor)
```

**Montaje:** el sensor mirando hacia abajo, centrado sobre el agua, a no menos de 3 cm de la superficie con el balde lleno (por debajo de ~2 cm no mide). Que no "vea" las paredes del balde.

## 3. Cableado para cuando conectes corriente y vibración (no hace falta para el lunes)

Ambos van al mismo bus I2C (comparten SDA y SCL).

**ADS1115** (dirección 0x48)

| ADS1115 | Raspberry |
|---|---|
| VDD | Pin 1 (3,3 V) |
| GND | Pin 9 (GND) |
| SCL | Pin 5 (GPIO3) |
| SDA | Pin 3 (GPIO2) |
| ADDR | GND |

**SCT-013-030** (sale 1 V eficaz a 30 A; ya trae la resistencia de carga): un cable del plug va a **A0** y el otro a **A1**. A A1 conectale además un punto medio de 1,65 V: dos resistencias de 10 kΩ en serie entre 3,3 V y GND, y un capacitor de 10 µF del punto medio a GND. El programa mide en diferencial A0-A1, a 860 muestras/s, y calcula el valor eficaz. La pinza abraza **un solo conductor** (una fase), nunca el cable completo. El trabajo dentro del tablero de 220/380 V lo hace un electricista.

**ADXL345** (dirección 0x53)

| ADXL345 | Raspberry |
|---|---|
| VCC | Pin 17 (3,3 V) |
| GND | Pin 14 (GND) |
| SDA | Pin 3 |
| SCL | Pin 5 |
| CS | 3,3 V (activa el modo I2C) |
| SDO | GND (dirección 0x53) |

Se atornilla o pega (epoxi o imán) a la carcasa del motor, cerca del rodamiento del lado de la bomba.

Para comprobar que la Raspberry los ve: `i2cdetect -y 1`. Tienen que aparecer **48** y **53**. Después cambiá `modo = real` en `[corriente]` y `[vibracion]` de `config.ini`.

---

## 4. Instalación (unos 15 minutos)

1. **Grabá la microSD** con *Raspberry Pi Imager*: Raspberry Pi OS (64-bit o 32-bit). En la ruedita de opciones poné hostname `centinela`, usuario y contraseña, **Wi-Fi del hotspot de tu celular**, y habilitá **SSH**.
2. Prendé la Raspberry y entrá desde la compu: `ssh tu_usuario@centinela.local`
3. Descomprimí `Centinela_demo_RPi.zip` en la compu (queda una carpeta `centinela`) y copiala a la Raspberry. Desde la compu, parado donde está esa carpeta:
   ```bash
   scp -r centinela tu_usuario@centinela.local:~/
   ```
   (O pasala con un pendrive a `/home/tu_usuario/centinela`.)
4. En la Raspberry:
   ```bash
   cd ~/centinela
   bash instalar.sh
   sudo reboot
   ```
5. Abrí en el navegador del celular o la notebook (en la misma red): **http://centinela.local:8080** (si no abre, usá la IP que mostró el instalador).

Prueba rápida sin ningún sensor conectado: `python3 centinela_demo.py --simular-todo` (primero pará el servicio con `sudo systemctl stop centinela`).

## 5. Crear el bot de Telegram

1. En Telegram buscá **@BotFather** → `/newbot` → poné un nombre (ej. *Centinela Escuelas*) y un usuario que termine en `bot` (ej. `centinela_demo_bot`).
2. BotFather te da un **token** del estilo `123456789:AAH...`. Pegalo en `config.local.ini` (la primera vez: `cp config.local.ini.ejemplo config.local.ini`), nunca en `config.ini`, que se sube a GitHub:
   ```ini
   [telegram]
   token = 123456789:AAH...
   ```
3. Abrí tu bot en Telegram y mandale cualquier mensaje (por ejemplo `/start`). Para que le llegue a varias personas, creá un grupo, agregá el bot y escribí algo en el grupo.
4. En la Raspberry:
   ```bash
   sudo systemctl stop centinela
   cd ~/centinela
   python3 centinela_demo.py --chat-id
   ```
   Te muestra el `chat_id` (el de un grupo empieza con `-`). Pegalo en `config.local.ini`:
   ```ini
   chat_ids = 123456789
   ```
   Para varios destinos, separalos con coma.
5. Probá y volvé a arrancar:
   ```bash
   python3 centinela_demo.py --probar-telegram
   sudo systemctl start centinela
   ```

**No compartas el token** (quien lo tenga puede manejar el bot). Si se filtra, en BotFather usá `/revoke`.

### Comandos que entiende el bot

| Comando | Qué hace |
|---|---|
| `/estado` | Nivel, litros, corriente, vibración, bomba y alertas activas |
| `/falla vibracion` | Rodamiento gastado: vibración ×4, da **aviso temprano** de comportamiento anómalo |
| `/falla rotura` | Rodamiento roto: vibración ×8, da **alarma crítica** |
| `/falla sobrecorriente` | Corriente +60 %, crítica |
| `/falla seco` | Marcha en seco: corriente −50 % y el tanque no sube |
| `/falla cavitacion` | 🧩 Falla no intuitiva: vibra más pero consume menos. Parece el rodamiento y no lo es |
| `/falla fuga` | Solo con nivel simulado: el tanque se vacía rápido |
| `/falla sensor` | Simula el sensor de nivel desconectado |
| `/normal` | Quita todas las fallas simuladas |

Cada `/falla` activa o quita esa falla, así que se pueden combinar varias.

Las fallas también se disparan con los botones del tablero web.

## 6. Calibrar el tanque (balde)

```bash
sudo systemctl stop centinela
python3 centinela_demo.py --probar-sensores
```
Anotá la distancia (`dist=`) con el balde **vacío** y con el balde **lleno**, y ponelas en `config.local.ini`:
```ini
[nivel]
distancia_vacio_cm = 38
distancia_lleno_cm = 6
capacidad_litros = 2000     # el número que quieras mostrar (tanque "de verdad")
```
Después: `sudo systemctl start centinela`.

## 7. Qué detecta y cuándo avisa

| Alerta | Gravedad | Cuándo |
|---|---|---|
| Nivel bajo | 🟠 Alerta | Nivel < 35 % |
| Nivel crítico | 🔴 Crítica | Nivel < 15 % |
| Descenso rápido | 🟠 Alerta | Baja más de 20 %/min (fuga, canilla abierta, consumo anormal) |
| Sensor de nivel sin lectura | 🟠 Alerta | 3 lecturas seguidas sin dato (cable suelto, sensor tapado) |
| Sobrecorriente | 🔴 Crítica | Corriente > 1,3 × nominal |
| Marcha en seco | 🔴 Crítica | Bomba encendida con corriente < 0,6 × nominal |
| Marcha excesiva | 🟠 Alerta | Bomba encendida más de 120 s seguidos (en un edificio real, 20-40 min) |
| Bomba que no llena | 🟠 Alerta | Bomba encendida 40 s y el nivel no sube 3 % |
| Vibración alta | 🔴 Crítica | Vibración eficaz > 0,25 g |
| Comportamiento anómalo | 🟡 Aviso | Vibración o corriente a más de 4 desvíos de lo **aprendido** como normal para esa bomba |

Cada alerta avisa **una vez** al activarse, vuelve a avisar cada 30 min si sigue activa y manda un 🟢 *Normalizado* cuando se resuelve. Todo se ajusta en `config.ini`. Cada muestra queda guardada en `data/registro_AAAA-MM-DD.csv`.

> Los umbrales de la demo están acortados para que se vea todo en pocos minutos. En una instalación real se calibran con la chapa del motor y con el historial de cada equipo.

## 8. Guion de la demo para el director (10 minutos)

**Qué hay en pantalla.** `http://centinela.local:8080` abre el **sistema completo por rol**: Director General, Gerente, Supervisor, Inspectora, Empresa contratista, Escuela y Operador. Arriba se elige con qué rol se entra. Todo está simulado salvo el **tanque de la Esc. Primaria N° 12**, que es el balde con el HC-SR04. El cuadro amarillo "En vivo" de cada inicio muestra ese sensor. El botón **"Simular situaciones ▾"** abre el panel de fallas, y cada alerta simulada también llega a Telegram (se puede apagar en el mismo panel).

**Antes de empezar:** Raspberry con el hotspot del celular, historial de 30 días cargado (sección 9), balde lleno y Telegram abierto en un celular. Tocá **Reiniciar** para arrancar limpio.

1. **Director General → Inicio.** Cuántas escuelas funcionan normal, emergencias y escaladas. Mostrale que solo le llega lo grave o lo que nadie atendió.
2. **El sensor real.** Sacá agua del balde. En el cuadro "En vivo" baja el nivel, llega el aviso a Telegram y aparece la alerta de la Esc. N° 12 con la etiqueta *Sensor real*.
3. **Cadena de mando.** Entrá como **Empresa** y marcá "En camino". Pasá a **Inspectora**: emite la orden de servicio. Pasá a **Escuela**: ve en lenguaje simple "Hay agua / queda poca" y da conformidad. Con **+2 h** se ve cómo escala al supervisor si nadie actúa.
4. **Varias fallas a la vez.** En el panel, **"Lunes complicado"** dispara un ascensor con personas, una calefacción apagada, consumo de agua de madrugada y cavitación en la bomba de la N° 12. Volvé a Director: cambia todo el tablero.
5. **Las fallas no intuitivas (🧩)**, que son el diferencial:
   - **Cavitación (bomba de la N° 12):** vibra más pero consume **menos** corriente. Cualquiera cambiaría el rodamiento, y no es eso: le falta agua en la succión.
   - **Consumo de madrugada (N° 18):** 1.150 L de noche con la escuela cerrada. Es una mochila que pierde o la válvula de retención. Nadie lo ve y se paga en la factura.
   - **Caldera que prende y apaga (Media N° 9):** las aulas están calientes y nadie se queja, pero el quemador se gasta 5 veces más rápido.
   - **Bomba que trabaja cada vez más horas (Técnica N° 21):** no hay alarma, solo la tendencia lo muestra.
6. **Largo plazo.** Desde la Esc. N° 12, el botón **📈 Tendencias** abre `/analisis`: pronóstico de rotura del rodamiento en días, consumo por día, fuga nocturna y cuándo se usa el agua.
7. **Control del contrato.** Pestañas **Certificación** (multa 1/10.000 por día de atraso), **Preventivo** y **Auditoría** (registro que nadie puede borrar).
8. **Sensor que se cuida solo.** Desenchufá el cable de ECHO: llega *Sensor de nivel sin señal* al operador. Volvé a enchufarlo y llenar el balde para mostrar los avisos de normalizado.

## 9. Tablero de análisis de largo plazo

Está en **http://centinela.local:8080/analisis**. También hay un botón en el tablero en vivo. Se puede abrir `analisis.html` directo desde la PC con doble clic: arriba se escribe la dirección de la Raspberry (por ejemplo `http://192.168.0.50:8080`).

Muestra, para 24 h, 7, 30 o 90 días:
- **Diagnóstico en una frase** por tema: autonomía del tanque en horas de clase, desgaste de rodamientos con **días estimados hasta la alarma**, rendimiento de la bomba (si llena más lento que antes) y consumo de agua con detección de **consumo nocturno anormal** (fugas).
- Nivel del tanque hora por hora, tendencia y pronóstico de vibración, litros por día, mapa de calor de *cuándo se usa el agua*, minutos de marcha y arranques de la bomba, corriente del motor contra su zona normal, y alertas por día con el registro de eventos.

Centinela guarda una fila por hora, así que un año entero ocupa menos de 1 MB.

**Para la demo del lunes** todavía no hay semanas de datos reales. Por eso se puede cargar un **historial simulado** de 30 días: una escuela con consumo en horario escolar, una fuga una madrugada, un corte del sensor y un rodamiento que se va gastando en las últimas dos semanas.
```bash
sudo systemctl stop centinela
python3 centinela_demo.py --generar-historial 30
sudo systemctl start centinela
```
El tablero muestra un cartel amarillo que dice que el período incluye datos simulados. Las mediciones reales se van sumando encima. Para empezar de cero solo con datos reales: `python3 centinela_demo.py --borrar-historial`.

## 10. Verlo en un monitor por HDMI (modo kiosco)

Con un monitor o TV conectado por HDMI, la Raspberry puede abrir Centinela sola, a pantalla completa, cada vez que se prende:

```bash
cd ~/centinela
bash kiosco.sh instalar     # instala Chromium si falta, habilita el escritorio con inicio automático
bash kiosco.sh relieves     # muestra qué arranca solo (para ubicar la caja de arena)
bash kiosco.sh relieves-off # opcional: que la caja de arena no arranque sola (se vuelve con relieves-on)
sudo reboot
```

- Con un mouse conectado se cambia de rol y se usan los botones igual que en la compu.
- Para salir del kiosco: **Alt+F4**. Para volver a abrirlo sin reiniciar: `bash kiosco.sh abrir`, desde una terminal del escritorio.
- Para dejar todo como antes: `bash kiosco.sh quitar` y `bash kiosco.sh relieves-on`.
- La Raspberry 3B+ tiene 1 GB de RAM: con Chromium y Centinela juntos anda bien, pero conviene que la caja de arena no corra al mismo tiempo.

## 11. Problemas comunes

| Síntoma | Solución |
|---|---|
| Nivel "sin dato" | Revisá el divisor y que ECHO vaya al pin 18 y TRIG al 16. Que el sensor no esté a menos de 3 cm del agua. |
| El nivel salta mucho | Superficie con olas o el sensor ve la pared del balde: centralo y alejalo un poco. |
| No llegan mensajes | Corré `python3 centinela_demo.py --probar-telegram`. Revisá el token, el chat_id y que haya internet. |
| "Este chat no está autorizado" | Ese chat_id no está en `config.local.ini`. Agregalo (separado por coma). |
| El tablero no abre | Usá la IP en lugar de `centinela.local`. Mirá los errores con `journalctl -u centinela -f`. |
| GPIO ocupado o "puerto ocupado" al probar | Primero `sudo systemctl stop centinela`. |
