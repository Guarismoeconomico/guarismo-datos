#!/usr/bin/env python3
"""
Guarismo — Publicador de Telegram.

Lee el ÚLTIMO snapshot de Supabase (el que escribe el conector) y publica un
posteo en el canal @guarismo_ar. NO baja datos ni toca el conector: solo lee
lo que ya está en Supabase y publica.

Tres tipos de posteo (según el argumento):
    apertura   → dólar + mercado          (11:00, lun-vie)
    media      → dólar + mercado          (14:00, lun-vie)
    cierre     → dólar + mercado + tasas  (~17:15, lun-vie)

Regla de oro (heredada de la app): NUNCA publica un dato que no se pueda
verificar. Si el mercado está cerrado (feriado entre semana), no inventa
variaciones — las omite, igual que la app con su `sinDeltas`. Si el dato está
demasiado viejo, no publica nada.

Uso:
    export TELEGRAM_TOKEN="123456:AAF..."      # el token de BotFather
    export SUPABASE_URL="https://....supabase.co"
    export SUPABASE_KEY="...anon o service..."  # con anon alcanza (solo lee)
    python guarismo_telegram.py cierre

Variables de entorno requeridas:
    TELEGRAM_TOKEN   token del bot (SECRETO — va en GitHub Secrets)
    SUPABASE_URL     misma que usa el conector
    SUPABASE_KEY     alcanza la anon (read-only) para leer guarismo_latest

El chat_id del canal está fijo abajo (no es secreto).
"""

import os
import sys
import datetime as dt
import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CHAT_ID = "-1004388956898"          # canal @guarismo_ar (no es secreto)
TIMEOUT = 20
HORA_AR = -3                        # Argentina = UTC-3

# Cuán viejo puede ser el dato para publicar (minutos). Coherente con la
# guardia de frescura de la app: si el snapshot tiene más de esto, algo falló
# en el pipeline y NO publicamos (mejor callar que mentir).
MAX_EDAD_MIN = 90

_MESES = ["ene", "feb", "mar", "abr", "may", "jun",
          "jul", "ago", "sep", "oct", "nov", "dic"]
_DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


# ---------------------------------------------------------------------------
# Helpers de fecha / formato
# ---------------------------------------------------------------------------
def _ahora_ar():
    return dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=HORA_AR)


def _fecha_larga(ahora):
    # "mié 16 jul"
    return f"{_DIAS[ahora.weekday()]} {ahora.day} {_MESES[ahora.month - 1]}"


def _money(v, dec=0):
    """1525.0 -> '$1.525' | 1515.7 -> '$1.516' (formato AR: punto miles)."""
    if v is None:
        return "s/d"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "s/d"
    s = f"{v:,.{dec}f}"                      # 1,525.00 (formato US)
    s = s.replace(",", "@").replace(".", ",").replace("@", ".")  # -> AR
    return f"${s}"


def _tasa(v):
    """Redondea la tasa a 1 decimal, como la app. 21.875 -> '21,9'."""
    if v is None:
        return None
    try:
        return f"{float(v):.1f}".replace(".", ",")
    except (TypeError, ValueError):
        return None


def _pct(d):
    """0.3 -> '▲0,3%' | -0.3 -> '▼0,3%' | 0 -> '0,0%' | None -> '' (sin dato)."""
    if d is None:
        return ""
    try:
        d = float(d)
    except (TypeError, ValueError):
        return ""
    if abs(d) < 0.05:
        return "0,0%"
    flecha = "▲" if d > 0 else "▼"
    return f"{flecha}{abs(d):.1f}".replace(".", ",") + "%"


# ---------------------------------------------------------------------------
# Lectura de Supabase (el mismo patrón que usa el conector en _prev)
# ---------------------------------------------------------------------------
def _leer_bucket(bucket):
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")
    if not (url and key):
        raise RuntimeError("faltan SUPABASE_URL / SUPABASE_KEY")
    r = requests.get(
        f"{url}/rest/v1/guarismo_latest?id=eq.{bucket}&select=data,updated_at",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        timeout=TIMEOUT)
    r.raise_for_status()
    j = r.json()
    if not j:
        return {}, None
    return (j[0].get("data") or {}), j[0].get("updated_at")


def _edad_min(updated_at):
    """Minutos desde updated_at (ISO). None si no se puede parsear."""
    if not updated_at:
        return None
    try:
        # updated_at viene sin zona → lo tomamos como hora AR (el conector
        # escribe con dt.datetime.now() local del runner, que corre en UTC…
        # pero el 'actualizado' se compara en términos relativos, así que
        # usamos UTC para ambos lados).
        t = dt.datetime.fromisoformat(updated_at.replace("Z", ""))
        ahora = dt.datetime.utcnow()
        return (ahora - t).total_seconds() / 60
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Armado del texto
# ---------------------------------------------------------------------------
TITULOS = {
    "apertura": "Apertura",
    "media":    "Media rueda",
    "cierre":   "Cierre",
}


