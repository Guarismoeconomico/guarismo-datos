#!/usr/bin/env python3
"""
Guarismo — Conector integral de datos (todo lo que se puede bajar gratis).

Cada fuente está etiquetada por "bucket" según su situación legal:

  🟢 OFICIAL       Datos públicos y redistribuibles sin fricción.
                   BCRA (monetarias + cambiarias) e INDEC (IPC).
  🟡 AGREGADOR     APIs libres que consolidan/derivan datos. Gratis de usar;
                   para uso COMERCIAL conviene revisar sus términos, porque el
                   MEP/CCL sale de precios de mercado.
                   dolarapi.com, argentinadatos.com.
  🔴 MERCADO       Gratis de BAJAR, pero es market data LICENCIADA: su
                   redistribución comercial requiere licencia (BYMA/A3/NYSE/CME).
                   Sirve para prototipo/uso propio; para el producto pago hay
                   que licenciar. yfinance (Merval, ADRs, metales), CoinGecko.

Salida: guarismo_datos.json

Uso:
    pip install requests
    pip install yfinance        # opcional (bloque 🔴 mercado/metales)
    python guarismo_conector.py

Ninguna fuente pide API key.
"""

import json, sys, datetime as dt
import requests, urllib3

VERIFY_BCRA = False
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
TIMEOUT = 20
UA = {"User-Agent": "Guarismo/1.0"}

def get(url, **kw):
    kw.setdefault("headers", UA); kw.setdefault("timeout", TIMEOUT)
    r = requests.get(url, **kw); r.raise_for_status(); return r.json()


# ===========================================================================
# 🟢 OFICIAL — Inflación (IPC INDEC) vía argentinadatos (dato oficial, limpio)
# ===========================================================================
AD_IND = "https://api.argentinadatos.com/v1/finanzas/indices"

def inflacion():
    mens = get(f"{AD_IND}/inflacion")              # mensual %: [{fecha, valor}]
    try:
        inter = get(f"{AD_IND}/inflacionInteranual")
    except Exception:
        inter = []
    if not mens:
        return {"error": "sin datos de inflación (argentinadatos)"}
    ult = mens[-1]
    ultimo_mes = ult["fecha"][:7]
    anio = ultimo_mes[:4]
    factor = 1.0
    for r in mens:                                  # acumulado del año (compuesto)
        if r["fecha"][:4] == anio and isinstance(r.get("valor"), (int, float)):
            factor *= 1 + r["valor"] / 100
    return {"ultimo_mes": ultimo_mes,
            "mensual": ult.get("valor"),
            "interanual": inter[-1].get("valor") if inter else None,
            "acum_anio": round((factor - 1) * 100, 1),
            "fuente": "INDEC vía argentinadatos"}


# ===========================================================================
# 🟢 OFICIAL — BCRA (reservas, base monetaria, tasas y dólar oficial)
# ===========================================================================
BCRA_MON = "https://api.bcra.gob.ar/estadisticas/v4.0/monetarias"
BCRA_FX  = "https://api.bcra.gob.ar/estadisticascambiarias/v1.0/Cotizaciones"

BCRA_MATCH = {
    "reservas_musd":   ["reservas", "internacionales"],
    "base_monetaria":  ["base", "monetaria"],
    "tasa_badlar":     ["badlar"],
    "tasa_plazo_fijo": ["plazo", "fijo"],
    "tasa_politica":   ["política", "monetaria"],
    "tasa_tamar":      ["tamar"],
}

def bcra_monetarias():
    variables = get(BCRA_MON, verify=VERIFY_BCRA).get("results", [])
    out = {}
    for clave, palabras in BCRA_MATCH.items():
        out[clave] = next(
            ({"valor": v.get("valor"), "fecha": v.get("fecha"),
              "desc": v.get("descripcion")}
             for v in variables
             if all(p in v.get("descripcion","").lower() for p in palabras)),
            None)
    return out

