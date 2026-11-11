# Instrucciones para Claude: instalar la demo Centinela en la Raspberry Pi de Nico

> Guardá este archivo como `CLAUDE.md` en la carpeta donde abrís Claude Code en PowerShell, al lado de `Centinela_demo_RPi.zip`. Claude lo lee solo al arrancar. También podés decirle: "leé CLAUDE_raspberry.md y seguilo".

## Contexto

- Estás en la PC de **Nico**, con Windows y PowerShell. Respondé siempre en **español rioplatense**, en frases cortas.
- Nico tiene una **Raspberry Pi 3B+** que **ya está conectada a la misma red** que esta PC. No sabemos su IP, su usuario ni qué sistema operativo tiene. Sospecha que tiene el escritorio (XFCE) deshabilitado. Eso no es problema: la demo corre sin escritorio.
- El objetivo es dejar andando la **demo de Centinela** para mostrarla el **lunes 16/11** en una reunión con el área de mantenimiento de escuelas del GCBA. La demo es un sistema de alerta temprana que:
  - mide el nivel de un tanque con un **HC-SR04** (el único sensor conectado de verdad; TRIG en GPIO23 = pin 16, ECHO en GPIO24 = pin 18, **a través de un divisor 1 kΩ / 2 kΩ** porque el ECHO sale con 5 V);
  - simula la corriente de la bomba (SCT-013 + ADS1115) y su vibración (ADXL345);
  - avisa por **Telegram** y muestra un **tablero web** en `http://IP:8080`.
- Los archivos vienen en `Centinela_demo_RPi.zip`, que al descomprimirse deja una carpeta `centinela/` con:

| Archivo | Para qué |
|---|---|
| `centinela_demo.py` | Programa principal (Python 3 con la biblioteca estándar, más gpiozero y smbus) |
| `config.ini` | Sitio, umbrales y modo `real` o `simulado` de cada sensor (se sube a GitHub, sin secretos) |
| `config.local.ini` | Token, chat_ids y calibración de esta Raspberry. Pisa a `config.ini` y no se sube |
| `sistema.html` | Sistema completo por rol (página principal `/`), con la Esc. N° 12 conectada al sensor real y el panel "Simular situaciones" |
| `tablero.html` | Tablero técnico en vivo (`/vivo`) |
| `analisis.html` + `historial.py` | Tablero de largo plazo (`/analisis`) y el registro por hora en `data/` |
| `instalar.sh` | Instala dependencias con apt, habilita I2C y crea el servicio systemd `centinela` |
| `LEEME.md` | Guía para humanos: cableado, Telegram, calibración y guion de la demo. **Leela entera antes de empezar.** |

- Opciones del programa: `--simular-todo` (sin hardware), `--probar-sensores` (10 lecturas), `--chat-id` (muestra el chat_id de Telegram), `--probar-telegram`.

## Reglas

1. **No pierdas el acceso a la Raspberry.** No toques la configuración de Wi-Fi ni de red que está en uso, ni SSH, el firewall o sudoers. Tampoco cambies la contraseña.
2. **Pedile permiso a Nico** antes de reiniciar, borrar algo fuera de `~/centinela`, actualizar el sistema completo (`apt upgrade`) o habilitar o deshabilitar el escritorio.
3. **El token del bot es secreto.** No lo muestres en pantalla, no lo pongas en comandos que queden en el historial y no lo escribas en otros archivos. Para cargarlo, editá `config.local.ini` en la Raspberry. Nunca lo pongas en `config.ini`, que se sube a GitHub.
4. **El cableado lo hace Nico.** Antes de probar el sensor, recordale el divisor del ECHO: un GPIO de la Raspberry a 5 V se quema.
5. No inventes resultados. Si algo falla, mostrá el error y explicá qué vas a probar.
6. SSH desde tu herramienta no es interactivo y no puede escribir contraseñas. Usá siempre `ssh -o BatchMode=yes -o ConnectTimeout=8 usuario@IP "comando"`. Si pide contraseña, seguí el paso 2.

## Paso 0: preguntarle a Nico

- Usuario de la Raspberry (por defecto suele ser `pi` o el que eligió en Raspberry Pi Imager) y, si la sabe, la IP o el hostname.
- Dónde está `Centinela_demo_RPi.zip` en esta PC.

## Paso 1: encontrar la Raspberry en la red

```powershell
ping -4 -n 1 centinela.local
ping -4 -n 1 raspberrypi.local
# Las Raspberry Pi 3B+ tienen una MAC que empieza con b8-27-eb
arp -a | Select-String "b8-27-eb"
```

Si no aparece, averiguá la red local con `ipconfig` (por ejemplo 192.168.0.x) y barré la red (PowerShell 7):

