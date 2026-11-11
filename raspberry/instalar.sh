#!/usr/bin/env bash
# Centinela demo - instalador para Raspberry Pi OS (Bookworm o Bullseye)
# Uso:  cd ~/centinela && bash instalar.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
USUARIO="$(whoami)"
echo "==> Instalando dependencias (puede tardar unos minutos)..."
sudo apt-get update
sudo apt-get install -y python3 python3-gpiozero python3-smbus i2c-tools
# lgpio es el backend de GPIO en Bookworm; en Bullseye no existe y no hace falta
sudo apt-get install -y python3-lgpio 2>/dev/null || true

echo "==> Habilitando I2C (para ADS1115 y ADXL345)..."
sudo raspi-config nonint do_i2c 0 || echo "   (no pude habilitar I2C solo: hacelo con sudo raspi-config > Interface Options > I2C)"
sudo usermod -aG gpio,i2c "$USUARIO" || true

echo "==> Creando el servicio centinela (arranca solo al prender la Raspberry)..."
sudo tee /etc/systemd/system/centinela.service >/dev/null <<UNIT
[Unit]
Description=Centinela demo - alerta temprana
After=network-online.target
Wants=network-online.target

[Service]
User=$USUARIO
WorkingDirectory=$DIR
ExecStart=/usr/bin/python3 $DIR/centinela_demo.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable centinela
sudo systemctl restart centinela
sleep 3
IP="$(hostname -I | awk '{print $1}')"
echo
echo "==> Listo. Tablero: http://$IP:8080"
echo "    Ver lo que hace:   journalctl -u centinela -f"
echo "    Reiniciar:         sudo systemctl restart centinela"
echo "    Si es la primera vez que habilitás I2C, reiniciá la Raspberry: sudo reboot"
