#!/usr/bin/env python3
"""
Guarismo — Exportar el archivo sellado para respaldo
=====================================================

Baja guarismo_historico completo y lo escribe a un JSON. GitHub Actions lo
sube como artifact, dejando un respaldo del activo más valioso FUERA de
Supabase, sin depender de una segunda cuenta.

    python exportar_archivo.py            # -> respaldo/archivo-YYYY-MM-DD.json

Solo lee (clave publishable). No toca nada.
"""

import datetime as dt
import json
import os
import pathlib
import sys

import requests

for _f in (sys.stdout, sys.stderr):
    try:
        _f.reconfigure(encoding="utf-8")
    except Exception:
        pass

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://rudepkizcatkhqprqjfw.supabase.co")
# Publishable/anon: read-only, pública. En Actions la inyecta el workflow.
ANON = os.getenv("SUPABASE_ANON", "PEGAR_ACA_LA_CLAVE_PUBLISHABLE")

TABLA = "guarismo_historico"
PAGINA = 1000
TIMEOUT = 60


def bajar_todo():
    headers = {"apikey": ANON, "Authorization": f"Bearer {ANON}"}
    filas, desde = [], 0
    while True:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/{TABLA}",
            params={"select": "*", "order": "id.asc"},
            headers={**headers, "Range": f"{desde}-{desde + PAGINA - 1}"},
            timeout=TIMEOUT)
        r.raise_for_status()
        lote = r.json()
        filas.extend(lote)
        if len(lote) < PAGINA:
            return filas
        desde += PAGINA


def main():
    if ANON.startswith("PEGAR"):
        print("✗ Falta SUPABASE_ANON.")
        return 2
    try:
        filas = bajar_todo()
    except Exception as e:
        print(f"✗ No se pudo bajar el archivo: {e}")
        return 1

    hoy = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    destino = pathlib.Path("respaldo")
    destino.mkdir(exist_ok=True)
    ruta = destino / f"archivo-{hoy}.json"

    doc = {
        "guarismo": "respaldo del archivo sellado",
        "generado_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "tabla": TABLA,
        "filas": len(filas),
        "datos": filas,
    }
    ruta.write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str),
                    encoding="utf-8")

    mb = ruta.stat().st_size / 1e6
    print(f"✓ {len(filas)} filas · {mb:.2f} MB · {ruta}")

    # Resumen por bucket, para verlo de un vistazo en el log
    por_bucket = {}
    for f in filas:
        por_bucket[f.get("bucket", "?")] = por_bucket.get(f.get("bucket", "?"), 0) + 1
    for b, n in sorted(por_bucket.items()):
        print(f"   {b:<12} {n} capturas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