```powershell
$red = "192.168.0"   # ajustar según ipconfig
1..254 | ForEach-Object -Parallel { if (Test-Connection "$using:red.$_" -Count 1 -TimeoutSeconds 1 -Quiet) { "$using:red.$_" } } -ThrottleLimit 64
arp -a | Select-String "b8-27-eb"
```

Para confirmar que es la Raspberry, probá el puerto de SSH: `Test-NetConnection IP -Port 22`. Si el 22 está cerrado, SSH está deshabilitado. En ese caso Nico tiene que habilitarlo con teclado y monitor (`sudo raspi-config` → Interface Options → SSH, o `sudo systemctl enable --now ssh`).

## Paso 2: acceso por SSH sin contraseña (una sola vez)

Generá la clave en la PC si no existe:

```powershell
if (-not (Test-Path "$env:USERPROFILE\.ssh\id_ed25519")) { ssh-keygen -t ed25519 -N '""' -f "$env:USERPROFILE\.ssh\id_ed25519" }
```

**Nico** tiene que correr esto en su propia ventana de PowerShell, porque pide la contraseña de la Raspberry:

```powershell
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh USUARIO@IP "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

Después comprobá: `ssh -o BatchMode=yes USUARIO@IP "echo ok"`.

Comprobá también si sudo funciona sin contraseña: `ssh -o BatchMode=yes USUARIO@IP "sudo -n true && echo sudo-ok"`. En Raspberry Pi OS suele funcionar. Si no, los comandos con `sudo` los corre Nico a mano y vos le pasás exactamente qué pegar.

## Paso 3: diagnóstico del sistema

```powershell
ssh -o BatchMode=yes USUARIO@IP "cat /etc/os-release | head -4; uname -m; python3 --version; systemctl get-default; free -h | head -2; df -h / | tail -1; ls /dev/gpiochip* /dev/i2c-* 2>/dev/null; which raspi-config; hostname -I"
```

Contale a Nico qué sistema tiene, en un párrafo. Así se interpreta:

- `systemctl get-default` = `multi-user.target` significa sin escritorio. Está bien así: el tablero se mira desde otra máquina. No lo cambies salvo que Nico lo pida (`sudo systemctl set-default graphical.target` y reiniciar).
- **Raspberry Pi OS (Debian bookworm o bullseye):** `instalar.sh` sirve tal cual.
- **Ubuntu, DietPi u otro:** puede faltar `raspi-config`. En ese caso habilitá I2C agregando `dtparam=i2c_arm=on` en `/boot/firmware/config.txt` (o `/boot/config.txt`) y cargá `i2c-dev` en `/etc/modules`. Para el lunes I2C no hace falta: solo se usa el HC-SR04 por GPIO. Si `python3-gpiozero` o `python3-lgpio` no existen en apt, probá `sudo pip3 install --break-system-packages gpiozero lgpio smbus2`, o `RPi.GPIO` en sistemas viejos.
- Si queda menos de 300 MB libres en `/`, avisale a Nico.

## Paso 4: copiar los archivos

```powershell
Expand-Archive -Force .\Centinela_demo_RPi.zip -DestinationPath .\demo
scp -o BatchMode=yes -r .\demo\centinela USUARIO@IP:~/
# Por si alguna herramienta de Windows metió fines de línea CRLF:
ssh -o BatchMode=yes USUARIO@IP "cd ~/centinela && sed -i 's/\r$//' *.sh *.py *.ini && chmod +x instalar.sh centinela_demo.py && ls -la"
```

Si en algún momento editás archivos en Windows, volvé a correr el `sed` después de copiarlos.

## Paso 5: instalar

```powershell
ssh -o BatchMode=yes USUARIO@IP "cd ~/centinela && bash instalar.sh"
```

Tarda unos minutos. Al terminar queda el servicio `centinela` activo y arrancando solo. Si es la primera vez que se habilita I2C, hace falta reiniciar: **pedile permiso a Nico** y avisale que vas a perder la conexión durante un minuto.

Verificación:

```powershell
ssh -o BatchMode=yes USUARIO@IP "systemctl is-active centinela; journalctl -u centinela -n 30 --no-pager; curl -s localhost:8080/api/estado | head -c 400"
```

Después pasale a Nico los links: `http://IP:8080` (sistema completo por rol, página principal), `http://IP:8080/vivo` (tablero técnico del sensor) y `http://IP:8080/analisis` (largo plazo).

## Paso 6: prueba sin sensores

Mientras Nico no haya cableado el HC-SR04, el programa intenta leerlo, no lo encuentra y pasa a modo simulado solo. Para una prueba completa, con todo simulado:

```powershell
ssh -o BatchMode=yes USUARIO@IP "sudo systemctl stop centinela; cd ~/centinela && timeout 25 python3 centinela_demo.py --simular-todo; sudo systemctl start centinela"
```

## Paso 7: sensor de nivel real (después de que Nico lo cablee)

