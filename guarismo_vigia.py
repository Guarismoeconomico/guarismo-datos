#!/usr/bin/env python3
"""
Guarismo — Vigía del archivo sellado
=====================================

Monitor INDEPENDIENTE del conector. Corre por separado y falla ruidosamente
(exit 1) cuando algo anda mal, para que GitHub Actions marque el workflow en
rojo y mande el mail automático. Sin infraestructura nueva, sin secretos.

    python guarismo_vigia.py


POR QUÉ EXISTE
--------------
`to_historico()` en el conector se traga todos los errores a propósito: el
archivo nunca puede romper el pipeline de datos. El costo de esa decisión es
que una falla del archivo es SILENCIOSA — workflow en verde, cero filas
escritas, nadie se entera. Este script cierra ese agujero desde afuera.


QUÉ CHEQUEA
-----------
1. PIPELINE VIVO
   `guarismo_latest` se reescribe en CADA corrida, haya cambiado el dato o no.
   Si su `updated_at` está viejo, el conector dejó de correr.

   Ojo: el archivo NO sirve para medir esto. Como dedupea, un fin de semana
   sin capturas nuevas es lo correcto, no una falla. Por eso miramos `latest`.

2. CADENA ÍNTEGRA
   Que ninguna captura haya sido alterada, borrada o reordenada.

3. ARCHIVO NO VACÍO
   Que cada bucket tenga al menos una captura.


Usa solo la clave `anon` (pública, de solo lectura). No necesita secretos.
"""

import hashlib
import sys
import datetime as dt

import requests

SUPABASE_URL = "https://rudepkizcatkhqprqjfw.supabase.co"
ANON_KEY = "sb_publishable_nwqxJVCewzhySYY1JZ6Lxw_DhbY0w8J"

# El job intradía corre cada 30 min. Toleramos 2 corridas perdidas + margen.
MAX_ATRASO_MIN = 95

BUCKETS = ("oficial", "agregador")
TIMEOUT = 30
PAGINA = 1000


def _sha(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def _headers():
    return {"apikey": ANON_KEY, "Authorization": f"Bearer {ANON_KEY}"}


# ---------------------------------------------------------------------------
# 1. ¿El pipeline sigue vivo?
# ---------------------------------------------------------------------------
def chequear_pipeline():
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/guarismo_latest",
        params={"id": "eq.agregador", "select": "updated_at"},
        headers=_headers(), timeout=TIMEOUT)
    r.raise_for_status()
    filas = r.json()
    if not filas:
        return ["guarismo_latest no tiene fila 'agregador': el conector nunca escribió."]

    crudo = filas[0]["updated_at"]
    ts = dt.datetime.fromisoformat(crudo.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)

    atraso = (dt.datetime.now(dt.timezone.utc) - ts).total_seconds() / 60
    print(f"   última escritura : {crudo}")
    print(f"   atraso           : {atraso:.0f} min (tolerado: {MAX_ATRASO_MIN})")

    if atraso > MAX_ATRASO_MIN:
        return [f"PIPELINE CAÍDO: {atraso:.0f} min sin escribir "
                f"(máximo tolerado {MAX_ATRASO_MIN}). "
                f"Revisar cron-job.org y GitHub Actions."]
    return []


# ---------------------------------------------------------------------------
# 2. ¿La cadena está intacta?
# ---------------------------------------------------------------------------
def bajar(bucket):
    filas, desde = [], 0
    while True:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/guarismo_historico",
            params={"bucket": f"eq.{bucket}",
                    "select": "id,capturado_en,contenido,hash_contenido,hash_previo,hash_cadena",
                    "order": "id.asc"},
            headers={**_headers(), "Range": f"{desde}-{desde + PAGINA - 1}"},
            timeout=TIMEOUT)
        r.raise_for_status()
        lote = r.json()
        filas.extend(lote)
        if len(lote) < PAGINA:
            return filas
        desde += PAGINA


def verificar_cadena(filas):
    errores, previo = [], None
    for pos, f in enumerate(filas):
        ident = f"id={f['id']}"
        if _sha(f["contenido"]) != f["hash_contenido"]:
            errores.append(f"{ident}: contenido alterado")
        if f["hash_previo"] != previo:
            errores.append(f"{ident}: cadena cortada")
        if _sha((f["hash_previo"] or "") + f["hash_contenido"]) != f["hash_cadena"]:
            errores.append(f"{ident}: eslabón inválido")
        if pos > 0 and f["hash_previo"] is None:
            errores.append(f"{ident}: génesis duplicado")
        previo = f["hash_cadena"]
    return errores


# ---------------------------------------------------------------------------
def main():
    if ANON_KEY.startswith("PEGAR"):
        print("✗ Falta configurar ANON_KEY.")
        return 2

    print("=" * 62)
    print("  GUARISMO — Vigía del archivo")
    print("=" * 62)

    problemas = []

    print("\n▸ 1. Pipeline vivo")
    try:
        problemas += chequear_pipeline()
    except Exception as e:
        problemas.append(f"no se pudo consultar guarismo_latest: {e}")

    print("\n▸ 2. Integridad de la cadena")
    for b in BUCKETS:
        try:
            filas = bajar(b)
        except Exception as e:
            problemas.append(f"[{b}] no se pudo leer el archivo: {e}")
            continue

        if not filas:
            problemas.append(f"[{b}] el archivo está vacío")
            continue

        errs = verificar_cadena(filas)
        estado = "✓ íntegra" if not errs else f"✗ {len(errs)} problema(s)"
        print(f"   {b:<10} {len(filas):>5} capturas   {estado}")
        problemas += [f"[{b}] {e}" for e in errs]

    print("\n" + "=" * 62)
    if problemas:
        print(f"  ✗ {len(problemas)} PROBLEMA(S):\n")
        for p in problemas:
            print(f"    · {p}")
        print("=" * 62)
        return 1

    print("  ✓ todo en orden")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