def bcra_dolar_oficial():
    d = get(BCRA_FX, verify=VERIFY_BCRA)["results"]
    fecha = d.get("fecha")
    usd = next((x for x in d.get("detalle", [])
                if x.get("codigoMoneda") == "USD"), None)
    if not usd:
        return None
    return {"fecha": fecha, "tipo_cotizacion": usd.get("tipoCotizacion")}


# ===========================================================================
# 🟡 AGREGADOR — dolarapi (blue/MEP/CCL/cripto/tarjeta) + argentinadatos
# ===========================================================================
CASAS = {"oficial":"Oficial","blue":"Blue","bolsa":"MEP","contadoconliqui":"CCL",
         "mayorista":"Mayorista","tarjeta":"Tarjeta","cripto":"Cripto"}

def dolares():
    out = {}
    for d in get("https://dolarapi.com/v1/dolares"):
        casa = d.get("casa")
        if casa in CASAS:
            out[casa] = {"nombre": CASAS[casa], "compra": d.get("compra"),
                         "venta": d.get("venta"), "fecha": d.get("fechaActualizacion")}
    return out

def riesgo_pais():
    d = get("https://api.argentinadatos.com/v1/finanzas/indices/riesgo-pais/ultimo")
    return {"valor": d.get("valor"), "fecha": d.get("fecha")}

def plazos_fijos():
    try:
        data = get("https://api.argentinadatos.com/v1/finanzas/tasas/plazoFijo")
        tasas = [x.get("tnaClientes") for x in data if x.get("tnaClientes")]
        if tasas:
            return {"promedio_tna": round(sum(tasas)/len(tasas)*100, 2),
                    "max_tna": round(max(tasas)*100, 2), "bancos": len(tasas)}
    except Exception as e:
        return {"error": str(e)}
    return None


# ===========================================================================
# 🔴 MERCADO — CoinGecko (cripto) + yfinance (Merval, ADRs, metales)
# ===========================================================================
def cripto():
    d = get("https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin,ethereum", "vs_currencies": "usd",
                    "include_24hr_change": "true"})
    return {k.upper(): {"usd": v["usd"], "var_24h": round(v.get("usd_24h_change", 0), 2)}
            for k, v in {"btc": d["bitcoin"], "eth": d["ethereum"]}.items()}

# Tickers Yahoo Finance. ADRs cotizan en NYSE; Merval = ^MERV;
# metales/petróleo = futuros; granos Chicago (CME) como referencia internacional.
YF = {
    "indices":     {"Merval": "^MERV", "S&P 500": "^GSPC"},
    "adrs":        {"YPF":"YPF","GGAL":"GGAL","PAM":"PAM","BMA":"BMA","CEPU":"CEPU",
                    "TGS":"TGS","EDN":"EDN","LOMA":"LOMA","CRESY":"CRESY",
                    "SUPV":"SUPV","BBAR":"BBAR"},
    "metales_oil": {"Oro":"GC=F","Plata":"SI=F","Brent":"BZ=F","WTI":"CL=F"},
    "granos_cme":  {"Soja":"ZS=F","Maíz":"ZC=F","Trigo":"ZW=F"},
}

# --- BYMA local (acciones + bonos) vía data912 — gratis, sin credenciales ----
# OJO: data912 se describe como dato "educativo/hobby", NO real-time (~2h de
# caché). Ideal para arrancar; para producción con suscriptores → feed licenciado.
DATA912 = "https://data912.com/live"

def mercado_local():
    def panel(path):
        rows = get(f"{DATA912}/{path}")
        out = {}
        for r in rows:
            sym = r.get("symbol") or r.get("ticker")
            if not sym:
                continue
            # Los nombres de campo pueden variar; confirmalos en la 1ª corrida.
            out[sym] = {"precio": r.get("c", r.get("last", r.get("px_ask"))),
                        "var_pct": r.get("pct_change", r.get("v"))}
        return out
    return {"acciones": panel("arg_stocks"), "bonos": panel("arg_bonds")}