def armar_texto(tipo):
    ahora = _ahora_ar()

    # --- Fin de semana: no se publica ---------------------------------------
    if ahora.weekday() >= 5:
        return None, "fin de semana"

    # --- Leer los buckets ----------------------------------------------------
    agg, upd_agg = _leer_bucket("agregador")
    ofi, _ = _leer_bucket("oficial")

    # --- Frescura: si el dato está viejo, no publicamos ---------------------
    edad = _edad_min(upd_agg)
    if edad is not None and edad > MAX_EDAD_MIN:
        return None, f"dato viejo ({edad:.0f} min) — no se publica"

    # --- Estado del mercado (lo calcula el conector y lo guarda) ------------
    mercado = agg.get("mercado") or {}
    abierto = mercado.get("abierto", False)
    dato_de_hoy = mercado.get("dato_de_hoy")

    # Feriado entre semana: el mercado no operó hoy. No inventamos variaciones.
    # (dato_de_hoy is False => hoy no hubo rueda.)
    sin_deltas = (dato_de_hoy is False) or (not abierto and tipo != "cierre")

    dolares = agg.get("dolares") or {}
    riesgo = agg.get("riesgo_pais") or {}

    def dl(casa):
        d = dolares.get(casa) or {}
        return d.get("venta"), (None if sin_deltas else d.get("d"))

    # --- Recolectar valores --------------------------------------------------
    ofi_v, _ = dl("oficial")
    blue_v, blue_d = dl("blue")
    mep_v, _ = dl("bolsa")
    ccl_v, _ = dl("contadoconliqui")

    rp_v = riesgo.get("valor")
    rp_d = None if sin_deltas else riesgo.get("d")

    mkt, _ = _leer_bucket("mercado")
    yf = (mkt.get("yfinance") or {}).get("indices") or {}
    mv = yf.get("Merval") or {}
    mv_v = mv.get("precio")
    mv_d = None if sin_deltas else mv.get("var_pct")

    # --- Filas del bloque de datos (etiqueta, valor, delta) -----------------
    # Se arman como tuplas y luego se alinean en columnas monoespaciadas.
    filas_dolar = [
        ("Oficial", _money(ofi_v), ""),                 # oficial sin variación
        ("Blue",    _money(blue_v), _pct(blue_d)),
        ("MEP",     _money(mep_v), ""),
        ("CCL",     _money(ccl_v), ""),
    ]
    filas_merc = []
    if rp_v is not None:
        filas_merc.append(("Riesgo país", f"{int(rp_v)} pb", _pct(rp_d)))
    if mv_v is not None:
        pts = f"{mv_v / 1_000_000:.2f}".replace(".", ",") + " M"
        filas_merc.append(("Merval", pts, _pct(mv_d)))

    filas_tasas = []
    if tipo == "cierre":
        bm = ofi.get("bcra_monetarias") or {}
        def tv(k):
            return _tasa((bm.get(k) or {}).get("valor"))
        for etq, k in (("BADLAR", "tasa_badlar"),
                       ("Plazo fijo", "tasa_plazo_fijo"),
                       ("TAMAR", "tasa_tamar")):
            val = tv(k)
            if val is not None:
                filas_tasas.append((etq, val + "%", ""))

    # --- Alineado en columnas (ancho fijo por columna) ----------------------
    todas = filas_dolar + filas_merc + filas_tasas
    w_lbl = max(len(f[0]) for f in todas)          # ancho etiqueta
    w_val = max(len(f[1]) for f in todas)          # ancho valor

    def fila(f):
        etq, val, delta = f
        s = f"{etq:<{w_lbl}}  {val:>{w_val}}"
        if delta:
            s += f"  {delta}"
        return s

    # --- Armado final (bloque monoespaciado con Markdown) -------------------
    titulo = TITULOS.get(tipo, "Guarismo")
    hora = ahora.strftime("%H:%M")

    cuerpo = []
    if sin_deltas:
        cuerpo.append("Mercado cerrado · último cierre")
        cuerpo.append("")
    cuerpo.append("DÓLAR")
    cuerpo += [fila(f) for f in filas_dolar]
    cuerpo.append("")
    cuerpo.append("MERCADO")
    cuerpo += [fila(f) for f in filas_merc]
    if filas_tasas:
        cuerpo.append("")
        cuerpo.append("TASAS (TNA)")
        cuerpo += [fila(f) for f in filas_tasas]

    bloque = "```\n" + "\n".join(cuerpo) + "\n```"

    texto = (
        f"📊 *{titulo}* · {_fecha_larga(ahora)} · {hora}\n\n"
        f"{bloque}\n\n"
        f"_Cada dato con su fuente y su hora, en_ guarismo.com.ar"
    )
    return texto, None


# ---------------------------------------------------------------------------
# Envío a Telegram
# ---------------------------------------------------------------------------
def enviar(texto):
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("falta TELEGRAM_TOKEN")
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": CHAT_ID, "text": texto,
              "parse_mode": "Markdown",
              "disable_web_page_preview": True},
        timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(f"Telegram {r.status_code}: {r.text}")
    return r.json()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    tipo = sys.argv[1] if len(sys.argv) > 1 else "cierre"
    if tipo not in TITULOS:
        print(f"Uso: python {sys.argv[0]} [apertura|media|cierre]")
        return 1

    texto, motivo = armar_texto(tipo)
    if texto is None:
        print(f"[skip] no se publica: {motivo}")
        return 0                       # no es error: es la guardia funcionando

    # Modo prueba: DRY=1 imprime sin enviar (para verificar el formato).
    if os.getenv("DRY") == "1":
        print("----- DRY RUN (no se envía) -----")
        print(texto)
        print("---------------------------------")
        return 0

    try:
        enviar(texto)
        print(f"[ok] publicado: {tipo}")
    except Exception as e:
        print(f"[error] {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
