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
AD_COT = "https://api.argentinadatos.com/v1/cotizaciones"   # dólar histórico por casa

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
    "cer":             ["coeficiente", "estabilización"],
    "uva":             ["valor", "adquisitivo"],
}

def bcra_monetarias():
    variables = get(BCRA_MON, verify=VERIFY_BCRA).get("results", [])
    prev = _prev("oficial").get("bcra_monetarias") or {}
    out = {}
    for clave, palabras in BCRA_MATCH.items():
        match = next(
            ({"valor": v.get("valor"), "fecha": v.get("fecha"),
              "desc": v.get("descripcion")}
             for v in variables
             if all(p in v.get("descripcion","").lower() for p in palabras)),
            None)
        if match:
            pv = (prev.get(clave) or {}).get("valor")
            v = match.get("valor")
            match["d"] = round((v/pv - 1)*100, 1) if (pv and v) else None
        out[clave] = match
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

def _prev(bucket):
    """Lee el snapshot anterior de un bucket desde Supabase (para calcular variación)."""
    import os
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")
    if not (url and key):
        return {}
    try:
        j = get(f"{url}/rest/v1/guarismo_latest?id=eq.{bucket}&select=data",
                headers={"apikey": key, "Authorization": f"Bearer {key}"})
        return (j[0]["data"] if j else {}) or {}
    except Exception:
        return {}

# --- Cierre anterior y estado del mercado ------------------------------------
# Problema que resuelve: un sábado, dolarapi devuelve el cierre del viernes.
# Antes comparábamos ese valor contra "el snapshot de hace 30 min" (que era el
# mismo número) → 0%, y la app afirmaba "el dólar no se movió" cuando la verdad
# era "el mercado está cerrado". Dos cosas muy distintas.
#
# Ahora: la variación se calcula SIEMPRE contra el cierre del último día hábil.
#   · mercado abierto → valor de ahora vs. cierre anterior  (variación de hoy)
#   · mercado cerrado → último cierre vs. cierre previo     (cómo cerró)
#
# argentinadatos rellena los días no hábiles repitiendo el valor del último día
# con operatoria. Eso nos deja detectar los cierres reales SIN mantener un
# calendario de feriados: colapsamos las repeticiones y lo que queda son los
# días que efectivamente operaron.

HORA_AR = -3                              # Argentina = UTC-3 (Actions corre en UTC)
MERCADO_DESDE, MERCADO_HASTA = 10, 18     # horario aproximado de operatoria

def _ahora_ar():
    return dt.datetime.utcnow() + dt.timedelta(hours=HORA_AR)

def _cierres_habiles(rows, hasta=None, n=2, tope=8):
    """Los últimos n cierres de días HÁBILES: [{valor, fecha}, ...] (más nuevo primero).
    Los días sin operatoria repiten el valor del último hábil → los colapsamos."""
    pts = [r for r in rows if r.get("venta") is not None and r.get("fecha")]
    if hasta:
        pts = [r for r in pts if r["fecha"] < hasta]
    out, i, pasos, lim = [], len(pts) - 1, 0, tope * n
    while i >= 0 and len(out) < n and pasos < lim:
        j = i
        while j > 0 and pts[j]["venta"] == pts[j-1]["venta"] and pasos < lim:
            j -= 1; pasos += 1
        out.append({"valor": pts[j]["venta"], "fecha": pts[j]["fecha"]})
        i = j - 1
    return out

_CACHE = {}

def _dolarapi():
    """Bajada única de dolarapi, reusada por dolares() y mercado_estado()."""
    if "dolarapi" not in _CACHE:
        _CACHE["dolarapi"] = get("https://dolarapi.com/v1/dolares")
    return _CACHE["dolarapi"]

