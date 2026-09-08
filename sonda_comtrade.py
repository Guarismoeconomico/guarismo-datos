"""SONDA COMTRADE — reconocimiento de la API, NO es un modulo de boveda.

No escribe en R2. No sella. No guarda estado. Corre una vez y reporta.

Su unico proposito es contestar, contra la fuente real, lo que no se puede
contestar leyendo documentacion:

  1. La clave autentica.
  2. Que forma tiene el JSON de cada endpoint (campos reales, no supuestos).
  3. Cual es la cuota REAL del tier gratuito (la declaran los headers, no
     los paquetes de terceros).
  4. Cuantos registros devuelve una consulta espejo de tamano minimo.
  5. Si getDa acepta rango de fecha de publicacion (define si la capa de
     alarma es backfilleable Clase B o no).

Reglas que respeta:
  - La clave NUNCA se imprime, ni entera ni parcial.
  - Todas las llamadas van por https, con timeout, y con pausa entre medio.
  - Presupuesto de llamadas ACOTADO: no quema la cuota diaria.
  - Ninguna llamada falla el script: todo error se reporta y se sigue.
"""

import json
import os
import sys
import time

import requests

BASE = "https://comtradeapi.un.org"
CLAVE = os.environ.get("COMTRADE_KEY", "").strip()
TIMEOUT = 60
PAUSA = 2.0

# Presupuesto duro. Si la sonda intenta pasarse, aborta.
MAX_LLAMADAS = 12
_llamadas = 0

# Codigos M49. INFERIDOS, no verificados: la etapa 5 los contrasta contra
# el archivo de referencia de la propia fuente.
AR = "32"
SOCIOS = {"Brasil": "76", "China": "156", "EEUU": "842"}

# Headers que interesan. Se imprimen TODOS los de respuesta igual; esta lista
# es solo para destacar los de cuota, que son los que definen el diseno.
HEADERS_CUOTA = [
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "ratelimit-limit",
    "ratelimit-remaining",
    "ratelimit-reset",
    "retry-after",
    "x-quota-limit",
    "x-quota-remaining",
]


def _linea(txt=""):
    print(txt, flush=True)


def _titulo(n, txt):
    _linea()
    _linea("=" * 72)
    _linea(f"[sonda] ETAPA {n} — {txt}")
    _linea("=" * 72)


def _forma(obj, prefijo="", prof=0):
    """Describe la forma de un JSON sin volcarlo entero."""
    if prof > 2:
        _linea(f"{prefijo}...")
        return
    if isinstance(obj, dict):
        for k, v in list(obj.items())[:40]:
            t = type(v).__name__
            if isinstance(v, (dict, list)):
                n = len(v)
                _linea(f"{prefijo}{k}: {t}({n})")
                if isinstance(v, list) and v:
                    _forma(v[0], prefijo + "    ", prof + 1)
                elif isinstance(v, dict):
                    _forma(v, prefijo + "    ", prof + 1)
            else:
                s = str(v)
                if len(s) > 60:
                    s = s[:60] + "..."
                _linea(f"{prefijo}{k}: {t} = {s}")
    elif isinstance(obj, list):
        _linea(f"{prefijo}lista de {len(obj)}")
        if obj:
            _forma(obj[0], prefijo + "    ", prof + 1)
    else:
        _linea(f"{prefijo}{type(obj).__name__} = {obj}")


def llamar(etiqueta, ruta, params=None, con_clave=True):
    """Una llamada. Reporta todo. Nunca levanta excepcion."""
    global _llamadas
    if _llamadas >= MAX_LLAMADAS:
        _linea(f"[sonda] {etiqueta}: OMITIDA — presupuesto de {MAX_LLAMADAS} llamadas agotado")
        return None
    _llamadas += 1

    url = BASE + ruta
    headers = {"Accept": "application/json"}
    if con_clave:
        if not CLAVE:
            _linea(f"[sonda] {etiqueta}: ABORTA — no hay COMTRADE_KEY en el entorno")
            return None
        # La clave viaja en header, JAMAS en la URL.
        headers["Ocp-Apim-Subscription-Key"] = CLAVE

    _linea()
    _linea(f"[sonda] --> {etiqueta}")
    _linea(f"[sonda]     GET {url}")
    _linea(f"[sonda]     params={params or {}}  clave={'si' if con_clave else 'NO'}")

    t0 = time.time()
    try:
        r = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
    except Exception as e:
        _linea(f"[sonda]     FALLO DE RED: {type(e).__name__}: {e}")
        return None
    ms = int((time.time() - t0) * 1000)

    _linea(f"[sonda]     status={r.status_code}  {ms} ms  {len(r.content)} bytes")
    _linea(f"[sonda]     content-type={r.headers.get('Content-Type', '(sin)')}")

    # La URL final: si hubo redireccion, se ve.
    if r.history:
        _linea(f"[sonda]     REDIRECCIONES: {len(r.history)}")
        for h in r.history:
            _linea(f"[sonda]        {h.status_code} {h.url}")
        _linea(f"[sonda]        final: {r.url}")

    cuota = {k: v for k, v in r.headers.items() if k.lower() in HEADERS_CUOTA}
    if cuota:
        _linea(f"[sonda]     *** HEADERS DE CUOTA: {cuota}")
    else:
        _linea("[sonda]     (sin headers de cuota reconocidos)")
    _linea(f"[sonda]     todos los headers: {dict(r.headers)}")

    if r.status_code != 200:
        cuerpo = r.text[:600]
        _linea(f"[sonda]     cuerpo del error: {cuerpo}")
        return None

    try:
        datos = r.json()
    except Exception as e:
        _linea(f"[sonda]     NO ES JSON ({e}). Primeros 300 bytes:")
        _linea(f"[sonda]     {r.text[:300]}")
        return None

    _linea("[sonda]     forma del JSON:")
    _forma(datos, "[sonda]        ")

    # Si hay una lista de registros, mostrar UNO entero: los nombres de campo
    # son la mitad del diseno del modulo.
    filas = None
    if isinstance(datos, dict):
        for k in ("data", "results", "value", "items"):
            if isinstance(datos.get(k), list):
                filas = datos[k]
                break
    elif isinstance(datos, list):
        filas = datos

    if filas is not None:
        _linea(f"[sonda]     REGISTROS: {len(filas)}")
        if filas:
            _linea("[sonda]     primer registro COMPLETO:")
            crudo = json.dumps(filas[0], ensure_ascii=False, indent=2)
            for ln in crudo.splitlines()[:60]:
                _linea(f"[sonda]        {ln}")

    time.sleep(PAUSA)
    return datos