def mercado_yf():
    try:
        import yfinance as yf
    except ImportError:
        return {"_nota": "instalá 'yfinance' para el bloque de mercado (pip install yfinance)"}
    out = {}
    for grupo, mapa in YF.items():
        out[grupo] = {}
        for nombre, ticker in mapa.items():
            try:
                h = yf.Ticker(ticker).history(period="5d")["Close"].dropna()
                if len(h) >= 2:
                    ult, prev = float(h.iloc[-1]), float(h.iloc[-2])
                    out[grupo][nombre] = {"precio": round(ult, 2),
                                          "var_pct": round((ult/prev-1)*100, 2)}
            except Exception:
                out[grupo][nombre] = None
    return out


# ===========================================================================
# Orquestador
# ===========================================================================
def bloque(nombre, fn):
    print(f"→ {nombre} ...")
    try:
        return fn()
    except Exception as e:
        print(f"   [error] {e}")
        return {"error": str(e)}


def to_supabase(datos):
    """Upsert de cada bucket como una fila en la tabla 'guarismo_latest'.
    Requiere las variables de entorno SUPABASE_URL y SUPABASE_KEY (service role).
    Si no están, no hace nada (solo queda el JSON local)."""
    import os
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")
    if not (url and key):
        print("   [supabase] sin SUPABASE_URL/KEY; se omite la escritura.")
        return
    filas = [{"id": k, "data": v, "updated_at": datos["actualizado"]}
             for k, v in datos.items() if isinstance(v, dict)]
    r = requests.post(
        f"{url}/rest/v1/guarismo_latest",
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates"},
        json=filas, timeout=TIMEOUT)
    r.raise_for_status()
    print(f"   [supabase] {len(filas)} filas escritas.")


# Qué buckets corre cada job (por cadencia):
#   diario   → oficial      (BCRA diario, INDEC mensual)
#   intradia → agregador + mercado (dólar/riesgo/cripto/acciones)
#   todo     → todo (default)
BLOQUES = {
    "oficial":   ("🟢 datos oficiales, redistribuibles", [
        ("inflacion", "INDEC · IPC", "inflacion"),
        ("bcra_monetarias", "BCRA · reservas/base/tasas", "bcra_monetarias"),
        ("dolar_oficial", "BCRA · dólar oficial", "bcra_dolar_oficial")]),
    "agregador": ("🟡 API libre; revisar términos para uso comercial", [
        ("dolares", "dolarapi · dólar", "dolares"),
        ("riesgo_pais", "argentinadatos · riesgo país", "riesgo_pais"),
        ("plazos_fijos", "argentinadatos · plazos fijos", "plazos_fijos")]),
    "mercado":   ("🔴 gratis de bajar, LICENCIADO para redistribución comercial", [
        ("local_data912", "data912 · acciones/bonos BYMA", "mercado_local"),
        ("cripto", "CoinGecko · BTC/ETH", "cripto"),
        ("yfinance", "yfinance · Merval/ADRs/metales", "mercado_yf")]),
}
JOBS = {"diario": ["oficial"], "intradia": ["agregador", "mercado"],
        "todo": ["oficial", "agregador", "mercado"]}


def main():
    job = sys.argv[1] if len(sys.argv) > 1 else "todo"
    if job not in JOBS:
        print(f"Uso: python {sys.argv[0]} [diario|intradia|todo]"); return 1

    r = {"actualizado": dt.datetime.now().isoformat(timespec="seconds")}
    for nombre_bucket in JOBS[job]:
        licencia, items = BLOQUES[nombre_bucket]
        r[nombre_bucket] = {"_licencia": licencia}
        for clave, etiqueta, fn_name in items:
            r[nombre_bucket][clave] = bloque(etiqueta, globals()[fn_name])

    with open("guarismo_datos.json", "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2, default=str)
    print("\n✔ Escrito guarismo_datos.json")

    try:
        to_supabase(r)
    except Exception as e:
        print(f"   [supabase] error: {e}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