def mercado_estado():
    """¿El mercado local está operando ahora? Tres chequeos:
      1. Día hábil (lun-vie)      → atrapa fines de semana.
      2. Horario de operatoria.
      3. La fecha del dato de dolarapi es de hoy → atrapa FERIADOS, que no
         podemos deducir de un calendario. Si el dato es de ayer, no operó.
    Cripto (USDT) opera 24/7: la app no le aplica este estado."""
    ahora = _ahora_ar()
    hoy = ahora.date().isoformat()
    habil = ahora.weekday() < 5
    en_horario = MERCADO_DESDE <= ahora.hour < MERCADO_HASTA
    dato_de_hoy = None
    try:
        f = next((d.get("fechaActualizacion") for d in _dolarapi()
                  if d.get("casa") == "blue"), None)
        if f:
            dato_de_hoy = (f[:10] == hoy)
    except Exception:
        pass
    abierto = bool(habil and en_horario and (dato_de_hoy is not False))
    return {"abierto": abierto, "hoy": hoy, "habil": habil,
            "en_horario": en_horario, "dato_de_hoy": dato_de_hoy,
            "hora_ar": ahora.strftime("%H:%M")}

def dolares():
    """Cotizaciones + variación contra el cierre del último día hábil.
    Devuelve por casa: venta, compra, d (%), cierre (valor y fecha de referencia).
    Si no se puede calcular la variación, d queda en None → la app no muestra
    nada (nunca un 0% inventado)."""
    est = mercado_estado()
    hoy = est["hoy"]
    out = {}
    for d in _dolarapi():
        casa = d.get("casa")
        if casa not in CASAS:
            continue
        venta = d.get("venta")
        delta = ref = None
        # Cripto opera 24/7: no tiene "cierre". Se compara contra el día anterior.
        try:
            rows = get(f"{AD_COT}/dolares/{casa}")
            cs = _cierres_habiles(rows, hasta=hoy, n=2)
            if est["abierto"] and cs and venta:
                # Mercado abierto → cuánto se movió HOY respecto del cierre anterior.
                delta = round((venta / cs[0]["valor"] - 1) * 100, 2)
                ref = cs[0]
            elif len(cs) >= 2 and cs[1]["valor"]:
                # Mercado cerrado → cómo cerró el último día hábil.
                delta = round((cs[0]["valor"] / cs[1]["valor"] - 1) * 100, 2)
                ref = cs[0]
        except Exception as e:
            print(f"   [cierre {casa}] {e}")
        out[casa] = {"nombre": CASAS[casa], "compra": d.get("compra"),
                     "venta": venta, "fecha": d.get("fechaActualizacion"),
                     "d": delta,
                     "cierre_fecha": (ref or {}).get("fecha"),
                     "cierre_valor": (ref or {}).get("valor")}
    return out

def riesgo_pais():
    """Riesgo país + variación contra el último valor distinto (mismo criterio:
    la serie repite los días sin rueda)."""
    d = get("https://api.argentinadatos.com/v1/finanzas/indices/riesgo-pais/ultimo")
    v, f = d.get("valor"), d.get("fecha")
    delta = None
    try:
        rows = get(f"{AD_IND}/riesgo-pais")
        pts = [r for r in rows if r.get("valor") is not None and r.get("fecha")]
        prev = next((r["valor"] for r in reversed(pts)
                     if r["fecha"] < (f or "9999") and r["valor"] != v), None)
        if prev and v:
            delta = round((v / prev - 1) * 100, 2)
    except Exception as e:
        print(f"   [riesgo prev] {e}")
    return {"valor": v, "fecha": f, "d": delta}

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
                # Traemos Open y Close de los últimos días.
                hist = yf.Ticker(ticker).history(period="5d")[["Open", "Close"]].dropna()
                if len(hist) >= 1:
                    ult = float(hist["Close"].iloc[-1])          # precio más reciente
                    apertura = float(hist["Open"].iloc[-1])      # apertura del día en curso
                    # Variación INTRADÍA (como la muestran Google/Yahoo): hoy vs apertura.
                    # Si no hay apertura válida, caemos a día-contra-día (cierre anterior).
                    if apertura and apertura > 0:
                        var = (ult / apertura - 1) * 100
                    elif len(hist) >= 2:
                        var = (ult / float(hist["Close"].iloc[-2]) - 1) * 100
                    else:
                        var = None
                    out[grupo][nombre] = {"precio": round(ult, 2),
                                          "var_pct": round(var, 2) if var is not None else None}
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
        ("mercado", "estado del mercado (abierto/cerrado)", "mercado_estado"),
        ("riesgo_pais", "argentinadatos · riesgo país", "riesgo_pais"),
        ("plazos_fijos", "argentinadatos · plazos fijos", "plazos_fijos")]),
    "mercado":   ("🔴 gratis de bajar, LICENCIADO para redistribución comercial", [
        ("local_data912", "data912 · acciones/bonos BYMA", "mercado_local"),
        ("cripto", "CoinGecko · BTC/ETH", "cripto"),
        ("yfinance", "yfinance · Merval/ADRs/metales", "mercado_yf")]),
}
JOBS = {"diario": ["oficial"], "intradia": ["agregador", "mercado"],
        "todo": ["oficial", "agregador", "mercado"]}


