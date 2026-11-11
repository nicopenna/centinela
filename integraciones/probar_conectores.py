#!/usr/bin/env python3
"""Prueba de punta a punta SIN internet: levanta dos servidores falsos en esta
PC que se comportan como Adminia (REST con Bearer) y Unitify (GraphQL con login),
y corre los conectores en modo REAL contra ellos. Incluye un 429 (límite de
pedidos) para probar los reintentos y la firma de webhooks.

    python3 probar_conectores.py
"""
import hashlib
import hmac
import json
import os
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AQUI = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")
sys.path.insert(0, AQUI)
from conectores.adminia import ConectorAdminia  # noqa: E402
from conectores.http import ErrorAPI  # noqa: E402
from conectores.unitify import ConectorUnitify, verificar_webhook  # noqa: E402

ADM = json.load(open(os.path.join(AQUI, "simulado/adminia/respuestas.json"), encoding="utf-8"))
UNI = json.load(open(os.path.join(AQUI, "simulado/unitify/respuestas.json"), encoding="utf-8"))
CLAVE = "am_live_prueba123"
estado = {"429_hecho": False, "logins": 0}


class FalsoAdminia(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer " + CLAVE:
            return self._r(401, {"error": "unauthorized"})
        ruta = self.path.split("?")[0].replace("/v1", "", 1)
        if ruta.endswith("/expenses") and not estado["429_hecho"]:
            estado["429_hecho"] = True
            self.send_response(429); self.send_header("Retry-After", "1"); self.end_headers(); return
        if ruta in ADM:
            return self._r(200, ADM[ruta])
        self._r(404, {"error": "not found"})

    def _r(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def log_message(self, *a):
        pass


class FalsoUnitify(FalsoAdminia):
    def do_POST(self):
        q = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        consulta, v = q["query"], q.get("variables", {})
        if "authenticateUserWithPassword" in consulta:
            estado["logins"] += 1
            ok = v.get("email") == "centinela@ejemplo.com" and v.get("password") == "secreta"
            return self._r(200, {"data": {"auth": {"token": "tok-xyz", "item": {"id": "1", "name": "C"}} if ok else None}})
        if self.headers.get("Authorization") != "Bearer tok-xyz":
            return self._r(200, {"errors": [{"message": "Not authenticated"}]})
        m = re.search(r"objs:\s*all(\w+)", consulta)
        lista = {"Properties": UNI["edificios"], "Contacts": UNI["contactos"], "Tickets": UNI["reclamos"]}.get(m.group(1) if m else "", None)
        if lista is None:
            if "createTicket" in consulta:
                return self._r(200, {"data": {"obj": {"id": "t-new", "number": 103}}})
            return self._r(200, {"errors": [{"message": "Cannot query field"}]})
        return self._r(200, {"data": {"objs": lista[v["skip"]:v["skip"] + v["first"]]}})


def levantar(clase):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), clase)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def check(cond, texto):
    print(("  ✓ " if cond else "  ✗ ") + texto)
    if not cond:
        check.fallas += 1


check.fallas = 0
callar = lambda *a: None  # noqa: E731


def main():
    sa, url_a = levantar(FalsoAdminia)
    su, url_u = levantar(FalsoUnitify)
    tmp = tempfile.mkdtemp()

    print("Adminia (modo real contra servidor falso)")
    c = ConectorAdminia({"api_key": CLAVE, "base_url": url_a + "/v1", "pedidos_por_minuto": 600}, log=callar)
    datos, avisos, pedidos = c.sincronizar()
    check(len(datos["edificios"]) == 2, "lee 2 edificios")
    check(len(datos["unidades"]) == 20, "lee 20 unidades")
    sumas = {}
    for u in datos["unidades"]:
        sumas[u["edificioId"]] = sumas.get(u["edificioId"], 0) + u["porcentual"]
    check(all(abs(s - 100) < 0.01 for s in sumas.values()), "coeficientes 0,08 pasados a porcentual (suman 100)")
    check(any(g["tipo"] == "extraordinaria" for g in datos["gastos"]), "detecta gasto extraordinario")
    check(all(g["periodo"] for g in datos["gastos"]), "todos los gastos tienen período")
    check(all(p["unidadId"] for p in datos["pagos"]), "cada pago queda asociado a su unidad")
    check(any((u.get("deudaExterna") or 0) > 0 for u in datos["unidades"]), "trae la deuda por unidad")
    check(estado["429_hecho"], "recibió un 429 y reintentó sin cortar")
    try:
        ConectorAdminia({"api_key": "mala", "base_url": url_a + "/v1"}, log=callar).sincronizar()
        check(False, "rechaza una clave inválida")
    except ErrorAPI as e:
        check(e.status == 401, "clave inválida → mensaje claro (401)")

    print("Unitify (modo real contra servidor falso)")
    cfg = {"graphql_url": url_u, "email": "centinela@ejemplo.com", "password": "secreta"}
    u = ConectorUnitify(cfg, carpeta_estado=tmp, log=callar)
    datos, avisos, _ = u.sincronizar()
    check(len(datos["edificios"]) == 1 and len(datos["unidades"]) == 8, "lee edificio y arma 8 unidades desde contactos")
    est = {r["externoId"]: r["estado"] for r in datos["reclamos"]}
    check(est == {"t-101": "nuevo", "t-102": "en_curso", "t-099": "resuelto"}, "traduce estados de reclamo")
    cat = {r["externoId"]: r["categoria"] for r in datos["reclamos"]}
    check(cat["t-102"] == "Ascensor" and cat["t-099"] == "Agua caliente o calefacción", "clasifica reclamos por categoría")
    check(all(r["unidadId"] for r in datos["reclamos"]), "cada reclamo queda asociado a su unidad")
    u2 = ConectorUnitify(cfg, carpeta_estado=tmp, log=callar)
    u2.sincronizar()
    check(estado["logins"] == 1, "reusa el token guardado (no gasta logins: límite 10/hora)")
    check(u2.crear_reclamo("org-1", "prop-7f3a", "Centinela: bomba sin presión")["number"] == 103,
          "crea un reclamo en Unitify desde una alarma")

    print("Webhook de pago de Unitify")
    cuerpo = json.dumps({"__typename": "Payment", "status": "done", "amount": "1500"}).encode()
    firma = hmac.new(b"s3cr3t", cuerpo, hashlib.sha256).hexdigest()
    h = {"X-Webhook-Signature": firma, "X-Webhook-Signature-Algorithm": "sha256"}
    check(verificar_webhook(cuerpo, h, "s3cr3t"), "acepta firma válida")
    check(not verificar_webhook(cuerpo + b" ", h, "s3cr3t"), "rechaza cuerpo alterado")
    check(not verificar_webhook(cuerpo, h, "otra"), "rechaza secreto equivocado")

    sa.shutdown(); su.shutdown()
    print("\nTodo OK" if not check.fallas else f"\n{check.fallas} pruebas fallaron")
    return 1 if check.fallas else 0


if __name__ == "__main__":
    sys.exit(main())
