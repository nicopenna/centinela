"""Cliente HTTP mínimo (solo librería estándar) con reintentos y límite de ritmo.

Lo usan los dos conectores. No guarda nunca tokens en disco ni en los logs.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request


class ErrorAPI(Exception):
    def __init__(self, mensaje, status=None, cuerpo=None):
        super().__init__(mensaje)
        self.status = status
        self.cuerpo = cuerpo


class ClienteHTTP:
    def __init__(self, base_url, headers=None, pedidos_por_minuto=60, timeout=20,
                 reintentos=4, agente="Centinela-Integraciones/1.0", log=print):
        self.base_url = base_url.rstrip("/")
        self.headers = dict(headers or {})
        self.headers.setdefault("Accept", "application/json")
        self.headers.setdefault("User-Agent", agente)
        self.intervalo = 60.0 / max(1, pedidos_por_minuto)
        self.timeout = timeout
        self.reintentos = reintentos
        self.log = log
        self._ultimo = 0.0
        self.pedidos = 0  # cuántos pedidos se hicieron en esta corrida

    def _esperar_turno(self):
        falta = self.intervalo - (time.monotonic() - self._ultimo)
        if falta > 0:
            time.sleep(falta)
        self._ultimo = time.monotonic()

    def pedir(self, metodo, ruta, params=None, cuerpo=None, headers=None):
        url = ruta if ruta.startswith("http") else self.base_url + ruta
        if params:
            limpios = {k: v for k, v in params.items() if v not in (None, "")}
            if limpios:
                url += ("&" if "?" in url else "?") + urllib.parse.urlencode(limpios)
        datos = None
        h = dict(self.headers)
        h.update(headers or {})
        if cuerpo is not None:
            datos = json.dumps(cuerpo).encode("utf-8")
            h["Content-Type"] = "application/json"

        espera = 2
        for intento in range(self.reintentos + 1):
            self._esperar_turno()
            self.pedidos += 1
            req = urllib.request.Request(url, data=datos, headers=h, method=metodo)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    texto = r.read().decode("utf-8") or "null"
                    return json.loads(texto)
            except urllib.error.HTTPError as e:
                texto = e.read().decode("utf-8", "replace")[:500]
                # 429 = demasiados pedidos, 5xx = falla del servidor: se reintenta
                if e.code == 429 or e.code >= 500:
                    if intento < self.reintentos:
                        pausa = _retry_after(e) or espera
                        self.log(f"  ! {e.code} en {ruta}, reintento en {pausa}s")
                        time.sleep(pausa)
                        espera *= 2
                        continue
                if e.code in (401, 403):
                    raise ErrorAPI(f"Credencial rechazada ({e.code}) en {ruta}. "
                                   "Revisá el token o sus permisos.", e.code, texto)
                raise ErrorAPI(f"Error {e.code} en {ruta}", e.code, texto)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if intento < self.reintentos:
                    self.log(f"  ! sin conexión en {ruta} ({e}), reintento en {espera}s")
                    time.sleep(espera)
                    espera *= 2
                    continue
                raise ErrorAPI(f"No se pudo conectar a {url}: {e}")
            except json.JSONDecodeError:
                raise ErrorAPI(f"La respuesta de {ruta} no es JSON")
        raise ErrorAPI(f"Se agotaron los reintentos en {ruta}")

    def get(self, ruta, params=None):
        return self.pedir("GET", ruta, params=params)

    def post(self, ruta, cuerpo, headers=None):
        return self.pedir("POST", ruta, cuerpo=cuerpo, headers=headers)


def _retry_after(e):
    try:
        return int(e.headers.get("Retry-After"))
    except (TypeError, ValueError):
        return None