# ===========================================================================
# 🟢 REM — Expectativas de mercado (BCRA). Alimenta el "esperado" y el sendero
#    proyectado de inflación, tasa (TAMAR) y dólar (TCN mayorista).
#
#    El REM se publica como INFORME mensual (PDF/Excel), no como API de sendero.
#    Por eso acá va transcripto: es el número OFICIAL publicado, con su fecha y
#    fuente — no un estimado propio. Se actualiza 1 vez al mes cuando sale el
#    informe (~día 6), copiando las medianas del cuadro. Toma 2 minutos.
#
#    Para automatizarlo más adelante: parsear el .xlsx de resultados del BCRA
#    (reemplazá rem() por esa lógica; el resto del pipeline no cambia).
#    Si el REM falta o queda desactualizado, la app muestra SOLO el dato oficial
#    (sin proyección). Nunca datos inventados.
# ===========================================================================
REM_FALLBACK = {
    "fecha_rem": "2026-07-06",                      # fecha de publicación del informe
    "fuente": "REM · BCRA (informe jul-2026)",
    # esperado = mediana del REM por mes (clave "YYYY-MM").
    "inflacion": {"esperado": {
        "2026-06": 2.0, "2026-07": 2.0, "2026-08": 1.8,
        "2026-09": 1.8, "2026-10": 1.7, "2026-11": 1.7, "2026-12": 1.8}},
    "tamar":     {"esperado": {"2026-07": 22.5, "2026-12": 22.0}},   # TNA
    "tcn":       {"esperado": {"2026-07": 1482, "2026-12": 1673}},   # $/USD mayorista
}

def rem():
    """Devuelve las expectativas del REM (inflación/tasa/dólar).
    Hoy: transcripción del informe (REM_FALLBACK). Mañana: parseo del xlsx."""
    return REM_FALLBACK

# El REM se apaga solo si está vencido: si nadie lo actualiza en ~45 días, las
# proyecciones y el "esperado" dejan de emitirse y la app muestra solo el dato
# oficial. Así el REM es opcional, nunca obligatorio, y jamás queda un dato viejo.
REM_VALIDEZ_DIAS = 45

def _rem_vigente(rem_data, dias=REM_VALIDEZ_DIAS):
    f = (rem_data or {}).get("fecha_rem")
    if not f:
        return False
    d = None
    for fmt in (None, "%Y-%m-%d", "%Y/%m/%d"):
        try:
            d = dt.datetime.fromisoformat(f) if fmt is None else dt.datetime.strptime(f, fmt)
            break
        except Exception:
            continue
    if d is None:
        return False
    return (dt.datetime.now() - d).days <= dias


# --- Helpers de fecha/formato -----------------------------------------------
_MES_ABBR = ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"]
_MES_NOM  = ["enero","febrero","marzo","abril","mayo","junio",
             "julio","agosto","septiembre","octubre","noviembre","diciembre"]

def _mes_abbr(ym):   # "2026-06" -> "jun"
    try: return _MES_ABBR[int(ym[5:7]) - 1]
    except Exception: return ym

def _mes_nombre(ym): # "2026-06" -> "junio"
    try: return _MES_NOM[int(ym[5:7]) - 1]
    except Exception: return ym

def _fmt(x):         # 2.3 -> "2,3"
    try: return ("%.1f" % float(x)).replace(".", ",")
    except Exception: return str(x)


