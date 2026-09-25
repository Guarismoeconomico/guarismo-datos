"""SONDA USDA (FAS) — reconocimiento, NO es un modulo de boveda. DESCARTABLE.

No escribe en R2. No sella. No guarda estado. No usa secretos. Corre una vez
y reporta. Se borra junto con .github/workflows/sonda-usda.yml cuando se
decida como se cablean PSD y GAIN (cola 5 del Tablero).

VUELTA 1 (fc6a4ab295): el runner entra a apps.fas.usda.gov y gain.fas.usda.gov;
    www.fas.usda.gov da 403.
VUELTA 2 (e199c11955): la app habla con https://apps.fas.usda.gov/newgainapi/api.
VUELTA 3 (f4561f2f64): los servicios arman la ruta como
        this.endpoint = apiEndpoint + '/Report/'   y despues   this.endpoint + 'Metodo'
    Por eso los nombres no salian. El token es client_credentials con un
    secreto que viene del login eAuth: es para usuarios del FAS.

VUELTA 4 (esta): lista CADA "this.endpoint + 'Metodo'" con el controlador de
su servicio, y el codigo alrededor de "Search" y "Published", para encontrar
el metodo PUBLICO que lista informes. No llama a ninguna ruta de la API.
"""

import hashlib
import re

import requests

UA = {"User-Agent": "Guarismo/1.0 (+https://guarismo.com.ar; infoguarismo@gmail.com)"}
TIMEOUT = 90
SCRIPT = "https://gain.fas.usda.gov/main-es2018.js"
ANCHO = 260
MAXIMO = 20


def main():
    print("[sonda-usda v4] metodos de la API GAIN, por controlador · solo lectura")
    try:
        r = requests.get(SCRIPT, headers=UA, timeout=TIMEOUT)
    except Exception as e:
        print(f"   ERROR {type(e).__name__}: {e}")
        return 0
    b = r.content or b""
    print(f"   {r.status_code}  {len(b)} bytes  sha {hashlib.sha256(b).hexdigest()[:12]}  {SCRIPT}")
    js = b.decode("utf-8", "replace")

    # Cada servicio: "this.endpoint = ...apiEndpoint + '/Controlador/'" y, hasta el
    # proximo servicio, sus "this.endpoint + 'Metodo'".
    cortes = [(m.start(), m.group(1)) for m in re.finditer(
        r"this\.endpoint\s*=\s*[A-Za-z0-9_\.]*apiEndpoint\s*\+\s*['\"]/?([A-Za-z]+)/?['\"]", js)]
    print(f"\n== Servicios con endpoint propio: {len(cortes)}")
    por_ctrl = {}
    for k, (ini, ctrl) in enumerate(cortes):
        fin = cortes[k + 1][0] if k + 1 < len(cortes) else len(js)
        bloque = js[ini:fin]
        for m in re.finditer(r"this\.endpoint\s*\+\s*['\"`]([A-Za-z0-9_]+)(\??[^'\"`]{0,60})", bloque):
            por_ctrl.setdefault(ctrl, set()).add(m.group(1) + m.group(2))
    for ctrl in sorted(por_ctrl):
        print(f"\n   {ctrl}/")
        for met in sorted(por_ctrl[ctrl]):
            print(f"      {met}")

    for patron in (r"Search", r"Published", r"getSummaryData"):
        vistos, n = set(), 0
        ocurr = [m.start() for m in re.finditer(patron, js)]
        print(f"\n== {patron}  ({len(ocurr)} ocurrencias; hasta {MAXIMO} distintas)")
        for i in ocurr:
            t = re.sub(r"\s+", " ", js[max(0, i - ANCHO // 2): i + ANCHO // 2])
            if t in vistos:
                continue
            vistos.add(t)
            print(f"   …{t}…")
            n += 1
            if n >= MAXIMO:
                break
    print("\n[sonda-usda v4] fin. No se llamo a ninguna ruta de la API.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
