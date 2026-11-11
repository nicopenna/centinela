"""Conector Unitify  <->  Centinela   (GraphQL)

Documentación pública (devportal.eu.unitify.com):
    - Endpoint GraphQL en la ruta /admin/api (el playground de prueba es
      https://app.unitify.com/admin/api; el host de producción no está publicado).
    - Login de usuario de servicio: mutation authenticateUserWithPassword(email, password)
      -> token. Después: Authorization: Bearer <token>.
    - Límite documentado: 10 logins o SMS por hora por IP (por eso el token se reusa).
    - Convención: allObjetos / createObjeto / updateObjeto.
    - Webhooks de pago de facturas: POST con X-Webhook-Signature (HMAC hex del cuerpo),
      X-Webhook-Signature-Algorithm (sha256/384/512) y X-Webhook-Id.

Lee: edificios (Property), contactos (Contact -> unidades) y reclamos (Ticket).
Escribe (opcional, apagado por defecto): crea un reclamo en Unitify cuando
Centinela detecta una alarma, para que el administrador la vea en su sistema.
"""
import hashlib
import hmac
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

from .comun import categoria_reclamo, idx, marcar, tomar, vacio
from .http import ClienteHTTP, ErrorAPI

ORIGEN = "unitify"
AQUI = os.path.dirname(os.path.abspath(__file__))

ESTADOS = {  # tipo de estado de Unitify -> estado de reclamo en Centinela
    "new_or_reopened": "nuevo",
    "processing": "en_curso",
    "deferred": "en_curso",
    "completed": "resuelto",
    "closed": "resuelto",
    "canceled": "resuelto",
}


def cargar_consultas(ruta=None):
    ruta = ruta or os.path.join(AQUI, "unitify_consultas.graphql")
    with open(ruta, encoding="utf-8") as f:
        texto = f.read()
    bloques = {}
    for m in re.finditer(r"# ---- (\w+)\n(.*?)(?=\n# ---- |\Z)", texto, re.S):
        bloques[m.group(1)] = m.group(2).strip()
    return bloques


class ClienteSimulado:
    def __init__(self, carpeta):
        with open(os.path.join(carpeta, "respuestas.json"), encoding="utf-8") as f:
            self.datos = json.load(f)
        self.pedidos = 0
        self.creados = []

    def gql(self, nombre, variables):
        self.pedidos += 1
        if nombre == "crear_reclamo":
            self.creados.append(variables)
            return {"obj": {"id": "sim-" + str(len(self.creados)), "number": 9000 + len(self.creados)}}
        if nombre == "login":
            return {"auth": {"token": "token-simulado", "item": {"id": "u1", "name": "Centinela"}}}
        lista = self.datos.get(nombre, [])
        skip, first = variables.get("skip", 0), variables.get("first", 100)
        return {"objs": lista[skip:skip + first]}


class ClienteGraphQL:
    def __init__(self, url, email, password, token_cache, log=print):
        self.http = ClienteHTTP(url, pedidos_por_minuto=60, log=log)
        self.url = url
        self.email, self.password = email, password
        self.token_cache = token_cache
        self.consultas = cargar_consultas()
        self.log = log
        self._token = None

    @property
    def pedidos(self):
        return self.http.pedidos

    def _post(self, consulta, variables, token=None):
        h = {"Authorization": "Bearer " + token} if token else {}
        r = self.http.post("", {"query": consulta, "variables": variables}, headers=h)
        if r.get("errors"):
            msg = "; ".join(e.get("message", "?") for e in r["errors"])[:400]
            raise ErrorAPI("GraphQL: " + msg, cuerpo=r["errors"])
        return r.get("data") or {}

    def token(self):
        if self._token:
            return self._token
        # Reusar el token guardado (Unitify limita los logins a 10 por hora).
        if os.path.exists(self.token_cache):
            try:
                with open(self.token_cache) as f:
                    t = json.load(f)
                if t.get("url") == self.url and time.time() - t.get("t", 0) < 6 * 3600:
                    self._token = t["token"]
                    return self._token
            except (ValueError, KeyError):
                pass
        if not (self.email and self.password):
            raise ValueError("Faltan email y contraseña del usuario de servicio de Unitify")
        d = self._post(self.consultas["login"], {"email": self.email, "password": self.password})
        tok = tomar(d, "auth.token")
        if not tok:
            raise ErrorAPI("Unitify rechazó el login del usuario de servicio")
        self._token = tok
        with open(self.token_cache, "w") as f:
            json.dump({"url": self.url, "token": tok, "t": time.time()}, f)
        os.chmod(self.token_cache, 0o600)
        return tok

    def gql(self, nombre, variables):
        if nombre == "login":
            return {"auth": {"token": self.token()}}
        try:
            return self._post(self.consultas[nombre], variables, self.token())
        except ErrorAPI as e:
            if e.status in (401, 403) or "authenticat" in str(e).lower():
                self._token = None
                if os.path.exists(self.token_cache):
                    os.remove(self.token_cache)
                return self._post(self.consultas[nombre], variables, self.token())
            raise