# --- Serie de inflación para el gráfico: oficial (INDEC) + proyección (REM) --
def build_infl_serie(rem_data, mens=None):
    """[{m, ym, v, tipo:'of'|'proy', nuevo?, esperado?}] del año en curso."""
    if mens is None:
        mens = get(f"{AD_IND}/inflacion")           # serie mensual completa INDEC
    if not mens:
        return None
    anio = mens[-1]["fecha"][:4]
    of = [{"m": _mes_abbr(x["fecha"][:7]), "ym": x["fecha"][:7],
           "v": x["valor"], "tipo": "of"}
          for x in mens if x["fecha"][:4] == anio and isinstance(x.get("valor"), (int, float))]
    if not of:
        return None
    esp_map = (((rem_data.get("inflacion", {}) or {}).get("esperado", {}) or {})
               if _rem_vigente(rem_data) else {})
    ult = of[-1]
    ult["nuevo"] = True
    if esp_map.get(ult["ym"]) is not None:
        ult["esperado"] = esp_map[ult["ym"]]
    # Proyección REM: meses posteriores al último dato oficial
    proy = [{"m": _mes_abbr(ym), "ym": ym, "v": v, "tipo": "proy"}
            for ym, v in sorted(esp_map.items()) if ym > ult["ym"]]
    return of + proy

def ipc_mensual(mens=None):
    """Serie mensual COMPLETA del IPC (todas las variaciones históricas), para
    que las calculadoras (inflación/UVA) se actualicen solas mes a mes en vez
    de depender de una serie embebida en el código de la app."""
    if mens is None:
        mens = get(f"{AD_IND}/inflacion")
    if not mens:
        return None
    return [{"ym": x["fecha"][:7], "v": x["valor"]} for x in mens
            if isinstance(x.get("valor"), (int, float))]

def uva_mensual(rows=None):
    """Valor de la UVA por mes (último día del mes), de la serie oficial diaria
    (BCRA vía argentinadatos). La calculadora UVA usa esto en vez de aproximarla
    con el IPC. Si falta, la app cae a la aproximación (nunca datos inventados)."""
    if rows is None:
        rows = get(f"{AD_IND}/uva")                 # [{fecha, valor}] diario
    if not rows:
        return None
    por_mes = {}
    for x in rows:                                  # en orden cronológico → queda el último
        ym = (x.get("fecha") or "")[:7]
        v = x.get("valor")
        if ym and isinstance(v, (int, float)):
            por_mes[ym] = v
    return [{"ym": ym, "v": v} for ym, v in sorted(por_mes.items())] or None


# --- Series de nivel (dólar y tasa): valor actual + sendero proyectado REM ----
# A diferencia de inflación (variación mensual → barras), dólar y tasa son
# NIVELES → la app los dibuja como línea (hoy sólido, proyección punteada).
def _dolar_actual(r):
    dd = (r.get("agregador", {}) or {}).get("dolares") or {}
    may = (dd.get("mayorista") or {}).get("venta") if isinstance(dd, dict) else None
    if may: return may
    pdd = (_prev("agregador").get("dolares") or {})
    may = (pdd.get("mayorista") or {}).get("venta")
    if may: return may
    do = (r.get("oficial", {}) or {}).get("dolar_oficial") or {}
    return do.get("tipo_cotizacion")

def _tasa_actual(r):
    bm = (r.get("oficial", {}) or {}).get("bcra_monetarias") or {}
    for k in ("tasa_tamar", "tasa_politica", "tasa_badlar"):
        v = (bm.get(k) or {}).get("valor") if isinstance(bm.get(k), dict) else None
        if v is not None:
            return v
    return None

def _serie_nivel(actual, esperado):
    """[{label, v, tipo}] = punto 'hoy' + puntos proyectados del REM.
    Si no hay proyección (REM vencido), devuelve solo 'hoy': la app muestra el
    valor sin sendero, en vez de caer a una proyección de muestra vieja."""
    if actual is None:
        return None
    pts = [{"label": "hoy", "v": round(float(actual), 1), "tipo": "of"}]
    for ym, v in sorted((esperado or {}).items()):
        pts.append({"label": _mes_abbr(ym), "ym": ym, "v": v, "tipo": "proy"})
    return pts

def build_dolar_serie(rem_data, r):
    esp = (rem_data.get("tcn", {}) or {}).get("esperado") if _rem_vigente(rem_data) else None
    return _serie_nivel(_dolar_actual(r), esp)

def build_tasa_serie(rem_data, r):
    esp = (rem_data.get("tamar", {}) or {}).get("esperado") if _rem_vigente(rem_data) else None
    return _serie_nivel(_tasa_actual(r), esp)