1. Pedile a Nico que confirme que puso el divisor del ECHO.
2. Leé el sensor:
   ```powershell
   ssh -o BatchMode=yes USUARIO@IP "sudo systemctl stop centinela; cd ~/centinela && python3 centinela_demo.py --probar-sensores; sudo systemctl start centinela"
   ```
   Si `dist` sale `None`, revisá con Nico el cableado (TRIG en el pin 16, ECHO en el 18 por el divisor, VCC al pin 2, GND al pin 6) y que el sensor esté a más de 3 cm del agua.
3. Calibrá: pedile a Nico que deje el balde **vacío** y medí, después **lleno** y medí. Guardá los dos valores en `config.local.ini`, en `[nivel] distancia_vacio_cm` y `distancia_lleno_cm`, y reiniciá con `sudo systemctl restart centinela`.

## Paso 8: Telegram

1. Nico crea el bot con @BotFather (`/newbot`) y te pasa el token, o lo pega él en `config.local.ini`.
2. Para cargarlo vos sin que quede a la vista, usá `cd ~/centinela && [ -f config.local.ini ] || cp config.local.ini.ejemplo config.local.ini; sed -i "s|^token =.*|token = TOKEN|" config.local.ini`. Después no lo repitas en tus respuestas.
3. Nico le escribe `/start` al bot (o lo agrega a un grupo y escribe algo). Entonces corré:
   ```powershell
   ssh -o BatchMode=yes USUARIO@IP "sudo systemctl stop centinela; cd ~/centinela && timeout 120 python3 centinela_demo.py --chat-id"
   ```
   Poné el número que te devuelve en `chat_ids =`. Si son varios, van separados por coma. Los grupos empiezan con `-`.
4. Probá con `python3 centinela_demo.py --probar-telegram` y después `sudo systemctl start centinela`. Al arrancar, el servicio manda "Centinela iniciado", y el bot responde a `/estado`.

## Paso 9: preparar la red del lunes (hotspot del celular)

En la escuela no va a estar esta red. Para que la Raspberry se conecte sola al hotspot del celular de Nico, **agregá** una conexión sin desconectar la actual. Esto funciona en sistemas con NetworkManager (bookworm):

```bash
sudo nmcli connection add type wifi con-name hotspot-nico ifname wlan0 ssid "NOMBRE_HOTSPOT" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "CLAVE" connection.autoconnect yes connection.autoconnect-priority 10
```

No uses `nmcli dev wifi connect`, porque cortaría la conexión actual. En bullseye (wpa_supplicant) se agrega un bloque `network={...}` en `/etc/wpa_supplicant/wpa_supplicant.conf`, siempre sin borrar el que ya existe. Para ver la IP en la escuela: en el celular, en la lista de dispositivos del hotspot, o probando `http://centinela.local:8080` si el hostname es `centinela`.

## Paso 9 bis: tablero de largo plazo

Para el lunes, cargá el historial simulado de 30 días: `sudo systemctl stop centinela; cd ~/centinela && python3 centinela_demo.py --generar-historial 30; sudo systemctl start centinela`. Comprobá que responde con `curl -s localhost:8080/api/historico?dias=30 | head -c 300` y pasale a Nico el link `http://IP:8080/analisis`. El tablero avisa que el historial es simulado: no lo ocultes.

## Paso 9 ter: monitor HDMI (modo kiosco)

Si Nico quiere ver Centinela en el monitor conectado a la Raspberry, corré `cd ~/centinela && bash kiosco.sh instalar`. Ese comando habilita el escritorio con inicio automático, y antes de correrlo **avisale a Nico**, porque cambia cómo arranca la Raspberry. Después corré `bash kiosco.sh relieves` y mostrale qué arranca solo. Usá `bash kiosco.sh relieves-off` **solo si Nico te lo pide**: desactiva la caja de arena de relieves, se revierte con `relieves-on` y guarda una copia. Para reiniciar (`sudo reboot`), pedile permiso.

## Paso 10: cierre

Dale a Nico un resumen de cinco o seis líneas: qué sistema tiene, IP y usuario, si el servicio está activo, el link del tablero, el estado de Telegram y qué le falta (cableado, calibración, hotspot). El guion de la demo está en `LEEME.md`, sección 8.

## Comandos útiles

| Para | Comando (en la Raspberry) |
|---|---|
| Ver lo que hace en vivo | `journalctl -u centinela -f` |
| Reiniciar después de cambiar `config.ini` | `sudo systemctl restart centinela` |
| Parar o arrancar | `sudo systemctl stop centinela` / `start` |
| Registro de mediciones | `~/centinela/data/registro_AAAA-MM-DD.csv` |
| Ver los dispositivos I2C (si se conectan ADS1115 y ADXL345) | `i2cdetect -y 1` (deben aparecer 48 y 53) |
| Simular fallas | Botones del tablero o `/falla vibracion`, `rotura`, `sobrecorriente`, `seco`, `fuga`, `sensor`, `/normal` en Telegram |