class ConectorUnitify:
    def __init__(self, cfg, simulado=False, carpeta_simulado=None, carpeta_estado=".", log=print):
        self.cfg = cfg
        self.log = log
        if simulado:
            self.api = ClienteSimulado(carpeta_simulado)
        else:
            self.api = ClienteGraphQL(
                cfg.get("graphql_url") or "https://app.unitify.com/admin/api",
                cfg.get("email") or os.environ.get("UNITIFY_EMAIL", ""),
                cfg.get("password") or os.environ.get("UNITIFY_PASSWORD", ""),
                os.path.join(carpeta_estado, ".unitify_token.json"), log=log)

    def _todo(self, nombre, **variables):
        salida, skip, first = [], 0, 100
        while True:
            d = self.api.gql(nombre, dict(variables, first=first, skip=skip))
            lote = d.get("objs") or []
            salida.extend(lote)
            if len(lote) < first:
                return salida
            skip += first

    def sincronizar(self, dias_reclamos=60):
        out, avisos = vacio(), []
        solo = [s.strip() for s in str(self.cfg.get("edificios", "")).split(",") if s.strip()]

        edificios = {}
        for p in self._todo("edificios"):
            pid = str(p["id"])
            if solo and pid not in solo:
                continue
            ed_id = idx("uni", pid)
            edificios[pid] = ed_id
            out["edificios"].append(marcar({
                "id": ed_id,
                "nombre": tomar(p, "address", "name", defecto="Edificio"),
                "barrio": "",
                "unidadesTotal": tomar(p, "unitsCount"),
                "notas": tomar(p, "name", defecto="") if tomar(p, "name") != tomar(p, "address") else "",
            }, ORIGEN, pid))
            self.log(f"  · {tomar(p, 'address', 'name', defecto=pid)}")

        # Unidades: Unitify no tiene "unidad funcional con porcentual"; se arma
        # desde los contactos (edificio + unitName). El porcentual queda en 0.
        unidades = {}
        for c in self._todo("contactos"):
            pid = str(tomar(c, "property.id", defecto=""))
            uname = (tomar(c, "unitName") or "").strip()
            if pid not in edificios or not uname:
                continue
            clave = (pid, uname.lower())
            u = unidades.get(clave)
            if not u:
                u = marcar({
                    "id": idx("uni", pid, uname.replace(" ", "")),
                    "edificioId": edificios[pid],
                    "nombre": uname,
                    "titular": "", "telefono": "", "porcentual": 0,
                }, ORIGEN, f"{pid}/{uname}")
                unidades[clave] = u
            if c.get("isMainContact") or not u["titular"]:
                u["titular"] = tomar(c, "name", defecto="")
                u["telefono"] = tomar(c, "phone", defecto="")
        out["unidades"] = list(unidades.values())
        if out["unidades"]:
            avisos.append("Unitify no trae porcentuales: cargalos en Centinela antes de liquidar expensas")

        desde = (datetime.now(timezone.utc) - timedelta(days=dias_reclamos)).isoformat()
        for t in self._todo("reclamos", desde=desde):
            pid = str(tomar(t, "property.id", defecto=""))
            if pid not in edificios:
                continue
            uname = (tomar(t, "unitName") or "").strip()
            u = unidades.get((pid, uname.lower()))
            detalle = tomar(t, "details", defecto="")
            primera = detalle.split("\n")[0] if detalle else ""
            titulo = primera[:80] if primera else f"Reclamo {tomar(t, 'number')}"
            resto = detalle[len(primera):].strip() if len(primera) <= 80 else detalle
            creado = _ms(tomar(t, "createdAt"))
            doc = marcar({
                "id": idx("uni", "r", tomar(t, "id")),
                "edificioId": edificios[pid],
                "unidadId": u["id"] if u else None,
                "categoria": categoria_reclamo(detalle),
                "titulo": f"#{tomar(t, 'number', defecto='')} {titulo}".strip(),
                "descripcion": resto + (f"{chr(10) * 2 if resto else ''}Vecino: {tomar(t, 'clientName')} {tomar(t, 'clientPhone', defecto='')}"
                                          if tomar(t, "clientName") else ""),
                "estado": ESTADOS.get(tomar(t, "status.type", defecto=""), "nuevo"),
            }, ORIGEN, tomar(t, "id"))
            if creado:
                doc["creado"] = creado
            out["reclamos"].append(doc)

        return out, avisos, self.api.pedidos

    # ------------------------------------------------------------ escritura
    def crear_reclamo(self, organizacion_id, propiedad_id, texto, unidad=None):
        """Crea un reclamo en Unitify (p. ej. 'Centinela: bomba 2 sin presión').
        Los campos obligatorios de TicketCreateInput hay que confirmarlos en el
        playground; acá van los de la convención Condo."""
        data = {
            "dv": 1,
            "sender": {"dv": 1, "fingerprint": "centinela"},
            "organization": {"connect": {"id": organizacion_id}},
            "property": {"connect": {"id": propiedad_id}},
            "details": texto,
        }
        if unidad:
            data["unitName"] = unidad
            data["unitType"] = "flat"
        return self.api.gql("crear_reclamo", {"data": data}).get("obj")


def _ms(iso):
    if not iso:
        return None
    try:
        return int(datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def verificar_webhook(cuerpo_crudo: bytes, headers, secreto: str) -> bool:
    """Valida la firma de un webhook de pago de Unitify (comparación en tiempo constante)."""
    firma = headers.get("X-Webhook-Signature") or ""
    algoritmo = (headers.get("X-Webhook-Signature-Algorithm") or "sha256").lower()
    if algoritmo not in ("sha256", "sha384", "sha512") or not secreto or not firma:
        return False
    esperado = hmac.new(secreto.encode(), cuerpo_crudo, getattr(hashlib, algoritmo)).hexdigest()
    return hmac.compare_digest(esperado, firma.strip().lower())
