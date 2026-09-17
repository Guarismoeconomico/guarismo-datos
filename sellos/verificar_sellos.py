#!/usr/bin/env python3
"""
Guarismo — Verificador de sellos (sin red)
===========================================

Para cada sellos/<fecha>.json comprueba que su .ots pruebe ESE archivo:
el "File sha256 hash" que declara `ots info` tiene que ser el sha256 del .json.

    python verificar_sellos.py            # todos los dias
    python verificar_sellos.py 2026-09-17 # uno

POR QUE EXISTE (16-sep-2026)
    Durante 58 dias el .json publicado NO fue el que probaba su .ots, y nada lo
    vio: el sellador solo miraba si el .ots existia, y verificar_archivo.py no
    mira el .ots. Lo que no se verifica automaticamente se rompe en silencio.

QUE CUENTA COMO FALLA (sale con codigo 1)
    NO COINCIDE      el .ots prueba otro contenido
    SIN .ots         un dia anterior a ayer sin anclar
    SIN BITCOIN      un dia anterior a ayer cuyo .ots sigue pendiente
Hoy y ayer pueden estar sin .ots o pendientes: el anclaje tarda horas.

Los <fecha>.sin_anclar.json (version posterior, sin prueba, conservada a la
vista) no se verifican: por nombre, no prometen nada.
"""
import datetime as dt
import hashlib
import pathlib
import re
import subprocess
import sys

DIR = pathlib.Path("sellos")
FECHA = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")


def info(ots):
    r = subprocess.run(["ots", "info", str(ots)], capture_output=True, text=True, timeout=60)
    m = re.search(r"File sha256 hash: ([0-9a-f]{64})", r.stdout)
    return (m.group(1) if m else None), r.stdout.count("BitcoinBlockHeaderAttestation")


def main(argv):
    hoy = dt.datetime.now(dt.timezone.utc).date()
    solo = set(argv)
    fallas = 0
    filas = sorted(p for p in DIR.glob("*.json") if FECHA.match(p.name))
    if solo:
        filas = [p for p in filas if p.name[:10] in solo]
    print(f"[sellos] {len(filas)} dia(s) a verificar")
    for p in filas:
        dia = dt.date.fromisoformat(p.name[:10])
        viejo = dia < hoy - dt.timedelta(days=1)
        ots = p.parent / f"{p.name}.ots"
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        if not ots.exists():
            estado = "SIN .ots" if viejo else "sin .ots todavia"
            fallas += viejo
            print(f"   {p.name}  {estado}")
            continue
        prueba, btc = info(ots)
        if prueba != sha:
            fallas += 1
            print(f"   {p.name}  NO COINCIDE  json={sha[:12]}  ots={str(prueba)[:12]}")
        elif btc == 0:
            estado = "SIN BITCOIN" if viejo else "pendiente en Bitcoin"
            fallas += viejo
            print(f"   {p.name}  coincide · {estado}")
        else:
            print(f"   {p.name}  OK · {btc} atestacion(es) de Bitcoin")
    print(f"[sellos] {len(filas) - fallas} bien · {fallas} con falla")
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
