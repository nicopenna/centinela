"""Conector Adminia Manager  ->  Centinela   (API REST v1, solo lectura)

Documentación pública (blog de Adminia, mayo 2026):
    Base:   https://api.adminiamanager.com/v1
    Auth:   Authorization: Bearer am_live_...
    Límite: 60 pedidos/min por clave. Plan gratis: 1.000 pedidos/mes.
    GET /buildings
    GET /buildings/:id
    GET /buildings/:id/units
    GET /buildings/:id/expenses?from=&to=
    GET /buildings/:id/collections?from=&to=
    GET /buildings/:id/debt
    GET /buildings/:id/liquidations
    GET /units/:id/ledger

Lo que Adminia NO publica: los nombres de campo de casi todas las respuestas.
Por eso el mapeo prueba varios nombres posibles (inglés y castellano) y el
modo --explorar guarda las respuestas crudas para ajustar el mapeo con datos reales.
"""
import json
import os
import time
from datetime import date, timedelta

from .comun import (como_lista, fecha_de, idx, marcar, numero, periodo_de,
                    porcentuales_a_100, rubro_de, tomar, vacio)
from .http import ClienteHTTP

ORIGEN = "adminia"
BASE_URL = "https://api.adminiamanager.com/v1"


class ClienteSimulado:
    """Responde como la API pero desde simulado/adminia/respuestas.json."""

    def __init__(self, carpeta):
        with open(os.path.join(carpeta, "respuestas.json"), encoding="utf-8") as f:
            self.datos = json.load(f)
        self.pedidos = 0

    def get(self, ruta, params=None):
        self.pedidos += 1
        if ruta not in self.datos:
            from .http import ErrorAPI
            raise ErrorAPI(f"(simulado) no hay respuesta para {ruta}", 404)
        return self.datos[ruta]