# --- Series HISTÓRICAS reales (para los gráficos de detalle) -----------------
# Reemplazan las series "de muestra" (generadas) del prototipo. Fuente gratis:
# argentinadatos (dólar por casa · 🟡, riesgo país · 🟡). Diario alcanza: el
# gráfico es contexto histórico; el valor de hoy lo pone el feed intradía.
HIST_DIAS = 370   # poco más de un año (para el rango "1A")

def _dolar_hist(casa, dias=HIST_DIAS):
    rows = get(f"{AD_COT}/dolares/{casa}")           # [{casa,compra,venta,fecha}]
    pts = [{"f": x.get("fecha"), "v": x.get("venta")}
           for x in rows if x.get("venta") is not None]
    return pts[-dias:]

def _riesgo_hist(dias=HIST_DIAS):
    rows = get(f"{AD_IND}/riesgo-pais")              # [{fecha,valor}]
    pts = [{"f": x.get("fecha"), "v": x.get("valor")}
           for x in rows if x.get("valor") is not None]
    return pts[-dias:]

def historicos():
    """Series históricas diarias por métrica, para los gráficos de detalle.
    Empezamos por dólar (todas las casas gratis) y riesgo país; el resto sigue
    con serie de muestra hasta conectar su fuente."""
    out = {}
    try:
        out["riesgo"] = _riesgo_hist()
    except Exception as e:
        out["riesgo"] = None; print(f"   [hist riesgo] {e}")
    for casa in ("blue", "oficial", "bolsa", "contadoconliqui", "cripto", "mayorista"):
        try:
            out[casa] = _dolar_hist(casa)
        except Exception as e:
            out[casa] = None; print(f"   [hist {casa}] {e}")
    return out


# --- Calendario económico (auto-generado del patrón mensual habitual) ---------
# Se genera solo cada día → nunca queda viejo, cero mantenimiento. Las fechas son
# ESTIMADAS (patrón de difusión habitual); la app las marca como tales. El día
# exacto se confirma contra el cronograma oficial de cada organismo.
# (día_habitual, título, organismo, ★destacado, lag_meses_del_dato | None, importancia 1-3)
CAL_PATRON = [
    (6,  "REM · Expectativas de mercado",            "BCRA",     False, None, 2),
    (14, "IPC · Inflación",                           "INDEC",    True,  1,    3),
    (14, "Canasta básica y línea de pobreza",         "INDEC",    False, 1,    3),
    (15, "Licitación del Tesoro",                     "Finanzas", False, None, 2),
    (15, "Uso de capacidad instalada",                "INDEC",    False, 2,    1),
    (17, "Precios mayoristas y costo de construcción","INDEC",    False, 1,    1),
    (20, "Comercio exterior",                         "INDEC",    False, 2,    2),
    (20, "Salarios",                                  "INDEC",    False, 2,    1),
    (22, "EMAE · Actividad económica",                "INDEC",    False, 2,    2),
    (26, "Industria y construcción",                  "INDEC",    False, 2,    2),
]

def calendario(meses=2, cap=14):
    hoy = dt.date.today()
    ev = []
    for k in range(meses + 1):
        y = hoy.year + (hoy.month - 1 + k) // 12
        mo = (hoy.month - 1 + k) % 12 + 1
        for dia, titulo, org, star, lag, imp in CAL_PATRON:
            try:
                fecha = dt.date(y, mo, dia)
            except ValueError:
                continue
            if fecha.weekday() == 5:   fecha += dt.timedelta(days=2)   # sáb → lun
            elif fecha.weekday() == 6: fecha += dt.timedelta(days=1)   # dom → lun
            if fecha < hoy:
                continue
            if lag is None:
                t = titulo
            else:
                rm = (mo - 1 - lag) % 12 + 1
                t = f"{titulo} de {_MES_NOM[rm - 1]}"
            ev.append({"f": fecha.isoformat(), "t": t, "s": org,
                       "star": bool(star), "conf": False, "imp": imp})
    ev.sort(key=lambda e: (e["f"], not e["star"]))
    seen, out = set(), []
    for e in ev:
        kk = (e["f"], e["t"])
        if kk not in seen:
            seen.add(kk); out.append(e)
    return out[:cap]


