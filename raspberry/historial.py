# -*- coding: utf-8 -*-
"""
Centinela - historial de largo plazo.

* ResumenHorario: junta las muestras de cada hora y guarda una fila por hora en
  data/resumen_horario.csv (pocos KB por mes, se lee rápido aunque pasen años).
* registrar_evento: guarda cada alerta y normalización en data/eventos.csv.
* leer_historico: lo que devuelve /api/historico al tablero de análisis.
* generar_historial: crea N días de historial SIMULADO para la demo, marcado
  con el archivo data/HISTORIAL_SIMULADO (el tablero lo muestra con un cartel).
"""

import csv
import math
import os
import random
import threading
from datetime import datetime, timedelta

CAMPOS = ["hora", "muestras", "sin_dato", "nivel_min", "nivel_prom", "nivel_max",
          "consumo_pct", "llenado_pct", "marcha_min", "arranques",
          "corriente_prom", "corriente_max", "vib_prom", "vib_max"]
CAMPOS_EV = ["fecha", "nivel", "clave", "texto"]
_lock = threading.Lock()


def _dir(base):
    d = os.path.join(base, "data")
    os.makedirs(d, exist_ok=True)
    return d


def _append(path, campos, filas):
    with _lock:
        nuevo = not os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=campos, extrasaction="ignore")
            if nuevo:
                w.writeheader()
            for f in filas:
                w.writerow(f)


class ResumenHorario:
    def __init__(self, base):
        self.path = os.path.join(_dir(base), "resumen_horario.csv")
        self.hora = None
        self._reset()

    def _reset(self):
        self.n = self.sin = 0
        self.niv = []
        self.consumo = self.llenado = 0.0
        self.marcha_s = 0.0
        self.arr = 0
        self.amp = []
        self.vib = []
        self.t_ant = None
        self.niv_ant = None
        self.on_ant = False

    def agregar(self, m):
        t = m["t"]
        h = datetime.fromtimestamp(t).strftime("%Y-%m-%dT%H:00")
        if self.hora is not None and h != self.hora:
            self.volcar()
            # conserva el estado anterior para no perder el arranque/consumo en el borde
            t_ant, niv_ant, on_ant = self.t_ant, self.niv_ant, self.on_ant
            self._reset()
            self.t_ant, self.niv_ant, self.on_ant = t_ant, niv_ant, on_ant
        self.hora = h
        self.n += 1
        dt = (t - self.t_ant) if self.t_ant and 0 < t - self.t_ant <= 120 else 0.0
        niv, on = m.get("nivel_pct"), bool(m.get("bomba_on"))
        if niv is None:
            self.sin += 1
        else:
            self.niv.append(niv)
            if self.niv_ant is not None:
                d = niv - self.niv_ant
                if on:
                    self.llenado += d
                else:
                    self.consumo -= d  # neto: el ruido se cancela
            self.niv_ant = niv
        if on:
            self.marcha_s += dt
            if not self.on_ant:
                self.arr += 1
            if m.get("corriente_a") is not None:
                self.amp.append(m["corriente_a"])
            if m.get("vibracion_g") is not None:
                self.vib.append(m["vibracion_g"])
        self.on_ant = on
        self.t_ant = t

    def fila(self):
        if self.hora is None:
            return None
        r = lambda v, d=2: None if v is None else round(v, d)
        prom = lambda xs: sum(xs) / len(xs) if xs else None
        return {
            "hora": self.hora, "muestras": self.n, "sin_dato": self.sin,
            "nivel_min": r(min(self.niv) if self.niv else None, 1),
            "nivel_prom": r(prom(self.niv), 1),
            "nivel_max": r(max(self.niv) if self.niv else None, 1),
            "consumo_pct": r(max(0.0, self.consumo), 1), "llenado_pct": r(max(0.0, self.llenado), 1),
            "marcha_min": r(self.marcha_s / 60, 1), "arranques": self.arr,
            "corriente_prom": r(prom(self.amp)), "corriente_max": r(max(self.amp) if self.amp else None),
            "vib_prom": r(prom(self.vib), 4), "vib_max": r(max(self.vib) if self.vib else None, 4),
        }

    def volcar(self):
        f = self.fila()
        if f and f["muestras"]:
            _append(self.path, CAMPOS, [f])


def registrar_evento(base, nivel, clave, texto):
    _append(os.path.join(_dir(base), "eventos.csv"), CAMPOS_EV,
            [{"fecha": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "nivel": nivel,
              "clave": clave, "texto": texto.splitlines()[0]}])


def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except ValueError:
        return v


def leer_historico(base, dias, parcial=None):
    d = _dir(base)
    desde = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%dT%H:00")
    horas, eventos = [], []
    p = os.path.join(d, "resumen_horario.csv")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row["hora"] >= desde:
                    horas.append({k: (row[k] if k == "hora" else _num(row[k])) for k in CAMPOS})
    if parcial:
        horas.append(parcial)
    p = os.path.join(d, "eventos.csv")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            eventos = [r for r in csv.DictReader(fh) if r["fecha"] >= desde][-1000:]
    return {"horas": horas, "eventos": eventos,
            "simulado": os.path.exists(os.path.join(d, "HISTORIAL_SIMULADO"))}


