#!/usr/bin/env bash
# =====================================================================
#  Centinela - modo kiosco por HDMI
#  Al prender la Raspberry abre el sistema a pantalla completa en el monitor.
#
#  Uso (en la Raspberry, desde ~/centinela):
#    bash kiosco.sh instalar        # deja el kiosco arrancando solo
#    bash kiosco.sh abrir           # lo abre ahora (desde el escritorio)
#    bash kiosco.sh quitar          # vuelve todo como estaba
#    bash kiosco.sh relieves        # muestra qué programas arrancan solos (caja de arena, etc.)
#    bash kiosco.sh relieves-off    # que la caja de arena NO arranque sola (guarda copia)
#    bash kiosco.sh relieves-on     # la vuelve a dejar como estaba
#
#  Para salir del kiosco con el teclado: Alt+F4 (o Ctrl+W).
# =====================================================================
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
URL="${CENTINELA_URL:-http://localhost:8080/}"
AUTO="$HOME/.config/autostart"
BAK="$HOME/.config/centinela-autostart-desactivados"
DESK="$AUTO/centinela-kiosco.desktop"

navegador() {
  for b in chromium-browser chromium firefox-esr firefox; do
    command -v "$b" >/dev/null 2>&1 && { echo "$b"; return; }
  done
}

abrir() {
  export DISPLAY="${DISPLAY:-:0}"
  # espera a que el servicio centinela responda (hasta 90 s)
  for i in $(seq 1 45); do curl -fs -o /dev/null "$URL" && break; sleep 2; done
  # que no se apague la pantalla (X11; en Wayland no hace falta o no aplica)
  xset s off -dpms s noblank 2>/dev/null || true
  command -v unclutter >/dev/null && (unclutter -idle 3 >/dev/null 2>&1 &) || true
  B="$(navegador)"
  [ -z "$B" ] && { echo "No hay navegador instalado: corré 'bash kiosco.sh instalar'"; exit 1; }
  case "$B" in
    chromium*)
      # limpia el aviso de "Chromium no se cerró correctamente"
      for f in "$HOME/.config/chromium/Default/Preferences" "$HOME/.config/chromium-browser/Default/Preferences"; do
        [ -f "$f" ] && sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[^"]*"/"exit_type":"Normal"/' "$f"
      done
      exec "$B" --kiosk --noerrdialogs --disable-infobars --no-first-run --disable-session-crashed-bubble \
        --disable-translate --check-for-update-interval=31536000 --password-store=basic \
        --ozone-platform-hint=auto "$URL" ;;
    firefox*) exec "$B" --kiosk "$URL" ;;
  esac
}

instalar() {
  echo "==> Instalando navegador y utilidades (si faltan)..."
  if [ -z "$(navegador)" ]; then
    sudo apt-get update
    sudo apt-get install -y chromium-browser 2>/dev/null || sudo apt-get install -y chromium || sudo apt-get install -y firefox-esr
  fi
  sudo apt-get install -y unclutter curl 2>/dev/null || true

  echo "==> Arranque automático del kiosco al entrar al escritorio..."
  mkdir -p "$AUTO"
  cat > "$DESK" <<EOF
[Desktop Entry]
Type=Application
Name=Centinela kiosco
Comment=Abre Centinela a pantalla completa
Exec=bash $DIR/kiosco.sh abrir
X-GNOME-Autostart-enabled=true
EOF
  # labwc (Raspberry Pi OS Bookworm con Wayland) usa su propio autostart
  if [ -d "$HOME/.config/labwc" ] || command -v labwc >/dev/null 2>&1; then
    mkdir -p "$HOME/.config/labwc"
    grep -q "kiosco.sh abrir" "$HOME/.config/labwc/autostart" 2>/dev/null || \
      echo "bash $DIR/kiosco.sh abrir &" >> "$HOME/.config/labwc/autostart"
  fi

  echo "==> Escritorio con inicio de sesión automático (para que arranque sin teclado)..."
  if [ "$(systemctl get-default)" != "graphical.target" ]; then
    echo "   El escritorio está deshabilitado: lo habilito (sudo systemctl set-default graphical.target)."
    sudo systemctl set-default graphical.target
  fi
  if command -v raspi-config >/dev/null 2>&1; then
    sudo raspi-config nonint do_boot_behaviour B4 || true   # escritorio con autologin
  elif [ -d /etc/lightdm ]; then
    sudo mkdir -p /etc/lightdm/lightdm.conf.d
    printf "[Seat:*]\nautologin-user=%s\nautologin-user-timeout=0\n" "$(whoami)" | \
      sudo tee /etc/lightdm/lightdm.conf.d/50-centinela-autologin.conf >/dev/null
  else
    echo "   No encontré raspi-config ni lightdm: configurá el inicio automático de sesión a mano."
  fi
  echo
  echo "Listo. Reiniciá con 'sudo reboot' y el monitor HDMI va a mostrar Centinela."
  echo "Si la caja de arena también arranca sola y tapa la pantalla: bash kiosco.sh relieves"
}

