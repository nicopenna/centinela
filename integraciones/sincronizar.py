#!/usr/bin/env python3
"""Centinela · sincronización con Adminia Manager y Unitify.

Uso:
    python3 sincronizar.py                  sincroniza todo lo activo
    python3 sincronizar.py --simulado       fuerza datos de prueba (sin internet ni claves)
    python3 sincronizar.py --solo adminia   una sola fuente
    python3 sincronizar.py --probar         prueba las credenciales y sale
    python3 sincronizar.py --explorar adminia   guarda respuestas crudas para ajustar el mapeo
    python3 sincronizar.py --webhooks       escucha los webhooks de pago de Unitify
    python3 sincronizar.py --alarma "Bomba 2 sin presión" --propiedad <id Unitify> [--unidad "Sala de máquinas"]

Resultado: salida/centinela_import.json  ->  se carga en la app Centinela
(Ajustes > Integraciones > Importar). También deja salida/eventos.json con lo
que cambió desde la corrida anterior.

Solo usa la librería estándar de Python 3.8+. Corre en la Raspberry, en el VPS o en una PC.
Para que corra solo cada mañana (crontab -e):
    15 6 * * * cd /home/pi/centinela/integraciones && python3 sincronizar.py >> salida/log.txt 2>&1
"""
import argparse
import configparser
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

from conectores.adminia import ConectorAdminia  # noqa: E402
from conectores.comun import COLECCIONES, ahora_iso, como_lista, vacio  # noqa: E402
from conectores.http import ErrorAPI  # noqa: E402
from conectores.unitify import ConectorUnitify, verificar_webhook  # noqa: E402


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def leer_cfg(ruta):
    cp = configparser.ConfigParser()
    if not cp.read(ruta, encoding="utf-8"):
        sys.exit(f"No encuentro {ruta}")
    return cp


def modo_de(sec, env_claves, forzar_simulado):
    if forzar_simulado:
        return "simulado"
    modo = sec.get("modo", "auto").strip().lower()
    if modo in ("real", "simulado"):
        return modo
    tiene = all((sec.get(k) or os.environ.get(e, "")).strip() for k, e in env_claves)
    return "real" if tiene else "simulado"


def armar(nombre, cfg, simulado, carpeta_salida):
    sec = cfg[nombre]
    sim_dir = os.path.join(AQUI, "simulado", nombre)
    if nombre == "adminia":
        modo = modo_de(sec, [("api_key", "ADMINIA_API_KEY")], simulado)
        return modo, ConectorAdminia(sec, simulado=(modo == "simulado"), carpeta_simulado=sim_dir, log=log)
    modo = modo_de(sec, [("email", "UNITIFY_EMAIL"), ("password", "UNITIFY_PASSWORD")], simulado)
    return modo, ConectorUnitify(sec, simulado=(modo == "simulado"), carpeta_simulado=sim_dir,
                                 carpeta_estado=carpeta_salida, log=log)


# ----------------------------------------------------------------- cupo Adminia
def contar_cupo(carpeta, pedidos, sec):
    ruta = os.path.join(carpeta, "uso_adminia.json")
    mes = datetime.now().strftime("%Y-%m")
    uso = {}
    if os.path.exists(ruta):
        with open(ruta) as f:
            uso = json.load(f)
    uso[mes] = uso.get(mes, 0) + pedidos
    with open(ruta, "w") as f:
        json.dump(uso, f)
    cupo = int(sec.get("cupo_mensual", 1000))
    pct = 100 * uso[mes] / cupo if cupo else 0
    msg = f"Adminia: {uso[mes]} de {cupo} pedidos usados este mes ({pct:.0f} %)"
    return msg, pct >= float(sec.get("avisar_cupo_pct", 80))


# ----------------------------------------------------------------- eventos
def comparar(anterior, nuevo):
    """Qué cambió desde la corrida anterior: para avisar sin repetir."""
    eventos = []
    prev = {c: {d["id"]: d for d in anterior.get("colecciones", {}).get(c, [])} for c in COLECCIONES}
    for r in nuevo["reclamos"]:
        viejo = prev["reclamos"].get(r["id"])
        if not viejo:
            eventos.append({"tipo": "reclamo_nuevo", "id": r["id"], "texto": f"Reclamo nuevo: {r['titulo']}",
                            "edificioId": r["edificioId"]})
        elif viejo.get("estado") != r["estado"]:
            eventos.append({"tipo": "reclamo_cambio", "id": r["id"],
                            "texto": f"{r['titulo']}: {viejo.get('estado')} → {r['estado']}",
                            "edificioId": r["edificioId"]})
    for u in nuevo["unidades"]:
        d_nueva = u.get("deudaExterna") or 0
        d_vieja = (prev["unidades"].get(u["id"]) or {}).get("deudaExterna") or 0
        if d_nueva > 0 and d_vieja <= 0:
            eventos.append({"tipo": "unidad_con_deuda", "id": u["id"],
                            "texto": f"{u['nombre']} pasó a deber $ {d_nueva:,.0f}".replace(",", "."),
                            "edificioId": u["edificioId"]})
    for p in nuevo["pagos"]:
        if p["id"] not in prev["pagos"]:
            eventos.append({"tipo": "pago_nuevo", "id": p["id"],
                            "texto": f"Pago $ {p['monto']:,.0f} ({p['medio']})".replace(",", "."),
                            "edificioId": p["edificioId"]})
    return eventos


