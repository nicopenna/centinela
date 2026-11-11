#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Centinela - Demo de alerta temprana sobre Raspberry Pi 3B+
-----------------------------------------------------------
Mide:
  * Nivel de tanque  -> HC-SR04 (ultrasonido)            [real o simulado]
  * Corriente bomba  -> SCT-013-030 + ADS1115 (I2C)      [real o simulado]
  * Vibracion bomba  -> ADXL345 (I2C)                     [real o simulado]

Detecta nivel bajo/critico, descenso rapido, falla de sensor, sobrecorriente,
marcha en seco, marcha excesiva, bomba que no llena, vibracion alta y
comportamiento anomalo (desvio de lo aprendido). Avisa por Telegram y
muestra un tablero web en http://<ip>:8080

Uso:
  python3 centinela_demo.py                 # normal (usa config.ini)
  python3 centinela_demo.py --simular-todo  # sin hardware, todo simulado
  python3 centinela_demo.py --chat-id       # muestra tu chat_id de Telegram
  python3 centinela_demo.py --probar-telegram
  python3 centinela_demo.py --probar-sensores

Solo usa la biblioteca estandar de Python + gpiozero (viene en Raspberry Pi OS)
+ smbus2 (solo si conectas ADS1115/ADXL345).
"""

import argparse
import configparser
import csv
import json
import math
import os
import random
import statistics
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import historial

BASE = os.path.dirname(os.path.abspath(__file__))
VERSION = "1.0-demo"


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


# =====================================================================
#  Configuracion
# =====================================================================
class Cfg:
    def __init__(self, path):
        self.p = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
        if not self.p.read(path, encoding="utf-8"):
            sys.exit(f"No encuentro {path}")
        # config.local.ini (no se sube a GitHub) pisa lo que quieras de config.ini:
        # ahí van el token de Telegram, los chat_ids y la calibración de este equipo.
        self.local = os.path.splitext(path)[0] + ".local.ini"
        self.p.read(self.local, encoding="utf-8")

    def s(self, sec, key, default=""):
        return self.p.get(sec, key, fallback=default).strip()

    def f(self, sec, key, default=0.0):
        v = self.s(sec, key, "")
        return float(v) if v else float(default)

    def i(self, sec, key, default=0):
        v = self.s(sec, key, "")
        return int(v, 0) if v else int(default)

    def b(self, sec, key, default=False):
        v = self.s(sec, key, "").lower()
        if not v:
            return default
        return v in ("si", "sí", "yes", "true", "1", "on")


# =====================================================================
#  Estado compartido de la simulacion (fallas inyectadas desde Telegram/web)
# =====================================================================
class Sim:
    """Fallas simuladas. Se pueden combinar varias a la vez (por ejemplo
    rodamiento gastado + sensor de nivel desconectado)."""
    FALLAS = {
        "normal": "Todo normal (quitar todas)",
        "vibracion": "Rodamiento gastado: vibración x4 (aviso temprano)",
        "rotura": "Rodamiento roto: vibración x8 (alarma)",
        "sobrecorriente": "Motor forzado: corriente +60 %",
        "seco": "Marcha en seco: corriente -50 %",
        "cavitacion": "Cavitación: vibra más y consume MENOS (no es el rodamiento: succión obstruida)",
        "fuga": "Fuga: el tanque se vacía rápido (solo con nivel simulado)",
        "sensor": "Sensor de nivel desconectado",
    }
    FALLAS_BOMBA = ("vibracion", "rotura", "sobrecorriente", "seco", "cavitacion")

    def __init__(self, cfg):
        self.activas = set()
        self.nivel = 70.0  # % (solo si el nivel es simulado)
        self.bomba_on = False
        self.arranque = cfg.f("simulacion", "arranque_pct", 40)
        self.parada = cfg.f("simulacion", "parada_pct", 90)
        self.nominal = cfg.f("corriente", "nominal_a", 4.5)
        self.lock = threading.Lock()

    @property
    def falla(self):
        return "+".join(sorted(self.activas)) if self.activas else "normal"

    def describir(self):
        return "; ".join(self.FALLAS[f] for f in sorted(self.activas)) or "ninguna"

    def set_falla(self, nombre, estado=None):
        """estado None = alterna; True = activa; False = quita. 'normal' quita todas."""
        nombre = (nombre or "normal").lower().strip()
        if nombre not in self.FALLAS:
            return False
        with self.lock:
            if nombre == "normal":
                self.activas.clear()
            else:
                on = (nombre not in self.activas) if estado is None else estado
                # rodamiento gastado y roto son excluyentes
                if on and nombre in ("vibracion", "rotura"):
                    self.activas.discard("rotura" if nombre == "vibracion" else "vibracion")
                (self.activas.add if on else self.activas.discard)(nombre)
        log("Fallas simuladas ->", self.falla)
        return True

    def control_bomba(self, nivel_pct):
        """Automatico de tanque: arranca abajo, para arriba."""
        if self.activas & set(self.FALLAS_BOMBA):
            self.bomba_on = True  # para mostrar la falla, la bomba arranca
            return
        if nivel_pct is None:
            return
        if nivel_pct <= self.arranque:
            self.bomba_on = True
        elif nivel_pct >= self.parada:
            self.bomba_on = False

    def paso_nivel(self, dt):
        consumo = 1.5 if "fuga" in self.activas else 0.15  # %/s
        llenado = 0.0
        if self.bomba_on and "seco" not in self.activas:
            llenado = 0.6 if "cavitacion" in self.activas else 1.0
        self.nivel += (llenado - consumo) * dt + random.gauss(0, 0.1)
        self.nivel = max(0.0, min(100.0, self.nivel))
        return self.nivel

    def corriente(self):
        if not self.bomba_on:
            return abs(random.gauss(0.05, 0.02))
        k = 1.0
        if "sobrecorriente" in self.activas:
            k *= 1.6
        if "seco" in self.activas:
            k *= 0.5
        if "cavitacion" in self.activas:
            k *= 0.8
        return max(0.0, random.gauss(self.nominal * k, 0.08))

    def vibracion(self):
        if not self.bomba_on:
            return abs(random.gauss(0.004, 0.001))
        k = 1.0
        if "vibracion" in self.activas:
            k = 4.0
        if "rotura" in self.activas:
            k = 8.0
        if "cavitacion" in self.activas:
            k = max(k, 2.6)
        return abs(random.gauss(0.045 * k, 0.004 * k))


# =====================================================================
#  Sensores reales
# =====================================================================
def abrir_i2c():
    try:
        from smbus2 import SMBus
    except ImportError:
        try:
            from smbus import SMBus  # python3-smbus de apt
        except ImportError:
            raise RuntimeError("Falta smbus2: pip install smbus2  (o sudo apt install python3-smbus)")
    return SMBus(1)


class HCSR04:
    """HC-SR04 con gpiozero. ECHO va con divisor de tension a 3,3 V."""

    def __init__(self, trig, echo):
        from gpiozero import DistanceSensor
        self.s = DistanceSensor(echo=echo, trigger=trig, max_distance=4.0, queue_len=3)
        time.sleep(0.3)

    def distancia_cm(self):
        lecturas = []
        for _ in range(5):
            d = self.s.distance  # metros, 0..max_distance
            if d is not None and 0.02 <= d < 3.99:
                lecturas.append(d * 100.0)
            time.sleep(0.06)
        if len(lecturas) < 3:
            return None
        return statistics.median(lecturas)


class ADS1115:
    """ADS1115 en modo diferencial A0-A1, +-2,048 V, 860 muestras/s."""

    def __init__(self, bus, addr):
        self.bus, self.addr = bus, addr
        cfg = 0x04E3  # MUX=A0-A1, PGA=2.048V, continuo, 860SPS, comparador off
        self.bus.write_i2c_block_data(addr, 0x01, [cfg >> 8, cfg & 0xFF])
        time.sleep(0.01)

    def vrms(self, ventana):
        muestras = []
        t_end = time.time() + ventana
        while time.time() < t_end:
            hi, lo = self.bus.read_i2c_block_data(self.addr, 0x00, 2)
            v = (hi << 8) | lo
            if v > 0x7FFF:
                v -= 0x10000
            muestras.append(v * 2.048 / 32768.0)
            time.sleep(1 / 860)
        if len(muestras) < 20:
            return None
        m = sum(muestras) / len(muestras)
        return math.sqrt(sum((x - m) ** 2 for x in muestras) / len(muestras))


class ADXL345:
    """ADXL345 por I2C, +-16 g resolucion completa. Devuelve g eficaz (sin gravedad)."""

    def __init__(self, bus, addr):
        self.bus, self.addr = bus, addr
        dev = bus.read_byte_data(addr, 0x00)
        if dev != 0xE5:
            raise RuntimeError(f"ADXL345 no responde (DEVID={dev:#x})")
        bus.write_byte_data(addr, 0x2C, 0x0C)  # 400 Hz
        bus.write_byte_data(addr, 0x31, 0x0B)  # full res, +-16 g
        bus.write_byte_data(addr, 0x2D, 0x08)  # medir

    def g_rms(self, ventana):
        xs, ys, zs = [], [], []
        t_end = time.time() + ventana
        while time.time() < t_end:
            d = self.bus.read_i2c_block_data(self.addr, 0x32, 6)
            vals = []
            for i in (0, 2, 4):
                v = d[i] | (d[i + 1] << 8)
                if v > 0x7FFF:
                    v -= 0x10000
                vals.append(v * 0.0039)
            xs.append(vals[0]); ys.append(vals[1]); zs.append(vals[2])
            time.sleep(0.0025)
        if len(xs) < 20:
            return None
        var = sum(statistics.pvariance(a) for a in (xs, ys, zs))
        return math.sqrt(var)


# =====================================================================
#  Linea base aprendida (para "comportamiento anomalo")
# =====================================================================
class Base:
    def __init__(self, n_min):
        self.n, self.media, self.m2, self.n_min = 0, 0.0, 0.0, n_min

    def lista(self):
        return self.n >= self.n_min

    def desvios(self, x):
        if not self.lista():
            return 0.0
        sd = math.sqrt(self.m2 / (self.n - 1)) if self.n > 1 else 0.0
        sd = max(sd, abs(self.media) * 0.05, 1e-6)
        return (x - self.media) / sd

    def aprender(self, x):
        # Welford, con techo de 500 muestras para que se adapte lento
        self.n = min(self.n + 1, 500)
        d = x - self.media
        self.media += d / self.n
        self.m2 += d * (x - self.media)
        if self.n == 500:
            self.m2 *= 499 / 500


# =====================================================================
#  Telegram
# =====================================================================
class Telegram:
    def __init__(self, token, chat_ids):
        self.token = token
        self.chats = [c.strip() for c in chat_ids.split(",") if c.strip()]
        self.ok = bool(token and self.chats)
        self.offset = 0

    def _api(self, metodo, datos=None, timeout=15):
        url = f"https://api.telegram.org/bot{self.token}/{metodo}"
        body = urllib.parse.urlencode(datos or {}).encode()
        with urllib.request.urlopen(url, data=body, timeout=timeout) as r:
            return json.loads(r.read().decode())

    def enviar(self, texto, chat=None):
        if not self.token:
            log("[telegram sin token] " + texto.replace("\n", " | "))
            return
        destinos = [chat] if chat else self.chats
        for c in destinos:
            for intento in range(3):
                try:
                    self._api("sendMessage", {"chat_id": c, "text": texto, "parse_mode": "HTML"})
                    break
                except Exception as e:
                    log(f"Telegram error ({intento + 1}/3):", e)
                    time.sleep(2)

    def actualizaciones(self, espera=25):
        r = self._api("getUpdates", {"offset": self.offset, "timeout": espera}, timeout=espera + 10)
        for u in r.get("result", []):
            self.offset = u["update_id"] + 1
            yield u


# =====================================================================
#  Alertas (activacion, reaviso y normalizacion)
# =====================================================================
class Alertas:
    def __init__(self, app):
        self.app = app
        self.activas = {}  # clave -> {nivel, texto, desde, ultimo_aviso}
        self.eventos = deque(maxlen=60)
        self.reaviso = app.cfg.f("telegram", "reaviso_min", 30) * 60
        self.avisar_ok = app.cfg.b("telegram", "avisar_normalizado", True)
        self.lock = threading.Lock()

    def evaluar(self, clave, condicion, nivel, texto):
        """nivel: 'critica' | 'alerta' | 'aviso'"""
        ahora = time.time()
        with self.lock:
            act = self.activas.get(clave)
            if condicion and not act:
                self.activas[clave] = {"nivel": nivel, "texto": texto, "desde": ahora, "ultimo": ahora}
                self._evento(nivel, texto, clave)
                self._notificar(nivel, texto)
            elif condicion and act:
                act["texto"] = texto
                if nivel != act["nivel"]:
                    act["nivel"] = nivel
                if self.reaviso and ahora - act["ultimo"] > self.reaviso:
                    act["ultimo"] = ahora
                    mins = int((ahora - act["desde"]) / 60)
                    self._notificar(nivel, texto + f"\n⏱ Sigue activa hace {mins} min.")
            elif not condicion and act:
                del self.activas[clave]
                msg = f"Normalizado: {act['texto'].splitlines()[0]}"
                self._evento("ok", msg, clave)
                if self.avisar_ok:
                    self._notificar("ok", msg)

    def _evento(self, nivel, texto, clave=""):
        self.eventos.appendleft({"hora": datetime.now().strftime("%H:%M:%S"), "nivel": nivel, "texto": texto})
        log(f"[{nivel.upper()}] {texto}")
        try:
            historial.registrar_evento(BASE, nivel, clave, texto)
        except Exception as e:
            log("No pude guardar el evento:", e)

    def _notificar(self, nivel, texto, sitio=None, equipo=None):
        icono = {"critica": "🔴 CRÍTICA", "alerta": "🟠 ALERTA", "aviso": "🟡 AVISO", "ok": "🟢 OK"}[nivel]
        c = self.app.cfg
        esc = lambda x: x.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        msg = (f"<b>{icono} · Centinela</b>\n"
               f"🏫 {esc(sitio or c.s('general', 'sitio'))}\n"
               f"⚙️ {esc(equipo or c.s('general', 'equipo'))}\n\n"
               f"{texto}\n\n"
               f"🕒 {datetime.now().strftime('%d/%m %H:%M:%S')}")
        threading.Thread(target=self.app.tg.enviar, args=(msg,), daemon=True).start()

    def lista(self):
        with self.lock:
            orden = {"critica": 0, "alerta": 1, "aviso": 2}
            return sorted(
                [{"clave": k, **v, "desde": datetime.fromtimestamp(v["desde"]).strftime("%H:%M:%S")}
                 for k, v in self.activas.items()],
                key=lambda a: orden.get(a["nivel"], 9))


# =====================================================================
#  Aplicacion
# =====================================================================
class App:
    def __init__(self, cfg, simular_todo=False):
        self.cfg = cfg
        self.sim = Sim(cfg)
        self.tg = Telegram(cfg.s("telegram", "token"), cfg.s("telegram", "chat_ids"))
        self.alertas = Alertas(self)
        self.hist = deque(maxlen=450)  # ~15 min a 2 s
        self.ultimo = {}
        self.inicio = time.time()

        modo = lambda sec: "simulado" if simular_todo else cfg.s(sec, "modo", "simulado").lower()
        self.modo = {"nivel": modo("nivel"), "corriente": modo("corriente"), "vibracion": modo("vibracion")}

        self.hc = self.ads = self.adxl = None
        if self.modo["nivel"] == "real":
            try:
                self.hc = HCSR04(cfg.i("nivel", "gpio_trigger", 23), cfg.i("nivel", "gpio_echo", 24))
                log("HC-SR04 listo")
            except Exception as e:
                log("No pude iniciar el HC-SR04 -> paso a simulado:", e)
                self.modo["nivel"] = "simulado"
        bus = None
        if "real" in (self.modo["corriente"], self.modo["vibracion"]):
            try:
                bus = abrir_i2c()
            except Exception as e:
                log("I2C no disponible:", e)
        if self.modo["corriente"] == "real":
            try:
                self.ads = ADS1115(bus, cfg.i("corriente", "i2c_direccion", 0x48))
                log("ADS1115 listo")
            except Exception as e:
                log("No pude iniciar el ADS1115 -> paso a simulado:", e)
                self.modo["corriente"] = "simulado"
        if self.modo["vibracion"] == "real":
            try:
                self.adxl = ADXL345(bus, cfg.i("vibracion", "i2c_direccion", 0x53))
                log("ADXL345 listo")
            except Exception as e:
                log("No pude iniciar el ADXL345 -> paso a simulado:", e)
                self.modo["vibracion"] = "simulado"
        log("Modos:", self.modo)

        n = cfg.i("vibracion", "muestras_aprendizaje", 30)
        self.base_vib = Base(n)
        self.base_amp = Base(n)
        if self.modo["corriente"] == "simulado" and self.modo["vibracion"] == "simulado":
            # Historial simulado: la bomba ya "conoce" su comportamiento normal
            on = self.sim.bomba_on
            self.sim.bomba_on = True
            for _ in range(60):
                self.base_vib.aprender(self.sim.vibracion())
                self.base_amp.aprender(self.sim.corriente())
            self.sim.bomba_on = on
        self.fallas_nivel = 0
        self.bomba_desde = None
        self.nivel_al_arrancar = None
        self.csv_dia = None
        self.resumen = historial.ResumenHorario(BASE)

    # ------------------------------------------------------------------
    def leer(self, dt):
        c = self.cfg
        # --- Nivel
        dist = None
        if self.modo["nivel"] == "real":
            dist = self.hc.distancia_cm()
            if "sensor" in self.sim.activas:
                dist = None
        else:
            if "sensor" not in self.sim.activas:
                pct = self.sim.paso_nivel(dt)
                vac, lle = c.f("nivel", "distancia_vacio_cm", 40), c.f("nivel", "distancia_lleno_cm", 5)
                dist = vac - pct / 100.0 * (vac - lle)
        nivel = None
        if dist is not None:
            vac, lle = c.f("nivel", "distancia_vacio_cm", 40), c.f("nivel", "distancia_lleno_cm", 5)
            nivel = max(0.0, min(100.0, (vac - dist) / (vac - lle) * 100.0))

        # La bomba simulada sigue al nivel (real o simulado)
        self.sim.control_bomba(nivel)

        # --- Corriente
        if self.modo["corriente"] == "real":
            v = self.ads.vrms(c.f("corriente", "ventana_rms_s", 0.4))
            amp = None if v is None else v * c.f("corriente", "factor_a_por_v", 30)
        else:
            amp = self.sim.corriente()

        # --- Vibracion
        if self.modo["vibracion"] == "real":
            vib = self.adxl.g_rms(c.f("vibracion", "ventana_s", 0.5))
        else:
            vib = self.sim.vibracion()

        bomba_on = amp is not None and amp >= c.f("corriente", "umbral_encendida_a", 0.8)
        return {
            "t": time.time(),
            "hora": datetime.now().strftime("%H:%M:%S"),
            "dist_cm": None if dist is None else round(dist, 1),
            "nivel_pct": None if nivel is None else round(nivel, 1),
            "litros": None if nivel is None else round(nivel / 100 * c.f("nivel", "capacidad_litros", 2000)),
            "corriente_a": None if amp is None else round(amp, 2),
            "vibracion_g": None if vib is None else round(vib, 4),
            "bomba_on": bomba_on,
        }

    # ------------------------------------------------------------------
    def analizar(self, m):
        c, A = self.cfg, self.alertas
        nivel = m["nivel_pct"]

        # 1) Falla de sensor de nivel (3 lecturas seguidas sin dato)
        self.fallas_nivel = self.fallas_nivel + 1 if nivel is None else 0
        A.evaluar("sensor_nivel", self.fallas_nivel >= 3, "alerta",
                  "📡 Sensor de nivel sin lectura (cable suelto, sensor tapado o dañado).\n"
                  "No se puede controlar el tanque hasta revisarlo.")

        if nivel is not None:
            bajo, crit = c.f("nivel", "nivel_bajo_pct", 35), c.f("nivel", "nivel_critico_pct", 15)
            lit = m["litros"]
            # 2) Nivel critico / bajo (histeresis 5 %)
            ya_crit = "nivel_critico" in A.activas
            A.evaluar("nivel_critico", nivel < crit or (ya_crit and nivel < crit + 5), "critica",
                      f"💧 Nivel CRÍTICO del tanque: {nivel:.0f} % (~{lit} L).\n"
                      f"Riesgo de quedarse sin agua en sanitarios y cocina.")
            ya_bajo = "nivel_bajo" in A.activas
            A.evaluar("nivel_bajo", nivel < bajo or (ya_bajo and nivel < bajo + 5), "alerta",
                      f"💧 Nivel bajo del tanque: {nivel:.0f} % (~{lit} L). Umbral {bajo:.0f} %.")

            # 3) Descenso rapido (pendiente en la ventana)
            vent = c.f("nivel", "ventana_descenso_s", 20)
            pts = [(h["t"], h["nivel_pct"]) for h in self.hist
                   if h["nivel_pct"] is not None and m["t"] - h["t"] <= vent] + [(m["t"], nivel)]
            caida = 0.0
            if len(pts) >= 4 and pts[-1][0] - pts[0][0] >= vent * 0.6:
                caida = (pts[0][1] - pts[-1][1]) / ((pts[-1][0] - pts[0][0]) / 60.0)  # %/min
            umbral = c.f("nivel", "descenso_rapido_pct_min", 20)
            A.evaluar("descenso", caida > umbral, "alerta",
                      f"📉 El nivel baja rápido: {caida:.0f} %/min (normal < {umbral:.0f}).\n"
                      f"Posible fuga, canilla abierta o consumo anormal.")
            m["caida_pct_min"] = round(caida, 1)

        # 4) Bomba: corriente
        amp, on = m["corriente_a"], m["bomba_on"]
        nom = c.f("corriente", "nominal_a", 4.5)
        if on:
            if self.bomba_desde is None:
                self.bomba_desde, self.nivel_al_arrancar = m["t"], nivel
            sobre = amp > nom * c.f("corriente", "sobrecorriente_factor", 1.3)
            seco = amp < nom * c.f("corriente", "marcha_seco_factor", 0.6)
        else:
            self.bomba_desde = self.nivel_al_arrancar = None
            sobre = seco = False
        A.evaluar("sobrecorriente", sobre, "critica",
                  f"⚡ Sobrecorriente en la bomba: {amp or 0:.1f} A (nominal {nom:.1f} A).\n"
                  f"Posible rodamiento trabado, impulsor obstruido o tensión baja. Riesgo de quemar el motor.")
        A.evaluar("marcha_seco", seco, "critica",
                  f"🔥 Bomba consumiendo poco: {amp or 0:.1f} A (nominal {nom:.1f} A).\n"
                  f"Posible marcha en seco (falta agua en cisterna) o acople cortado.")

        marcha = (m["t"] - self.bomba_desde) if self.bomba_desde else 0
        m["marcha_s"] = int(marcha)
        mmax = c.f("corriente", "marcha_max_s", 120)
        A.evaluar("marcha_larga", marcha > mmax, "alerta",
                  f"⏱ La bomba lleva {int(marcha)} s encendida sin parar (máx. {int(mmax)} s).\n"
                  f"Posible flotante trabado, pérdida o bomba que perdió rendimiento.")
        sl = c.f("corriente", "sin_llenado_s", 40)
        no_llena = (on and marcha > sl and nivel is not None and self.nivel_al_arrancar is not None
                    and nivel - self.nivel_al_arrancar < 3)
        A.evaluar("no_llena", no_llena, "alerta",
                  f"🚱 La bomba funciona hace {int(marcha)} s pero el tanque no sube "
                  f"({nivel or 0:.0f} %).\nPosible cañería rota, válvula cerrada o bomba descebada.")

        # 5) Vibracion
        vib = m["vibracion_g"]
        if vib is not None:
            alarma = c.f("vibracion", "alarma_g", 0.25)
            A.evaluar("vibracion_alta", vib > alarma, "critica",
                      f"📳 Vibración ALTA en la bomba: {vib:.3f} g (alarma {alarma} g).\n"
                      f"Posible rodamiento dañado, desbalanceo o anclaje flojo.")
            nd = c.f("vibracion", "anomalia_desvios", 4)
            if on:
                zv, za = self.base_vib.desvios(vib), self.base_amp.desvios(amp)
                # Cavitación: vibra MÁS pero consume MENOS. Parece rodamiento, pero es la succión.
                ya = "cavitacion" in A.activas  # con histéresis para que no titile
                cav = ((zv > nd and za < -2) or (ya and zv > nd / 2 and za < -1.2)) and not (vib > alarma or seco)
                A.evaluar("cavitacion", cav, "alerta",
                          f"🌀 Posible CAVITACIÓN: la bomba vibra más ({vib:.3f} g, normal {self.base_vib.media:.3f} g) "
                          f"pero consume menos ({amp:.2f} A, normal {self.base_amp.media:.2f} A).\n"
                          f"No es el rodamiento: falta agua en la succión (cisterna baja, filtro o válvula de pie tapados, "
                          f"aire en la cañería). Si sigue, rompe el impulsor en semanas.")
                anom = (abs(zv) > nd or abs(za) > nd) and not (vib > alarma or sobre or seco or cav)
                partes = []
                if abs(zv) > nd:
                    partes.append(f"vibración {vib:.3f} g (lo normal es {self.base_vib.media:.3f} g)")
                if abs(za) > nd:
                    partes.append(f"corriente {amp:.2f} A (lo normal es {self.base_amp.media:.2f} A)")
                A.evaluar("anomalia", anom, "aviso",
                          "🤖 Comportamiento anómalo de la bomba: " + " y ".join(partes or ["fuera de patrón"]) +
                          ".\nTodavía no es falla: conviene programar una revisión.")
                if not anom and not cav and vib <= alarma and not sobre and not seco:
                    self.base_vib.aprender(vib)
                    self.base_amp.aprender(amp)
            else:
                A.evaluar("anomalia", False, "aviso", "")
                A.evaluar("cavitacion", False, "alerta", "")
        m["aprendiendo"] = not self.base_vib.lista()

    # ------------------------------------------------------------------
    def guardar(self, m):
        if not self.cfg.b("general", "guardar_csv", True):
            return
        d = os.path.join(BASE, "data")
        os.makedirs(d, exist_ok=True)
        f = os.path.join(d, f"registro_{datetime.now():%Y-%m-%d}.csv")
        campos = ["hora", "dist_cm", "nivel_pct", "litros", "corriente_a", "vibracion_g", "bomba_on", "marcha_s"]
        nuevo = not os.path.exists(f)
        with open(f, "a", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=campos, extrasaction="ignore")
            if nuevo:
                w.writeheader()
            w.writerow(m)

    def bucle(self):
        intervalo = max(1.0, self.cfg.f("general", "intervalo_s", 2))
        ant = time.time()
        while True:
            t0 = time.time()
            try:
                m = self.leer(t0 - ant)
                ant = t0
                self.analizar(m)
                self.hist.append(m)
                self.ultimo = m
                self.guardar(m)
                self.resumen.agregar(m)
            except Exception as e:
                log("Error en el ciclo de medicion:", repr(e))
            time.sleep(max(0.05, intervalo - (time.time() - t0)))

    # ------------------------------------------------------------------
    def texto_estado(self):
        m = self.ultimo or {}
        f = lambda v, u, d=1: "sin dato" if v is None else f"{v:.{d}f} {u}"
        al = self.alertas.lista()
        lineas = [
            f"<b>📊 Estado · {self.cfg.s('general', 'sitio')}</b>",
            f"💧 Nivel: {f(m.get('nivel_pct'), '%', 0)} ({m.get('litros') or '-'} L)  [{self.modo['nivel']}]",
            f"⚡ Corriente: {f(m.get('corriente_a'), 'A', 2)}  [{self.modo['corriente']}]",
            f"📳 Vibración: {f(m.get('vibracion_g'), 'g', 3)}  [{self.modo['vibracion']}]",
            f"🔄 Bomba: {'ENCENDIDA' if m.get('bomba_on') else 'apagada'}",
            "",
            f"Alertas activas: {len(al)}" if al else "✅ Sin alertas activas",
        ]
        for a in al:
            lineas.append(f"• {a['texto'].splitlines()[0]}")
        if self.sim.activas:
            lineas.append(f"\n🧪 Fallas simuladas: {self.sim.describir()}")
        return "\n".join(lineas)

    def comandos_telegram(self):
        if not self.tg.token:
            return
        ayuda = ("<b>Comandos Centinela</b>\n"
                 "/estado – valores actuales y alertas\n"
                 "/falla vibracion | rotura | sobrecorriente | seco | cavitacion | fuga | sensor – activa o quita una falla (se pueden combinar)\n"
                 "/normal – quitar todas las fallas simuladas\n"
                 "/ayuda – esta ayuda")
        while True:
            try:
                for u in self.tg.actualizaciones():
                    msg = u.get("message") or {}
                    chat = str((msg.get("chat") or {}).get("id", ""))
                    txt = (msg.get("text") or "").strip()
                    if not txt:
                        continue
                    if chat not in self.tg.chats:
                        self.tg.enviar(f"Este chat no está autorizado. Tu chat_id es {chat}: agregalo en config.local.ini.", chat)
                        continue
                    cmd, *args = txt.split()
                    cmd = cmd.split("@")[0].lower()
                    if cmd in ("/estado", "/status"):
                        self.tg.enviar(self.texto_estado(), chat)
                    elif cmd == "/falla":
                        if args and self.sim.set_falla(args[0]):
                            self.tg.enviar(f"🧪 Fallas activas: {self.sim.describir()}", chat)
                        else:
                            self.tg.enviar("Usá: /falla vibracion | rotura | sobrecorriente | seco | cavitacion | fuga | sensor", chat)
                    elif cmd == "/normal":
                        self.sim.set_falla("normal")
                        self.tg.enviar("✅ Falla simulada quitada.", chat)
                    else:
                        self.tg.enviar(ayuda, chat)
            except Exception as e:
                log("Telegram (lectura de comandos):", e)
                time.sleep(10)


# =====================================================================
#  Tablero web
# =====================================================================
def servidor_web(app, puerto):
    paginas = {"/": "sistema.html", "/index.html": "sistema.html", "/vivo": "tablero.html",
               "/analisis": "analisis.html"}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            # permite abrir analisis.html desde otra PC (archivo local) y leer la API
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/api/estado"):
                c = app.cfg
                datos = {
                    "sitio": c.s("general", "sitio"), "equipo": c.s("general", "equipo"),
                    "modo": app.modo, "falla": app.sim.falla, "fallas": Sim.FALLAS,
                    "activas": sorted(app.sim.activas),
                    "ultimo": app.ultimo, "alertas": app.alertas.lista(),
                    "eventos": list(app.alertas.eventos),
                    "hist": [{k: h.get(k) for k in ("t", "nivel_pct", "corriente_a", "vibracion_g")} for h in app.hist],
                    "umbrales": {
                        "bajo": c.f("nivel", "nivel_bajo_pct", 35), "critico": c.f("nivel", "nivel_critico_pct", 15),
                        "nominal": c.f("corriente", "nominal_a", 4.5),
                        "sobre": c.f("corriente", "nominal_a", 4.5) * c.f("corriente", "sobrecorriente_factor", 1.3),
                        "vib": c.f("vibracion", "alarma_g", 0.25),
                    },
                    "telegram": app.tg.ok,
                    "uptime_s": int(time.time() - app.inicio),
                }
                self._send(200, json.dumps(datos).encode(), "application/json")
            elif self.path.startswith("/api/historico"):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                try:
                    dias = max(1, min(400, int(q.get("dias", ["30"])[0])))
                except ValueError:
                    dias = 30
                c = app.cfg
                datos = historial.leer_historico(BASE, dias, app.resumen.fila())
                datos.update({
                    "sitio": c.s("general", "sitio"), "equipo": c.s("general", "equipo"),
                    "modo": app.modo, "ultimo": app.ultimo, "alertas": app.alertas.lista(),
                    "config": {
                        "capacidad_l": c.f("nivel", "capacidad_litros", 2000),
                        "bajo": c.f("nivel", "nivel_bajo_pct", 35), "critico": c.f("nivel", "nivel_critico_pct", 15),
                        "nominal": c.f("corriente", "nominal_a", 4.5),
                        "sobre": c.f("corriente", "nominal_a", 4.5) * c.f("corriente", "sobrecorriente_factor", 1.3),
                        "seco": c.f("corriente", "nominal_a", 4.5) * c.f("corriente", "marcha_seco_factor", 0.6),
                        "vib": c.f("vibracion", "alarma_g", 0.25),
                    },
                })
                self._send(200, json.dumps(datos).encode(), "application/json")
            elif self.path.split("?")[0] in paginas:
                with open(os.path.join(BASE, paginas[self.path.split("?")[0]]), "rb") as fh:
                    self._send(200, fh.read(), "text/html; charset=utf-8")
            else:
                self._send(404, b"no", "text/plain")

        def do_POST(self):
            if self.path.startswith("/api/falla/"):
                # /api/falla/<nombre> alterna; ?on=1 activa, ?on=0 quita
                u = urllib.parse.urlparse(self.path)
                q = urllib.parse.parse_qs(u.query).get("on", [None])[0]
                ok = app.sim.set_falla(u.path.rsplit("/", 1)[-1], None if q is None else q == "1")
                self._send(200 if ok else 400, json.dumps({"ok": ok, "activas": sorted(app.sim.activas)}).encode(),
                           "application/json")
            elif self.path.startswith("/api/evento"):
                # Lo usa la pantalla por rol para mandar a Telegram las alertas simuladas de otras escuelas
                try:
                    n = int(self.headers.get("Content-Length", "0"))
                    d = json.loads(self.rfile.read(min(n, 8000)) or b"{}")
                    nivel = d.get("nivel") if d.get("nivel") in ("critica", "alerta", "aviso", "ok") else "aviso"
                    texto = str(d.get("texto", ""))[:1500]
                    if not texto:
                        raise ValueError("sin texto")
                    app.alertas._evento(nivel, "🧪 " + texto, "demo")
                    seguro = texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    app.alertas._notificar(nivel, seguro, sitio=str(d.get("sitio", ""))[:120] or None,
                                           equipo=str(d.get("equipo", ""))[:120] or None)
                    self._send(200, b'{"ok":true}', "application/json")
                except Exception as e:
                    self._send(400, json.dumps({"ok": False, "error": str(e)}).encode(), "application/json")
            else:
                self._send(404, b"no", "text/plain")

    try:
        srv = ThreadingHTTPServer(("0.0.0.0", puerto), H)
    except OSError as e:
        log(f"No pude abrir el tablero en el puerto {puerto} ({e}). "
            "¿Ya está corriendo el servicio? Probá: sudo systemctl stop centinela")
        return
    log(f"Tablero web en http://<ip-de-la-raspberry>:{puerto}")
    srv.serve_forever()


# =====================================================================
#  Utilidades de linea de comandos
# =====================================================================
def obtener_chat_id(cfg):
    tok = cfg.s("telegram", "token")
    if not tok:
        sys.exit("Primero pegá el token del bot en config.local.ini ([telegram] token = ...). Ver config.local.ini.ejemplo")
    tg = Telegram(tok, "")
    print("Escribile cualquier mensaje a tu bot en Telegram (o agregalo a un grupo y escribí algo)...")
    while True:
        for u in tg.actualizaciones():
            msg = u.get("message") or u.get("my_chat_member") or {}
            chat = msg.get("chat") or {}
            if chat:
                nombre = chat.get("title") or chat.get("first_name") or ""
                print(f"\n  chat_id = {chat['id']}   ({nombre})\n")
                print("Copialo en config.local.ini -> [telegram] chat_ids = ...")
                return


def probar_sensores(app):
    print("Leyendo 10 muestras (Ctrl+C para cortar)...")
    for _ in range(10):
        m = app.leer(1.0)
        print(f"  dist={m['dist_cm']} cm  nivel={m['nivel_pct']} %  I={m['corriente_a']} A  "
              f"vib={m['vibracion_g']} g  bomba={'ON' if m['bomba_on'] else 'off'}")
        time.sleep(1)


def main():
    ap = argparse.ArgumentParser(description="Centinela demo Raspberry Pi")
    ap.add_argument("--config", default=os.path.join(BASE, "config.ini"))
    ap.add_argument("--simular-todo", action="store_true", help="ignora el hardware y simula todo")
    ap.add_argument("--chat-id", action="store_true", help="muestra el chat_id de Telegram")
    ap.add_argument("--probar-telegram", action="store_true")
    ap.add_argument("--probar-sensores", action="store_true")
    ap.add_argument("--generar-historial", type=int, metavar="DIAS",
                    help="crea DIAS días de historial SIMULADO para mostrar el tablero de análisis")
    ap.add_argument("--borrar-historial", action="store_true", help="borra el historial (simulado o real)")
    a = ap.parse_args()
    cfg = Cfg(a.config)

    if a.chat_id:
        return obtener_chat_id(cfg)
    if a.generar_historial:
        n, e = historial.generar_historial(BASE, a.generar_historial, cfg.f("corriente", "nominal_a", 4.5),
                                           cfg.f("simulacion", "arranque_pct", 40), cfg.f("simulacion", "parada_pct", 90))
        return print(f"Listo: {n} horas y {e} eventos simulados en data/. Reiniciá el servicio para verlos.")
    if a.borrar_historial:
        historial.borrar_simulado(BASE)
        return print("Historial borrado. Reiniciá el servicio.")
    if a.probar_telegram:
        tg = Telegram(cfg.s("telegram", "token"), cfg.s("telegram", "chat_ids"))
        if not tg.ok:
            sys.exit("Falta token o chat_ids en config.local.ini")
        tg.enviar(f"✅ Centinela conectado en {cfg.s('general', 'sitio')}. ¡Funciona!")
        return print("Mensaje enviado. Revisá Telegram.")

    app = App(cfg, a.simular_todo)
    if a.probar_sensores:
        return probar_sensores(app)

    log(f"Centinela demo {VERSION} · {cfg.s('general', 'sitio')}")
    if not app.tg.ok:
        log("Telegram sin configurar: las alertas solo se ven en pantalla y en el tablero.")
    import atexit, signal
    atexit.register(app.resumen.volcar)  # guarda la hora en curso al apagar
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    threading.Thread(target=servidor_web, args=(app, cfg.i("general", "puerto_web", 8080)), daemon=True).start()
    threading.Thread(target=app.comandos_telegram, daemon=True).start()
    if app.tg.ok:
        app.tg.enviar(f"🟢 <b>Centinela iniciado</b>\n🏫 {cfg.s('general', 'sitio')}\n"
                      f"Sensores: nivel {app.modo['nivel']}, corriente {app.modo['corriente']}, "
                      f"vibración {app.modo['vibracion']}.\nEscribí /estado para ver los valores.")
    try:
        app.bucle()
    except KeyboardInterrupt:
        log("Fin.")


if __name__ == "__main__":
    main()
