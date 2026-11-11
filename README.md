# Centinela

Sistema de alerta temprana para el mantenimiento preventivo de edificios: calderas, termotanques, bombas de agua, ascensores y tanques. Los sensores detectan las fallas antes de que pasen, avisan por Telegram y cada responsable ve lo que le toca en su pantalla.

> Repositorio **privado**. Proyecto de Nico (hardware y electrónica) y Alan (ingeniería y comercial).

## Qué hay

| Carpeta | Qué es |
|---|---|
| [`raspberry/`](raspberry/) | **Demo en Raspberry Pi 3B+**: nivel de tanque real (HC-SR04), corriente y vibración de bomba simuladas, alertas por Telegram, sistema completo por rol (estética GCBA), tablero técnico en vivo, análisis de largo plazo y modo kiosco para un monitor HDMI. Guía: [`raspberry/LEEME.md`](raspberry/LEEME.md) |
| [`integraciones/`](integraciones/) | Conectores con software de administración de consorcios (Adminia, Unitify). Guía: [`integraciones/LEEME.md`](integraciones/LEEME.md) |
| [`maquetas/`](maquetas/) | Maquetas HTML de la app (cartera, usuarios por rol, app con expensas y órdenes de trabajo, escuelas GCBA). Algunas se publicaron como Artifacts de Claude y guardan los datos ahí, así que fuera de Claude se ven con datos de ejemplo o sin guardar |
| [`docs/`](docs/) | Capturas e instructivos |

## Arranque rápido (Raspberry)

```bash
git clone git@github-centinela:USUARIO/centinela.git ~/centinela-repo
cd ~/centinela-repo/raspberry
cp config.local.ini.ejemplo config.local.ini   # token de Telegram y calibración (no se sube)
bash instalar.sh
bash kiosco.sh instalar                        # opcional: pantalla completa por HDMI
```

- Sistema por rol: `http://IP:8080`
- Tablero técnico en vivo: `http://IP:8080/vivo`
- Análisis de largo plazo: `http://IP:8080/analisis`

Para actualizar: `cd ~/centinela-repo && git pull && sudo systemctl restart centinela`.

## Secretos

**Nunca** subas tokens ni contraseñas. El token de Telegram, los chat_ids y la calibración van en `raspberry/config.local.ini`. Las claves de Adminia y Unitify se pasan por variables de entorno (`ADMINIA_API_KEY`, `UNITIFY_EMAIL`, `UNITIFY_PASSWORD`, `UNITIFY_WEBHOOK_SECRET`). El `.gitignore` ya deja afuera `*.local.ini`, `.env` y los datos medidos.
