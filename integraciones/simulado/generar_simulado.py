"""Genera los datos de prueba de simulado/ (imitan las respuestas de cada API)."""
import json, os, random
AQUI = os.path.dirname(os.path.abspath(__file__))
random.seed(7)
R = {}
bs = [{"id": "bld_1042", "name": "Consorcio Av. Rivadavia 4520", "address": "Av. Rivadavia 4520", "cuit": "30-71234567-8", "active_period": "2026-11", "total_units": 12},
      {"id": "bld_2210", "name": "Consorcio Gascón 735", "address": "Gascón 735", "cuit": "30-70987654-3", "active_period": "2026-11", "total_units": 8}]
R["/buildings"] = {"data": bs}
names = ["Marta Gómez", "Jorge Pereyra", "Lucía Fernández", "Raúl Ibarra", "Ana Sosa", "Pablo Díaz", "Inés Romero", "Carlos Ruiz", "Silvia Paz", "Diego Luna", "Elena Vidal", "Tomás Acosta"]
for b in bs:
    n = b["total_units"]; units = []
    pisos = [f"{p}° {l}" for p in range(1, 7) for l in "AB"][:n]
    coef = [round(1 / n + random.uniform(-0.01, 0.01), 4) for _ in range(n)]; coef[-1] = round(1 - sum(coef[:-1]), 4)
    for i, (p, c) in enumerate(zip(pisos, coef)):
        units.append({"id": f"{b['id']}_u{i+1:02d}", "uf": i + 1, "name": p, "coefficient": c, "owner_name": names[i % len(names)], "owner_phone": f"+54911{random.randint(30000000, 69999999)}"})
    R[f"/buildings/{b['id']}/units"] = {"data": units}
    R[f"/buildings/{b['id']}"] = b
    R[f"/buildings/{b['id']}/debt"] = {"data": [{"unit_id": u["id"], "total_debt": random.choice([0, 0, 0, 0, 85400.0, 171300.5])} for u in units]}
    exp, col, liq, k = [], [], [], 0
    base = [("Sueldo encargado y cargas sociales (F.931)", 1450000), ("Edesur - servicios comunes", 182000), ("AySA", 96500), ("Abono ascensor - Ascensores del Plata", 210000), ("Honorarios administración", 320000), ("Seguro integral consorcio", 88000), ("Monitoreo Centinela", 45000), ("Artículos de limpieza", 38700), ("Comisiones bancarias", 12900)]
    if b["id"] == "bld_2210":
        base = base[:3] + base[4:]
    for per in ["2026-09", "2026-10", "2026-11"]:
        tot = 0
        for d, m in base:
            k += 1; m = round(m * random.uniform(0.97, 1.05), 2); tot += m
            exp.append({"id": f"exp_{k}", "date": per + "-05", "period": per, "description": d, "amount": m, "type": "ordinary"})
        if per == "2026-10":
            k += 1; tot += 265000
            exp.append({"id": f"exp_{k}", "date": per + "-18", "period": per, "description": "Reparación bomba elevadora - cambio de sello mecánico", "supplier": {"name": "Bombas Sur SRL"}, "amount": 265000, "type": "extraordinary"})
        k += 1; f = round(tot * 0.05, 2); tot += f
        exp.append({"id": f"exp_{k}", "date": per + "-05", "period": per, "description": "Fondo de reserva 5%", "amount": f, "type": "ordinary"})
        if per != "2026-11":
            liq.append({"id": f"liq_{b['id']}_{per}", "period": per, "status": "published", "due_date": per + "-10", "total": round(tot, 2)})
            for u in units:
                if random.random() < 0.8:
                    k += 1
                    col.append({"id": f"col_{k}", "unit_id": u["id"], "date": per + "-%02d" % random.randint(3, 12), "period": per, "amount": round(tot * u["coefficient"], 2), "method": random.choice(["Expensas Pagas", "Transferencia", "Pago Mis Cuentas"])})
    R[f"/buildings/{b['id']}/expenses"] = {"data": exp}
    R[f"/buildings/{b['id']}/collections"] = {"data": col}
    R[f"/buildings/{b['id']}/liquidations"] = {"data": liq}
R["/units/bld_1042_u01/ledger"] = {"data": [{"date": "2026-10-01", "concept": "Expensas 2026-10", "debit": 242000, "credit": 0}]}
json.dump(R, open(os.path.join(AQUI, "adminia", "respuestas.json"), "w"), ensure_ascii=False, indent=1)

P = "prop-7f3a"
U = {"edificios": [{"id": P, "name": "Edificio Thames", "address": "Thames 1830", "unitsCount": 10}],
     "contactos": [{"id": f"c{i}", "name": n, "phone": f"+54911{5000000 + i * 7311}", "email": None, "unitName": f"{(i // 2) + 1}{'AB'[i % 2]}", "unitType": "flat", "isMainContact": True, "property": {"id": P}}
                   for i, n in enumerate(["Rocío Méndez", "Hernán Castro", "Valeria Ortiz", "Martín Ríos", "Sofía Benítez", "Nicolás Herrera", "Paula Molina", "Gustavo Navarro"])],
     "reclamos": [
         {"id": "t-101", "number": 101, "details": "Pérdida de agua en el techo del baño, viene del 3A", "status": {"type": "new_or_reopened", "name": "Nuevo"}, "property": {"id": P}, "unitName": "2A", "clientName": "Valeria Ortiz", "clientPhone": "+5491150014622", "createdAt": "2026-11-10T13:20:00Z", "updatedAt": "2026-11-10T13:20:00Z"},
         {"id": "t-102", "number": 102, "details": "El ascensor hace un ruido fuerte al frenar en PB", "status": {"type": "processing", "name": "En proceso"}, "property": {"id": P}, "unitName": "4B", "clientName": "Paula Molina", "clientPhone": "+5491150051177", "createdAt": "2026-11-08T09:05:00Z", "updatedAt": "2026-11-09T16:40:00Z"},
         {"id": "t-099", "number": 99, "details": "No hay agua caliente desde ayer a la noche", "status": {"type": "completed", "name": "Resuelto"}, "property": {"id": P}, "unitName": "1B", "clientName": "Hernán Castro", "clientPhone": "", "createdAt": "2026-11-02T22:10:00Z", "updatedAt": "2026-11-03T11:00:00Z"}]}
json.dump(U, open(os.path.join(AQUI, "unitify", "respuestas.json"), "w"), ensure_ascii=False, indent=1)
print("ok")
