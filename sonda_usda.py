"""SONDA USDA (FAS) — reconocimiento, NO es un modulo de boveda. DESCARTABLE.

No escribe en R2. No sella. No guarda estado. No usa secretos. Se borra junto
con .github/workflows/sonda-usda.yml cuando se decida como se cablean PSD y
GAIN (cola 5 del Tablero).

VUELTAS 1-4 (fc6a4ab295 · e199c11955 · f4561f2f64 · 0b79041f49):
  - El runner entra a apps.fas.usda.gov (la API) y a gain.fas.usda.gov.
  - La API tiene un controlador Search/ con GetSearchResults,
    GetQuickLinksResults?fromDate= y GetVersionHistory?id=.
  - En la app, la ruta /search pide los permisos 'Registered' o 'Activated'.
    Hipotesis: buscar exige usuario. Esta vuelta lo prueba.

VUELTA 5 (esta), en dos partes:
  A. Codigo: como arma la app el pedido a GetSearchResults y a
     GetQuickLinksResults (cuerpo, formato de fecha). Sin red extra.
  B. UNA llamada, SIN token, a cada uno de cinco GET de solo lectura. Se
     informa status y el principio de la respuesta. Nada se guarda.
     Los parametros (anio, fecha) estan ARMADOS: es una sonda, y va marcado.
"""

import hashlib
import re
import time

import requests

UA = {"User-Agent": "Guarismo/1.0 (+https://guarismo.com.ar; infoguarismo@gmail.com)",
      "Accept": "application/json"}
TIMEOUT = 60
PAUSA = 3.0
SCRIPT = "https://gain.fas.usda.gov/main-es2018.js"
API = "https://apps.fas.usda.gov/newgainapi/api"
ANCHO = 420

LLAMADAS = [
    ("Lookup/GetReportYears", "sin parametros"),
    ("Lookup/GetAllCountries", "sin parametros"),
    ("Search/GetQuickLinksResults?fromDate=2026-09-01", "fecha ARMADA"),
    ("Report/GetSummaryData?year=2026", "anio ARMADO"),
    ("Report/GetAllReportsBasedOnYear?reportYear=2026", "anio ARMADO"),
]


def contexto(js, patron, maximo=4):
    vistos, n = set(), 0
    for m in re.finditer(patron, js):
        t = re.sub(r"\s+", " ", js[max(0, m.start() - ANCHO // 2): m.start() + ANCHO // 2])
        if t in vistos:
            continue
        vistos.add(t)
        print(f"   …{t}…")
        n += 1
        if n >= maximo:
            break


def main():
    print("[sonda-usda v5] ¿se puede listar GAIN sin usuario? · solo lectura")
    print("\n== A. Codigo")
    try:
        js = requests.get(SCRIPT, headers=UA, timeout=90).content.decode("utf-8", "replace")
    except Exception as e:
        print(f"   ERROR {type(e).__name__}: {e}")
        js = ""
    for patron in (r"GetSearchResults", r"GetQuickLinksResults", r"class SearchFilter",
                   r"GetVersionHistory"):
        print(f"\n-- {patron}")
        contexto(js, patron)

    print("\n== B. Una llamada sin token a cada GET")
    for ruta, nota in LLAMADAS:
        url = f"{API}/{ruta}"
        print(f"\n-- {ruta}   [{nota}]")
        try:
            r = requests.get(url, headers=UA, timeout=TIMEOUT)
            b = r.content or b""
            print(f"   status {r.status_code} · {r.headers.get('Content-Type')} · {len(b)} bytes · "
                  f"sha {hashlib.sha256(b).hexdigest()[:12]}")
            if r.headers.get("WWW-Authenticate"):
                print(f"   WWW-Authenticate: {r.headers.get('WWW-Authenticate')}")
            print(f"   principio: {b[:500].decode('utf-8', 'replace')!r}")
        except Exception as e:
            print(f"   ERROR {type(e).__name__}: {e}")
        time.sleep(PAUSA)
    print("\n[sonda-usda v5] fin. Nada se guardo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