def avisar_telegram(cfg, eventos, nombres):
    tok = cfg["general"].get("telegram_token", "").strip()
    chats = [c.strip() for c in cfg["general"].get("telegram_chat_ids", "").split(",") if c.strip()]
    importantes = [e for e in eventos if e["tipo"] in ("reclamo_nuevo", "unidad_con_deuda")]
    if not (tok and chats and importantes):
        return
    texto = "🦉 Centinela · novedades del software de administración\n\n" + "\n".join(
        f"• {nombres.get(e['edificioId'], '')}: {e['texto']}" for e in importantes[:20])
    for chat in chats:
        datos = urllib.parse.urlencode({"chat_id": chat, "text": texto}).encode()
        try:
            urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", datos, timeout=15)
        except Exception as e:  # noqa: BLE001
            log("  ! Telegram:", e)


# ----------------------------------------------------------------- principal
def sincronizar(cfg, args):
    salida = os.path.join(AQUI, cfg["general"].get("carpeta_salida", "salida"))
    os.makedirs(salida, exist_ok=True)
    total = vacio()
    resumen = {"generado": ahora_iso(), "fuentes": {}, "avisos": []}
    fuentes = [args.solo] if args.solo else ["adminia", "unitify"]
    hubo_error = False

    for nombre in fuentes:
        if cfg[nombre].get("activo", "si").strip().lower() != "si":
            log(f"{nombre}: desactivado en integraciones.ini")
            continue
        modo, con = armar(nombre, cfg, args.simulado, salida)
        log(f"{nombre}: modo {modo}")
        try:
            if nombre == "adminia":
                datos, avisos, pedidos = con.sincronizar(meses=int(cfg["general"].get("meses", 3)))
                if modo == "real":
                    msg, alerta = contar_cupo(salida, pedidos, cfg["adminia"])
                    log("  " + msg)
                    if alerta:
                        avisos.append(msg + ". Bajá la frecuencia o pasá a plan pago.")
            else:
                datos, avisos, pedidos = con.sincronizar(dias_reclamos=int(cfg["general"].get("dias_reclamos", 60)))
        except (ErrorAPI, ValueError) as e:
            log(f"  ✗ {nombre}: {e}")
            resumen["fuentes"][nombre] = {"modo": modo, "error": str(e)}
            hubo_error = True
            continue
        if modo == "simulado":
            for c in COLECCIONES:
                for d in datos[c]:
                    d["ejemplo"] = True  # así se borran con "Borrar datos de ejemplo"
        for c in COLECCIONES:
            total[c].extend(datos[c])
        resumen["fuentes"][nombre] = {"modo": modo, "pedidos": pedidos,
                                      **{c: len(datos[c]) for c in COLECCIONES}}
        resumen["avisos"].extend(f"{nombre}: {a}" for a in avisos)
        log("  " + ", ".join(f"{len(datos[c])} {c}" for c in COLECCIONES))

    # pagos recibidos por webhook de Unitify (si hay)
    ruta_wh = os.path.join(salida, "unitify_pagos.jsonl")
    if os.path.exists(ruta_wh):
        with open(ruta_wh, encoding="utf-8") as f:
            resumen["webhooksPago"] = sum(1 for _ in f)

    ruta_import = os.path.join(salida, "centinela_import.json")
    anterior = {}
    if os.path.exists(ruta_import):
        with open(ruta_import, encoding="utf-8") as f:
            try:
                anterior = json.load(f)
            except ValueError:
                anterior = {}
    eventos = comparar(anterior, total)
    paquete = {"formato": "centinela-import/1", **resumen, "eventos": eventos, "colecciones": total}
    tmp = ruta_import + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(paquete, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ruta_import)
    with open(os.path.join(salida, "eventos.json"), "w", encoding="utf-8") as f:
        json.dump(eventos, f, ensure_ascii=False, indent=1)

    for a in resumen["avisos"]:
        log("  ⚠", a)
    log(f"Listo: {ruta_import}  ({len(eventos)} novedades)")
    avisar_telegram(cfg, eventos, {e["id"]: e["nombre"] for e in total["edificios"]})
    return 1 if hubo_error else 0


