"""SONDA USDA (FAS) — reconocimiento, NO es un modulo de boveda. DESCARTABLE.

No escribe en R2. No sella. No guarda estado. No usa secretos. Corre una vez
y reporta. Se borra junto con .github/workflows/sonda-usda.yml cuando se
decida como se cablean PSD y GAIN (cola 5 del Tablero).

LA PREGUNTA: ¿el runner puede bajar de los hosts del FAS? usda.gov le da 403
y ESMIS no. Que yo pueda leerlos desde el chat no prueba nada: se prueba aca.

POR QUE ESTAS URLS, Y DE DONDE SALEN (25-sep-2026)
  - ESMIS dejo de publicar las circulares del FAS: Grain y Oilseeds World
    Markets and Trade figuran con "Latest Release Aug 12 2025".
  - Las circulares vigentes estan en apps.fas.usda.gov/psdonline/circulars/:
    URL FIJA que se reescribe cada mes (la de hoy dice septiembre 2026).
  - Los GAIN de Argentina viven en www.fas.usda.gov: una pagina por informe
    y el PDF en /data/gain-report/AAAA/MM/.
  Todas salieron de resultados de busqueda. Ninguna se armo, salvo UNA: la
  de psdDataPublications, que el propio PDF del FAS cita en http:// y aca va
  en https:// (mismo host). Esta marcada.

QUE REPORTA POR URL: status, cadena de redirecciones, content-type, bytes,
sha256 (12), Last-Modified, ETag, y si el cuerpo parece HTML donde se
esperaba un PDF (el 200 no prueba nada).

Ninguna llamada corta el script: todo error se reporta y se sigue.
"""

import hashlib
import time

import requests

UA = {"User-Agent": "Guarismo/1.0 (+https://guarismo.com.ar; infoguarismo@gmail.com)"}
TIMEOUT = 60
PAUSA = 2.0

URLS = [
    # (que es, url, lo que se espera)
    ("control ESMIS (anda)",
     "https://esmis.nal.usda.gov/publication/world-agricultural-supply-and-demand-estimates", "html"),
    ("ESMIS Grain WM&T (listado)",
     "https://esmis.nal.usda.gov/publication/grain-world-markets-and-trade", "html"),
    ("circular Grain vigente (URL fija)",
     "https://apps.fas.usda.gov/psdonline/circulars/grain.pdf", "pdf"),
    ("circular Oilseeds vigente (URL fija)",
     "https://apps.fas.usda.gov/psdonline/circulars/oilseeds.pdf", "pdf"),
    ("pagina de publicaciones PSD [http->https, ARMADA]",
     "https://apps.fas.usda.gov/psdonline/psdDataPublications.aspx", "html"),
    ("Oilseeds may-2026 en fas.usda.gov",
     "https://www.fas.usda.gov/sites/default/files/2026-05/oilseeds.pdf", "pdf"),
    ("GAIN AR2026-0006, pagina del informe",
     "https://www.fas.usda.gov/data/gain/2026/04/argentina-grain-and-feed-annual", "html"),
    ("GAIN AR2026-0011, PDF",
     "https://www.fas.usda.gov/data/gain-report/2026/07/Grain%20and%20Feed%20Update_Buenos%20Aires_Argentina_AR2026-0011.pdf", "pdf"),
    ("GAIN cronograma 2026 (gain.fas.usda.gov)",
     "https://gain.fas.usda.gov/assets/GAIN%20Report%20Schedule.pdf", "pdf"),
]


def parece_html(b):
    c = b[:2048].lstrip().lower()
    return c.startswith(b"<!doctype") or c.startswith(b"<html") or b"<html" in c[:1024]


def sondear(que, url, espera):
    print(f"\n== {que}\n   {url}")
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
    except Exception as e:
        print(f"   ERROR   {type(e).__name__}: {e}")
        return "ERROR"
    for s in r.history:
        print(f"   salto   {s.status_code} -> {s.headers.get('Location')}")
    b = r.content or b""
    ct = r.headers.get("Content-Type")
    html = parece_html(b)
    print(f"   status  {r.status_code}   final {r.url}")
    print(f"   tipo    {ct}   bytes {len(b)}   sha {hashlib.sha256(b).hexdigest()[:12]}")
    print(f"   headers Last-Modified={r.headers.get('Last-Modified')}  ETag={r.headers.get('ETag')}"
          f"  Date={r.headers.get('Date')}")
    if espera == "pdf":
        es_pdf = b[:5] == b"%PDF-"
        print(f"   cuerpo  {'PDF de verdad' if es_pdf else ('HTML donde se esperaba PDF' if html else 'otra cosa: ' + repr(b[:40]))}")
        veredicto = "OK" if (r.status_code == 200 and es_pdf) else "NO"
    else:
        print(f"   cuerpo  {'HTML' if html else 'no parece HTML: ' + repr(b[:40])}"
              f"   primeros 80: {b[:200].decode('utf-8', 'replace').split(chr(10))[0][:80]!r}")
        veredicto = "OK" if (r.status_code == 200 and html) else "NO"
    print(f"   => {veredicto}")
    return veredicto


def main():
    print("[sonda-usda] reconocimiento de hosts del FAS desde el runner · solo lectura")
    res = []
    for que, url, espera in URLS:
        res.append((que, sondear(que, url, espera)))
        time.sleep(PAUSA)
    print("\n[sonda-usda] RESUMEN")
    for que, v in res:
        print(f"   {v:<5} {que}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