# =====================================================================
#  Historial simulado para la demo
# =====================================================================
def generar_historial(base, dias=30, nominal=4.5, arranque=40, parada=90, semilla=7):
    """Simula minuto a minuto una escuela: consumo en horario escolar, bomba
    con automático, desgaste progresivo del rodamiento (sube la vibración),
    una fuga nocturna y un corte del sensor. Escribe resumen horario y eventos."""
    rnd = random.Random(semilla)
    d = _dir(base)
    for nombre in ("resumen_horario.csv", "eventos.csv"):
        p = os.path.join(d, nombre)
        if os.path.exists(p):
            os.replace(p, p + ".bak")
    fin = datetime.now().replace(minute=0, second=0, microsecond=0)
    ini = fin - timedelta(days=dias)
    filas, evs = [], []
    nivel, on = 75.0, False
    t = ini
    fuga_dia = max(1, int(dias * 0.6))     # noche de la fuga
    sensor_dia = max(1, int(dias * 0.25))  # hora sin lectura
    desgaste_ini = int(dias * 0.5)         # empieza a gastarse el rodamiento
    activo = {}

    def ev(cuando, nivel_, clave, texto, abrir=True):
        if abrir and clave in activo:
            return
        if not abrir and clave not in activo:
            return
        if abrir:
            activo[clave] = 1
        else:
            del activo[clave]
            nivel_, texto = "ok", "Normalizado: " + texto
        evs.append({"fecha": cuando.strftime("%Y-%m-%dT%H:%M:%S"), "nivel": nivel_, "clave": clave, "texto": texto})

    marcha_ini = None
    while t < fin:
        acc = ResumenHorario.__new__(ResumenHorario)
        acc.hora = None
        acc._reset()
        h0 = t
        for minuto in range(60):
            dia = (t - ini).days
            habil = t.weekday() < 5
            hh = t.hour + t.minute / 60
            # consumo %/min: escuela (8-17 h) con picos en recreos y almuerzo
            if habil and 7.5 <= hh < 17.5:
                c = 0.16 + (0.12 if (10 <= hh < 10.5 or 12 <= hh < 13.5 or 15 <= hh < 15.5) else 0)
            elif habil and 17.5 <= hh < 21:
                c = 0.05  # limpieza / turno noche
            else:
                c = 0.012
            fuga = dia == fuga_dia and 1 <= hh < 5.5
            if fuga:
                c += 0.32
            c *= rnd.uniform(0.7, 1.3)
            if nivel <= arranque:
                on = True
            elif nivel >= parada:
                on = False
            wear = max(0.0, (dia - desgaste_ini) / max(1, dias - desgaste_ini))
            # con el desgaste la bomba pierde caudal: tarda más en llenar
            nivel += ((2.2 - 0.75 * wear ** 1.5) if on else 0) - c
            nivel = max(0.0, min(100.0, nivel))
            amp = rnd.gauss(nominal * (1 + 0.06 * wear), 0.07) if on else abs(rnd.gauss(0.04, 0.01))
            vib = abs(rnd.gauss(0.045 + 0.085 * wear ** 1.6, 0.004 + 0.01 * wear)) if on else 0.004
            sin = dia == sensor_dia and t.hour == 14 and minuto < 40
            # 2 muestras por minuto (alcanza para el resumen y es rápido)
            for s in (0, 30):
                acc.agregar({"t": t.timestamp() + s, "nivel_pct": None if sin else round(nivel + rnd.gauss(0, 0.3), 1),
                             "bomba_on": on, "corriente_a": round(amp, 2), "vibracion_g": round(vib, 4)})
            # eventos
            if on and marcha_ini is None:
                marcha_ini = t
            if not on:
                marcha_ini = None
            larga = marcha_ini is not None and (t - marcha_ini).total_seconds() > 40 * 60
            ev(t, "alerta", "marcha_larga", "⏱ Bomba más de 40 min seguidos encendida", abrir=True) if larga else \
                ev(t, "alerta", "marcha_larga", "⏱ Bomba más de 40 min seguidos encendida", abrir=False)
            if fuga and hh >= 1.6:
                ev(t, "alerta", "descenso", "📉 Descenso de nivel nocturno: posible fuga")
            elif not fuga:
                ev(t, "alerta", "descenso", "📉 Descenso de nivel nocturno: posible fuga", abrir=False)
            if sin and minuto >= 1:
                ev(t, "alerta", "sensor_nivel", "📡 Sensor de nivel sin lectura")
            elif not sin:
                ev(t, "alerta", "sensor_nivel", "📡 Sensor de nivel sin lectura", abrir=False)
            if on and vib > 0.095:
                ev(t, "aviso", "anomalia", f"🤖 Comportamiento anómalo: vibración {vib:.3f} g (normal 0,045 g)")
            elif not on and "anomalia" in activo:
                ev(t, "aviso", "anomalia", "🤖 Comportamiento anómalo de la bomba", abrir=False)
            t += timedelta(minutes=1)
        acc.hora = h0.strftime("%Y-%m-%dT%H:00")
        filas.append(acc.fila())
    _append(os.path.join(d, "resumen_horario.csv"), CAMPOS, filas)
    _append(os.path.join(d, "eventos.csv"), CAMPOS_EV, evs)
    with open(os.path.join(d, "HISTORIAL_SIMULADO"), "w") as fh:
        fh.write(f"Historial simulado de {dias} días generado el {datetime.now():%Y-%m-%d %H:%M}.\n"
                 "Borrá este archivo y data/resumen_horario.csv + data/eventos.csv para empezar solo con datos reales.\n")
    return len(filas), len(evs)


def borrar_simulado(base):
    d = _dir(base)
    for n in ("resumen_horario.csv", "eventos.csv", "HISTORIAL_SIMULADO"):
        p = os.path.join(d, n)
        if os.path.exists(p):
            os.remove(p)