def probar(cfg, args):
    ok = True
    for nombre in ([args.solo] if args.solo else ["adminia", "unitify"]):
        salida = os.path.join(AQUI, cfg["general"].get("carpeta_salida", "salida"))
        os.makedirs(salida, exist_ok=True)
        modo, con = armar(nombre, cfg, args.simulado, salida)
        try:
            if nombre == "adminia":
                n = len(como_lista(con.api.get("/buildings"), "buildings"))
                log(f"{nombre} ({modo}): OK, {n} edificios visibles")
            else:
                con.api.gql("login", {})
                n = len(con._todo("edificios"))
                log(f"{nombre} ({modo}): OK, {n} edificios visibles")
        except Exception as e:  # noqa: BLE001
            ok = False
            log(f"{nombre} ({modo}): FALLA → {e}")
    return 0 if ok else 1


def webhooks(cfg):
    sec = cfg["unitify"]
    secreto = sec.get("webhook_secret") or os.environ.get("UNITIFY_WEBHOOK_SECRET", "")
    if not secreto:
        sys.exit("Falta webhook_secret de Unitify (se obtiene al crear la factura).")
    salida = os.path.join(AQUI, cfg["general"].get("carpeta_salida", "salida"))
    os.makedirs(salida, exist_ok=True)
    ruta = os.path.join(salida, "unitify_pagos.jsonl")
    vistos = set()

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            largo = int(self.headers.get("Content-Length", 0) or 0)
            if largo > 1_000_000:
                self.send_response(413); self.end_headers(); return
            cuerpo = self.rfile.read(largo)
            if not verificar_webhook(cuerpo, self.headers, secreto):
                self.send_response(401); self.end_headers(); return
            wid = self.headers.get("X-Webhook-Id", "")
            if wid and wid in vistos:  # Unitify reintenta: no duplicar
                self.send_response(200); self.end_headers(); return
            try:
                pago = json.loads(cuerpo)
                with open(ruta, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"recibido": ahora_iso(), "id": wid, "pago": pago}, ensure_ascii=False) + "\n")
            except (ValueError, OSError):
                self.send_response(500); self.end_headers(); return
            vistos.add(wid)
            log(f"pago {pago.get('status')} ${pago.get('amount')} factura {(pago.get('invoice') or {}).get('number')}")
            self.send_response(200); self.end_headers()

        def log_message(self, *a):
            pass

    puerto = int(sec.get("webhook_puerto", 8090))
    log(f"Escuchando webhooks de Unitify en el puerto {puerto} (Ctrl+C para salir)")
    HTTPServer(("0.0.0.0", puerto), H).serve_forever()


def main():
    ap = argparse.ArgumentParser(description="Centinela · integración con Adminia y Unitify")
    ap.add_argument("--config", default=os.path.join(AQUI, "integraciones.ini"))
    ap.add_argument("--simulado", action="store_true", help="usar datos de prueba")
    ap.add_argument("--solo", choices=["adminia", "unitify"])
    ap.add_argument("--probar", action="store_true", help="probar credenciales")
    ap.add_argument("--explorar", choices=["adminia"], help="guardar respuestas crudas")
    ap.add_argument("--webhooks", action="store_true", help="recibir pagos de Unitify")
    ap.add_argument("--alarma", help="texto del reclamo a crear en Unitify")
    ap.add_argument("--propiedad", help="id del edificio en Unitify")
    ap.add_argument("--unidad")
    args = ap.parse_args()
    cfg = leer_cfg(args.config)

    if args.probar:
        return probar(cfg, args)
    if args.explorar:
        salida = os.path.join(AQUI, cfg["general"].get("carpeta_salida", "salida"))
        _, con = armar("adminia", cfg, args.simulado, salida)
        log("Guardado en", con.explorar(salida))
        return 0
    if args.webhooks:
        return webhooks(cfg)
    if args.alarma:
        sec = cfg["unitify"]
        if sec.get("crear_reclamos_desde_alarmas", "no").strip().lower() != "si" and not args.simulado:
            sys.exit("Crear reclamos está apagado (crear_reclamos_desde_alarmas = no).")
        if not args.propiedad:
            sys.exit("Falta --propiedad (id del edificio en Unitify)")
        _, con = armar("unitify", cfg, args.simulado, os.path.join(AQUI, "salida"))
        r = con.crear_reclamo(sec.get("organizacion_id", "sim-org"), args.propiedad,
                              "Centinela: " + args.alarma, args.unidad)
        log("Reclamo creado en Unitify:", r)
        return 0
    return sincronizar(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