# ===========================================================================
# Breaking news — detector genérico
#
#   Tipos de disparo:
#     · "agendado" → salta cuando cambia el período del dato oficial (ej. sale
#                    el IPC del mes). Trae "esperado" del REM → sorpresa/beat-miss.
#     · "umbral"   → salta cuando un valor cruza un nivel o marca récord (dólar,
#                    riesgo país, merval). No lleva "esperado".
#   Valencia (para el color en la app): "menos_mejor" | "mas_mejor" | "neutro".
#   Regla de oro: UNO solo a la vez, el de mayor prioridad (menor número). Escaso.
# ===========================================================================
def _breaking(bid, text, source, prioridad, metric=None, valor=None,
              esperado=None, valencia="neutro", label="Breaking news"):
    it = {"id": bid, "label": label, "text": text, "source": source,
          "published_at": dt.datetime.now().isoformat(timespec="seconds"),
          "valencia": valencia, "_prioridad": prioridad}
    if metric is not None:   it["metric"]   = metric
    if valor is not None:    it["valor"]    = valor
    if esperado is not None: it["esperado"] = esperado
    return it

def _vigente(item):
    """True hasta las 23:59:59 del día de publicación (igual que la app)."""
    try:
        pub = dt.datetime.fromisoformat(item["published_at"])
    except Exception:
        return False
    fin = pub.replace(hour=23, minute=59, second=59, microsecond=0)
    return dt.datetime.now() <= fin