class ConectorAdminia:
    def __init__(self, cfg, simulado=False, carpeta_simulado=None, log=print):
        self.cfg = cfg
        self.log = log
        if simulado:
            self.api = ClienteSimulado(carpeta_simulado)
        else:
            token = (cfg.get("api_key") or os.environ.get("ADMINIA_API_KEY", "")).strip()
            if not token:
                raise ValueError("Falta la API key de Adminia (integraciones.ini o ADMINIA_API_KEY)")
            self.api = ClienteHTTP(cfg.get("base_url") or BASE_URL,
                                   headers={"Authorization": "Bearer " + token},
                                   pedidos_por_minuto=int(cfg.get("pedidos_por_minuto", 55)),
                                   log=log)

    # ---------------------------------------------------------------- lectura
    def explorar(self, carpeta):
        """Baja una respuesta cruda de cada endpoint para ver los campos reales."""
        os.makedirs(carpeta, exist_ok=True)
        crudo = {}
        edificios = como_lista(self.api.get("/buildings"), "buildings")
        crudo["/buildings"] = edificios
        if edificios:
            b = str(tomar(edificios[0], "id"))
            for r in ["", "/units", "/expenses", "/collections", "/debt", "/liquidations"]:
                ruta = f"/buildings/{b}{r}"
                try:
                    crudo[ruta] = self.api.get(ruta)
                except Exception as e:  # noqa: BLE001
                    crudo[ruta] = {"error": str(e)}
            unidades = como_lista(crudo.get(f"/buildings/{b}/units"), "units")
            if unidades:
                u = str(tomar(unidades[0], "id"))
                try:
                    crudo[f"/units/{u}/ledger"] = self.api.get(f"/units/{u}/ledger")
                except Exception as e:  # noqa: BLE001
                    crudo[f"/units/{u}/ledger"] = {"error": str(e)}
        ruta = os.path.join(carpeta, "adminia_crudo.json")
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(crudo, f, ensure_ascii=False, indent=2)
        return ruta

    def sincronizar(self, meses=3):
        hasta = date.today()
        desde = (hasta.replace(day=1) - timedelta(days=31 * (meses - 1))).replace(day=1)
        rango = {"from": desde.isoformat(), "to": hasta.isoformat()}
        solo = [s.strip() for s in str(self.cfg.get("edificios", "")).split(",") if s.strip()]

        out = vacio()
        avisos = []
        edificios = como_lista(self.api.get("/buildings"), "buildings")
        for b in edificios:
            bid = str(tomar(b, "id"))
            if solo and bid not in solo:
                continue
            ed_id = idx("adm", bid)
            out["edificios"].append(self._edificio(b, ed_id))
            self.log(f"  · {tomar(b, 'name', 'address', defecto=bid)}")

            unidades_raw = como_lista(self.api.get(f"/buildings/{bid}/units"), "units")
            unidades = [self._unidad(u, ed_id) for u in unidades_raw]
            porcentuales_a_100(unidades)
            mapa_u = {}
            for u, raw in zip(unidades, unidades_raw):
                for clave in ("id", "code", "uf", "number", "name"):
                    v = tomar(raw, clave)
                    if v is not None:
                        mapa_u[str(v)] = u["id"]
            total = sum(u["porcentual"] for u in unidades)
            if unidades and abs(total - 100) > 0.5:
                avisos.append(f"{ed_id}: los porcentuales suman {total:.2f}, no 100")

            # deuda actual por unidad
            try:
                deuda = self.api.get(f"/buildings/{bid}/debt")
                for d in como_lista(deuda, "units", "debts"):
                    uid = mapa_u.get(str(tomar(d, "unit_id", "unitId", "unit.id", "id")))
                    for u in unidades:
                        if u["id"] == uid:
                            u["deudaExterna"] = numero(tomar(d, "total_debt", "debt", "amount", "balance"))
            except Exception as e:  # noqa: BLE001
                avisos.append(f"{ed_id}: no se pudo leer la deuda ({e})")
            out["unidades"].extend(unidades)

            for g in como_lista(self.api.get(f"/buildings/{bid}/expenses", rango), "expenses"):
                out["gastos"].append(self._gasto(g, ed_id))

            for c in como_lista(self.api.get(f"/buildings/{bid}/collections", rango), "collections"):
                p = self._pago(c, ed_id, mapa_u)
                if p["unidadId"]:
                    out["pagos"].append(p)
                else:
                    avisos.append(f"{ed_id}: cobro {p['externoId']} sin unidad reconocible, se omitió")

            for liq in como_lista(self.api.get(f"/buildings/{bid}/liquidations"), "liquidations"):
                L = self._liquidacion(liq, ed_id)
                if L:
                    out["liquidaciones"].append(L)

        return out, avisos, self.api.pedidos

    # ---------------------------------------------------------------- mapeo
    def _edificio(self, b, ed_id):
        nombre = tomar(b, "address", "direccion", "name", "nombre", defecto="Edificio")
        alias = tomar(b, "name", "nombre")
        notas = []
        if alias and alias != nombre:
            notas.append(alias)
        if tomar(b, "active_period"):
            notas.append("Período activo en Adminia: " + str(tomar(b, "active_period")))
        return marcar({
            "id": ed_id,
            "nombre": nombre,
            "barrio": tomar(b, "neighborhood", "barrio", "city", defecto=""),
            "cuit": tomar(b, "cuit", "tax_id", defecto=""),
            "unidadesTotal": tomar(b, "total_units"),
            "notas": " · ".join(notas),
        }, ORIGEN, tomar(b, "id"))

    def _unidad(self, u, ed_id):
        uid = tomar(u, "id")
        return marcar({
            "id": idx("adm", ed_id[4:], uid),
            "edificioId": ed_id,
            "nombre": str(tomar(u, "name", "label", "unit", "floor_unit", "code", "uf", defecto=uid)),
            "titular": tomar(u, "owner_name", "owner.name", "titular", "owner", defecto=""),
            "telefono": tomar(u, "owner_phone", "owner.phone", "phone", "telefono", defecto=""),
            "porcentual": numero(tomar(u, "coefficient", "percentage", "porcentual", "share", "coeficiente")),
        }, ORIGEN, uid)

    def _gasto(self, g, ed_id):
        gid = tomar(g, "id")
        detalle = tomar(g, "description", "concept", "detail", "detalle", defecto="")
        proveedor = tomar(g, "supplier", "supplier.name", "provider", "proveedor", defecto="")
        if isinstance(proveedor, dict):
            proveedor = tomar(proveedor, "name", defecto="")
        crudo = str(tomar(g, "type", "kind", "tipo", defecto="ordinaria")).lower()
        tipo = "extraordinaria" if "extra" in crudo else "ordinaria"
        return marcar({
            "id": idx("adm", ed_id[4:], "g", gid),
            "edificioId": ed_id,
            "periodo": periodo_de(tomar(g, "period", "periodo"), tomar(g, "date", "fecha")),
            "rubro": rubro_de(tomar(g, "category", "rubro", defecto=""), detalle, proveedor),
            "detalle": " · ".join(x for x in [str(detalle), str(proveedor)] if x),
            "monto": numero(tomar(g, "amount", "total", "monto", "importe")),
            "tipo": tipo,
        }, ORIGEN, gid)

    def _pago(self, c, ed_id, mapa_u):
        cid = tomar(c, "id")
        uref = tomar(c, "unit_id", "unitId", "unit.id", "unit", "uf")
        if isinstance(uref, dict):
            uref = tomar(uref, "id")
        fecha = fecha_de(tomar(c, "date", "paid_at", "fecha"))
        return marcar({
            "id": idx("adm", ed_id[4:], "p", cid),
            "edificioId": ed_id,
            "unidadId": mapa_u.get(str(uref)),
            "monto": numero(tomar(c, "amount", "total", "monto", "importe")),
            "periodo": periodo_de(tomar(c, "period", "periodo"), fecha),
            "fecha": fecha,
            "medio": tomar(c, "method", "payment_method", "channel", "medio", defecto="Adminia"),
            "estado": "confirmado",
        }, ORIGEN, cid)

    def _liquidacion(self, l, ed_id):
        per = periodo_de(tomar(l, "period", "periodo"), tomar(l, "date", "issued_at"))
        if not per:
            return None
        estado = str(tomar(l, "status", "estado", defecto="published")).lower()
        return marcar({
            "id": f"{ed_id}_{per}",
            "edificioId": ed_id,
            "periodo": per,
            # Las publicadas en Adminia ya están emitidas. El fondo de reserva
            # viene como gasto, por eso fondoPct = 0 (si no, Centinela lo sumaría dos veces).
            "estado": "emitida" if estado in ("published", "publicada", "emitida", "closed", "issued") else "borrador",
            "vence": fecha_de(tomar(l, "due_date", "first_due_date", "vencimiento")),
            "fondoPct": 0,
            "emitida": int(time.time() * 1000),
            "totalExterno": numero(tomar(l, "total", "amount", "total_amount")),
        }, ORIGEN, tomar(l, "id", defecto=per))
