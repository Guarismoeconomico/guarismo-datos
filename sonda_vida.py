#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sonda_vida.py — DESCARTABLE. Reconocimiento de solo lectura para la
consulta 19.4 (un listado vacio tiene que sonar), 26-sep-2026.

No escribe en R2 ni en Supabase. No usa secretos. Un GET por URL, sin
reintentos. Se borra junto con sonda-vida.yml.

Tres preguntas, con el codigo REAL de boveda_mecanicad.py (el de main):

  A. Las paginas reales de las 15 fuentes: ¿cuantos links les saca su
     etiquetador, pagina por pagina, y declaran article:modified_time?
     Premisa de la 19.4: ninguna da 0 con la fuente viva (salvo el
     calendario del BCRA, que no tiene links por diseño).

  B. Una pagina que NO existe en cada host de la mecanica D: ¿que status,
     que bytes, cuantos links? Si el status es 404, bajar() ya lo toma como
     hueco. Si es 200, la 19.4 es la que la frena. Estas URLs estan ARMADAS
     a proposito y el log lo dice: son la excepcion a "una URL se saca de la
     pagina", porque lo que se prueba es justamente una que no existe.

  C. SAGyP con un mes futuro (vacio con razon): ¿se distingue de la pagina
     de error del 25-sep? Es el residuo conocido de la 19.4 (mes en curso).
"""
import hashlib
import re
import sys
import time
from datetime import datetime, timezone

import requests

import boveda_mecanicad as m

PAUSA = 1.5
TIMEOUT = 60


def visible(html_txt, n=160):
    t = re.search(r"<title[^>]*>(.*?)</title>", html_txt, re.I | re.S)
    titulo = m._limpiar(t.group(1))[:70] if t else "-"
    cuerpo = re.search(r"<main\b[^>]*>(.*?)</main>", html_txt, re.I | re.S)
    base = cuerpo.group(1) if cuerpo else html_txt
    base = re.sub(r"<(script|style|noscript)\b.*?</\1>", " ", base, flags=re.I | re.S)
    return titulo, m._limpiar(base)[:n], bool(cuerpo)


def pedir(etiqueta, url, etiquetador=None, armada=False):
    marca = "ARMADA " if armada else ""
    try:
        r = requests.get(url, headers=m.UA, timeout=TIMEOUT)
    except Exception as e:
        print(f"{etiqueta:<44} {marca}ERROR {type(e).__name__}: {e}")
        print(f"    url {url}")
        return None
    cadena = " > ".join(str(h.status_code) for h in r.history) or "-"
    txt = r.content.decode("utf-8", errors="replace")
    titulo, texto, tiene_main = visible(txt)
    links = "-"
    if etiquetador:
        try:
            links = len(m.ETIQUETADORES[etiquetador](txt, url))
        except Exception as e:
            links = f"EXC {type(e).__name__}: {e}"
    mt = m.modified_time(txt)
    print(f"{etiqueta:<44} {marca}status {r.status_code} (redir {cadena}) · "
          f"{len(r.content)} bytes · sha {hashlib.sha256(r.content).hexdigest()[:12]} · "
          f"links {links} · mod {mt or 'NO'} · main {'si' if tiene_main else 'NO'}")
    print(f"    url   {url}")
    if r.url != url:
        print(f"    final {r.url}")
    print(f"    ct    {r.headers.get('Content-Type')}")
    print(f"    title {titulo!r}")
    print(f"    texto {texto!r}")
    return r, txt


def main():
    ahora = datetime.now(timezone.utc)
    print(f"[sonda-vida] {ahora.isoformat(timespec='seconds')} · "
          f"boveda_mecanicad con {len(m.FUENTES)} fuentes")

    print("\n===== A. Paginas reales, etiquetador real =====")
    wasde_txt = None
    for fuente, cfg in sorted(m.FUENTES.items()):
        for cl_pag, url_pag, mes in m.paginas_de(fuente, cfg, ahora):
            res = pedir(cl_pag, url_pag, cfg["etiquetador"])
            if res and fuente == "usda_wasde":
                wasde_txt = res[1]
            time.sleep(PAUSA)

    print("\n===== B. Paginas que no existen (URLs ARMADAS a proposito) =====")
    casos = [
        ("argentina.gob.ar inexistente",
         "https://www.argentina.gob.ar/economia/finanzas/guarismo-sonda-no-existe", "finanzas"),
        ("bcra.gob.ar inexistente",
         "https://www.bcra.gob.ar/guarismo-sonda-no-existe/", "rem"),
        ("magyp inexistente",
         m.FUENTES["sagyp_estimaciones"]["url"] + "guarismo-sonda-no-existe/", "sagyp"),
        ("esmis inexistente",
         "https://esmis.nal.usda.gov/publication/guarismo-sonda-no-existe", "wasde"),
    ]
    for etq, url, et in casos:
        pedir(etq, url, et, armada=True)
        time.sleep(PAUSA)

    # La pagina de EDICION del WASDE: la URL real sale del listado; despues
    # se le cambia la fecha por una que no existe.
    if wasde_txt:
        ediciones = [i for i in m.ETIQUETADORES["wasde"](
            wasde_txt, m.FUENTES["usda_wasde"]["url"]) if i.get("revisar_contenido")]
        if ediciones:
            real = ediciones[0]["url"]
            falsa = re.sub(r"\d{4}-\d{2}-\d{2}", "1999-01-01", real)
            print(f"(edicion real sacada del listado: {real})")
            if falsa != real:
                pedir("esmis edicion inexistente", falsa, None, armada=True)
            else:
                print("esmis edicion inexistente: la URL real no trae fecha AAAA-MM-DD; no se arma nada")
        else:
            print("esmis edicion inexistente: el listado no dio paginas de edicion")
    else:
        print("esmis edicion inexistente: no se pudo bajar el listado del WASDE")

    print("\n===== C. SAGyP: mes futuro (vacio con razon) =====")
    pedir("sagyp mes 2026-12 (futuro)",
          m.FUENTES["sagyp_estimaciones"]["url"] + "?mes=2026-12", "sagyp", armada=True)

    print("\n[sonda-vida] fin. No se escribio nada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