def _cruce_nivel(prev, cur, paso):
    """Si 'cur' cruzó hacia arriba un múltiplo de 'paso' respecto de 'prev',
    devuelve ese nivel; si no, None. (Para hitos de dólar/riesgo/merval.)"""
    try:
        prev, cur = float(prev), float(cur)
    except (TypeError, ValueError):
        return None
    if cur > prev:
        nivel = (int(cur // paso)) * paso
        if nivel > prev and nivel <= cur:
            return nivel
    return None

def _cruce_bidir(prev, cur, paso):
    """Cruce de un múltiplo de 'paso' en cualquier dirección (para tasas, que
    suben y bajan). Devuelve (nivel, subio) o (None, None)."""
    try:
        prev, cur = float(prev), float(cur)
    except (TypeError, ValueError):
        return None, None
    if prev == cur:
        return None, None
    lo, hi = sorted((prev, cur))
    nivel = (int(lo // paso) + 1) * paso
    if lo < nivel <= hi:
        return nivel, (cur > prev)
    return None, None

def detectar_breaking(r, rem_data):
    """Devuelve UN breaking (o None). Prioriza agendados sobre umbrales."""
    prev = _prev("breaking")
    cand = []

    # 1) AGENDADO — Inflación (INDEC). Sale ~día 14. valencia: menos es mejor.
    of = r.get("oficial", {})
    infl = of.get("inflacion")
    if isinstance(infl, dict) and not infl.get("error"):
        mes = infl.get("ultimo_mes")
        prev_mes = ((_prev("oficial").get("inflacion") or {}) or {}).get("ultimo_mes")
        if mes and mes != prev_mes and infl.get("mensual") is not None:
            esp = (((rem_data.get("inflacion", {}) or {}).get("esperado", {}) or {}).get(mes)
                   if _rem_vigente(rem_data) else None)
            cand.append(_breaking(
                f"ipc-{mes}",
                f"Inflación de {_mes_nombre(mes)}: {_fmt(infl['mensual'])}%\u00a0mensual",
                "INDEC", prioridad=1, metric="inflacion",
                valor=infl["mensual"], esperado=esp, valencia="menos_mejor"))

    # 2) UMBRAL — Tasa TAMAR cruza un entero % (sube o baja). valencia: neutro.
    bm = of.get("bcra_monetarias") or {}
    tam = (bm.get("tasa_tamar") or {}).get("valor") if isinstance(bm.get("tasa_tamar"), dict) else None
    if tam is not None:
        prev_tam = (((_prev("oficial").get("bcra_monetarias") or {}) or {}).get("tasa_tamar") or {}).get("valor")
        nivel, subio = _cruce_bidir(prev_tam, tam, 1)   # paso=1 punto; ajustable
        if nivel is not None:
            cand.append(_breaking(
                f"tamar-{int(nivel)}-{'up' if subio else 'dn'}",
                f"Tasa TAMAR: {'superó' if subio else 'perforó'} el {int(nivel)}%",
                "BCRA", prioridad=2, metric="tasa",
                valor=tam, valencia="neutro"))

    # 3) UMBRAL — Dólar mayorista cruza un múltiplo de $50 (hito). valencia: neutro.
    ag = r.get("agregador", {})
    dd = ag.get("dolares")
    if isinstance(dd, dict) and not dd.get("error"):
        may = (dd.get("mayorista") or {}).get("venta")
        prev_may = (((_prev("agregador").get("dolares") or {}) or {}).get("mayorista") or {}).get("venta")
        lvl = _cruce_nivel(prev_may, may, 50)
        if lvl:
            cand.append(_breaking(
                f"usd-mayorista-{int(lvl)}",
                f"El dólar mayorista superó los ${int(lvl):,}".replace(",", "."),
                "A3 · BCRA", prioridad=3, metric="dolar",
                valor=may, valencia="neutro"))

    # 3) UMBRAL — Riesgo país cruza un múltiplo de 50 pb. valencia: menos es mejor.
    rp = ag.get("riesgo_pais")
    if isinstance(rp, dict) and rp.get("valor") is not None:
        prev_rp = (_prev("agregador").get("riesgo_pais") or {}).get("valor")
        # récord a la baja o cruce hacia arriba: reportamos el cruce de nivel
        lvl = _cruce_nivel(prev_rp, rp["valor"], 50)
        if lvl:
            cand.append(_breaking(
                f"riesgo-{int(lvl)}",
                f"Riesgo país: superó los {int(lvl)} puntos básicos",
                "JP Morgan · EMBI", prioridad=4, metric="riesgo",
                valor=rp["valor"], valencia="menos_mejor"))

    # (Se agregan más igual: merval récord, reservas, fiscal, etc.)

    if not cand:
        # Sin evento nuevo: mantené el último del día si sigue vigente.
        return prev if (prev and _vigente(prev)) else None

    cand.sort(key=lambda x: x["_prioridad"])
    best = cand[0]
    best.pop("_prioridad", None)
    # Si es el MISMO evento que ya estaba, conservá su hora original (no resetear).
    if prev and prev.get("id") == best["id"]:
        best["published_at"] = prev.get("published_at", best["published_at"])
    return best


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

    # --- REM + serie de inflación + breaking news ---------------------------
    rem_data = rem()
    if isinstance(rem_data, dict):
        rem_data = {**rem_data, "_vigente": _rem_vigente(rem_data)}
    if "oficial" in r:                              # solo cuando corre el bloque oficial
        r["oficial"]["rem"] = rem_data
        mens_raw = None                             # serie mensual IPC (se baja 1 vez)
        try:
            mens_raw = get(f"{AD_IND}/inflacion")
        except Exception as e:
            print(f"   [ipc] {e}")
        if isinstance(mens_raw, list) and mens_raw:
            r["oficial"]["ipc_mensual"] = ipc_mensual(mens_raw)   # para las calculadoras
        r["oficial"]["uva_mensual"] = bloque("UVA mensual (BCRA)", uva_mensual)  # calc UVA real
        r["oficial"]["calendario"] = bloque("calendario (estimado)", calendario)
        r["oficial"]["infl_serie"] = bloque("serie inflación (INDEC+REM)",
                                            lambda: build_infl_serie(rem_data, mens_raw))
        r["oficial"]["dolar_serie"] = bloque("serie dólar (nivel+REM)",
                                             lambda: build_dolar_serie(rem_data, r))
        r["oficial"]["tasa_serie"] = bloque("serie tasa TAMAR (nivel+REM)",
                                            lambda: build_tasa_serie(rem_data, r))
        hist = bloque("series históricas (dólar+riesgo)", historicos)
        if isinstance(hist, dict):
            r["hist"] = hist                        # fila propia; la app lee raw.hist
    brk = bloque("breaking news", lambda: detectar_breaking(r, rem_data))
    if brk and not brk.get("error"):
        r["breaking"] = brk                         # fila propia; la app lee raw.breaking
        print(f"   [breaking] {brk.get('id')} · {brk.get('text','')[:48]}")

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
