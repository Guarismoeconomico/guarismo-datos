"""SONDA USDA (FAS) — reconocimiento, NO es un modulo de boveda. DESCARTABLE.

No escribe en R2. No sella. No guarda estado. No usa secretos. Corre una vez
y reporta. Se borra junto con .github/workflows/sonda-usda.yml cuando se
decida como se cablean PSD y GAIN (cola 5 del Tablero).

VUELTA 1 (commit fc6a4ab295): el runner entra a apps.fas.usda.gov y a
gain.fas.usda.gov; www.fas.usda.gov le da 403.

VUELTA 2 (commit e199c11955): la aplicacion GAIN habla con
    https://apps.fas.usda.gov/newgainapi/api      (y .../newgainapi/token)
por dos controladores, Report/ y Lookup/. Los NOMBRES de los metodos se pegan
en el codigo aparte, por eso no salieron.

VUELTA 3 (esta): muestra el CODIGO ALREDEDOR de cada uso de Report/, Lookup/,
token y Authorization en main-es2018.js, para leer que metodo lista informes,
con que parametros, y si pide token. Solo el script propio de la aplicacion:
nada de Google. No llama a ninguna ruta de la API.
"""

import hashlib
import re

import requests

UA = {"User-Agent": "Guarismo/1.0 (+https://guarismo.com.ar; infoguarismo@gmail.com)"}
TIMEOUT = 90
SCRIPT = "https://gain.fas.usda.gov/main-es2018.js"
BUSCAR = [r"Report/", r"Lookup/", r"newgainapi/token", r"grant_type",
          r"Authorization", r"Bearer", r"DownloadReportByFileName"]
ANCHO = 220
MAXIMO = 25


def main():
    print("[sonda-usda v3] ¿con que metodo lista informes la app GAIN? · solo lectura")
    try:
        r = requests.get(SCRIPT, headers=UA, timeout=TIMEOUT)
    except Exception as e:
        print(f"   ERROR {type(e).__name__}: {e}")
        return 0
    b = r.content or b""
    print(f"   {r.status_code}  {len(b)} bytes  sha {hashlib.sha256(b).hexdigest()[:12]}  {SCRIPT}")
    js = b.decode("utf-8", "replace")

    print("\n== Nombres de metodo pegados a Report/ o Lookup/")
    nombres = sorted(set(re.findall(r'["\'`](?:Report|Lookup|ReportSchedule)/[A-Za-z0-9_]+', js)))
    for n in nombres:
        print(f"   {n.strip(chr(34)+chr(39)+'`')}")

    for patron in BUSCAR:
        vistos = set()
        ocurr = [m.start() for m in re.finditer(patron, js)]
        print(f"\n== {patron}  ({len(ocurr)} ocurrencias; se muestran hasta {MAXIMO} distintas)")
        n = 0
        for i in ocurr:
            trozo = js[max(0, i - ANCHO // 2): i + ANCHO // 2].replace("\n", " ")
            clave = re.sub(r"\s+", " ", trozo)
            if clave in vistos:
                continue
            vistos.add(clave)
            print(f"   …{clave}…")
            n += 1
            if n >= MAXIMO:
                break
    print("\n[sonda-usda v3] fin. No se llamo a ninguna ruta de la API.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