def main():
    _linea("[sonda] SONDA COMTRADE — reconocimiento. NO escribe nada, en ningun lado.")
    _linea(f"[sonda] clave presente: {'SI' if CLAVE else 'NO'} · largo: {len(CLAVE)}")
    _linea(f"[sonda] presupuesto: {MAX_LLAMADAS} llamadas · pausa {PAUSA}s")

    if not CLAVE:
        _linea("[sonda] SIN CLAVE. Cargar COMTRADE_KEY en Secrets. Aborto.")
        sys.exit(1)

    # ---------------------------------------------------------------
    _titulo(1, "La clave autentica (endpoint verificado en la doc)")
    # Sin clave dio 401. Con clave tiene que dar 200.
    llamar("getLiveUpdate", "/data/v1/getLiveUpdate")

    # ---------------------------------------------------------------
    _titulo(2, "getDa — la capa de ALARMA (la que reemplaza al hash)")
    # La doc nombra el endpoint pelado. La firma con path parts es INFERIDA:
    # se prueban las dos y el log dice cual anda.
    llamar("getDa pelado", "/data/v1/getDa")
    llamar(
        "getDa con path C/M/HS",
        "/data/v1/getDa/C/M/HS",
        {"reporterCode": SOCIOS["Brasil"], "period": "202601"},
    )

    # ---------------------------------------------------------------
    _titulo(3, "getDa por RANGO DE FECHA DE PUBLICACION")
    # Define si la capa de alarma es backfilleable (Clase B) o no.
    llamar(
        "getDa publishedDateFrom/To",
        "/data/v1/getDa/C/M/HS",
        {
            "reporterCode": SOCIOS["Brasil"],
            "publishedDateFrom": "2026-01-01",
            "publishedDateTo": "2026-09-08",
        },
    )

    # ---------------------------------------------------------------
    _titulo(4, "El DATO espejo, en su version mas chica posible")
    # Brasil declarando su comercio con Argentina, un mes, total agregado.
    # Si esto anda, el modulo entero es esta llamada con otros parametros.
    llamar(
        "get C/M/HS Brasil<-Argentina TOTAL",
        "/data/v1/get/C/M/HS",
        {
            "reporterCode": SOCIOS["Brasil"],
            "period": "202601",
            "partnerCode": AR,
            "cmdCode": "TOTAL",
            "flowCode": "M",
        },
    )

    # ---------------------------------------------------------------
    _titulo(5, "Los codigos de pais, contra la fuente y no contra mi memoria")
    # AR=32, BR=76 son INFERIDOS. Esta etapa los verifica o los desmiente.
    llamar("referencia de reporters", "/files/v1/app/reference/Reporters.json", con_clave=False)
    llamar("referencia de partners", "/files/v1/app/reference/partnerAreas.json", con_clave=False)

    # ---------------------------------------------------------------
    _titulo(6, "El endpoint de preview, que segun la doc no pide clave")
    # Si anda sin clave, es una red de seguridad para probar sin gastar cuota.
    llamar(
        "preview sin clave",
        "/public/v1/preview/C/M/HS",
        {
            "reporterCode": SOCIOS["Brasil"],
            "period": "202601",
            "partnerCode": AR,
            "cmdCode": "TOTAL",
            "flowCode": "M",
        },
        con_clave=False,
    )

    # ---------------------------------------------------------------
    _linea()
    _linea("=" * 72)
    _linea(f"[sonda] FIN · {_llamadas} llamadas gastadas de {MAX_LLAMADAS}")
    _linea("[sonda] No se escribio nada. Ni R2, ni Supabase, ni estado.")
    _linea("=" * 72)


if __name__ == "__main__":
    main()
