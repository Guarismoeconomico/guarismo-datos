"""SONDA USDA (FAS) — reconocimiento, NO es un modulo de boveda. DESCARTABLE.

No escribe en R2. No sella. No guarda estado. No usa secretos. Corre una vez
y reporta. Se borra junto con .github/workflows/sonda-usda.yml cuando se
decida como se cablean PSD y GAIN (cola 5 del Tablero).

VUELTA 1 (25-sep-2026, commit fc6a4ab295): el runner entra a
apps.fas.usda.gov y a gain.fas.usda.gov; www.fas.usda.gov le da 403.

VUELTA 2 (esta): ¿DE DONDE SALE LA LISTA DE LOS GAIN?
  gain.fas.usda.gov es una aplicacion JavaScript ("Loading..."): el HTML no
  trae los informes. Los informes se bajan de
      apps.fas.usda.gov/newgainapi/api/Report/DownloadReportByFileName?fileName=...
  (visto en resultados de busqueda). Falta el LISTADO: que endpoint usa la
  aplicacion para buscar. Esta vuelta baja el HTML de gain.fas.usda.gov, sus
  scripts, y lista las rutas de API que aparecen escritas adentro. No llama a
  ninguna de esas rutas: solo las muestra.
"""

import hashlib
import re
import time
from urllib.parse import urljoin

import requests

UA = {"User-Agent": "Guarismo/1.0 (+https://guarismo.com.ar; infoguarismo@gmail.com)"}
TIMEOUT = 60
PAUSA = 2.0
RAIZ = "https://gain.fas.usda.gov/"


def bajar(url):
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
    except Exception as e:
        print(f"   ERROR   {url}  {type(e).__name__}: {e}")
        return None
    b = r.content or b""
    print(f"   {r.status_code}  {len(b):>9} bytes  sha {hashlib.sha256(b).hexdigest()[:12]}  "
          f"{r.headers.get('Content-Type')}  {url}")
    return b if r.status_code == 200 else None


def main():
    print("[sonda-usda v2] ¿de donde sale la lista de los GAIN? · solo lectura")
    print("\n== HTML de la aplicacion")
    html = bajar(RAIZ)
    if not html:
        return 0
    t = html.decode("utf-8", "replace")
    scripts = re.findall(r'<script[^>]+src="([^"]+)"', t, re.I)
    print(f"   scripts declarados: {len(scripts)}")
    for s in scripts:
        print(f"      {s}")
    rutas = {}
    print("\n== Scripts")
    for s in scripts:
        time.sleep(PAUSA)
        b = bajar(urljoin(RAIZ, s))
        if not b:
            continue
        js = b.decode("utf-8", "replace")
        for m in re.finditer(r'["\'`]((?:https?://[^"\'`\s]*)?/?(?:newgainapi|api)/[A-Za-z0-9_/\-\.\?=&{}$]+)["\'`]', js):
            rutas.setdefault(m.group(1), s)
        for m in re.finditer(r'https?://[a-z0-9\.\-]*usda\.gov[^"\'`\s]*', js):
            rutas.setdefault(m.group(0), s)
        # Rutas armadas en el codigo ("${base}/newgainapi/..."): el pedazo fijo.
        for m in re.finditer(r'newgainapi/api/[A-Za-z0-9_/\-]+', js):
            rutas.setdefault(m.group(0), s)
    print(f"\n== Rutas de API escritas en los scripts: {len(rutas)}")
    for r_, s in sorted(rutas.items()):
        print(f"   {r_}")
    print("\n[sonda-usda v2] fin. No se llamo a ninguna de esas rutas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