quitar() {
  rm -f "$DESK"
  [ -f "$HOME/.config/labwc/autostart" ] && sed -i '/kiosco.sh abrir/d' "$HOME/.config/labwc/autostart"
  sudo rm -f /etc/lightdm/lightdm.conf.d/50-centinela-autologin.conf 2>/dev/null || true
  echo "Kiosco quitado. El escritorio y el inicio automático de sesión quedan como están."
}

lista_autostart() {
  echo "Programas que arrancan solos al entrar al escritorio:"
  ls -1 "$AUTO"/*.desktop /etc/xdg/autostart/*.desktop 2>/dev/null | while read -r f; do
    printf "  %-60s %s\n" "$f" "$(grep -m1 '^Exec=' "$f" | cut -c6-)"
  done
  for f in "$HOME/.config/lxsession/LXDE-pi/autostart" /etc/xdg/lxsession/LXDE-pi/autostart "$HOME/.config/labwc/autostart"; do
    [ -f "$f" ] && { echo "  -- $f"; sed 's/^/     /' "$f"; }
  done
  echo "Servicios del sistema que podrían ser la caja de arena:"
  systemctl list-unit-files --type=service 2>/dev/null | grep -Ei "sand|arena|relie|kinect|vrui" || echo "  (ninguno)"
  echo "Procesos ahora:"
  ps -eo pid,cmd | grep -Ei "sarndbox|sandbox|arena|relie|kinect|vrui" | grep -v grep || echo "  (ninguno)"
}

relieves_off() {
  mkdir -p "$BAK"
  n=0
  for f in "$AUTO"/*.desktop; do
    [ -f "$f" ] || continue
    case "$(basename "$f")" in centinela-kiosco.desktop) continue ;; esac
    if grep -qiE "sarndbox|sandbox|arena|relie|kinect|vrui" "$f"; then
      mv "$f" "$BAK/" && echo "Desactivado: $f (copia en $BAK)" && n=$((n+1))
    fi
  done
  for f in "$HOME/.config/lxsession/LXDE-pi/autostart" "$HOME/.config/labwc/autostart"; do
    if [ -f "$f" ] && grep -qiE "sarndbox|sandbox|arena|relie|kinect|vrui" "$f"; then
      cp "$f" "$BAK/$(basename "$(dirname "$f")")-autostart.bak"
      sed -i -E '/sarndbox|sandbox|arena|relie|kinect|vrui/I s/^/#centinela# /' "$f"
      echo "Comentado en $f (copia en $BAK)"; n=$((n+1))
    fi
  done
  [ "$n" = 0 ] && { echo "No encontré la caja de arena en el autoarranque del usuario. Mirá 'bash kiosco.sh relieves' y pasale la salida a Claude."; exit 0; }
  echo "Listo. Se vuelve a activar con: bash kiosco.sh relieves-on"
}

relieves_on() {
  [ -d "$BAK" ] || { echo "No hay nada para restaurar."; exit 0; }
  for f in "$BAK"/*.desktop; do [ -f "$f" ] && mv "$f" "$AUTO/" && echo "Restaurado: $(basename "$f")"; done
  for f in "$HOME/.config/lxsession/LXDE-pi/autostart" "$HOME/.config/labwc/autostart"; do
    [ -f "$f" ] && sed -i 's/^#centinela# //' "$f"
  done
  echo "La caja de arena vuelve a arrancar sola."
}

case "${1:-}" in
  instalar) instalar ;;
  abrir) abrir ;;
  quitar) quitar ;;
  relieves) lista_autostart ;;
  relieves-off) relieves_off ;;
  relieves-on) relieves_on ;;
  *) sed -n '2,16p' "$0" ;;
esac
