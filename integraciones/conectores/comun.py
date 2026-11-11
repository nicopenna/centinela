"""Utilidades compartidas para traducir datos externos al formato de Centinela.

Formato de salida (el mismo que usa la app centinela_app.html):
    edificios     {id, nombre, barrio, pisos, encargado, notas, cuit}
    unidades      {id, edificioId, nombre, titular, telefono, porcentual, deudaExterna}
    gastos        {id, edificioId, periodo 'AAAA-MM', rubro, detalle, monto, tipo}
    liquidaciones {id 'edificioId_periodo', edificioId, periodo, estado, vence, fondoPct}
    pagos         {id, edificioId, unidadId, monto, periodo, fecha, medio, estado}
    reclamos      {id, edificioId, unidadId, categoria, titulo, descripcion, estado}
Todo registro importado lleva ademas: origen, externoId, sincronizado.
"""
import re
import unicodedata
from datetime import datetime, timezone

RUBROS = ["Sueldos y cargas sociales", "Servicios públicos", "Abonos de mantenimiento",
          "Reparaciones", "Honorarios de administración", "Seguros", "Monitoreo Centinela",
          "Limpieza y varios", "Gastos bancarios e impuestos", "Otro"]

CATEGORIAS_RECLAMO = ["Agua o pérdida", "Ascensor", "Luz de espacios comunes",
                      "Agua caliente o calefacción", "Ruidos", "Limpieza",
                      "Puerta o seguridad", "Otro"]

COLECCIONES = ["edificios", "unidades", "gastos", "liquidaciones", "pagos", "reclamos"]


def ahora_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def tomar(d, *claves, defecto=None):
    """Devuelve el primer campo presente. Acepta rutas con punto: 'owner.name'."""
    if not isinstance(d, dict):
        return defecto
    for c in claves:
        v = d
        for parte in c.split("."):
            if isinstance(v, dict) and parte in v:
                v = v[parte]
            else:
                v = None
                break
        if v not in (None, ""):
            return v
    return defecto


def numero(v, defecto=0.0):
    if v is None or v == "":
        return defecto
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("$", "").replace(" ", "")
    # "1.234.567,89" (AR) o "1234567.89"
    if "," in s and s.rfind(",") > s.rfind("."):
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return defecto


def periodo_de(*valores):
    """Saca 'AAAA-MM' de '2026-10', '2026-10-15', '10/2026', '2026-10-15T...'."""
    for v in valores:
        if not v:
            continue
        s = str(v)
        m = re.search(r"(20\d\d)[-/](\d{1,2})", s)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}"
        m = re.search(r"(\d{1,2})[-/](20\d\d)", s)
        if m:
            return f"{m.group(2)}-{int(m.group(1)):02d}"
    return None


def fecha_de(v):
    if not v:
        return None
    m = re.search(r"(20\d\d)-(\d\d)-(\d\d)", str(v))
    if m:
        return m.group(0)
    m = re.search(r"(\d{1,2})/(\d{1,2})/(20\d\d)", str(v))
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return None


def sin_acentos(s):
    return "".join(c for c in unicodedata.normalize("NFD", str(s or "").lower())
                   if unicodedata.category(c) != "Mn")


_PALABRAS_RUBRO = [
    ("Sueldos y cargas sociales", ["sueldo", "encargad", "suterh", "cargas soc", "f.931", "931", "aguinaldo"]),
    ("Servicios públicos", ["edesur", "edenor", "aysa", "metrogas", "luz", "agua", "gas", "electric"]),
    ("Reparaciones", ["repar", "arreglo", "plomer", "electricista", "obra", "cambio de"]),
    ("Abonos de mantenimiento", ["abono", "ascensor", "matafuego", "manten"]),
    ("Honorarios de administración", ["honorario", "administraci"]),
    ("Seguros", ["seguro", "poliza", "art "]),
    ("Monitoreo Centinela", ["centinela", "monitoreo"]),
    ("Limpieza y varios", ["limpieza", "articulos", "varios", "insumo"]),
    ("Gastos bancarios e impuestos", ["banco", "bancari", "impuesto", "comision", "abl", "arba", "agip"]),
]


def rubro_de(*textos):
    t = sin_acentos(" ".join(str(x) for x in textos if x))
    for rubro, palabras in _PALABRAS_RUBRO:
        if any(p in t for p in palabras):
            return rubro
    return "Otro"


_PALABRAS_RECLAMO = [
    ("Ascensor", ["ascensor", "elevador", "lift"]),
    ("Agua caliente o calefacción", ["caldera", "calefac", "agua caliente", "termotanque", "radiador"]),
    ("Agua o pérdida", ["agua", "perdida", "filtracion", "humedad", "canilla", "cano", "inund", "tanque"]),
    ("Luz de espacios comunes", ["luz", "lampara", "foco", "palier", "electric"]),
    ("Ruidos", ["ruido", "musica"]),
    ("Limpieza", ["limpieza", "basura", "sucio"]),
    ("Puerta o seguridad", ["puerta", "porton", "llave", "cerradura", "portero", "seguridad", "camara"]),
]


def categoria_reclamo(*textos):
    t = sin_acentos(" ".join(str(x) for x in textos if x))
    for cat, palabras in _PALABRAS_RECLAMO:
        if any(p in t for p in palabras):
            return cat
    return "Otro"


def porcentuales_a_100(unidades):
    """Adminia y otros pueden mandar coeficientes como 0,0585 en vez de 5,85.
    Si la suma da ~1, se pasan a porcentaje."""
    suma = sum(numero(u.get("porcentual")) for u in unidades)
    if 0.5 < suma < 1.5:
        for u in unidades:
            u["porcentual"] = round(numero(u.get("porcentual")) * 100, 4)
    return unidades


def idx(prefijo, *partes):
    limpio = [re.sub(r"[^A-Za-z0-9_-]", "", str(p)) for p in partes]
    return prefijo + "-" + "-".join(limpio)


def marcar(doc, origen, externo_id):
    doc["origen"] = origen
    doc["externoId"] = str(externo_id)
    doc["sincronizado"] = ahora_iso()
    return doc


def vacio():
    return {c: [] for c in COLECCIONES}


def como_lista(resp, *claves):
    """Muchas APIs devuelven [..] o {data:[..]} o {items:[..]}."""
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        for c in claves + ("data", "items", "results", "rows"):
            if isinstance(resp.get(c), list):
                return resp[c]
    return []
